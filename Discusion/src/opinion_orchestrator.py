"""
意見オーケストレーター

各セットの議論ログ（銘柄名_setN.md）に対して2体のopinionサブエージェントを
並行起動し、買う/買わないの意見を独立に出させる。
3セット × 2体 = 合計6体を同時に走らせる。
全opinionが完了したら、各セットのペアをjudgeに渡して一致/不一致を判定させる。
オーケストレーター自体はLLMを使わず、プログラムだけで制御する。
"""
import re
import sys
from pathlib import Path

import anyio

from AgentUtil import call_agent, AgentResult

# プロジェクトルート
PROJECT_ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = PROJECT_ROOT / ".claude" / "agents"
LOGS_DIR = PROJECT_ROOT / "logs"


def find_set_logs(ticker: str) -> list[Path]:
    """指定銘柄のセットログ（_opinion_を除く）を検索"""
    all_files = sorted(LOGS_DIR.glob(f"{ticker.upper()}_set*.md"))
    return [p for p in all_files if "_opinion_" not in p.name]


def get_next_opinion_num(ticker: str, set_num: int) -> int:
    """既存のopinionファイルから次の番号を返す"""
    pattern = f"{ticker.upper()}_set{set_num}_opinion_*.md"
    existing = list(LOGS_DIR.glob(pattern))
    if not existing:
        return 1
    nums = []
    for p in existing:
        m = re.search(r"_opinion_(\d+)\.md$", p.name)
        if m:
            nums.append(int(m.group(1)))
    return max(nums) + 1 if nums else 1


def build_opinion_prompt(ticker: str, set_num: int, opinion_num: int) -> str:
    """opinionエージェントに渡すプロンプトを組み立てる"""
    log_abs = str(LOGS_DIR / f"{ticker.upper()}_set{set_num}.md")
    output_abs = str(LOGS_DIR / f"{ticker.upper()}_set{set_num}_opinion_{opinion_num}.md")

    return (
        f"銘柄「{ticker.upper()}」の議論ログを読み、意見を出してください。\n"
        f"\n"
        f"対象ログ: {log_abs}\n"
        f"出力ファイル: {output_abs}\n"
        f"opinion_no: {opinion_num}\n"
        f"\n"
        f"上記の出力ファイルパスに、opinion_no={opinion_num} として意見ファイルを新規作成してください。\n"
        f"Glob による番号採番は不要です（オーケストレーターが決定済み）。"
    )


async def run_single_opinion(
    ticker: str,
    set_num: int,
    opinion_num: int,
) -> AgentResult:
    """1体のopinionエージェントを実行"""
    label = f"Set{set_num} Opinion#{opinion_num}"
    print(f"[起動] {label}")

    prompt = build_opinion_prompt(ticker, set_num, opinion_num)
    agent_file = AGENTS_DIR / "opinion.md"

    result = await call_agent(
        prompt,
        file_path=str(agent_file),
        show_cost=True,
        show_tools=False,
    )

    print(f"[完了] {label}")
    if result.cost:
        print(f"  コスト: ${result.cost:.4f}")

    return result


async def run_opinion_orchestrator(
    ticker: str,
    opinions_per_set: int = 2,
):
    """
    意見オーケストレーターのメインループ。

    各セットの議論ログに対して複数のopinionエージェントを並行起動する。

    Args:
        ticker: 銘柄コード（例: "GOOGL"）
        opinions_per_set: 各セットに対するopinionエージェント数（デフォルト: 2）
    """
    set_logs = find_set_logs(ticker)

    if not set_logs:
        print(f"エラー: {ticker.upper()} のセットログが見つかりません（logs/{ticker.upper()}_set*.md）")
        return

    # セット番号を抽出
    set_nums = []
    for p in set_logs:
        m = re.search(r"_set(\d+)\.md$", p.name)
        if m:
            set_nums.append(int(m.group(1)))

    total = len(set_nums) * opinions_per_set

    print(f"=== {ticker.upper()} 意見オーケストレーター ===")
    print(f"対象セット: {len(set_nums)}個 ({', '.join(f'set{n}' for n in set_nums)})")
    print(f"各セット {opinions_per_set}体 × {len(set_nums)}セット = 合計 {total}体")
    print()

    # 各セットのopinion番号を事前に決定（競合回避）
    tasks = []
    for sn in set_nums:
        base_num = get_next_opinion_num(ticker, sn)
        for i in range(opinions_per_set):
            tasks.append((sn, base_num + i))

    # 全体を並行実行
    results: list[AgentResult] = [None] * len(tasks)

    async def _run(idx: int, set_num: int, opinion_num: int):
        results[idx] = await run_single_opinion(ticker, set_num, opinion_num)

    async with anyio.create_task_group() as tg:
        for idx, (sn, on) in enumerate(tasks):
            tg.start_soon(_run, idx, sn, on)

    # 結果まとめ
    print()
    print("=" * 60)
    print(f"=== 全{total}体完了 — 結果一覧 ===")
    print("=" * 60)

    total_cost = 0.0
    for idx, (sn, on) in enumerate(tasks):
        r = results[idx]
        cost = r.cost if r and r.cost else 0.0
        total_cost += cost

        # opinionファイルからEXPORTを簡易読み取り
        opinion_path = LOGS_DIR / f"{ticker.upper()}_set{sn}_opinion_{on}.md"
        side = "N/A"
        buy_score = "?"
        notbuy_score = "?"
        winner = "?"
        basis = "?"
        if opinion_path.exists():
            content = opinion_path.read_text(encoding="utf-8")
            m_side = re.search(r"supported_side:\s*(\S+)", content)
            if m_side:
                side = m_side.group(1)
            m_buy = re.search(r"buy_support:\s*(\d+)", content)
            if m_buy:
                buy_score = m_buy.group(1)
            m_notbuy = re.search(r"not_buy_support:\s*(\d+)", content)
            if m_notbuy:
                notbuy_score = m_notbuy.group(1)
            m_winner = re.search(r"winner_agent:\s*(\S+)", content)
            if m_winner:
                winner = m_winner.group(1)
            m_basis = re.search(r"win_basis:\s*(\S+)", content)
            if m_basis:
                basis = m_basis.group(1)

        print(f"  Set{sn} Opinion#{on}: {side}  (Buy:{buy_score} / NotBuy:{notbuy_score})  winner={winner}({basis})  ${cost:.4f}")

    print(f"\n  合計コスト: ${total_cost:.4f}")
    print("=" * 60)

    # --- Phase: Judge ---
    # 各セットの opinion ペアを judge に渡す
    from judge_orchestrator import run_judge_orchestrator

    # tasks は (set_num, opinion_num) のリスト。同一setの連続2つがペア。
    opinion_pairs = []
    pairs_by_set: dict[int, list[int]] = {}
    for sn, on in tasks:
        pairs_by_set.setdefault(sn, []).append(on)
    for sn, nums in pairs_by_set.items():
        if len(nums) >= 2:
            opinion_pairs.append((sn, nums[0], nums[1]))

    if opinion_pairs:
        print()
        print(f">>> Opinion完了 → Judgeフェーズへ移行")
        print()
        await run_judge_orchestrator(ticker, opinion_pairs)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python opinion_orchestrator.py <TICKER> [opinions_per_set]")
        print("例: python opinion_orchestrator.py GOOGL 2")
        sys.exit(1)

    ticker = sys.argv[1]
    opinions_per_set = int(sys.argv[2]) if len(sys.argv) > 2 else 2

    anyio.run(lambda: run_opinion_orchestrator(ticker, opinions_per_set))
