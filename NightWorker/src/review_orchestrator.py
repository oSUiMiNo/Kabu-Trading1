"""
Archive 品質レビュー オーケストレーター

archive テーブルのレコードを自動レビューし、品質問題を検出する。
検出結果は archive_reviews テーブルに記録し、重大な問題は GitHub Issue を作成する。

Usage:
    python review_orchestrator.py                    # デフォルト（最大20件）
    python review_orchestrator.py --max-reviews 5    # 件数指定
    python review_orchestrator.py --dry-run           # Issue作成なし（プレビューのみ）
"""
import argparse
import json
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import anyio
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "shared"))
from supabase_client import (
    safe_db,
    fetch_unreviewed_archives,
    create_archive_review,
)

from AgentUtil import call_agent
from issue_creator import create_issue, should_create_issue

PROJECT_ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = PROJECT_ROOT / ".claude" / "commands"
MAX_REVIEW_RETRIES = 2
MAX_ISSUES_PER_RUN = 5


def _truncate(text: str | None, length: int = 500) -> str:
    if not text:
        return "(なし)"
    return text[:length] + "..." if len(text) > length else text


def run_static_checks(record: dict) -> list[dict]:
    """データ整合性の静的チェック（LLM不使用）。"""
    issues = []

    lanes = record.get("lanes")
    final_judge = record.get("final_judge")
    newplan_full = record.get("newplan_full")
    verdict = record.get("verdict")
    active = record.get("active", False)
    created_at = record.get("created_at", "")

    if lanes and not final_judge:
        issues.append({
            "category": "データ整合性",
            "severity": "中",
            "title": "Discussion中断の可能性",
            "detail": "lanes（議論ログ）は存在しますが、final_judge（最終判定）がありません。Discussionが途中で中断した可能性があります。",
        })

    if final_judge and not newplan_full:
        issues.append({
            "category": "データ整合性",
            "severity": "高",
            "title": "Planning未完了",
            "detail": "final_judge は存在しますが newplan_full（投資プラン）がありません。Planningが失敗した可能性があります。",
        })

    if active and created_at:
        try:
            ct = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
            age = datetime.now(timezone.utc) - ct
            if age > timedelta(hours=48):
                issues.append({
                    "category": "データ整合性",
                    "severity": "高",
                    "title": "active=True のまま長時間放置",
                    "detail": f"作成から{age.days}日{age.seconds // 3600}時間経過しても active=True のままです。パイプラインが途中で停止した可能性があります。",
                })
        except (ValueError, TypeError):
            pass

    if verdict and newplan_full:
        try:
            parsed_plan = yaml.safe_load(newplan_full) if isinstance(newplan_full, str) else newplan_full
            if isinstance(parsed_plan, dict):
                decision = parsed_plan.get("decision", {})
                plan_verdict = decision.get("decision_final", "")
                if plan_verdict and verdict and plan_verdict.upper() != verdict.upper():
                    issues.append({
                        "category": "データ整合性",
                        "severity": "高",
                        "title": "verdict不整合",
                        "detail": f"archive.verdict は「{verdict}」ですが、newplan_full 内の decision_final は「{plan_verdict}」です。",
                    })
        except (yaml.YAMLError, AttributeError):
            pass

    return issues


def _build_review_prompt(record: dict) -> str:
    """レビューエージェントに渡すプロンプトを組み立てる。"""
    archive_id = record.get("id", "?")
    ticker = record.get("ticker", "?")
    mode = record.get("mode", "?")
    span = record.get("span", "?")
    created_at = record.get("created_at", "?")
    verdict = record.get("verdict", "?")

    parts = [
        f"# Archive #{archive_id} レビュー\n",
        f"- 銘柄：{ticker}",
        f"- モード：{mode}",
        f"- 期間：{span}",
        f"- 作成日時：{created_at}",
        f"- verdict：{verdict}\n",
    ]

    monitor = record.get("monitor")
    if monitor:
        if isinstance(monitor, str):
            try:
                monitor = json.loads(monitor)
            except (ValueError, TypeError):
                pass
        if isinstance(monitor, dict):
            parts.append("## Monitor結果")
            parts.append(f"- result：{monitor.get('result', '?')}")
            parts.append(f"- risk_flags：{monitor.get('risk_flags', [])}")
            parts.append(f"- summary：{_truncate(monitor.get('summary'), 300)}")
            if monitor.get("ng_reason"):
                parts.append(f"- ng_reason：{_truncate(monitor.get('ng_reason'), 300)}")
            parts.append("")

    lanes = record.get("lanes")
    if lanes:
        if isinstance(lanes, str):
            try:
                lanes = json.loads(lanes)
            except (ValueError, TypeError):
                lanes = {}
        if isinstance(lanes, dict):
            parts.append("## Discussion（lanes）")
            for lane_id, lane_data in sorted(lanes.items()):
                if not isinstance(lane_data, dict):
                    continue
                parts.append(f"### レーン {lane_id}：{lane_data.get('theme', '?')}")
                parts.append(f"- opinion_1：{_truncate(lane_data.get('opinion_1'), 500)}")
                parts.append(f"- opinion_2：{_truncate(lane_data.get('opinion_2'), 500)}")
                parts.append(f"- judge_md：{_truncate(lane_data.get('judge_md'), 500)}")
                parts.append("")

    final_judge = record.get("final_judge")
    if final_judge:
        if isinstance(final_judge, str):
            try:
                final_judge = json.loads(final_judge)
            except (ValueError, TypeError):
                pass
        if isinstance(final_judge, dict):
            parts.append("## 最終判定（final_judge）")
            parts.append(f"```json\n{json.dumps(final_judge, ensure_ascii=False, indent=2)[:1000]}\n```\n")

    newplan_full = record.get("newplan_full")
    if newplan_full:
        parts.append("## 投資プラン（newplan_full）")
        parts.append(f"```yaml\n{_truncate(str(newplan_full), 1000)}\n```\n")

    parts.append("上記の内容を品質基準に照らしてレビューし、YAML形式で結果を出力してください。")

    return "\n".join(parts)


