"""
引き継ぎ文・ストーリー生成

- 引き継ぎ文：月切り替え時に前月のアクションログから GLM-4.7 で生成し DB にキャッシュ
- ストーリー：各行のストーリーを、その月の流れに沿った文章として GLM-4.7 で生成
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "shared"))
from supabase_client import (
    safe_db,
    get_action_log_handoff,
    upsert_action_log_handoff,
    list_action_logs,
    list_all_action_logs,
    update_action_log,
    get_archivelog_by_id,
    get_holding,
)
from llm_client import call_glm

from data_service import get_monthly_data

import calendar

_COMMANDS_DIR = Path(__file__).resolve().parent.parent / ".claude" / "commands"


def _prev_month(year: int, month: int) -> tuple[int, int]:
    """前月の (year, month) を返す。"""
    if month == 1:
        return year - 1, 12
    return year, month - 1


def _build_prompt(ticker: str, prev_year: int, prev_month: int, rows: list[dict]) -> str:
    """前月データからサブエージェントに渡すプロンプトを構築する。"""
    lines = [
        f"銘柄: {ticker}",
        f"前月: {prev_year}年{prev_month}月",
        "",
        "前月のアクション一覧:",
    ]
    for r in rows:
        date = str(r.get("action_date", ""))[:10]
        action = r.get("action_text", "")
        story = r.get("story", "")
        invested = r.get("cumulative_invested", "")
        pnl = r.get("pnl", "")
        lines.append(f"- {date}: {action}")
        if story:
            lines.append(f"  ストーリー: {story}")
        if invested:
            lines.append(f"  入れたお金（累計）: {invested:,}円" if isinstance(invested, (int, float)) else f"  入れたお金（累計）: {invested}")
        if pnl is not None and pnl != "":
            lines.append(f"  損益: {pnl:,}円" if isinstance(pnl, (int, float)) else f"  損益: {pnl}")

    lines.append("")
    lines.append("上記の前月データをもとに、今月への引き継ぎ文を生成してください。")
    return "\n".join(lines)


async def generate_handoff(ticker: str, year: int, month: int) -> str | None:
    """引き継ぎ文を LLM（Opus）で生成する。前月データがない場合は None。"""
    prev_year, prev_month = _prev_month(year, month)
    prev_rows = get_monthly_data(ticker, prev_year, prev_month)

    if not prev_rows:
        return None

    prompt = _build_prompt(ticker, prev_year, prev_month, prev_rows)
    file_path = str(_COMMANDS_DIR / "handoff-generator.md")

    result = await call_glm(prompt, file_path=file_path, model="glm-4.7")
    text = result.text if result else None
    return text.strip() if text else None


async def get_or_generate_handoff(ticker: str, year: int, month: int) -> str | None:
    """引き継ぎ文を取得する。DB にキャッシュ済みならそれを返す。なければ生成して保存する。

    初月（前月データなし）の場合は None を返す。
    """
    year_month = f"{year:04d}-{month:02d}"

    cached = safe_db(get_action_log_handoff, ticker, year_month)
    if cached and cached.get("handoff_text"):
        return cached["handoff_text"]

    text = await generate_handoff(ticker, year, month)
    if text:
        safe_db(upsert_action_log_handoff, ticker, year_month, text)
    return text


def get_cached_handoff(ticker: str, year: int, month: int) -> str | None:
    """DB キャッシュのみをチェックする（LLM 呼び出しなし・同期関数）。"""
    year_month = f"{year:04d}-{month:02d}"
    cached = safe_db(get_action_log_handoff, ticker, year_month)
    if cached and cached.get("handoff_text"):
        return cached["handoff_text"]
    return None


# ── ストーリー生成 ─────────────────────────────────────────


def _build_story_prompt(
    ticker: str,
    handoff_text: str | None,
    previous_stories: list[dict],
    current_row: dict,
    monitor_summary: str = "",
    holding: dict | None = None,
) -> str:
    """ストーリー生成サブエージェントに渡すプロンプトを構築する。"""
    lines = [f"銘柄: {ticker}", ""]

    if handoff_text:
        lines.append(f"引き継ぎ文（先月のまとめ）: {handoff_text}")
        lines.append("")

    if previous_stories:
        lines.append("今月のこれまでのストーリー:")
        for s in previous_stories:
            date = str(s.get("action_date", ""))[:10]
            action = s.get("action_text", "")
            story = s.get("story", "")
            lines.append(f"- {date} [{action}]: {story}")
        lines.append("")

    if monitor_summary:
        lines.append(f"議論サマリー（専門的な内容なので初心者向けに噛み砕いて）: {monitor_summary}")
        lines.append("")

    h = holding or {}
    shares = int(h.get("shares") or 0)
    avg_cost = float(h.get("avg_cost") or 0)
    total_assets = current_row.get("total_assets")
    cumulative = current_row.get("cumulative_invested")
    if shares > 0:
        lines.append("現在の保有状況:")
        lines.append(f"- 保有株数: {shares}株")
        lines.append(f"- 平均取得単価: {avg_cost:,.2f}")
        if total_assets is not None:
            lines.append(f"- 総資産評価額: {int(float(total_assets)):,}円")
        if cumulative is not None:
            lines.append(f"- 累計投資額: {int(float(cumulative)):,}円")
        lines.append("")

    lines.append("今回の行の情報:")
    date = str(current_row.get("action_date", ""))[:10]
    action = current_row.get("action_text", "")
    lines.append(f"- 日付: {date}")
    lines.append(f"- アクション: {action}")

    raw_story = current_row.get("_raw_summary", "") or current_row.get("story", "")
    if raw_story:
        lines.append(f"- 元データ: {raw_story}")
    else:
        lines.append(f"- 元データ: （なし。アクション内容と前後の流れから簡潔に書いてください）")

    decision = current_row.get("decision", "")
    if decision:
        lines.append(f"- 判定: {decision}")

    money_in = current_row.get("money_in", 0)
    if money_in:
        lines.append(f"- 投入額: {money_in:,}円" if isinstance(money_in, (int, float)) else f"- 投入額: {money_in}")

    pnl = current_row.get("pnl")
    if pnl is not None and pnl != 0:
        lines.append(f"- 損益: {pnl:,}円" if isinstance(pnl, (int, float)) else f"- 損益: {pnl}")

    lines.append("")
    lines.append("上記の情報をもとに、今月のストーリーの流れに沿った文章を生成してください。")
    return "\n".join(lines)


async def generate_story(
    ticker: str,
    current_row: dict,
    year: int | None = None,
    month: int | None = None,
) -> str:
    """1行分のストーリーを、月の流れに沿った文章として Opus で生成する。"""
    date_str = str(current_row.get("action_date", ""))[:10]
    if year is None or month is None:
        if len(date_str) >= 7:
            year = int(date_str[:4])
            month = int(date_str[5:7])
        else:
            return current_row.get("story", "") or ""

    handoff_text = get_cached_handoff(ticker, year, month)

    from_date = f"{year:04d}-{month:02d}-01"
    last_day = calendar.monthrange(year, month)[1]
    to_date = f"{year:04d}-{month:02d}-{last_day:02d}"
    month_rows = safe_db(list_action_logs, ticker, from_date, to_date) or []

    current_id = current_row.get("id")
    previous_stories = []
    for r in month_rows:
        if r["id"] == current_id:
            break
        if r.get("story"):
            previous_stories.append(r)

    monitor_summary = ""
    archive_id = current_row.get("archive_id")
    if archive_id:
        arc = safe_db(get_archivelog_by_id, archive_id)
        if arc:
            mon = arc.get("monitor") or {}
            if isinstance(mon, dict):
                monitor_summary = mon.get("summary", "") or ""

    holding = safe_db(get_holding, ticker) or {}

    prompt = _build_story_prompt(
        ticker, handoff_text, previous_stories, current_row,
        monitor_summary=monitor_summary,
        holding=holding,
    )
    file_path = str(_COMMANDS_DIR / "story-generator.md")

    result = await call_glm(prompt, file_path=file_path, model="glm-4.7")
    text = result.text if result else None
    return text.strip() if text else (current_row.get("story", "") or "")


async def regenerate_all_stories(ticker: str) -> int:
    """指定銘柄の全行のストーリーを先頭から順に再生成する。"""
    all_rows = safe_db(list_all_action_logs, ticker) or []
    if not all_rows:
        return 0

    count = 0
    for row in all_rows:
        date_str = str(row.get("action_date", ""))[:10]
        if len(date_str) < 7:
            continue

        row["_raw_summary"] = row.get("story", "")

        new_story = await generate_story(ticker, row)
        if new_story:
            safe_db(update_action_log, row["id"], story=new_story)
            row["story"] = new_story
            count += 1
            print(f"  [{ticker}] {date_str}: ストーリー再生成完了")

    return count
