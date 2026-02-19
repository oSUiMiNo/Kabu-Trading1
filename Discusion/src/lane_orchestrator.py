"""
レーンオーケストレーター

1レーン分のフロー（議論 → Opinion → Judge）を一気通貫で実行する。
レーンは互いに独立しており、並列実行可能。

フロー:
  1. run_discussion() → 議論ログ生成（Analyst vs Devil's Advocate）
  2. run_single_opinion() ×2 → Opinion生成（並列）
  3. run_single_judge() → 一致判定
  4. LaneResult を返す
"""
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import anyio

from discussion_orchestrator import (
    LOGS_DIR,
    run_discussion,
    get_last_export,
)
from opinion_orchestrator import (
    run_single_opinion,
)
from judge_orchestrator import (
    run_single_judge,
    get_next_judge_num,
)
from AgentUtil import AgentResult, side_ja


@dataclass
class LaneResult:
    """1レーンの実行結果"""
    レーン番号: int
    一致度: str  # "AGREED" | "DISAGREED" | "ERROR"
    支持側: str | None
    合計コスト: float
    db_data: dict = field(default_factory=dict)


def get_set_log_path(ticker: str, set_num: int, session_dir: Path | None = None) -> Path:
    """レーン番号付きのログパスを返す"""
    base = session_dir if session_dir else LOGS_DIR
    return base / f"{ticker.upper()}_set{set_num}.md"


async def run_lane(
    ticker: str,
    set_num: int,
    max_rounds: int = 6,
    initial_prompt: str | None = None,
    opinions_per_lane: int = 2,
    mode: str = "buy",
    theme: str | None = None,
    horizon: str = "mid",
    session_dir: Path | None = None,
) -> LaneResult:
    """
    1レーン分のフローを一気通貫で実行する。

    処理フロー:
      1. run_discussion()         → 議論ログ
      2. run_single_opinion() ×N → Opinion生成（並列）
      3. run_single_judge()      → 一致判定
      4. LaneResult を返す（DB書き込みは呼び出し元が行う）
    """
    t = ticker.upper()
    log_path = get_set_log_path(ticker, set_num, session_dir)
    total_cost = 0.0

    print(f"\n{'='*60}")
    print(f"=== レーン{set_num} 開始: {t} ===")
    print(f"{'='*60}")

    try:
        # --- フェーズ1: 議論 ---
        print(f"\n[レーン{set_num}] 議論 開始")
        await run_discussion(
            ticker,
            max_rounds=max_rounds,
            initial_prompt=initial_prompt,
            log_path=log_path,
            mode=mode,
            theme=theme,
            horizon=horizon,
        )
        print(f"[レーン{set_num}] 議論 完了")

        # --- フェーズ2: 意見生成 ---
        print(f"\n[レーン{set_num}] 意見 ({opinions_per_lane}体) 開始")

        opinion_nums = [1 + i for i in range(opinions_per_lane)]
        opinion_results: list[AgentResult] = [None] * opinions_per_lane

        async def _run_opinion(idx: int, opinion_num: int):
            opinion_results[idx] = await run_single_opinion(ticker, set_num, opinion_num, mode, session_dir=session_dir)

        async with anyio.create_task_group() as tg:
            for idx, on in enumerate(opinion_nums):
                tg.start_soon(_run_opinion, idx, on)

        for r in opinion_results:
            if r and r.cost:
                total_cost += r.cost

        print(f"[レーン{set_num}] 意見 完了")

        # --- フェーズ3: 判定 ---
        print(f"\n[レーン{set_num}] 判定 開始")

        opinion_a_text = opinion_results[0].text if opinion_results[0] and opinion_results[0].text else ""
        opinion_b_text = opinion_results[1].text if opinion_results[1] and opinion_results[1].text else ""

        judge_num = get_next_judge_num(ticker, set_num, session_dir)
        judge_result = await run_single_judge(
            ticker,
            set_num,
            opinion_nums[0],
            opinion_a_text,
            opinion_nums[1],
            opinion_b_text,
            judge_num,
            mode,
            session_dir=session_dir,
        )

        if judge_result and judge_result.cost:
            total_cost += judge_result.cost

        print(f"[レーン{set_num}] 判定 完了")

        # --- 結果解析 ---
        content = judge_result.text if judge_result and judge_result.text else ""
        agreement = "ERROR"
        agreed_side = None

        if content:
            m_agree = re.search(r"(?:一致度|agreement):\s*\**(\w+)", content)
            if m_agree:
                agreement = m_agree.group(1)
            m_side = re.search(r"(?:一致支持側|agreed_supported_side):\s*\**(\S+?)(?:\*|$|\s)", content)
            if m_side:
                agreed_side = m_side.group(1)

        # DB用データを構築（書き込みは parallel_orchestrator が行う）
        _final_export = get_last_export(log_path)
        _discussion_md = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
        lane_db_data = {
            "theme": theme,
            "discussion_md": _discussion_md,
            "final_stance": _final_export.get("stance") if _final_export else None,
            "agreement": agreement,
            "agreed_side": agreed_side,
            "judge_md": content,
        }

        # レーン完了表示
        print(f"\n{'='*60}")
        print(f"=== レーン{set_num} 完了 ===")
        print(f"  結果: {agreement}")
        if agreed_side:
            print(f"  支持側: {side_ja(agreed_side)}")
        print(f"  コスト: ${total_cost:.4f}")
        print(f"{'='*60}")

        return LaneResult(
            レーン番号=set_num,
            一致度=agreement,
            支持側=agreed_side,
            合計コスト=total_cost,
            db_data=lane_db_data,
        )

    except Exception as e:
        print(f"\n[レーン{set_num}] エラー発生: {e}")
        return LaneResult(
            レーン番号=set_num,
            一致度="ERROR",
            支持側=None,
            合計コスト=total_cost,
        )


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("使い方: python lane_orchestrator.py <銘柄コード> <レーン番号> [モード] [最大ラウンド数] [意見数] [追加指示]")
        print("  モード: '買う' or '売る' (デフォルト: 買う)")
        print("例: python lane_orchestrator.py NVDA 1 買う 6 2 '特にAI市場に注目して'")
        sys.exit(1)

    ticker = sys.argv[1]
    set_num = int(sys.argv[2])
    _mode_map = {"買う": "buy", "売る": "sell", "buy": "buy", "sell": "sell"}
    mode = _mode_map.get(sys.argv[3], "buy") if len(sys.argv) > 3 else "buy"
    max_rounds = int(sys.argv[4]) if len(sys.argv) > 4 else 6
    opinions_per_lane = int(sys.argv[5]) if len(sys.argv) > 5 else 2
    initial_prompt = sys.argv[6] if len(sys.argv) > 6 else None

    result = anyio.run(
        lambda: run_lane(ticker, set_num, max_rounds, initial_prompt, opinions_per_lane, mode)
    )
    print(f"\n最終結果: {result}")
