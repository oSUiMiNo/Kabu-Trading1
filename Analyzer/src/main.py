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
        print(f"  [{ticker}] 重要指標スクリプトが見つかりません: {script}")
        return
    if not venv_python.exists():
        print(f"  [{ticker}] 重要指標 venv が見つかりません: {venv_python}")
        return
    try:
        proc = subprocess.run(
            [str(venv_python), str(script), "--ticker", ticker, "--archive-id", archive_id],
            timeout=120,
        )
        if proc.returncode != 0:
            print(f"  [{ticker}] 重要指標取得失敗（exit code: {proc.returncode}）")
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
            indicators = data.get("indicators", {})
            raw = indicators.get("raw", {})
            derived = indicators.get("derived", {})
            if derived:
                trend = derived.get("trend", {})
                momentum = derived.get("momentum", {})
                volatility = derived.get("volatility", {})
                volume_d = derived.get("volume", {})
                lines = []
                if trend:
                    t_parts = [f"{k}={v}" for k, v in trend.items()]
                    for rk in ("sma_20", "sma_50", "sma_200", "adx"):
                        rv = raw.get(rk)
                        if rv is not None:
                            t_parts.append(f"{rk}={rv}")
                    lines.append(f"  トレンド: {', '.join(t_parts)}")
                if momentum:
                    m_parts = [f"{k}={v}" for k, v in momentum.items()]
                    for rk in ("rsi_14", "stoch_k"):
                        rv = raw.get(rk)
                        if rv is not None:
                            m_parts.append(f"{rk}={round(rv, 1) if isinstance(rv, float) else rv}")
                    macd_raw = raw.get("macd")
                    if isinstance(macd_raw, dict) and macd_raw.get("macd") is not None:
                        m_parts.append(f"macd={round(macd_raw['macd'], 2)}")
                    lines.append(f"  モメンタム: {', '.join(m_parts)}")
                if volatility:
                    v_parts = [f"{k}={v}" for k, v in volatility.items()]
                    for rk in ("atr", "natr"):
                        rv = raw.get(rk)
                        if rv is not None:
                            v_parts.append(f"{rk}={round(rv, 2) if isinstance(rv, float) else rv}")
                    lines.append(f"  ボラティリティ: {', '.join(v_parts)}")
                if volume_d:
                    vol_parts = [f"{k}={v}" for k, v in volume_d.items()]
                    for rk in ("mfi",):
                        rv = raw.get(rk)
                        if rv is not None:
                            vol_parts.append(f"{rk}={round(rv, 1) if isinstance(rv, float) else rv}")
                    lines.append(f"  出来高: {', '.join(vol_parts)}")
                if lines:
                    parts.append(f"【テクニカル指標 ({tf})】")
                    parts.extend(lines)

    # 重要指標
    ii = record.get("important_indicators")
    if ii and isinstance(ii, dict):
        market = ii.get("market", {})
        if market:
            items = []
            if market.get("vix") is not None:
                items.append(f"VIX {market['vix']}")
            if market.get("us_10y_yield") is not None:
                items.append(f"米10年債 {market['us_10y_yield']}%")
            if market.get("ffr") is not None:
                items.append(f"FRB金利 {market['ffr']}%")
            if market.get("boj_rate") is not None:
                items.append(f"日銀金利 {market['boj_rate']}%")
            if items:
                parts.append(f"【市場環境】{', '.join(items)}")

        event = ii.get("event_risk", {})
        if event.get("nearest_event"):
            ev_text = f"【イベントリスク】{event['nearest_event']} まで {event.get('days_to_event', '?')}日"
            if event.get("implied_move_pct"):
                ev_text += f"（期待変動 {event['implied_move_pct']}%）"
            parts.append(ev_text)

        earnings = ii.get("earnings", {})
        if earnings.get("eps_actual") is not None:
            parts.append(
                f"【直近決算】EPS 予想{earnings.get('eps_estimate', '?')} → 実績{earnings['eps_actual']}"
                f"（サプライズ {earnings.get('eps_surprise_pct', '?')}%）"
            )
        if earnings.get("revenue_actual") is not None:
            rev_text = f"【売上】実績{earnings['revenue_actual']:,.0f}"
            if earnings.get("revenue_estimate") is not None:
                rev_text += f" vs 予想{earnings['revenue_estimate']:,.0f}"
            if earnings.get("revenue_surprise_pct") is not None:
                rev_text += f"（サプライズ {earnings['revenue_surprise_pct']}%）"
            parts.append(rev_text)

        rs = ii.get("relative_strength", {})
        if rs.get("vs_index_3m_pct") is not None:
            rs_text = f"【相対強度】指数対比 {rs['vs_index_3m_pct']:+.1f}%"
            if rs.get("vs_sector_3m_pct") is not None:
                rs_text += f", セクター対比 {rs['vs_sector_3m_pct']:+.1f}%"
            parts.append(rs_text)

        vol = ii.get("volume", {})
        vol_parts = []
        if vol.get("volume_ratio_5d") is not None:
            vol_parts.append(f"5日平均比 {vol['volume_ratio_5d']}倍")
        if vol.get("dollar_volume") is not None:
            currency = vol.get("currency", "USD")
            if currency == "JPY":
                vol_parts.append(f"売買代金 {vol['dollar_volume']/100_000_000:,.0f}億円")
            else:
                vol_parts.append(f"売買代金 ${vol['dollar_volume']/1_000_000:,.0f}M")
        if vol_parts:
            parts.append(f"【出来高】{', '.join(vol_parts)}")

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

    _arc = safe_db(get_archivelog_by_id, _db_archivelog_id) if _db_archivelog_id else None
    _arc_tech = (_arc.get("technical") or {}) if _arc and isinstance(_arc.get("technical"), dict) else {}
    holding = _arc_tech.get("holdings_snapshot") or {}

    t = ticker.upper()

    from analyzer_orchestrator import _HORIZON_LABELS
    _mode_labels = {"sell": "保有中", "add": "保有中", "buy": "未保有", "review": "保有中"}
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
            holding=holding,
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
    all_opinion_sides = []
    for r in lane_results:
        if r and r.一致度 != "ERROR":
            if r.支持側:
                set_sides[r.レーン番号] = r.支持側
            all_opinion_sides.extend(r.opinion_sides)

    fj_result = await run_final_judge_orchestrator(
        ticker, agreed_sets, mode=mode,
        disagreed_sets=disagreed_sets, set_sides=set_sides,
        all_opinion_sides=all_opinion_sides,
        discusion_dir=discusion_dir,
        holding=holding,
    )

    # DB: 最終判定 + セッション完了
    if _db_archivelog_id and fj_result:
        safe_db(
            update_archivelog, _db_archivelog_id,
            final_judge=fj_result.db_data,
            verdict=fj_result.判定結果,
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
    _mode_map = {"買う": "buy", "売る": "sell", "買い増す": "add", "見直す": "review", "buy": "buy", "sell": "sell", "add": "add", "review": "review"}
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
