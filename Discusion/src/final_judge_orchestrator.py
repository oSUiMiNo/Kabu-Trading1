"""
最終判定オーケストレーター

各セットの judge 結果を集約し、銘柄ごとに1つの最終結論を出す。
オーケストレーター自体はLLMを使わず、プログラムだけで制御する。
"""
import re
import sys
from pathlib import Path

import anyio

from AgentUtil import call_agent, AgentResult, load_debug_config, save_result_log

# プロジェクトルート
PROJECT_ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = PROJECT_ROOT / ".claude" / "commands"
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


def build_final_judge_prompt(
    ticker: str,
    final_no: int,
    agreed_sets: list[int] | None = None,
    mode: str = "buy",
    disagreed_sets: list[int] | None = None,
) -> str:
    """final_judgeエージェントに渡すプロンプトを組み立てる"""
    t = ticker.upper()
    output = str(LOGS_DIR / f"{t}_final_judge_{final_no}.md")

    # 対象セットを決定（指定がなければ1〜3全部をagreedとして扱う）
    if agreed_sets is None:
        agreed_sets = [1, 2, 3]
    if disagreed_sets is None:
        disagreed_sets = []

    # 全セット = agreed + disagreed（ソート済み）
    all_sets = sorted(set(agreed_sets) | set(disagreed_sets))

    # 元ログのリストを作成（一致度ラベル付き）
    source_logs = []
    for sn in all_sets:
        label = "AGREED" if sn in agreed_sets else "DISAGREED"
        source_logs.append(f"  set{sn} [{label}]: {LOGS_DIR / f'{t}_set{sn}.md'}")
    source_logs_str = "\n".join(source_logs)

    # 対象セット表示
    target_sets_str = ", ".join(f"set{sn}" for sn in all_sets)

    # 一致度情報
    agreement_info_lines = []
    if agreed_sets:
        agreement_info_lines.append(f"  一致(AGREED): {', '.join(f'set{sn}' for sn in agreed_sets)} — 2体のopinionが一致。根拠として重み高。")
    if disagreed_sets:
        agreement_info_lines.append(f"  不一致(DISAGREED): {', '.join(f'set{sn}' for sn in disagreed_sets)} — 2体のopinionが不一致。両論を参考材料として扱う。")
    agreement_info = "\n".join(agreement_info_lines)

    if mode == "sell":
        mode_line = "【議論モード: 売る】売るべきか・売らないべきか（保有継続）の議論です。\n\n"
    else:
        mode_line = "【議論モード: 買う】買うべきか・買わないべきかの議論です。\n\n"

    return (
        f"{mode_line}"
        f"銘柄「{t}」について最終判定を行ってください。\n"
        f"\n"
        f"【対象セット】{target_sets_str}（全セット対象）\n"
        f"\n"
        f"【各セットの一致度】\n"
        f"{agreement_info}\n"
        f"\n"
        f"【重要】まず各setの元ログを読んでから、judge/opinionを評価してください。\n"
        f"\n"
        f"元ログ（Analyst vs Devils）:\n"
        f"{source_logs_str}\n"
        f"\n"
        f"対象フォルダ: {LOGS_DIR}\n"
        f"\n"
        f"1. 最初に対象セットの元ログを読み、議論の内容を把握してください。\n"
        f"2. 次に対象セットの `{t}_set*_judge_*.md` を読み、各setの判定結果を確認してください。\n"
        f"3. 各setの結果を集約して最終判定を**テキスト応答として出力**してください。\n"
        f"   ファイルへの書き込みは不要です。採番もオーケストレーターが決定済みです。"
    )


async def run_final_judge_orchestrator(
    ticker: str,
    agreed_sets: list[int] | None = None,
    mode: str = "buy",
    disagreed_sets: list[int] | None = None,
) -> AgentResult:
    """
    最終判定オーケストレーターを実行。

    Args:
        ticker: 銘柄コード
        agreed_sets: opinionが一致したセット番号のリスト（Noneなら全セット対象）
        disagreed_sets: opinionが不一致だったセット番号のリスト
    """
    t = ticker.upper()
    final_no = get_next_final_judge_num(ticker)

    # 対象セットを決定
    if agreed_sets is None:
        agreed_sets = [1, 2, 3]
    if disagreed_sets is None:
        disagreed_sets = []

    all_sets = sorted(set(agreed_sets) | set(disagreed_sets))
    target_sets_str = ", ".join(f"set{sn}" for sn in all_sets)

    print(f"=== {t} 最終判定オーケストレーター ===")
    print(f"  対象セット: {target_sets_str}")
    if agreed_sets:
        print(f"    AGREED: {', '.join(f'set{sn}' for sn in agreed_sets)}")
    if disagreed_sets:
        print(f"    DISAGREED: {', '.join(f'set{sn}' for sn in disagreed_sets)}")
    print(f"  出力: {t}_final_judge_{final_no}.md")
    print()

    prompt = build_final_judge_prompt(ticker, final_no, agreed_sets, mode, disagreed_sets)
    agent_file = AGENTS_DIR / "final-judge.md"

    print(f"[起動] Final Judge")
    dbg = load_debug_config("final_judge")
    result = await call_agent(
        prompt,
        file_path=str(agent_file),
        show_cost=True,
        show_tools=False,
        **dbg,
    )
    print(f"[完了] Final Judge")
    if result.cost:
        print(f"  コスト: ${result.cost:.4f}")

    # オーケストレーター側でログファイルに書き出し
    final_path = LOGS_DIR / f"{t}_final_judge_{final_no}.md"
    saved = save_result_log(result, final_path)
    if saved:
        print(f"  ログ書き出し: {saved.name}")

    # 結果表示（result.text からパース）
    print()
    print("=" * 60)
    print(f"=== Final Judge 結果 ===")
    print("=" * 60)

    content = result.text if result and result.text else ""
    if content:
        # 日本語フィールド名を優先、フォールバックで英語も対応
        m_side = re.search(r"(?:支持側|supported_side):\s*(\S+)", content)
        m_agree = re.search(r"(?:総合一致度|overall_agreement):\s*(\S+)", content)
        side = m_side.group(1) if m_side else "N/A"
        agree = m_agree.group(1) if m_agree else "N/A"
        print(f"  最終判定: {side}")
        print(f"  一致度: {agree}")
    else:
        print(f"  応答テキストが空です")

    print("=" * 60)

    return result


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python final_judge_orchestrator.py <TICKER>")
        print("例: python final_judge_orchestrator.py GOOGL")
        sys.exit(1)

    ticker = sys.argv[1]
    anyio.run(lambda: run_final_judge_orchestrator(ticker))
