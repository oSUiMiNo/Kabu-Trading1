"""
並行オーケストレーター（レーン型アーキテクチャ）

同一銘柄に対して複数のレーンを並行実行する。
各レーンは「議論 → Opinion → Judge」のフローを独立して完結させる。
全レーン完了後、全レーン（AGREED+DISAGREED）でFinal Judgeを実行する。

DB書き込みはこのオーケストレーターに集約。
全レーン完了後にlanes JSONB一括書き込み、final_judge完了後に最終判定書き込み。
"""
import sys
from datetime import datetime
from pathlib import Path

import anyio

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "shared"))
from supabase_client import safe_db, create_session, update_session, get_discussion_config

from discussion_orchestrator import LOGS_DIR
from lane_orchestrator import run_lane, LaneResult
from AgentUtil import side_ja, load_debug_config
from final_judge_orchestrator import run_final_judge_orchestrator

SET_THEMES: dict[int, str] = {
    1: "ファンダメンタル（事業・決算・バリュエーション）",
    2: "カタリスト＆リスク（ニュース・イベント・規制・マクロ＆テクニカル・需給）",
}


async def run_parallel(
    ticker: str,
    num_sets: int | None = None,
    max_rounds: int | None = None,
    initial_prompt: str | None = None,
    opinions_per_set: int | None = None,
    mode: str = "buy",
    horizon: str = "mid",
):
    if num_sets is None or max_rounds is None or opinions_per_set is None:
        disc_cfg = safe_db(get_discussion_config) or {}
        num_sets = num_sets or disc_cfg.get("num_sets", 2)
        max_rounds = max_rounds or disc_cfg.get("max_rounds", 4)
        opinions_per_set = opinions_per_set or disc_cfg.get("opinions_per_set", 2)

    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    session_name = datetime.now().strftime("%y%m%d_%H%M")
    session_dir = LOGS_DIR / session_name
    session_dir.mkdir(parents=True, exist_ok=True)

    # DB: セッション作成（1行）
    _db_session = safe_db(create_session, ticker, mode, horizon)
    _db_session_id = _db_session["id"] if _db_session else None

    t = ticker.upper()

    from discussion_orchestrator import _HORIZON_LABELS
    _mode_labels = {"sell": "売る/売らない", "add": "買い増す/買い増さない", "buy": "買う/買わない"}
    mode_label = _mode_labels.get(mode, "買う/買わない")
    horizon_label = _HORIZON_LABELS.get(horizon, horizon)
    print(f"{'='*70}")
    print(f"=== {t} {num_sets}レーン ===")
    print(f"=== 議論モード: {mode_label} / 投資期間: {horizon_label} ===")
    print(f"=== セッション: {session_name} ===")
    print(f"{'='*70}")
    for i in range(1, num_sets + 1):
        theme = SET_THEMES.get(i) if num_sets >= 2 else None
        theme_label = f"  【{theme}】" if theme else ""
        print(f"  レーン{i}: {f'{t}_set{i}.md'}{theme_label}")
    print()

    # 全レーンを並行実行
    lane_results: list[LaneResult] = [None] * num_sets

    async def _run_lane(idx: int, set_num: int):
        theme = SET_THEMES.get(set_num) if num_sets >= 2 else None
        lane_results[idx] = await run_lane(
            ticker,
            set_num=set_num,
            max_rounds=max_rounds,
            initial_prompt=initial_prompt,
            opinions_per_lane=opinions_per_set,
            mode=mode,
            theme=theme,
            horizon=horizon,
            session_dir=session_dir,
        )

    async with anyio.create_task_group() as tg:
        for i in range(num_sets):
            tg.start_soon(_run_lane, i, i + 1)

    # 全レーン完了 — 結果集約
    print()
    print("=" * 70)
    print(f"=== 全{num_sets}レーン完了 — 結果一覧 ===")
    print("=" * 70)

    show_cost = load_debug_config("discussion").get("show_cost", False)
    total_cost = 0.0
    agreed_sets: list[int] = []
    disagreed_sets: list[int] = []
    error_sets: list[int] = []

    for r in lane_results:
        if r is None:
            continue
        total_cost += r.合計コスト

        cost_suffix = f"  ${r.合計コスト:.4f}" if show_cost else ""
        if r.一致度 == "AGREED":
            agreed_sets.append(r.レーン番号)
            print(f"  レーン{r.レーン番号}: ✓ 一致 ({side_ja(r.支持側)}){cost_suffix}")
        elif r.一致度 == "DISAGREED":
            disagreed_sets.append(r.レーン番号)
            print(f"  レーン{r.レーン番号}: ✗ 不一致{cost_suffix}")
        else:
            error_sets.append(r.レーン番号)
            print(f"  レーン{r.レーン番号}: ⚠ エラー{cost_suffix}")

    if show_cost:
        print(f"\n  レーン合計コスト: ${total_cost:.4f}")
    print("=" * 70)

    if error_sets:
        print(f"\n※エラー※ レーン {', '.join(map(str, error_sets))} でエラーが発生しました")
    if disagreed_sets:
        print(f"\n△不一致 レーン {', '.join(map(str, disagreed_sets))} は意見が不一致")

    # DB: 全レーンのデータを一括書き込み
    if _db_session_id:
        lanes_data = {}
        for r in lane_results:
            if r and r.db_data:
                lanes_data[str(r.レーン番号)] = r.db_data
        if lanes_data:
            safe_db(update_session, _db_session_id, lanes=lanes_data)

    # --- 最終判定 ---
    if not agreed_sets and not disagreed_sets:
        print()
        print(" ※終了 全レーンがエラーのため、最終判定は実行しません")
        if _db_session_id:
            safe_db(update_session, _db_session_id, status="error")
        return

    print()
    if disagreed_sets and agreed_sets:
        print(f">>> 一致レーン {', '.join(map(str, agreed_sets))} + 不一致レーン {', '.join(map(str, disagreed_sets))} で最終判定へ")
    elif agreed_sets:
        print(f">>> 全レーン一致 → 最終判定へ")
    else:
        print(f">>> 全レーン不一致 → 最終判定へ（不一致のみ）")
    print()

    set_sides = {}
    for r in lane_results:
        if r and r.一致度 == "AGREED" and r.支持側:
            set_sides[r.レーン番号] = r.支持側

    fj_result = await run_final_judge_orchestrator(
        ticker, agreed_sets, mode=mode,
        disagreed_sets=disagreed_sets, set_sides=set_sides,
        session_dir=session_dir,
    )

    # DB: 最終判定 + セッション完了
    if _db_session_id and fj_result:
        safe_db(
            update_session, _db_session_id,
            final_judge=fj_result.db_data,
            verdict=fj_result.判定結果,
            status="completed",
        )


