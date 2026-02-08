"""
最終判定オーケストレーター

各レーンの judge 結果を集約し、銘柄ごとに1つの最終結論を出す。
オーケストレーター自体はLLMを使わず、プログラムだけで制御する。
"""
import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path

import anyio

from AgentUtil import call_agent, AgentResult, load_debug_config, save_result_log


@dataclass
class FinalJudgeResult:
    """Final Judge の実行結果（呼び出し元へ返すシンプルな構造体）"""
    判定結果: str       # "BUY" | "NOT_BUY_WAIT" | "SELL" | "NOT_SELL_HOLD"
    賛成票: int         # アクション側（BUY/SELL）の票数
    反対票: int         # 安全側（NOT_BUY_WAIT/NOT_SELL_HOLD）の票数
    ログパス: Path      # 生成された final judge ログのパス

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


def _is_action_vote(side: str, mode: str) -> bool:
    """supported_side がアクション側（BUY/SELL）かどうかを判定"""
    s = side.upper()
    if mode == "buy":
        return "BUY" in s and "NOT_BUY" not in s
    else:
        return "SELL" in s and "NOT_SELL" not in s


def compute_vote_tally(
    agreed_sets: list[int],
    disagreed_sets: list[int],
    set_sides: dict[int, str],
    mode: str,
) -> tuple[int, int, int, str]:
    """
    投票集計と判定を行う。

    AGREED レーン: supported_side に2票
    DISAGREED レーン: 各 side に1票ずつ（split）

    閾値:
      買うモード: 全会一致のみ BUY
      売るモード: SELL票 ≥ ceil(全票数 × 2/3) なら SELL

    Returns:
        (action_votes, safe_votes, total_votes, verdict)
    """
    action_votes = 0
    safe_votes = 0

    for sn in agreed_sets:
        side = set_sides.get(sn, "")
        if _is_action_vote(side, mode):
            action_votes += 2
        else:
            safe_votes += 2

    for sn in disagreed_sets:
        action_votes += 1
        safe_votes += 1

    total = action_votes + safe_votes

    if mode == "buy":
        # 全会一致のみ BUY
        verdict = "BUY" if safe_votes == 0 and total > 0 else "NOT_BUY_WAIT"
    else:
        # 2/3 以上で SELL
        threshold = math.ceil(total * 2 / 3) if total > 0 else 1
        verdict = "SELL" if action_votes >= threshold else "NOT_SELL_HOLD"

    return action_votes, safe_votes, total, verdict


def build_final_judge_prompt(
    ticker: str,
    final_no: int,
    agreed_sets: list[int] | None = None,
    mode: str = "buy",
    disagreed_sets: list[int] | None = None,
    set_sides: dict[int, str] | None = None,
) -> str:
    """final_judgeエージェントに渡すプロンプトを組み立てる"""
    t = ticker.upper()
    output = str(LOGS_DIR / f"{t}_final_judge_{final_no}.md")

    # 対象レーンを決定（指定がなければ1〜3全部をagreedとして扱う）
    if agreed_sets is None:
        agreed_sets = [1, 2, 3]
    if disagreed_sets is None:
        disagreed_sets = []

    # 全レーン = agreed + disagreed（ソート済み）
    all_sets = sorted(set(agreed_sets) | set(disagreed_sets))

    # 元ログのリストを作成（一致度ラベル付き）
    source_logs = []
    for sn in all_sets:
        label = "AGREED" if sn in agreed_sets else "DISAGREED"
        source_logs.append(f"  set{sn} [{label}]: {LOGS_DIR / f'{t}_set{sn}.md'}")
    source_logs_str = "\n".join(source_logs)

    # 対象レーン表示
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

    # 投票集計（set_sides が渡された場合のみ）
    vote_section = ""
    if set_sides is not None:
        action, safe, total, verdict = compute_vote_tally(
            agreed_sets, disagreed_sets, set_sides, mode
        )
        action_label = "BUY" if mode == "buy" else "SELL"
        safe_label = "NOT_BUY_WAIT" if mode == "buy" else "NOT_SELL_HOLD"
        if mode == "buy":
            rule_desc = "全会一致のみ BUY（1票でも反対 → NOT_BUY_WAIT）"
        else:
            rule_desc = f"SELL票 ≥ {math.ceil(total * 2 / 3) if total > 0 else 1}/{total} で SELL（2/3以上）"
        vote_section = (
            f"\n"
            f"【投票集計（オーケストレーター算出・確定値）】\n"
            f"  {action_label}票: {action} / {safe_label}票: {safe} / 合計: {total}票\n"
            f"  適用ルール: {rule_desc}\n"
            f"  → **確定判定: {verdict}**\n"
            f"  ※ この判定は投票閾値ルールに基づく確定値です。最終判定（supported_side）はこれに従ってください。\n"
        )

    return (
        f"{mode_line}"
        f"銘柄「{t}」について最終判定を行ってください。\n"
        f"\n"
        f"【対象レーン】{target_sets_str}（全レーン対象）\n"
        f"\n"
        f"【各レーンの一致度】\n"
        f"{agreement_info}\n"
        f"{vote_section}\n"
        f"【重要】まず各setの元ログを読んでから、judge/opinionを評価してください。\n"
        f"\n"
        f"元ログ（Analyst vs Devils）:\n"
        f"{source_logs_str}\n"
        f"\n"
        f"対象フォルダ: {LOGS_DIR}\n"
        f"\n"
        f"1. 最初に対象レーンの元ログを読み、議論の内容を把握してください。\n"
        f"2. 次に対象レーンの `{t}_set*_judge_*.md` を読み、各レーンの判定結果を確認してください。\n"
        f"3. 各レーンの結果を集約して最終判定を**テキスト応答として出力**してください。\n"
        f"   ファイルへの書き込みは不要です。採番もオーケストレーターが決定済みです。"
    )


