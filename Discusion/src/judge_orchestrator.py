"""
判定オーケストレーター

各セットの opinion ペア（A, B）を judge サブエージェントに渡し、
一致/不一致を判定させる。
3セット分を並行実行する。
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


def get_next_judge_num(ticker: str, set_num: int) -> int:
    """既存のjudgeファイルから次の番号を返す"""
    pattern = f"{ticker.upper()}_set{set_num}_judge_*.md"
    existing = list(LOGS_DIR.glob(pattern))
    if not existing:
        return 1
    nums = []
    for p in existing:
        m = re.search(r"_judge_(\d+)\.md$", p.name)
        if m:
            nums.append(int(m.group(1)))
    return max(nums) + 1 if nums else 1


def build_judge_prompt(
    ticker: str,
    set_num: int,
    opinion_a: int,
    opinion_b: int,
    judge_num: int,
) -> str:
    """judgeエージェントに渡すプロンプトを組み立てる"""
    t = ticker.upper()
    file_a = str(LOGS_DIR / f"{t}_set{set_num}_opinion_{opinion_a}.md")
    file_b = str(LOGS_DIR / f"{t}_set{set_num}_opinion_{opinion_b}.md")
    output = str(LOGS_DIR / f"{t}_set{set_num}_judge_{judge_num}.md")

    return (
        f"銘柄「{t}」の set{set_num} の2つの opinion を読み、一致/不一致を判定してください。\n"
        f"\n"
        f"opinion_A: {file_a}\n"
        f"opinion_B: {file_b}\n"
        f"出力ファイル: {output}\n"
        f"judge_no: {judge_num}\n"
        f"\n"
        f"上記の2つの opinion ファイルを読み、supported_side が一致しているか判定し、\n"
        f"結果を出力ファイルに新規作成してください。\n"
        f"Glob による番号採番は不要です（オーケストレーターが決定済み）。"
    )


async def run_single_judge(
    ticker: str,
    set_num: int,
    opinion_a: int,
    opinion_b: int,
    judge_num: int,
) -> AgentResult:
    """1体のjudgeエージェントを実行"""
    label = f"Set{set_num} Judge#{judge_num} (opinion {opinion_a} vs {opinion_b})"
    print(f"[起動] {label}")

    prompt = build_judge_prompt(ticker, set_num, opinion_a, opinion_b, judge_num)
    agent_file = AGENTS_DIR / "judge.md"

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


async def run_judge_orchestrator(
    ticker: str,
    opinion_pairs: list[tuple[int, int, int]],
):
    """
    判定オーケストレーターのメインループ。

    opinion_pairs の各ペアに対して judge を並行起動する。

    Args:
        ticker: 銘柄コード
        opinion_pairs: [(set_num, opinion_a, opinion_b), ...] のリスト
    """
    if not opinion_pairs:
        print("エラー: 判定対象の opinion ペアがありません")
        return

    total = len(opinion_pairs)

    print(f"=== {ticker.upper()} 判定オーケストレーター ===")
    print(f"対象: {total}セット")
    for sn, oa, ob in opinion_pairs:
        print(f"  Set{sn}: opinion_{oa} vs opinion_{ob}")
    print()

    # 各セットの judge 番号を事前に決定
    tasks = []
    for sn, oa, ob in opinion_pairs:
        jn = get_next_judge_num(ticker, sn)
        tasks.append((sn, oa, ob, jn))

    # 全体を並行実行
    results: list[AgentResult] = [None] * len(tasks)

    async def _run(idx: int, set_num: int, oa: int, ob: int, jn: int):
        results[idx] = await run_single_judge(ticker, set_num, oa, ob, jn)

    async with anyio.create_task_group() as tg:
        for idx, (sn, oa, ob, jn) in enumerate(tasks):
            tg.start_soon(_run, idx, sn, oa, ob, jn)

    # 結果まとめ
    print()
    print("=" * 60)
    print(f"=== 全{total}件完了 — Judge 結果一覧 ===")
    print("=" * 60)

    total_cost = 0.0
    for idx, (sn, oa, ob, jn) in enumerate(tasks):
        r = results[idx]
        cost = r.cost if r and r.cost else 0.0
        total_cost += cost

        # judgeファイルからEXPORTを簡易読み取り
        judge_path = LOGS_DIR / f"{ticker.upper()}_set{sn}_judge_{jn}.md"
        agreement = "N/A"
        agreed_side = "N/A"
        if judge_path.exists():
            content = judge_path.read_text(encoding="utf-8")
            m_agree = re.search(r"agreement:\s*(\S+)", content)
            if m_agree:
                agreement = m_agree.group(1)
            m_side = re.search(r"agreed_supported_side:\s*(\S+)", content)
            if m_side:
                agreed_side = m_side.group(1)

        print(f"  Set{sn} Judge#{jn}: {agreement}  side={agreed_side}  ${cost:.4f}")

    print(f"\n  合計コスト: ${total_cost:.4f}")
    print("=" * 60)

    # --- Phase: Final Judge ---
    from final_judge_orchestrator import run_final_judge_orchestrator

    print()
    print(f">>> Judge完了 → Final Judgeフェーズへ移行")
    print()
    await run_final_judge_orchestrator(ticker)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python judge_orchestrator.py <TICKER> [set_nums (comma-sep)]")
        print("例: python judge_orchestrator.py GOOGL 1,2,3")
        print("  各セットの最新 opinion ペアを自動検出して judge を実行")
        sys.exit(1)

    from opinion_orchestrator import get_next_opinion_num

    ticker = sys.argv[1]
    if len(sys.argv) > 2:
        set_nums = [int(x) for x in sys.argv[2].split(",")]
    else:
        set_nums = [1, 2, 3]

    # 各セットの最新ペアを自動検出
    pairs = []
    for sn in set_nums:
        latest = get_next_opinion_num(ticker, sn) - 1
        if latest >= 2:
            pairs.append((sn, latest - 1, latest))
        else:
            print(f"  Set{sn}: opinion が2つ未満のためスキップ")

    anyio.run(lambda: run_judge_orchestrator(ticker, pairs))
