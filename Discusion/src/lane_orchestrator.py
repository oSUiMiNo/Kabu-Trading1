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
from dataclasses import dataclass
from pathlib import Path

import anyio

from discussion_orchestrator import (
    LOGS_DIR,
    run_discussion,
    get_last_export,
)
from opinion_orchestrator import (
    run_single_opinion,
    get_next_opinion_num,
)
from judge_orchestrator import (
    run_single_judge,
    get_next_judge_num,
)
from AgentUtil import AgentResult


@dataclass
class LaneResult:
    """1レーンの実行結果"""
    set_num: int
    agreement: str  # "AGREED" | "DISAGREED" | "ERROR"
    agreed_side: str | None  # 一致した場合の支持側
    total_cost: float


def get_set_log_path(ticker: str, set_num: int) -> Path:
    """セット番号付きのログパスを返す"""
    return LOGS_DIR / f"{ticker.upper()}_set{set_num}.md"


async def run_lane(
    ticker: str,
    set_num: int,
    max_rounds: int = 6,
    initial_prompt: str | None = None,
    opinions_per_lane: int = 2,
    mode: str = "buy",
) -> LaneResult:
    """
    1レーン分のフローを一気通貫で実行する。

    処理フロー:
      1. run_discussion()         → 議論ログ生成
      2. run_single_opinion() ×N → Opinion生成（並列）
      3. run_single_judge()      → 一致判定
      4. LaneResult を返す

    Args:
        ticker: 銘柄コード（例: "NVDA"）
        set_num: セット番号（1, 2, 3, ...）
        max_rounds: 議論の最大ラウンド数
        initial_prompt: 初回Analystへの追加指示（省略可）
        opinions_per_lane: このレーンで生成するOpinion数（デフォルト: 2）
        mode: 議論モード（"buy" = 買う/買わない、"sell" = 売る/売らない）

    Returns:
        LaneResult: レーンの実行結果
    """
    t = ticker.upper()
    log_path = get_set_log_path(ticker, set_num)
    total_cost = 0.0

    print(f"\n{'='*60}")
    print(f"=== Lane {set_num} 開始: {t} ===")
    print(f"{'='*60}")

    try:
        # --- Phase 1: 議論 ---
        print(f"\n[Lane {set_num}] Phase 1: 議論開始")
        await run_discussion(
            ticker,
            max_rounds=max_rounds,
            initial_prompt=initial_prompt,
            log_path=log_path,
            mode=mode,
        )
        print(f"[Lane {set_num}] Phase 1: 議論完了")

        # --- Phase 2: Opinion ---
        print(f"\n[Lane {set_num}] Phase 2: Opinion開始 ({opinions_per_lane}体)")

        # opinion番号を事前に決定（競合回避）
        base_opinion_num = get_next_opinion_num(ticker, set_num)
        opinion_nums = [base_opinion_num + i for i in range(opinions_per_lane)]

        # 並列実行
        opinion_results: list[AgentResult] = [None] * opinions_per_lane

        async def _run_opinion(idx: int, opinion_num: int):
            opinion_results[idx] = await run_single_opinion(ticker, set_num, opinion_num, mode)

        async with anyio.create_task_group() as tg:
            for idx, on in enumerate(opinion_nums):
                tg.start_soon(_run_opinion, idx, on)

        # opinionコスト集計
        for r in opinion_results:
            if r and r.cost:
                total_cost += r.cost

        print(f"[Lane {set_num}] Phase 2: Opinion完了")

        # --- Phase 3: Judge ---
        print(f"\n[Lane {set_num}] Phase 3: Judge開始")

        judge_num = get_next_judge_num(ticker, set_num)
        judge_result = await run_single_judge(
            ticker,
            set_num,
            opinion_nums[0],
            opinion_nums[1],
            judge_num,
            mode,
        )

        if judge_result and judge_result.cost:
            total_cost += judge_result.cost

        print(f"[Lane {set_num}] Phase 3: Judge完了")

        # --- 結果解析 ---
        judge_path = LOGS_DIR / f"{t}_set{set_num}_judge_{judge_num}.md"
        agreement = "ERROR"
        agreed_side = None

        if judge_path.exists():
            content = judge_path.read_text(encoding="utf-8")
            # 日本語フィールド名を優先、フォールバックで英語も対応
            # **AGREED** のようなマークダウン太字にも対応
            m_agree = re.search(r"(?:一致度|agreement):\s*\**(\w+)", content)
            if m_agree:
                agreement = m_agree.group(1)
            m_side = re.search(r"(?:一致支持側|agreed_supported_side):\s*\**(\S+?)(?:\*|$|\s)", content)
            if m_side:
                agreed_side = m_side.group(1)

        # レーン完了表示
        print(f"\n{'='*60}")
        print(f"=== Lane {set_num} 完了 ===")
        print(f"  結果: {agreement}")
        if agreed_side:
            print(f"  支持側: {agreed_side}")
        print(f"  コスト: ${total_cost:.4f}")
        print(f"{'='*60}")

        return LaneResult(
            set_num=set_num,
            agreement=agreement,
            agreed_side=agreed_side,
            total_cost=total_cost,
        )

    except Exception as e:
        print(f"\n[Lane {set_num}] エラー発生: {e}")
        return LaneResult(
            set_num=set_num,
            agreement="ERROR",
            agreed_side=None,
            total_cost=total_cost,
        )


if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3:
        print("Usage: python lane_orchestrator.py <TICKER> <SET_NUM> [mode] [max_rounds] [opinions_per_lane] [initial_prompt]")
        print("  mode: '買う' or '売る' (デフォルト: 買う)")
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