async def run_final_judge_orchestrator(
    ticker: str,
    agreed_sets: list[int] | None = None,
    mode: str = "buy",
    disagreed_sets: list[int] | None = None,
    set_sides: dict[int, str] | None = None,
) -> FinalJudgeResult:
    """
    最終判定オーケストレーターを実行。

    Args:
        ticker: 銘柄コード
        agreed_sets: opinionが一致したレーン番号のリスト（Noneなら全レーン対象）
        disagreed_sets: opinionが不一致だったレーン番号のリスト
    """
    t = ticker.upper()
    final_no = get_next_final_judge_num(ticker)

    # 対象レーンを決定
    if agreed_sets is None:
        agreed_sets = [1, 2, 3]
    if disagreed_sets is None:
        disagreed_sets = []

    all_sets = sorted(set(agreed_sets) | set(disagreed_sets))
    target_sets_str = ", ".join(f"set{sn}" for sn in all_sets)

    # 投票集計
    if set_sides is not None:
        action_votes, safe_votes, _total, verdict = compute_vote_tally(
            agreed_sets, disagreed_sets, set_sides, mode
        )
    else:
        action_votes, safe_votes, verdict = 0, 0, "UNKNOWN"

    print(f"=== {t} 最終判定オーケストレーター ===")
    print(f"  対象レーン: {target_sets_str}")
    if agreed_sets:
        print(f"    一致: {', '.join(f'set{sn}' for sn in agreed_sets)}")
    if disagreed_sets:
        print(f"    不一致: {', '.join(f'set{sn}' for sn in disagreed_sets)}")
    if set_sides is not None:
        action_label = "BUY" if mode == "buy" else "SELL"
        safe_label = "NOT_BUY_WAIT" if mode == "buy" else "NOT_SELL_HOLD"
        print(f"  投票: {action_label} {action_votes} / {safe_label} {safe_votes} (計{action_votes + safe_votes}票)")
        print(f"  確定判定: {verdict}")
    print(f"  出力: {t}_final_judge_{final_no}.md")
    print()

    prompt = build_final_judge_prompt(ticker, final_no, agreed_sets, mode, disagreed_sets, set_sides)
    agent_file = AGENTS_DIR / "final-judge.md"

    print(f"[全レーン] 最終判定 起動")
    dbg = load_debug_config("final_judge")
    result = await call_agent(
        prompt,
        file_path=str(agent_file),
        show_cost=True,
        show_tools=False,
        **dbg,
    )
    print(f"[全レーン] 最終判定 完了")
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
    print(f"=== 最終判定結果 ===")
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

    return FinalJudgeResult(
        判定結果=verdict,
        賛成票=action_votes,
        反対票=safe_votes,
        ログパス=final_path,
    )


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("使い方: python final_judge_orchestrator.py <銘柄コード>")
        print("例: python final_judge_orchestrator.py GOOGL")
        sys.exit(1)

    ticker = sys.argv[1]
    anyio.run(lambda: run_final_judge_orchestrator(ticker))
