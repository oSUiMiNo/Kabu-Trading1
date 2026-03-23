"""
Analyzer オーケストレーター（レーン型アーキテクチャ）

指定された1銘柄に対して複数のレーンを並行実行する。
各レーンは「議論 → Opinion → Judge」のフローを独立して完結させる。
全レーン完了後、全レーン（AGREED+DISAGREED）でFinal Judgeを実行する。
複数銘柄の並列実行は analyzer_batch.py（PJTルート）が担う。

Usage:
    python main.py <銘柄> <投資期間> [モード] [--archive-id ID] [--display-name 名前]
"""
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import anyio

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "shared"))
from supabase_client import (
    safe_db,
    create_archivelog,
    update_archivelog,
    get_analyzer_config,
    get_archivelog_by_id,
    ensure_technical_data,
)

from analyzer_orchestrator import LOGS_DIR
from lane_orchestrator import run_lane, LaneResult
from AgentUtil import side_ja, load_debug_config
from final_judge_orchestrator import run_final_judge_orchestrator

def _ensure_important_indicators(archive_id: str, ticker: str):
    """important_indicators が未取得なら重要指標ブロックを呼び出す。"""
    record = safe_db(get_archivelog_by_id, archive_id)
    if record and record.get("important_indicators"):
        return
    ii_dir = Path(__file__).resolve().parent.parent.parent / "ImportantIndicators"
    venv_python = ii_dir / ".venv" / "Scripts" / "python.exe"
    if not venv_python.exists():
        venv_python = ii_dir / ".venv" / "bin" / "python"
    script = ii_dir / "src" / "main.py"
    if not script.exists():
        return
    try:
        subprocess.run(
            [str(venv_python), str(script), "--ticker", ticker, "--archive-id", archive_id],
            timeout=120,
        )
    except Exception as e:
        print(f"  [{ticker}] 重要指標取得エラー: {e}")


