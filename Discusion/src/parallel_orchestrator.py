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


def get_set_log_path(ticker: str, set_num: int) -> Path:
    """セット番号付きのログパスを返す"""
    return LOGS_DIR / f"{ticker.upper()}_set{set_num}.md"


async def run_parallel(
    ticker: str,
    num_sets: int = 3,
    max_rounds: int = 6,
    initial_prompt: str | None = None,
):
    """
    同一銘柄に対して複数セットの議論を並行実行する。

    各セットは独立したログファイル（例: AMZN_set1.md, AMZN_set2.md, ...）に
    書き込むため、互いに干渉しない。
    全セット完了後に結果を比較表示する。

    Args:
        ticker: 銘柄コード（例: "NVDA"）
        num_sets: 並行セット数（デフォルト: 3）
        max_rounds: 各セットの最大ラウンド数
        initial_prompt: 初回Analystへの追加指示（省略可）
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


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python parallel_orchestrator.py <TICKER> [num_sets] [max_rounds] [initial_prompt]")
        print("例: python parallel_orchestrator.py NVDA 3 6 '特にAI市場に注目して'")
        sys.exit(1)

    ticker = sys.argv[1]
    num_sets = int(sys.argv[2]) if len(sys.argv) > 2 else 3
    max_rounds = int(sys.argv[3]) if len(sys.argv) > 3 else 6
    initial_prompt = sys.argv[4] if len(sys.argv) > 4 else None

    anyio.run(lambda: run_parallel(ticker, num_sets, max_rounds, initial_prompt))
