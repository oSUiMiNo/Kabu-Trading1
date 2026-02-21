"""
最終判定オーケストレーター

各レーンの judge 結果を集約し、銘柄ごとに1つの最終結論を出す。
オーケストレーター自体はLLMを使わず、プログラムだけで制御する。
"""
import json
import math
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import anyio

from AgentUtil import call_agent, AgentResult, load_debug_config, save_result_log, side_ja


@dataclass
class FinalJudgeResult:
    """Final Judge の実行結果（呼び出し元へ返すシンプルな構造体）"""
    判定結果: str       # "BUY" | "NOT_BUY_WAIT" | "SELL" | "NOT_SELL_HOLD"
    賛成票: int         # アクション側（BUY/SELL）の票数
    反対票: int         # 安全側（NOT_BUY_WAIT/NOT_SELL_HOLD）の票数
    ログパス: Path      # 生成された final judge ログのパス
    db_data: dict = field(default_factory=dict)

# プロジェクトルート
PROJECT_ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = PROJECT_ROOT / ".claude" / "commands"
LOGS_DIR = PROJECT_ROOT / "logs"


def get_next_final_judge_num(ticker: str, session_dir: Path | None = None) -> int:
    """既存のfinal_judgeファイルから次の番号を返す"""
    base = session_dir if session_dir else LOGS_DIR
    pattern = f"{ticker.upper()}_final_judge_*.md"
    existing = list(base.glob(pattern))
    if not existing:
        return 1
    nums = []
    for p in existing:
        m = re.search(r"_final_judge_(\d+)\.md$", p.name)
        if m:
            nums.append(int(m.group(1)))
    return max(nums) + 1 if nums else 1


def _is_action_vote(side: str, mode: str) -> bool:
    """supported_side がアクション側（BUY/SELL/ADD）かどうかを判定"""
    s = side.upper()
    if mode == "buy":
        return "BUY" in s and "NOT_BUY" not in s
    elif mode == "add":
        return "ADD" in s and "NOT_ADD" not in s
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
      買い増しモード: 全会一致のみ ADD
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
        verdict = "BUY" if safe_votes == 0 and total > 0 else "NOT_BUY_WAIT"
    elif mode == "add":
        verdict = "ADD" if safe_votes == 0 and total > 0 else "NOT_ADD_HOLD"
    else:
        threshold = math.ceil(total * 2 / 3) if total > 0 else 1
        verdict = "SELL" if action_votes >= threshold else "NOT_SELL_HOLD"

    return action_votes, safe_votes, total, verdict


def _read_latest_judge(ticker: str, set_num: int, session_dir: Path | None = None) -> str:
    """指定レーンの最新 judge ファイルを読んで返す（無ければ空文字）"""
    base = session_dir if session_dir else LOGS_DIR
    t = ticker.upper()
    pattern = f"{t}_set{set_num}_judge_*.md"
    existing = sorted(base.glob(pattern))
    if not existing:
        return ""
    latest = existing[-1]
    return latest.read_text(encoding="utf-8")


def _read_discussion_export(ticker: str, set_num: int, session_dir: Path | None = None) -> str:
    """議論ログから暫定結論・EXPORT 付近を抽出する（全文は大きすぎるため）"""
    base = session_dir if session_dir else LOGS_DIR
    t = ticker.upper()
    log_path = base / f"{t}_set{set_num}.md"
    if not log_path.exists():
        return ""
    content = log_path.read_text(encoding="utf-8")
    # 最後の「暫定結論」以降を抽出（EXPORT 含む）
    idx = content.rfind("暫定結論")
    if idx != -1:
        # 少し前から取る（セクション見出し含む）
        start = max(0, content.rfind("\n#", 0, idx))
        return content[start:].strip()
    # 見つからなければ末尾 3000 文字
    return content[-3000:].strip() if len(content) > 3000 else content


