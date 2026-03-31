"""
最終判定オーケストレーター

各レーンの judge 結果を集約し、銘柄ごとに1つの最終結論を出す。
オーケストレーター自体はLLMを使わず、プログラムだけで制御する。
"""
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

import anyio

from AgentUtil import call_agent, AgentResult, load_debug_config, save_result_log, side_ja


VALID_STANCES = ("BUY", "SELL", "ADD", "REDUCE", "HOLD")


@dataclass
class FinalJudgeResult:
    """Final Judge の実行結果（呼び出し元へ返すシンプルな構造体）"""
    判定結果: str       # "BUY" | "SELL" | "ADD" | "REDUCE" | "HOLD"
    得票: dict          # {stance: vote_count} 例: {"BUY": 4, "HOLD": 2}
    ログパス: Path      # 生成された final judge ログのパス
    db_data: dict = field(default_factory=dict)

# プロジェクトルート
PROJECT_ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = PROJECT_ROOT / ".claude" / "commands"
LOGS_DIR = PROJECT_ROOT / "logs"


def get_next_final_judge_num(ticker: str, discusion_dir: Path | None = None) -> int:
    """既存のfinal_judgeファイルから次の番号を返す"""
    base = discusion_dir if discusion_dir else LOGS_DIR
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


def compute_vote_tally(
    all_opinion_sides: list[str],
    mode: str,
) -> tuple[dict[str, int], int, str]:
    """
    全 Opinion の個別票で投票集計と判定を行う。

    未保有（mode="buy"）: BUY は全会一致の場合のみ。1票でも HOLD があれば HOLD。
    保有中（mode="review"）: 最多票が勝ち。同点なら HOLD。

    Returns:
        (vote_counts, total_votes, verdict)
    """
    votes: dict[str, int] = {s: 0 for s in VALID_STANCES}

    for side in all_opinion_sides:
        s = side.upper()
        if s in votes:
            votes[s] += 1
        else:
            votes["HOLD"] += 1

    total = sum(votes.values())

    if total == 0:
        return votes, 0, "HOLD"

    if mode == "buy":
        if votes["BUY"] == total:
            verdict = "BUY"
        else:
            verdict = "HOLD"
    else:
        max_count = max(votes.values())
        top_stances = [s for s, c in votes.items() if c == max_count]
        if len(top_stances) == 1:
            verdict = top_stances[0]
        else:
            verdict = "HOLD"

    return votes, total, verdict


def _read_latest_judge(ticker: str, set_num: int, discusion_dir: Path | None = None) -> str:
    """指定レーンの最新 judge ファイルを読んで返す（無ければ空文字）"""
    base = discusion_dir if discusion_dir else LOGS_DIR
    t = ticker.upper()
    pattern = f"{t}_set{set_num}_judge_*.md"
    existing = sorted(base.glob(pattern))
    if not existing:
        return ""
    latest = existing[-1]
    return latest.read_text(encoding="utf-8")


def _extract_analyzer_summary(content: str) -> str:
    """議論テキストから暫定結論・EXPORT 付近を抽出する（全文は大きすぎるため）"""
    if not content:
        return ""
    idx = content.rfind("暫定結論")
    if idx != -1:
        start = max(0, content.rfind("\n#", 0, idx))
        return content[start:].strip()
    return content[-3000:].strip() if len(content) > 3000 else content


def _read_analyzer_export(ticker: str, set_num: int, discusion_dir: Path | None = None) -> str:
    """議論ログから暫定結論・EXPORT 付近を抽出する（全文は大きすぎるため）"""
    base = discusion_dir if discusion_dir else LOGS_DIR
    t = ticker.upper()
    log_path = base / f"{t}_set{set_num}.md"
    if not log_path.exists():
        return ""
    content = log_path.read_text(encoding="utf-8")
    return _extract_analyzer_summary(content)


