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

from AgentUtil import call_agent, AgentResult, load_debug_config, side_ja

# プロジェクトルート
PROJECT_ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = PROJECT_ROOT / ".claude" / "commands"
LOGS_DIR = PROJECT_ROOT / "logs"


def find_set_logs(ticker: str) -> list[Path]:
    """指定銘柄のセットログ（_opinion_を除く）を検索"""
    all_files = sorted(LOGS_DIR.glob(f"{ticker.upper()}_set*.md"))
    return [p for p in all_files if "_opinion_" not in p.name]


def build_opinion_prompt(ticker: str, set_num: int, opinion_num: int, mode: str = "buy", session_dir: Path | None = None) -> str:
    """opinionエージェントに渡すプロンプトを組み立てる"""
    base = session_dir if session_dir else LOGS_DIR
    log_abs = (base / f"{ticker.upper()}_set{set_num}.md").as_posix()

    if mode == "sell":
        mode_line = "【議論モード: 売る】売るべきか・売らないべきか（保有継続）の議論ログです。\n\n"
    else:
        mode_line = "【議論モード: 買う】買うべきか・買わないべきかの議論ログです。\n\n"

    return (
        f"{mode_line}"
        f"銘柄「{ticker.upper()}」の議論ログを読み、意見を出してください。\n"
        f"\n"
        f"対象ログ: {log_abs}\n"
        f"opinion_no: {opinion_num}\n"
        f"\n"
        f"opinion_no={opinion_num} として意見を **応答テキストとして出力** してください。\n"
        f"ファイルの作成は不要です。フォーマットに従ってテキストで応答してください。"
    )


async def run_single_opinion(
    ticker: str,
    set_num: int,
    opinion_num: int,
    mode: str = "buy",
    session_dir: Path | None = None,
) -> AgentResult:
    """1体のopinionエージェントを実行"""
    label = f"意見#{opinion_num}"
    print(f"[レーン{set_num}] {label} 起動")

    prompt = build_opinion_prompt(ticker, set_num, opinion_num, mode, session_dir)
    agent_file = AGENTS_DIR / "opinion.md"

    dbg = load_debug_config("opinion")
    result = await call_agent(
        prompt,
        file_path=str(agent_file),
        show_cost=True,
        show_tools=False,
        **dbg,
    )

    print(f"[レーン{set_num}] {label} 完了")

    return result


async def run_opinion_orchestrator(
    ticker: str,
    opinions_per_set: int = 2,
    mode: str = "buy",
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
        print(f"エラー: {ticker.upper()} のレーンログが見つかりません（logs/{ticker.upper()}_set*.md）")
        return

    # セット番号を抽出
    set_nums = []
    for p in set_logs:
        m = re.search(r"_set(\d+)\.md$", p.name)
        if m:
            set_nums.append(int(m.group(1)))

    total = len(set_nums) * opinions_per_set

    print(f"対象レーン: {len(set_nums)}個 ({', '.join(f'set{n}' for n in set_nums)})")
    print(f"各レーン {opinions_per_set}体 × {len(set_nums)}レーン = 合計 {total}体")
    print()

    # 各セットのopinion番号を固定連番で決定
    tasks = []
    for sn in set_nums:
        for i in range(opinions_per_set):
            tasks.append((sn, 1 + i))

    # 全体を並行実行
    results: list[AgentResult] = [None] * len(tasks)

    async def _run(idx: int, set_num: int, opinion_num: int):
        results[idx] = await run_single_opinion(ticker, set_num, opinion_num, mode)

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

        # result.text からEXPORTを簡易読み取り（日本語フィールド対応）
        content = r.text if r and r.text else ""
        side = "N/A"
        pos_score = "?"
        neg_score = "?"
        winner = "?"
        basis = "?"
        if content:
            # 日本語フィールド名を優先、フォールバックで英語も対応
            m_side = re.search(r"(?:支持側|supported_side):\s*(\S+)", content)
            if m_side:
                side = m_side.group(1)
            # buy mode: 買い支持 / 買わない支持
            m_buy = re.search(r"(?:買い支持|buy_support):\s*(\d+)", content)
            m_notbuy = re.search(r"(?:買わない支持|not_buy_support):\s*(\d+)", content)
            # sell mode: 売り支持 / 売らない支持
            m_sell = re.search(r"(?:売り支持|sell_support):\s*(\d+)", content)
            m_notsell = re.search(r"(?:売らない支持|not_sell_support):\s*(\d+)", content)
            if m_buy:
                pos_score = m_buy.group(1)
            elif m_sell:
                pos_score = m_sell.group(1)
            if m_notbuy:
                neg_score = m_notbuy.group(1)
            elif m_notsell:
                neg_score = m_notsell.group(1)
            m_winner = re.search(r"(?:勝者エージェント|winner_agent):\s*(\S+)", content)
            if m_winner:
                winner = m_winner.group(1)
            m_basis = re.search(r"(?:勝因|win_basis):\s*(\S+)", content)
            if m_basis:
                basis = m_basis.group(1)

        # モードに応じた表示ラベル
        if side in ("SELL", "NOT_SELL_HOLD"):
            pos_label, neg_label = "売り", "売らない"
        else:
            pos_label, neg_label = "買い", "買わない"
        print(f"  レーン{sn} 意見#{on}: {side_ja(side)}  ({pos_label}:{pos_score} / {neg_label}:{neg_score})  勝者={winner}({basis})  ${cost:.4f}")

    print(f"\n  合計コスト: ${total_cost:.4f}")
    print("=" * 60)

    # --- Phase: Judge ---
    # 各セットの opinion テキストペアを judge に渡す
    from judge_orchestrator import run_judge_orchestrator

    # tasks は (set_num, opinion_num) のリスト。同一setの連続2つがペア。
    opinion_pairs = []
    pairs_by_set: dict[int, list[tuple[int, str]]] = {}
    for idx, (sn, on) in enumerate(tasks):
        r = results[idx]
        text = r.text if r and r.text else ""
        pairs_by_set.setdefault(sn, []).append((on, text))
    for sn, items in pairs_by_set.items():
        if len(items) >= 2:
            # (set_num, opinion_num_a, opinion_text_a, opinion_num_b, opinion_text_b)
            opinion_pairs.append((sn, items[0][0], items[0][1], items[1][0], items[1][1]))

    if opinion_pairs:
        print()
        print(f">>> 意見生成完了 → 判定フェーズへ移行")
        print()
        await run_judge_orchestrator(ticker, opinion_pairs, mode)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("使い方: python opinion_orchestrator.py <銘柄コード> [モード] [意見数]")
        print("  モード: '買う' or '売る' (デフォルト: 買う)")
        print("例: python opinion_orchestrator.py GOOGL 買う 2")
        sys.exit(1)

    ticker = sys.argv[1]
    _mode_map = {"買う": "buy", "売る": "sell", "buy": "buy", "sell": "sell"}
    mode = _mode_map.get(sys.argv[2], "buy") if len(sys.argv) > 2 else "buy"
    opinions_per_set = int(sys.argv[3]) if len(sys.argv) > 3 else 2

    anyio.run(lambda: run_opinion_orchestrator(ticker, opinions_per_set, mode))
