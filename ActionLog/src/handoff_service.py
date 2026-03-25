"""
引き継ぎ文の生成・キャッシュ

月切り替え時に前月のアクションログから引き継ぎ文を Opus で生成し、
DB にキャッシュする。2回目以降はキャッシュから即座に返す。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "shared"))
from supabase_client import (
    safe_db,
    get_action_log_handoff,
    upsert_action_log_handoff,
)

from data_service import get_monthly_data
from AgentUtil import call_agent, extract_text

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

    result = await call_agent(prompt, file_path=file_path)
    text = extract_text(result)
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
