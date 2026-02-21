"""
判定オーケストレーター

各レーンの opinion ペア（A, B）を judge サブエージェントに渡し、
一致/不一致を判定させる。
3レーン分を並行実行する。
オーケストレーター自体はLLMを使わず、プログラムだけで制御する。
"""
import re
import sys
from pathlib import Path

import anyio

from AgentUtil import call_agent, AgentResult, load_debug_config, save_result_log, side_ja

# プロジェクトルート
PROJECT_ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = PROJECT_ROOT / ".claude" / "commands"
LOGS_DIR = PROJECT_ROOT / "logs"


def get_next_judge_num(ticker: str, set_num: int, session_dir: Path | None = None) -> int:
    """既存のjudgeファイルから次の番号を返す"""
    base = session_dir if session_dir else LOGS_DIR
    pattern = f"{ticker.upper()}_set{set_num}_judge_*.md"
    existing = list(base.glob(pattern))
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
    opinion_a_num: int,
    opinion_a_text: str,
    opinion_b_num: int,
    opinion_b_text: str,
    judge_num: int,
    mode: str = "buy",
    session_dir: Path | None = None,
) -> str:
    """judgeエージェントに渡すプロンプトを組み立てる（opinionテキストをインライン埋め込み）"""
    t = ticker.upper()
    base = session_dir if session_dir else LOGS_DIR
    source_log = (base / f"{t}_set{set_num}.md").as_posix()

    if mode == "sell":
        mode_line = "【議論モード: 売る】売るべきか・売らないべきか（保有継続）の議論です。\n\n"
    else:
        mode_line = "【議論モード: 買う】買うべきか・買わないべきかの議論です。\n\n"

    return (
        f"{mode_line}"
        f"銘柄「{t}」の set{set_num} について判定してください。\n"
        f"\n"
        f"【重要】まず元の議論ログを読んでから、opinionを評価してください。\n"
        f"\n"
        f"元ログ（Analyst vs Devils）: {source_log}\n"
        f"judge_no: {judge_num}\n"
        f"\n"
        f"--- opinion_A (opinion#{opinion_a_num}) ここから ---\n"
        f"{opinion_a_text}\n"
        f"--- opinion_A ここまで ---\n"
        f"\n"
        f"--- opinion_B (opinion#{opinion_b_num}) ここから ---\n"
        f"{opinion_b_text}\n"
        f"--- opinion_B ここまで ---\n"
        f"\n"
        f"1. 最初に元ログを Read し、議論の内容を把握してください。\n"
        f"2. 上記の opinion_A / opinion_B テキストから supported_side が一致しているか判定してください。\n"
        f"3. 結果を **応答テキストとして出力** してください。ファイルの作成は不要です。\n"
        f"Glob による番号採番は不要です（オーケストレーターが決定済み）。"
    )


async def run_single_judge(
    ticker: str,
    set_num: int,
    opinion_a_num: int,
    opinion_a_text: str,
    opinion_b_num: int,
    opinion_b_text: str,
    judge_num: int,
    mode: str = "buy",
    session_dir: Path | None = None,
) -> AgentResult:
    """1体のjudgeエージェントを実行し、結果テキストをファイルに書き出す"""
    label = f"判定#{judge_num} (意見{opinion_a_num} vs 意見{opinion_b_num})"
    print(f"[レーン{set_num}] {label} 起動")

    prompt = build_judge_prompt(ticker, set_num, opinion_a_num, opinion_a_text, opinion_b_num, opinion_b_text, judge_num, mode, session_dir)
    agent_file = AGENTS_DIR / "judge.md"

    dbg = load_debug_config("judge")
    show_cost = dbg.pop("show_cost", False)
    result = await call_agent(
        prompt,
        file_path=str(agent_file),
        show_cost=show_cost,
        show_tools=False,
        **dbg,
    )

    print(f"[レーン{set_num}] {label} 完了")
    if show_cost and result.cost:
        print(f"  コスト: ${result.cost:.4f}")

    # オーケストレーター側でファイル書き出し
    base = session_dir if session_dir else LOGS_DIR
    judge_path = base / f"{ticker.upper()}_set{set_num}_judge_{judge_num}.md"
    saved = save_result_log(result, judge_path)
    if saved:
        print(f"  ログ: {saved.name}")

    return result


