"""
Issue 統合モジュール

品質レビューで作成された [品質レビュー] Issue から同じ根本原因を持つ問題を検出し、
[統合] Issue としてまとめる。LLM エージェントが根本原因の類似性を判断する。

Usage:
    python issue_consolidator.py                # 実行
    python issue_consolidator.py --dry-run      # プレビューのみ
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import anyio
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "shared"))

from AgentUtil import call_agent

PROJECT_ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = PROJECT_ROOT / ".claude" / "commands"
MAX_CONSOLIDATION_RETRIES = 2


def _gh(args: list[str], timeout: int = 30) -> str | None:
    try:
        result = subprocess.run(
            ["gh"] + args,
            capture_output=True, text=True, encoding="utf-8", timeout=timeout,
        )
        return result.stdout.strip() if result.returncode == 0 else None
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None


def _gh_json(args: list[str], timeout: int = 30) -> list | dict | None:
    raw = _gh(args, timeout)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def fetch_open_issues(prefix: str) -> list[dict]:
    """指定プレフィックスの open Issue を取得する。"""
    issues = _gh_json([
        "issue", "list",
        "--label", "night-worker",
        "--search", f"{prefix} in:title",
        "--state", "open",
        "--json", "number,title,body,labels",
        "--limit", "100",
    ])
    if not issues:
        return []
    return [i for i in issues if i.get("title", "").startswith(prefix)]


def parse_review_problems(body: str) -> list[dict]:
    """[品質レビュー] Issue のボディから問題リストを抽出する。"""
    problems = []
    pattern = (
        r"### \d+\.\s+(.+?)（(.+?)、重要度：(.+?)）"
        r"\s*\n\s*\n(.*?)(?=\n---|\n### |\*このIssue|\Z)"
    )
    for m in re.finditer(pattern, body, re.DOTALL):
        problems.append({
            "title": m.group(1).strip(),
            "category": m.group(2).strip(),
            "severity": m.group(3).strip(),
            "detail": m.group(4).strip(),
        })
    return problems


def _build_consolidation_prompt(
    review_issues: list[dict],
    existing_consolidations: list[dict],
) -> str:
    """統合エージェントに渡すプロンプトを組み立てる。"""
    parts = ["# Issue 統合分析\n"]

    parts.append("## オープン中の [品質レビュー] Issue\n")
    for issue in review_issues:
        num = issue["number"]
        title = issue["title"]
        problems = issue.get("_problems", [])
        parts.append(f"### #{num}: {title}")
        if problems:
            for p in problems:
                parts.append(
                    f"- **{p['title']}**（{p['category']}、{p['severity']}）"
                    f"：{p['detail'][:200]}"
                )
        else:
            parts.append("（問題抽出なし）")
        parts.append("")

    parts.append("## オープン中の [統合] Issue\n")
    if existing_consolidations:
        for issue in existing_consolidations:
            num = issue["number"]
            title = issue["title"]
            body = issue.get("body", "")[:500]
            parts.append(f"### #{num}: {title}")
            parts.append(body)
            parts.append("")
    else:
        parts.append("（なし）\n")

    parts.append("上記を分析し、統合プランを YAML で出力してください。")
    return "\n".join(parts)


def _parse_consolidation_plan(agent_output: str) -> dict | None:
    """エージェント出力から統合プランの YAML をパースする。"""
    if not agent_output:
        return None
    blocks = re.findall(r"```ya?ml\s*\n(.*?)```", agent_output, re.DOTALL)
    for block in reversed(blocks):
        try:
            data = yaml.safe_load(block.strip())
            if isinstance(data, dict) and "consolidation_plan" in data:
                return data["consolidation_plan"]
        except yaml.YAMLError:
            continue
    return None


def _create_consolidation_issue(item: dict) -> str | None:
    """新規 [統合] Issue を作成する。"""
    title = item.get("title", "")
    if not title.startswith("[統合]"):
        title = f"[統合] {title}"

    source_issues = item.get("source_issues", [])
    source_refs = ", ".join(f"#{n}" for n in source_issues)

    body = "\n".join([
        "## 概要\n",
        item.get("summary", ""),
        "\n## 問題点\n",
        item.get("problems_description", ""),
        "\n## 原因（推定）\n",
        item.get("estimated_cause", ""),
        f"\n## 該当する元イシュー\n\n{source_refs}",
    ])

    labels = list(item.get("labels", ["night-worker"]))
    if "night-worker" not in labels:
        labels.insert(0, "night-worker")

    return _gh([
        "issue", "create",
        "--title", title,
        "--body", body,
        "--label", ",".join(labels),
    ])


def _update_consolidation_issue(item: dict) -> bool:
    """既存 [統合] Issue にコメントを追加する。"""
    issue_number = item.get("issue_number")
    if not issue_number:
        return False

    new_refs = item.get("new_source_issues", [])
    refs = ", ".join(f"#{n}" for n in new_refs)
    comment = item.get("comment", f"新たな該当イシュー：{refs}")

    result = _gh([
        "issue", "comment", str(issue_number),
        "--body", comment,
    ])
    return result is not None


async def run_consolidation(dry_run: bool = False) -> dict:
    """メインの統合実行。"""
    print("\n[NightWorker] Issue 統合分析 開始")

    review_issues = fetch_open_issues("[品質レビュー]")
    consolidation_issues = fetch_open_issues("[統合]")

    print(f"  [品質レビュー] オープン：{len(review_issues)}件")
    print(f"  [統合] オープン：{len(consolidation_issues)}件")

    if len(review_issues) < 2:
        print("  品質レビュー Issue が2件未満のため統合不要。スキップ。")
        return {"new": 0, "updated": 0, "skipped": len(review_issues)}

    for issue in review_issues:
        issue["_problems"] = parse_review_problems(issue.get("body", ""))
        print(f"  #{issue['number']}: 問題 {len(issue['_problems'])}件抽出")

    prompt = _build_consolidation_prompt(review_issues, consolidation_issues)
    agent_file = AGENTS_DIR / "issue-consolidator.md"

    plan = None
    for attempt in range(1, MAX_CONSOLIDATION_RETRIES + 1):
        print(f"  LLM 統合分析 実行中...（試行{attempt}/{MAX_CONSOLIDATION_RETRIES}）")
        llm_result = await call_agent(prompt, file_path=str(agent_file), show_cost=True)
        if llm_result and llm_result.text:
            plan = _parse_consolidation_plan(llm_result.text)
            if plan:
                break
            print(f"  YAML パース失敗（試行{attempt}）")
        else:
            print(f"  エージェント応答なし（試行{attempt}）")

    if not plan:
        print("  統合プラン取得失敗。スキップ。")
        return {"new": 0, "updated": 0, "skipped": len(review_issues)}

    new_items = plan.get("new", [])
    update_items = plan.get("update", [])
    skip_items = plan.get("skip", [])

    print(
        f"  統合プラン：新規 {len(new_items)}件、"
        f"更新 {len(update_items)}件、対象外 {len(skip_items)}件"
    )

    created = 0
    updated = 0

    for item in new_items:
        title = item.get("title", "?")
        sources = item.get("source_issues", [])
        if dry_run:
            print(f"  [dry-run] 新規統合：{title}（元：{sources}）")
            continue
        url = _create_consolidation_issue(item)
        if url:
            print(f"  統合 Issue 作成：{url}")
            created += 1
        else:
            print(f"  統合 Issue 作成失敗：{title}")

    for item in update_items:
        num = item.get("issue_number", "?")
        new_refs = item.get("new_source_issues", [])
        if dry_run:
            print(f"  [dry-run] 既存 #{num} に追加：{new_refs}")
            continue
        if _update_consolidation_issue(item):
            print(f"  #{num} にコメント追加")
            updated += 1
        else:
            print(f"  #{num} へのコメント追加失敗")

    result = {"new": created, "updated": updated, "skipped": len(skip_items)}
    print(
        f"\n[NightWorker] Issue 統合分析 完了"
        f"（新規：{created}件、更新：{updated}件、対象外：{len(skip_items)}件）"
    )
    return result


async def main():
    parser = argparse.ArgumentParser(description="Issue 統合分析")
    parser.add_argument("--dry-run", action="store_true", help="統合を実行せずプレビューのみ")
    args = parser.parse_args()
    await run_consolidation(dry_run=args.dry_run)


if __name__ == "__main__":
    anyio.run(main)
