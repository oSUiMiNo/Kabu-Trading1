"""
GitHub Issue 作成モジュール

品質レビューで検出された問題を GitHub Issue として作成する。
gh CLI を使用するため、GH_TOKEN 環境変数が必要。
"""
import subprocess


_CATEGORY_LABEL = {
    "Discussion品質": "discussion-quality",
    "Planning品質": "planning-quality",
    "データ整合性": "data-integrity",
}

_SEVERITY_JA = {"高": "高", "中": "中", "低": "低"}

_WHY_IT_MATTERS = {
    "Discussion中断の可能性": "議論が完了していないため、最終判定が不正確な可能性があります。",
    "Planning未完了": "投資プランが生成されておらず、watchlist の更新が不完全です。",
    "active=True のまま長時間放置": "パイプラインが途中で停止し、後続の処理が実行されていません。",
    "verdict不整合": "議論の結論とプランの判定が食い違っており、投資判断の信頼性に影響します。",
}


def should_create_issue(issues: list[dict], overall_quality: str) -> bool:
    """Issue作成が必要かどうかを判定する。"""
    if not issues:
        return False
    if overall_quality == "問題あり":
        return True
    high = sum(1 for i in issues if i.get("severity") == "高")
    mid = sum(1 for i in issues if i.get("severity") == "中")
    return high >= 1 or mid >= 2


def _check_existing_issue(archive_id: int) -> bool:
    """同一 archive_id の Issue が既に存在するか確認する。"""
    try:
        result = subprocess.run(
            ["gh", "issue", "list", "--label", "night-worker",
             "--search", f"archive #{archive_id}", "--state", "all", "--json", "number"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip() not in ("", "[]"):
            return True
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return False


def _build_issue_body(
    archive_id: int,
    ticker: str,
    record: dict,
    issues: list[dict],
    overall_quality: str,
) -> str:
    """Issue の本文を構築する。"""
    verdict = record.get("verdict", "?")
    mode = record.get("mode", "?")
    span = record.get("span", "?")
    created_at = record.get("created_at", "?")

    monitor = record.get("monitor") or {}
    monitor_result = monitor.get("result", "?") if isinstance(monitor, dict) else "?"

    lines = [
        "## 品質レビュー結果\n",
        "| 項目 | 内容 |",
        "|------|------|",
        f"| アーカイブID | #{archive_id} |",
        f"| 銘柄 | {ticker} |",
        f"| モード | {mode} |",
        f"| 期間 | {span} |",
        f"| 作成日時 | {created_at} |",
        f"| verdict | {verdict} |",
        f"| Monitor結果 | {monitor_result} |",
        f"| **総合評価** | **{overall_quality}** |",
        "",
        "## 検出された問題\n",
    ]

    for i, issue in enumerate(issues, 1):
        title = issue.get("title", "不明")
        category = issue.get("category", "不明")
        severity = issue.get("severity", "不明")
        detail = issue.get("detail", "詳細なし")
        why = _WHY_IT_MATTERS.get(title, "品質基準を満たしていない可能性があります。")

        lines.append(f"### {i}. {title}（{category}、重要度：{severity}）\n")
        lines.append(f"{detail}\n")
        lines.append(f"**なぜこれが問題なのか：** {why}\n")
        lines.append("---\n")

    lines.append("*このIssueは Archive品質レビューエージェント により自動作成されました。*")

    return "\n".join(lines)


def create_issue(
    archive_id: int,
    ticker: str,
    record: dict,
    issues: list[dict],
    overall_quality: str,
) -> str | None:
    """GitHub Issue を作成し、Issue URL を返す。既存Issueがあればスキップ。"""
    if _check_existing_issue(archive_id):
        print(f"  Issue 既存のためスキップ（archive #{archive_id}）")
        return None

    labels = ["night-worker"]
    high = sum(1 for i in issues if i.get("severity") == "高")
    if high > 0:
        labels.append("severity:high")
    else:
        labels.append("severity:medium")

    categories = {i.get("category", "") for i in issues}
    for cat, label in _CATEGORY_LABEL.items():
        if cat in categories:
            labels.append(label)

    title = f"[品質レビュー] {ticker} (archive #{archive_id}) - {overall_quality}"
    body = _build_issue_body(archive_id, ticker, record, issues, overall_quality)

    try:
        result = subprocess.run(
            ["gh", "issue", "create",
             "--title", title,
             "--body", body,
             "--label", ",".join(labels)],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            url = result.stdout.strip()
            return url
        else:
            print(f"  Issue作成失敗：{result.stderr.strip()}")
            return None
    except subprocess.TimeoutExpired:
        print(f"  Issue作成タイムアウト")
        return None
    except FileNotFoundError:
        print(f"  gh コマンドが見つかりません")
        return None