async def run_judge_orchestrator(
    ticker: str,
    opinion_pairs: list[tuple[int, int, str, int, str]],
    mode: str = "buy",
):
    """
    判定オーケストレーターのメインループ。

    opinion_pairs の各ペアに対して judge を並行起動する。

    Args:
        ticker: 銘柄コード
        opinion_pairs: [(set_num, opinion_a_num, opinion_a_text, opinion_b_num, opinion_b_text), ...] のリスト
    """
    if not opinion_pairs:
        print("エラー: 判定対象の opinion ペアがありません")
        return

    total = len(opinion_pairs)

    print(f"=== {ticker.upper()} 判定オーケストレーター ===")
    print(f"対象: {total}レーン")
    for sn, oa_num, _, ob_num, _ in opinion_pairs:
        print(f"  レーン{sn}: 意見{oa_num} vs 意見{ob_num}")
    print()

    # 各レーンの judge 番号を事前に決定
    tasks = []
    for sn, oa_num, oa_text, ob_num, ob_text in opinion_pairs:
        jn = get_next_judge_num(ticker, sn)
        tasks.append((sn, oa_num, oa_text, ob_num, ob_text, jn))

    # 全体を並行実行
    results: list[AgentResult] = [None] * len(tasks)

    async def _run(idx: int, set_num: int, oa_num: int, oa_text: str, ob_num: int, ob_text: str, jn: int):
        results[idx] = await run_single_judge(ticker, set_num, oa_num, oa_text, ob_num, ob_text, jn, mode)

    async with anyio.create_task_group() as tg:
        for idx, (sn, oa_num, oa_text, ob_num, ob_text, jn) in enumerate(tasks):
            tg.start_soon(_run, idx, sn, oa_num, oa_text, ob_num, ob_text, jn)

    # 結果まとめ
    print()
    print("=" * 60)
    print(f"=== 全{total}件完了 — 判定結果一覧 ===")
    print("=" * 60)

    show_cost = load_debug_config("judge").get("show_cost", False)
    total_cost = 0.0
    agreed_sets = []  # AGREEDのレーンを記録
    disagreed_sets = []  # DISAGREEDのレーンを記録

    for idx, (sn, _oa_num, _oa_text, _ob_num, _ob_text, jn) in enumerate(tasks):
        r = results[idx]
        cost = r.cost if r and r.cost else 0.0
        total_cost += cost

        # result.text からEXPORTを簡易読み取り（日本語フィールド対応）
        content = r.text if r and r.text else ""
        agreement = "N/A"
        agreed_side = "N/A"
        if content:
            # 日本語フィールド名を優先、フォールバックで英語も対応
            # **AGREED** のようなマークダウン太字にも対応
            m_agree = re.search(r"(?:一致度|agreement):\s*\**(\w+)", content)
            if m_agree:
                agreement = m_agree.group(1)
            m_side = re.search(r"(?:一致支持側|agreed_supported_side):\s*\**(\S+?)(?:\*|$|\s)", content)
            if m_side:
                agreed_side = m_side.group(1)

        # 一致/不一致を判定
        cost_suffix = f"  ${cost:.4f}" if show_cost else ""
        if agreement == "AGREED":
            agreed_sets.append(sn)
            print(f"  レーン{sn} 判定#{jn}: ✓ 一致 ({side_ja(agreed_side)}){cost_suffix}")
        else:
            disagreed_sets.append(sn)
            print(f"  レーン{sn} 判定#{jn}: ✗ 不一致 → このレーンのフローは終了{cost_suffix}")

    if show_cost:
        print(f"\n  合計コスト: ${total_cost:.4f}")
    print("=" * 60)

    # --- 不一致レーンの処理 ---
    if disagreed_sets:
        print()
        print(f"【不一致】レーン {', '.join(map(str, disagreed_sets))} は意見が不一致（最終判定で考慮されます）")

    # --- 最終判定フェーズ ---
    if not agreed_sets and not disagreed_sets:
        print()
        print("【終了】全レーンがエラーのため、最終判定は実行しません")
        return

    from final_judge_orchestrator import run_final_judge_orchestrator

    print()
    if disagreed_sets and agreed_sets:
        print(f">>> 一致レーン {', '.join(map(str, agreed_sets))} + 不一致レーン {', '.join(map(str, disagreed_sets))} で最終判定フェーズへ移行")
    elif agreed_sets:
        print(f">>> 全レーン一致 → 最終判定フェーズへ移行")
    else:
        print(f">>> 全レーン不一致 → 最終判定フェーズへ移行（不一致のみ）")
    print()
    await run_final_judge_orchestrator(ticker, agreed_sets, mode=mode, disagreed_sets=disagreed_sets)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("使い方: python judge_orchestrator.py <銘柄コード> [レーン番号 (カンマ区切り)]")
        print("例: python judge_orchestrator.py GOOGL 1,2,3")
        print("  各レーンの最新意見ペアをファイルから読み込んで判定を実行")
        sys.exit(1)

    ticker = sys.argv[1]
    if len(sys.argv) > 2:
        set_nums = [int(x) for x in sys.argv[2].split(",")]
    else:
        set_nums = [1, 2, 3]

    # 各レーンの opinion ファイルを検出してテキストを読み込む
    pairs = []
    for sn in set_nums:
        pattern = f"{ticker.upper()}_set{sn}_opinion_*.md"
        existing = sorted(LOGS_DIR.glob(pattern))
        if len(existing) >= 2:
            # 最新2つを使用
            file_a = existing[-2]
            file_b = existing[-1]
            num_a = int(re.search(r"_opinion_(\d+)\.md$", file_a.name).group(1))
            num_b = int(re.search(r"_opinion_(\d+)\.md$", file_b.name).group(1))
            text_a = file_a.read_text(encoding="utf-8")
            text_b = file_b.read_text(encoding="utf-8")
            pairs.append((sn, num_a, text_a, num_b, text_b))
        else:
            print(f"  レーン{sn}: 意見が2つ未満のためスキップ")

    anyio.run(lambda: run_judge_orchestrator(ticker, pairs))