def _parse_review_result(agent_output: str) -> dict | None:
    """エージェント出力からYAMLブロックをパースする。"""
    if not agent_output:
        return None
    blocks = re.findall(r"```ya?ml\s*\n(.*?)```", agent_output, re.DOTALL)
    for block in reversed(blocks):
        try:
            data = yaml.safe_load(block.strip())
            review = data.get("review_result", data) if isinstance(data, dict) else None
            if isinstance(review, dict) and "overall_quality" in review:
                return review
        except yaml.YAMLError:
            continue
    return None


async def review_one_archive(record: dict, dry_run: bool = False) -> dict:
    """1件のarchiveをレビューする。"""
    archive_id = record["id"]
    ticker = record.get("ticker", "?")

    print(f"\n{'='*50}")
    print(f"[レビュー] archive #{archive_id} ({ticker})")
    print(f"{'='*50}")

    static_issues = run_static_checks(record)
    if static_issues:
        print(f"  静的チェック：{len(static_issues)}件の問題検出")
        for si in static_issues:
            print(f"    - [{si['severity']}] {si['title']}")

    llm_review = None
    llm_result = None
    has_discussion = bool(record.get("lanes"))

    if has_discussion:
        print(f"  LLMレビュー実行中...")
        prompt = _build_review_prompt(record)
        agent_file = AGENTS_DIR / "archive-reviewer.md"

        for attempt in range(1, MAX_REVIEW_RETRIES + 1):
            llm_result = await call_agent(prompt, file_path=str(agent_file), show_cost=True)
            if llm_result and llm_result.text:
                llm_review = _parse_review_result(llm_result.text)
                if llm_review:
                    print(f"  LLMレビュー完了：{llm_review.get('overall_quality', '?')}")
                    break
                else:
                    print(f"  YAMLパース失敗（試行{attempt}/{MAX_REVIEW_RETRIES}）")
            else:
                print(f"  エージェント応答なし（試行{attempt}/{MAX_REVIEW_RETRIES}）")
    else:
        print(f"  lanes なし → LLMレビュースキップ")

    all_issues = static_issues + (llm_review.get("issues", []) if llm_review else [])
    overall = (llm_review or {}).get("overall_quality", "良好")

    if static_issues and not llm_review:
        high = sum(1 for i in static_issues if i["severity"] == "高")
        mid = sum(1 for i in static_issues if i["severity"] == "中")
        if high > 0:
            overall = "問題あり"
        elif mid >= 2:
            overall = "要改善"

    issue_url = None
    if should_create_issue(all_issues, overall):
        if dry_run:
            print(f"  [dry-run] Issue作成対象（スキップ）")
        else:
            issue_url = create_issue(archive_id, ticker, record, all_issues, overall)
            if issue_url:
                print(f"  Issue作成：{issue_url}")

    cost = None
    if llm_result and hasattr(llm_result, "cost"):
        cost = llm_result.cost

    safe_db(
        create_archive_review,
        archive_id=archive_id,
        overall_quality=overall,
        issues_json=all_issues,
        issue_url=issue_url,
        review_cost_usd=cost,
    )
    print(f"  レビュー記録完了（{overall}）")

    return {
        "archive_id": archive_id,
        "ticker": ticker,
        "overall_quality": overall,
        "issue_count": len(all_issues),
        "issue_url": issue_url,
    }


async def run_review(max_reviews: int = 20, dry_run: bool = False):
    """メインのレビュー実行ループ。"""
    JST = timezone(timedelta(hours=9))
    now = datetime.now(JST)
    print(f"[NightWorker] 開始 {now.strftime('%Y-%m-%d %H:%M %Z')}")
    print(f"  max_reviews={max_reviews}, dry_run={dry_run}")

    records = safe_db(fetch_unreviewed_archives, max_reviews)
    if not records:
        print("[NightWorker] レビュー対象なし。終了。")
        return

    print(f"[NightWorker] 対象：{len(records)}件")

    results = []
    issues_created = 0

    for record in records:
        if issues_created >= MAX_ISSUES_PER_RUN and not dry_run:
            print(f"  Issue作成上限（{MAX_ISSUES_PER_RUN}件）に達したため、残りはログ記録のみ")

        r = await review_one_archive(record, dry_run=(dry_run or issues_created >= MAX_ISSUES_PER_RUN))
        results.append(r)
        if r.get("issue_url"):
            issues_created += 1

    good = sum(1 for r in results if r["overall_quality"] == "良好")
    needs_work = sum(1 for r in results if r["overall_quality"] == "要改善")
    problematic = sum(1 for r in results if r["overall_quality"] == "問題あり")

    print(f"\n[NightWorker] 完了")
    print(f"  良好：{good}件 / 要改善：{needs_work}件 / 問題あり：{problematic}件")
    print(f"  Issue作成：{issues_created}件")


async def main():
    parser = argparse.ArgumentParser(description="Archive 品質レビュー")
    parser.add_argument("--max-reviews", type=int, default=20, help="レビュー上限数")
    parser.add_argument("--dry-run", action="store_true", help="Issue作成なし（プレビューのみ）")
    args = parser.parse_args()
    await run_review(max_reviews=args.max_reviews, dry_run=args.dry_run)


if __name__ == "__main__":
    anyio.run(main)