def build_final_judge_prompt(
    ticker: str,
    final_no: int,
    agreed_sets: list[int] | None = None,
    mode: str = "buy",
    disagreed_sets: list[int] | None = None,
    set_sides: dict[int, str] | None = None,
    all_opinion_sides: list[str] | None = None,
    discusion_dir: Path | None = None,
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

    if mode == "review":
        mode_line = "【アクション判定】保有中の銘柄に対する議論です。選択肢は HOLD / ADD / REDUCE / SELL です。\n\n"
    else:
        mode_line = "【アクション判定】未保有の銘柄に対する議論です。選択肢は BUY / HOLD です。\n\n"

    vote_section = ""
    if all_opinion_sides:
        votes, total, verdict = compute_vote_tally(all_opinion_sides, mode)
        vote_lines = " / ".join(f"{s}: {c}票" for s, c in votes.items() if c > 0)
        if mode == "buy":
            rule_desc = "未保有のため BUY は全会一致の場合のみ。1票でも HOLD があれば HOLD"
        else:
            rule_desc = "最多得票が勝ち。同数の場合は HOLD 優先"
        vote_section = (
            f"\n"
            f"【投票集計（オーケストレーター算出・確定値）】\n"
            f"  {vote_lines} / 合計: {total}票\n"
            f"  適用ルール: {rule_desc}\n"
            f"  → **確定判定: {verdict}**\n"
            f"  ※ この判定は投票ルールに基づく確定値です。最終判定（supported_side）はこれに従ってください。\n"
        )

    # 各レーンの judge 内容と議論結論をインライン埋め込み
    inline_sections = []
    for sn in all_sets:
        label = "AGREED" if sn in agreed_sets else "DISAGREED"
        judge_content = _read_latest_judge(ticker, sn, discusion_dir)
        analyzer_export = _read_analyzer_export(ticker, sn, discusion_dir)

        section = f"--- set{sn} [{label}] ここから ---\n"
        if analyzer_export:
            section += f"【議論ログ（暫定結論抜粋）】\n{analyzer_export}\n\n"
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
    all_opinion_sides: list[str] | None = None,
    discusion_dir: Path | None = None,
    holding: dict | None = None,
) -> FinalJudgeResult:
    """
    最終判定オーケストレーターを実行。

    Args:
        ticker: 銘柄コード
        agreed_sets: opinionが一致したレーン番号のリスト（Noneなら全レーン対象）
        disagreed_sets: opinionが不一致だったレーン番号のリスト
    """
    t = ticker.upper()
    final_no = get_next_final_judge_num(ticker, discusion_dir)

    # 対象レーンを決定
    if agreed_sets is None:
        agreed_sets = [1, 2, 3]
    if disagreed_sets is None:
        disagreed_sets = []

    all_sets = sorted(set(agreed_sets) | set(disagreed_sets))
    target_sets_str = ", ".join(f"set{sn}" for sn in all_sets)

    if all_opinion_sides:
        votes, _total, verdict = compute_vote_tally(all_opinion_sides, mode)
    elif set_sides is not None:
        votes, verdict = {s: 0 for s in VALID_STANCES}, "UNKNOWN"
    else:
        votes, verdict = {}, "UNKNOWN"

    print(f"=== {t} 最終判定オーケストレーター ===")
    print(f"  対象レーン: {target_sets_str}")
    if agreed_sets:
        print(f"    一致: {', '.join(f'set{sn}' for sn in agreed_sets)}")
    if disagreed_sets:
        print(f"    不一致: {', '.join(f'set{sn}' for sn in disagreed_sets)}")
    if set_sides is not None:
        vote_str = " / ".join(f"{side_ja(s)} {c}" for s, c in votes.items() if c > 0)
        print(f"  投票: {vote_str} (計{sum(votes.values())}票)")
        print(f"  確定判定: {side_ja(verdict)}")
    print(f"  出力: {t}_final_judge_{final_no}.md")
    print()

    prompt = build_final_judge_prompt(ticker, final_no, agreed_sets, mode, disagreed_sets, set_sides, all_opinion_sides, discusion_dir)
    agent_file = AGENTS_DIR / "final-judge.md"

    MAX_AGENT_RETRIES = 3
    print(f"[全レーン] 最終判定 起動")
    dbg = load_debug_config("final_judge")
    show_cost = dbg.pop("show_cost", False)

    result = AgentResult()
    for attempt in range(1, MAX_AGENT_RETRIES + 1):
        if attempt > 1:
            print(f"[全レーン] 最終判定 リトライ {attempt}/{MAX_AGENT_RETRIES}")
        try:
            result = await call_agent(
                prompt,
                file_path=str(agent_file),
                show_cost=show_cost,
                show_tools=False,
                **dbg,
            )
            if result and result.text:
                break
            print(f"[全レーン] 最終判定 警告: 応答なし")
        except Exception as e:
            print(f"[全レーン] 最終判定 エラー: {e}")

    print(f"[全レーン] 最終判定 完了")
    if show_cost and result.cost:
        print(f"  コスト: ${result.cost:.4f}")

    # オーケストレーター側でログファイルに書き出し
    base = discusion_dir if discusion_dir else LOGS_DIR
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
        m_side = re.search(r"(?:支持側|supported_side|統合支持側|merged_side):\s*\**(\S+?)(?:\*|$|\s)", content)
        m_agree = re.search(r"(?:総合一致度|overall_agreement):\s*\**(\S+?)(?:\*|$|\s)", content)
        side = m_side.group(1) if m_side else "N/A"
        agree = m_agree.group(1) if m_agree else "N/A"
        print(f"  最終判定: {side_ja(side)}")
        print(f"  一致度: {agree}")
    else:
        side = "N/A"
        agree = "N/A"
        print(f"  応答テキストが空です")

    print("=" * 60)

    # 投票を action/safe に集約（Planning互換）
    if set_sides is not None:
        _action = ("BUY", "SELL", "ADD", "REDUCE")
        action_votes = sum(votes.get(s, 0) for s in _action)
        safe_votes = votes.get("HOLD", 0)
    else:
        action_votes = 0
        safe_votes = 0

    fj_db_data = {
        "votes": votes if set_sides is not None else {},
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
        得票=votes if set_sides is not None else {},
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
