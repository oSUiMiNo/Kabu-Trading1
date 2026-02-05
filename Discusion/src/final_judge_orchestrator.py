"""
最終判定オーケストレーター

各セットの judge 結果を集約し、銘柄ごとに1つの最終結論を出す。
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


def get_next_final_judge_num(ticker: str) -> int:
    """既存のfinal_judgeファイルから次の番号を返す"""
    pattern = f"{ticker.upper()}_final_judge_*.md"
    existing = list(LOGS_DIR.glob(pattern))
    if not existing:
        return 1
    nums = []
    for p in existing:
        m = re.search(r"_final_judge_(\d+)\.md$", p.name)
        if m:
            nums.append(int(m.group(1)))
    return max(nums) + 1 if nums else 1


def build_final_judge_prompt(ticker: str, final_no: int) -> str:
    """final_judgeエージェントに渡すプロンプトを組み立てる"""
    t = ticker.upper()
    output = str(LOGS_DIR / f"{t}_final_judge_{final_no}.md")

    return (
        f"銘柄「{t}」の全セット（set1〜3）の judge / opinion 結果を集約し、最終結論を出してください。\n"
        f"\n"
        f"対象フォルダ: {LOGS_DIR}\n"
        f"出力ファイル: {output}\n"
        f"final_no: {final_no}\n"
        f"\n"
        f"logs/ フォルダ内で `{t}_set*_judge_*.md` や `{t}_set*_opinion_*.md` を探索し、\n"
        f"各setの結果を集約して最終判定を出力ファイルに新規作成してください。\n"
        f"Glob による番号採番は不要です（オーケストレーターが決定済み）。"
    )


async def run_final_judge_orchestrator(ticker: str) -> AgentResult:
    """
    最終判定オーケストレーターを実行。

    Args:
        ticker: 銘柄コード
    """
    t = ticker.upper()
    final_no = get_next_final_judge_num(ticker)

    print(f"=== {t} 最終判定オーケストレーター ===")
    print(f"  出力: {t}_final_judge_{final_no}.md")
    print()

    prompt = build_final_judge_prompt(ticker, final_no)
    agent_file = AGENTS_DIR / "final-judge.md"

    print(f"[起動] Final Judge")
    result = await call_agent(
        prompt,
        file_path=str(agent_file),
        show_cost=True,
        show_tools=False,
    )
    print(f"[完了] Final Judge")
    if result.cost:
        print(f"  コスト: ${result.cost:.4f}")

    # 結果表示
    print()
    print("=" * 60)
    print(f"=== Final Judge 結果 ===")
    print("=" * 60)

    final_path = LOGS_DIR / f"{t}_final_judge_{final_no}.md"
    if final_path.exists():
        content = final_path.read_text(encoding="utf-8")
        # 日本語フィールド名を優先、フォールバックで英語も対応
        m_side = re.search(r"(?:支持側|supported_side):\s*(\S+)", content)
        m_agree = re.search(r"(?:総合一致度|overall_agreement):\s*(\S+)", content)
        side = m_side.group(1) if m_side else "N/A"
        agree = m_agree.group(1) if m_agree else "N/A"
        print(f"  最終判定: {side}")
        print(f"  一致度: {agree}")
    else:
        print(f"  ファイルが見つかりません: {final_path}")

    print("=" * 60)

    return result


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python final_judge_orchestrator.py <TICKER>")
        print("例: python final_judge_orchestrator.py GOOGL")
        sys.exit(1)

    ticker = sys.argv[1]
    anyio.run(lambda: run_final_judge_orchestrator(ticker))