def _build_market_context(archive_id: str | None) -> str:
    """archive の monitor/technical データから議論用の市場コンテキストを構築する。"""
    if not archive_id:
        return ""
    record = safe_db(get_archivelog_by_id, archive_id)
    if not record:
        return ""

    parts = []
    technical = record.get("technical")
    tech_price = None
    if technical and isinstance(technical, dict):
        tech_price = technical.get("latest_price")

    monitor = record.get("monitor")
    display_price = tech_price
    if display_price is None and monitor and isinstance(monitor, dict):
        display_price = monitor.get("current_price")

    if display_price is not None:
        parts.append(f"【現在株価】{display_price}")

    if monitor and isinstance(monitor, dict):
        change = monitor.get("price_change_pct")
        if change is not None:
            parts.append(f"  プラン時からの変動: {change}%")

    technical = record.get("technical")
    if technical and isinstance(technical, dict) and "timeframes" in technical:
        for tf, data in technical["timeframes"].items():
            if not isinstance(data, dict):
                continue
            derived = data.get("indicators", {}).get("derived", {})
            if derived:
                trend = derived.get("trend", {})
                momentum = derived.get("momentum", {})
                volatility = derived.get("volatility", {})
                lines = []
                if trend:
                    lines.append(f"  トレンド: {', '.join(f'{k}={v}' for k, v in trend.items())}")
                if momentum:
                    lines.append(f"  モメンタム: {', '.join(f'{k}={v}' for k, v in momentum.items())}")
                if volatility:
                    lines.append(f"  ボラティリティ: {', '.join(f'{k}={v}' for k, v in volatility.items())}")
                if lines:
                    parts.append(f"【テクニカル指標 ({tf})】")
                    parts.extend(lines)

    return "\n".join(parts) if parts else ""


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
    display_name: str = "",
    existing_archive_id: str | None = None,
):
    if num_sets is None or max_rounds is None or opinions_per_set is None:
        disc_cfg = safe_db(get_analyzer_config) or {}
        num_sets = num_sets or disc_cfg.get("num_sets", 2)
        max_rounds = max_rounds or disc_cfg.get("max_rounds", 4)
        opinions_per_set = opinions_per_set or disc_cfg.get("opinions_per_set", 2)

    LOGS_DIR.mkdir(parents=True, exist_ok=True)

    discusion_name = datetime.now().strftime("%y%m%d_%H%M")
    discusion_dir = LOGS_DIR / discusion_name
    discusion_dir.mkdir(parents=True, exist_ok=True)

    # DB: 既存 archive があればそこに書き足す。なければ新規作成
    if existing_archive_id:
        _db_archivelog_id = existing_archive_id
    else:
        _db_archivelog = safe_db(create_archivelog, ticker, mode, horizon)
        _db_archivelog_id = _db_archivelog["id"] if _db_archivelog else None

    if _db_archivelog_id:
        ensure_technical_data(_db_archivelog_id)
        _ensure_important_indicators(_db_archivelog_id, ticker)

    market_context = _build_market_context(_db_archivelog_id)
    if market_context:
        initial_prompt = f"{market_context}\n\n{initial_prompt}" if initial_prompt else market_context

    t = ticker.upper()

    from analyzer_orchestrator import _HORIZON_LABELS
    _mode_labels = {"sell": "保有中", "add": "保有中", "buy": "未保有"}
    mode_label = _mode_labels.get(mode, "未保有")
    horizon_label = _HORIZON_LABELS.get(horizon, horizon)
    print(f"{'='*70}")
    print(f"=== {t} {num_sets}レーン ===")
    print(f"=== 保有状況: {mode_label} / 投資期間: {horizon_label} ===")
    print(f"=== セッション: {discusion_name} ===")
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
            discusion_dir=discusion_dir,
            display_name=display_name,
        )

    async with anyio.create_task_group() as tg:
        for i in range(num_sets):
            tg.start_soon(_run_lane, i, i + 1)

    # 全レーン完了 — 結果集約
    print()
    print("=" * 70)
    print(f"=== 全{num_sets}レーン完了 — 結果一覧 ===")
    print("=" * 70)

    show_cost = load_debug_config("analyzer").get("show_cost", False)
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
    if _db_archivelog_id:
        lanes_data = {}
        for r in lane_results:
            if r and r.db_data:
                lanes_data[str(r.レーン番号)] = r.db_data
        if lanes_data:
            safe_db(update_archivelog, _db_archivelog_id, lanes=lanes_data)

    # --- 最終判定 ---
    if not agreed_sets and not disagreed_sets:
        print()
        print(" ※終了 全レーンがエラーのため、最終判定は実行しません")
        if _db_archivelog_id:
            safe_db(update_archivelog, _db_archivelog_id, status="error")
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
        discusion_dir=discusion_dir,
    )

    # DB: 最終判定 + セッション完了
    if _db_archivelog_id and fj_result:
        safe_db(
            update_archivelog, _db_archivelog_id,
            final_judge=fj_result.db_data,
            verdict=fj_result.判定結果,
            status="completed",
        )


if __name__ == "__main__":
    _horizon_map = {"短期": "short", "中期": "mid", "長期": "long",
                    "short": "short", "mid": "mid", "long": "long"}

    if len(sys.argv) < 3 or sys.argv[2] not in _horizon_map:
        print("使い方:")
        print("  python main.py <銘柄> <投資期間> [モード] [--archive-id ID] [--display-name 名前]")
        print()
        print("  投資期間（必須）: '短期' / '中期' / '長期'")
        print("  モード: '買う' / '売る' / '買い増す' (デフォルト: 買う)")
        print()
        print("  バッチ実行は analyzer_batch.py を使用してください。")
        sys.exit(1)

    ticker = sys.argv[1]
    horizon = _horizon_map[sys.argv[2]]
    _mode_map = {"買う": "buy", "売る": "sell", "買い増す": "add", "buy": "buy", "sell": "sell", "add": "add"}
    mode = _mode_map.get(sys.argv[3], "buy") if len(sys.argv) > 3 else "buy"

    _archive_id = None
    _display_name = ""
    num_sets = None
    max_rounds = None
    opinions_per_set = None
    initial_prompt = None

    args = sys.argv[4:]
    i = 0
    while i < len(args):
        if args[i] == "--archive-id" and i + 1 < len(args):
            _archive_id = args[i + 1]
            i += 2
        elif args[i] == "--display-name" and i + 1 < len(args):
            _display_name = args[i + 1]
            i += 2
        else:
            i += 1

    anyio.run(lambda: run_parallel(
        ticker, num_sets, max_rounds, initial_prompt, opinions_per_set,
        mode, horizon, display_name=_display_name, existing_archive_id=_archive_id,
    ))
