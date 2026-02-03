"""
並行オーケストレーター

同一銘柄に対して複数セットの議論（Analyst vs Devil's Advocate）を
並行で実行し、セットごとに独立したログを生成する。
各セットが終わった後、全セットの最終結論を一覧表示する。
"""
import sys
from pathlib import Path

import anyio

from orchestrator import (
    LOGS_DIR,
    run_orchestrator,
    get_last_export,
)
from opinion_orchestrator import run_opinion_orchestrator


def get_set_log_path(ticker: str, set_num: int) -> Path:
    """セット番号付きのログパスを返す"""
    return LOGS_DIR / f"{ticker.upper()}_set{set_num}.md"


async def run_parallel(
    ticker: str,
    num_sets: int = 3,
    max_rounds: int = 6,
    initial_prompt: str | None = None,
    opinions_per_set: int = 2,
):
    """
    同一銘柄に対して複数セットの議論を並行実行し、
    全セット完了後にopinionエージェントを並行起動する。

    フロー:
      1. 3セットのAnalyst vs Devil's Advocate 議論を並行実行
      2. 全セット完了後、各セットに対して2体のopinionエージェントを並行起動

    Args:
        ticker: 銘柄コード（例: "NVDA"）
        num_sets: 並行セット数（デフォルト: 3）
        max_rounds: 各セットの最大ラウンド数
        initial_prompt: 初回Analystへの追加指示（省略可）
        opinions_per_set: 各セットに対するopinionエージェント数（デフォルト: 2）
    """
    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    log_paths = [get_set_log_path(ticker, i + 1) for i in range(num_sets)]

    print(f"=== {ticker.upper()} 並行オーケストレーター ({num_sets}セット) ===")
    for i, lp in enumerate(log_paths, 1):
        print(f"  Set {i}: {lp}")
    print()

    # 全セットを並行実行
    async with anyio.create_task_group() as tg:
        for i in range(num_sets):
            tg.start_soon(
                run_orchestrator,
                ticker,
                max_rounds,
                initial_prompt,
                log_paths[i],
            )

    # 全セット完了後、結果を比較表示
    print()
    print("=" * 60)
    print(f"=== 全{num_sets}セット完了 — 結果比較 ===")
    print("=" * 60)

    for i, lp in enumerate(log_paths, 1):
        export = get_last_export(lp)
        if export:
            stance = export.get("stance", export.get("rating", "N/A"))
            confidence = export.get("confidence", "N/A")
            print(f"  Set {i}: stance={stance}  confidence={confidence}")
        else:
            print(f"  Set {i}: EXPORT なし")

    print("=" * 60)

    # --- Phase 2: Opinion ---
    print()
    print(f">>> 議論完了 → Opinionフェーズへ移行")
    print()
    await run_opinion_orchestrator(ticker, opinions_per_set)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python parallel_orchestrator.py <TICKER> [num_sets] [max_rounds] [opinions_per_set] [initial_prompt]")
        print("例: python parallel_orchestrator.py NVDA 3 6 2 '特にAI市場に注目して'")
        sys.exit(1)

    ticker = sys.argv[1]
    num_sets = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    max_rounds = int(sys.argv[3]) if len(sys.argv) > 3 else 6
    opinions_per_set = int(sys.argv[4]) if len(sys.argv) > 4 else 2
    initial_prompt = sys.argv[5] if len(sys.argv) > 5 else None

    anyio.run(lambda: run_parallel(ticker, num_sets, max_rounds, initial_prompt, opinions_per_set))