if __name__ == "__main__":
    _horizon_map = {"短期": "short", "中期": "mid", "長期": "long",
                    "short": "short", "mid": "mid", "long": "long"}

    if len(sys.argv) < 3 or sys.argv[2] not in _horizon_map:
        print("使い方: python parallel_orchestrator.py <銘柄コード> <投資期間> [モード] [レーン数] [最大ラウンド数] [意見数] [追加指示]")
        print()
        print("  投資期間（必須）: '短期' / '中期' / '長期'")
        print("  モード: '買う' / '売る' / '買い増す' (デフォルト: 買う)")
        if len(sys.argv) >= 2 and (len(sys.argv) < 3 or sys.argv[2] not in _horizon_map):
            print()
            print(f"⚠ 投資期間が指定されていません。銘柄の直後に '短期' '中期' '長期' のいずれかを指定してください。")
        sys.exit(1)

    ticker = sys.argv[1]
    horizon = _horizon_map[sys.argv[2]]
    _mode_map = {"買う": "buy", "売る": "sell", "買い増す": "add", "buy": "buy", "sell": "sell", "add": "add"}
    mode = _mode_map.get(sys.argv[3], "buy") if len(sys.argv) > 3 else "buy"
    num_sets = int(sys.argv[4]) if len(sys.argv) > 4 else None
    max_rounds = int(sys.argv[5]) if len(sys.argv) > 5 else None
    opinions_per_set = int(sys.argv[6]) if len(sys.argv) > 6 else None
    initial_prompt = sys.argv[7] if len(sys.argv) > 7 else None

    anyio.run(lambda: run_parallel(ticker, num_sets, max_rounds, initial_prompt, opinions_per_set, mode, horizon))