def build_final_judge_prompt(
    ticker: str,
    final_no: int,
    agreed_sets: list[int] | None = None,
    mode: str = "buy",
    disagreed_sets: list[int] | None = None,
    set_sides: dict[int, str] | None = None,
    session_dir: Path | None = None,
) -> str:
    """final_judgeエージェントに渡すプロンプトを組み立てる（ファイル内容インライン埋め込み）"""
    t = ticker.upper()

    # 対象レーンを決定（指定がなければ1〜3全部をagreedとして扱う）
    if agreed_sets is None:
        agreed_sets = [1, 2, 3]
    if disagreed_sets is None:
        disagreed_sets = []

    # 全レーン = agreed + disagreed（ソート済み）
    all_sets = sorted(set(agreed_sets) | set(disagreed_sets))

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
    elif mode == "add":
        mode_line = "【議論モード: 買い増し】買い増すべきか・買い増さないべきか（現状維持）の議論です。\n\n"
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

    # 各レーンの judge 内容と議論結論をインライン埋め込み
    inline_sections = []
    for sn in all_sets:
        label = "AGREED" if sn in agreed_sets else "DISAGREED"
        judge_content = _read_latest_judge(ticker, sn, session_dir)
        discussion_export = _read_discussion_export(ticker, sn, session_dir)

        section = f"--- set{sn} [{label}] ここから ---\n"
        if discussion_export:
            section += f"【議論ログ（暫定結論抜粋）】\n{discussion_export}\n\n"
        if judge_content:
            section += f"【judge 判定ログ】\n{judge_content}\n"
        else:
            section += "【judge 判定ログ】\n（judge ファイルが見つかりません）\n"
        section += f"--- set{sn} ここまで ---"
        inline_sections.append(section)

    inline_content = "\n\n".join(inline_sections)

    return (
        f"{mode_line}"
        f"銘柄「{t}」について最終判定を行ってください。\n"
        f"\n"
        f"【対象レーン】{target_sets_str}（全レーン対象）\n"
        f"\n"
        f"【各レーンの一致度】\n"
        f"{agreement_info}\n"
        f"{vote_section}\n"
        f"以下に各レーンの判定結果と議論の結論をインラインで提供します。\n"
        f"これらの内容をもとに最終判定を行ってください。\n"
        f"\n"
        f"{inline_content}\n"
        f"\n"
        f"上記の内容を集約して最終判定を**テキスト応答として出力**してください。\n"
        f"ファイルへの書き込みは不要です。採番もオーケストレーターが決定済みです。"
    )


async def run_final_judge_orchestrator(
    ticker: str,
    agreed_sets: list[int] | None = None,
    mode: str = "buy",
    disagreed_sets: list[int] | None = None,
    set_sides: dict[int, str] | None = None,
    session_dir: Path | None = None,
) -> FinalJudgeResult:
    """
    最終判定オーケストレーターを実行。

    Args:
        ticker: 銘柄コード
        agreed_sets: opinionが一致したレーン番号のリスト（Noneなら全レーン対象）
        disagreed_sets: opinionが不一致だったレーン番号のリスト
    """
    t = ticker.upper()
    final_no = get_next_final_judge_num(ticker, session_dir)

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
        action_ja = side_ja("BUY") if mode == "buy" else side_ja("SELL")
        safe_ja = side_ja("NOT_BUY_WAIT") if mode == "buy" else side_ja("NOT_SELL_HOLD")
        print(f"  投票: {action_ja} {action_votes} / {safe_ja} {safe_votes} (計{action_votes + safe_votes}票)")
        print(f"  確定判定: {side_ja(verdict)}")
    print(f"  出力: {t}_final_judge_{final_no}.md")
    print()

    prompt = build_final_judge_prompt(ticker, final_no, agreed_sets, mode, disagreed_sets, set_sides, session_dir)
    agent_file = AGENTS_DIR / "final-judge.md"

    print(f"[全レーン] 最終判定 起動")
    dbg = load_debug_config("final_judge")
    show_cost = dbg.pop("show_cost", False)
    result = await call_agent(
        prompt,
        file_path=str(agent_file),
        show_cost=show_cost,
        show_tools=False,
        **dbg,
    )
    print(f"[全レーン] 最終判定 完了")
    if show_cost and result.cost:
        print(f"  コスト: ${result.cost:.4f}")

    # オーケストレーター側でログファイルに書き出し
    base = session_dir if session_dir else LOGS_DIR
    final_path = base / f"{t}_final_judge_{final_no}.md"
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
        print(f"  最終判定: {side_ja(side)}")
        print(f"  一致度: {agree}")
    else:
        side = "N/A"
        agree = "N/A"
        print(f"  応答テキストが空です")

    print("=" * 60)

    # DB用データを構築（書き込みは parallel_orchestrator が行う）
    fj_db_data = {
        "action_votes": action_votes,
        "safe_votes": safe_votes,
        "overall_agreement": agree,
        "lane_results": {
            "agreed_sets": agreed_sets,
            "disagreed_sets": disagreed_sets,
            "set_sides": set_sides or {},
        },
        "markdown": content,
    }

    return FinalJudgeResult(
        判定結果=verdict,
        賛成票=action_votes,
        反対票=safe_votes,
        ログパス=final_path,
        db_data=fj_db_data,
    )


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("使い方: python final_judge_orchestrator.py <銘柄コード>")
        print("例: python final_judge_orchestrator.py GOOGL")
        sys.exit(1)

    ticker = sys.argv[1]
    anyio.run(lambda: run_final_judge_orchestrator(ticker))
