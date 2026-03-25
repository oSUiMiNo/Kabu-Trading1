"""
Monitor オーケストレーター

指定された1銘柄について、最新プランの前提が現在の市場状況で維持されているかをチェックする。
複数銘柄の並列実行は monitor_batch.py（PJTルート）が担う。

Usage:
    python main.py --ticker NVDA
"""
import re
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

import asyncio

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "shared"))
from supabase_client import (
    safe_db,
    get_latest_archivelog_with_newplan,
    get_archivelog_by_id,
    create_archivelog,
    update_archivelog,
    get_client,
)

from AgentUtil import call_agent, load_debug_config

PROJECT_ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = PROJECT_ROOT / ".claude" / "commands"

JST = timezone(timedelta(hours=9))


def build_check_prompt(ticker: str, archivelog: dict, today_archive: dict | None = None) -> str:
    """monitor-checker エージェントに渡すプロンプトを組み立てる。
    archivelog: 過去の newplan_full 付きアーカイブ（プラン情報の参照用）
    today_archive: 今日の archive（重要指標・テクニカルデータの参照用）
    """
    newplan_full = archivelog.get("newplan_full", "")
    verdict = archivelog.get("verdict", "N/A")

    plan_price = "不明"
    horizon = "不明"
    confidence = "N/A"
    basis_text = "  （なし）"
    monitoring_hint = {}

    if newplan_full:
        try:
            parsed = yaml.safe_load(newplan_full)
            if parsed:
                data_checks = parsed.get("data_checks", {})
                plan_price = data_checks.get("current_price", "不明")

                decision = parsed.get("decision", {})
                horizon = decision.get("horizon", "不明")
                confidence = decision.get("confidence", "N/A")

                basis_list = decision.get("decision_basis", [])
                if basis_list:
                    basis_text = "\n".join(
                        f"  - [{b.get('lane', '?')}] {b.get('why_it_matters', '')}"
                        for b in basis_list
                    )

                monitoring_hint = parsed.get("monitoring_hint", {})
        except yaml.YAMLError:
            pass

    # テクニカル指標の要約（今日の archive から取得）
    technical_text = ""
    _src = today_archive if today_archive else archivelog
    technical = _src.get("technical")
    if technical and isinstance(technical, dict) and "timeframes" in technical:
        tech_lines = []
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
                if trend:
                    parts = [f"{k}={v}" for k, v in trend.items()]
                    for rk in ("sma_20", "sma_50", "sma_200", "adx"):
                        rv = raw.get(rk)
                        if rv is not None:
                            parts.append(f"{rk}={rv}")
                    tech_lines.append(f"  トレンド: {', '.join(parts)}")
                if momentum:
                    parts = [f"{k}={v}" for k, v in momentum.items()]
                    for rk in ("rsi_14", "stoch_k"):
                        rv = raw.get(rk)
                        if rv is not None:
                            parts.append(f"{rk}={round(rv, 1) if isinstance(rv, float) else rv}")
                    macd_raw = raw.get("macd")
                    if isinstance(macd_raw, dict) and macd_raw.get("macd") is not None:
                        parts.append(f"macd={round(macd_raw['macd'], 2)}")
                    tech_lines.append(f"  モメンタム: {', '.join(parts)}")
                if volatility:
                    parts = [f"{k}={v}" for k, v in volatility.items()]
                    for rk in ("atr", "natr"):
                        rv = raw.get(rk)
                        if rv is not None:
                            parts.append(f"{rk}={round(rv, 2) if isinstance(rv, float) else rv}")
                    tech_lines.append(f"  ボラティリティ: {', '.join(parts)}")
                if volume_d:
                    parts = [f"{k}={v}" for k, v in volume_d.items()]
                    for rk in ("mfi",):
                        rv = raw.get(rk)
                        if rv is not None:
                            parts.append(f"{rk}={round(rv, 1) if isinstance(rv, float) else rv}")
                    tech_lines.append(f"  出来高: {', '.join(parts)}")
        if tech_lines:
            technical_text = "【テクニカル指標】\n" + "\n".join(tech_lines) + "\n\n"

    # 重要指標の要約（今日の archive から取得）
    indicators_text = ""
    ii = _src.get("important_indicators")
    if ii and isinstance(ii, dict):
        ii_lines = []
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
                ii_lines.append(f"  市場環境: {', '.join(items)}")

        event = ii.get("event_risk", {})
        if event.get("nearest_event"):
            ev_text = f"  イベントリスク: {event['nearest_event']} まで {event.get('days_to_event', '?')}日"
            if event.get("implied_move_pct"):
                ev_text += f"（期待変動 {event['implied_move_pct']}%）"
            ii_lines.append(ev_text)

        earnings = ii.get("earnings", {})
        if earnings.get("eps_actual") is not None:
            ii_lines.append(
                f"  直近決算: EPS 予想{earnings.get('eps_estimate', '?')} → 実績{earnings['eps_actual']}"
                f"（サプライズ {earnings.get('eps_surprise_pct', '?')}%）"
            )
        if earnings.get("revenue_actual") is not None:
            rev_text = f"  売上: 実績{earnings['revenue_actual']:,.0f}"
            if earnings.get("revenue_estimate") is not None:
                rev_text += f" vs 予想{earnings['revenue_estimate']:,.0f}"
            if earnings.get("revenue_surprise_pct") is not None:
                rev_text += f"（サプライズ {earnings['revenue_surprise_pct']}%）"
            ii_lines.append(rev_text)

        rs = ii.get("relative_strength", {})
        if rs.get("vs_index_3m_pct") is not None:
            rs_text = f"  相対強度: 指数対比 {rs['vs_index_3m_pct']:+.1f}%"
            if rs.get("vs_sector_3m_pct") is not None:
                rs_text += f", セクター対比 {rs['vs_sector_3m_pct']:+.1f}%"
            ii_lines.append(rs_text)

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
            ii_lines.append(f"  出来高: {', '.join(vol_parts)}")

        if ii_lines:
            indicators_text = "【重要指標（API取得データ）】\n" + "\n".join(ii_lines) + "\n\n"

    return (
        f"以下の投資プランが現在も有効かチェックしてください。\n"
        f"\n"
        f"【銘柄】{ticker}\n"
        f"【判定】{verdict}\n"
        f"【confidence】{confidence}\n"
        f"【投資期間】{horizon}\n"
        f"【プラン時点の株価】{plan_price}\n"
        f"\n"
        f"【判定根拠】\n{basis_text}\n"
        f"\n"
        f"【モニタリングヒント】\n"
        f"  強度: {monitoring_hint.get('intensity', 'N/A')}\n"
        f"  理由: {monitoring_hint.get('reason', 'N/A')}\n"
        f"\n"
        f"{technical_text}"
        f"{indicators_text}"
        f"上記プランの前提が現在の市場状況でまだ有効か、"
        f"WebSearch で最新の株価・ニュースを調査して判定してください。\n"
        f"テクニカル指標と重要指標は API から取得した定量データです。"
        f"これらを参考にしつつ、WebSearch で最新情報を補完してください。\n"
    )


def parse_monitor_result(agent_output: str) -> dict | None:
    """monitor-checker エージェントの出力から monitor_result を抽出する。"""
    blocks = re.findall(r"```yaml\n(.*?)```", agent_output, re.DOTALL)
    for block in reversed(blocks):
        try:
            data = yaml.safe_load(block.strip())
        except yaml.YAMLError:
            continue

        if not isinstance(data, dict):
            continue

        monitor_result = data.get("monitor_result", data)
        if isinstance(monitor_result, dict) and "result" in monitor_result:
            return monitor_result

    m = re.search(r"result:\s*(OK|NG|ERROR)", agent_output)
    if m:
        return {"result": m.group(1), "summary": "パース不完全（フォールバック）"}

    return None


def _extract_plan_price(newplan_full: str) -> float | None:
    """newplan_full（YAML テキスト）からプラン時の current_price を返す。"""
    if not newplan_full:
        return None
    try:
        parsed = yaml.safe_load(newplan_full)
        return parsed.get("data_checks", {}).get("current_price")
    except yaml.YAMLError:
        return None


MAX_MONITOR_RETRIES = 3


async def check_one_ticker(ticker: str, archivelog: dict | None = None, target_archive_id: str | None = None) -> dict | None:
    """1銘柄に対する監視チェックを実行する。エージェント呼び出し〜パースを最大3回リトライ。
    target_archive_id: Technical が作成した archive ID。指定時はそこに書き込む。"""
    now = datetime.now(JST)

    if not archivelog:
        archivelog = safe_db(get_latest_archivelog_with_newplan, ticker)
    if not archivelog:
        print(f"  [{ticker}] スキップ: プラン付きセッションが見つかりません")
        return None

    newplan_full = archivelog.get("newplan_full")
    if not newplan_full:
        print(f"  [{ticker}] スキップ: newplan_full が null")
        return None

    print(f"  [{ticker}] チェック開始")

    # 今日の archive から重要指標・テクニカルデータを取得してプロンプトに注入
    today_archive = None
    if target_archive_id:
        today_archive = safe_db(get_archivelog_by_id, target_archive_id)
    prompt = build_check_prompt(ticker, archivelog, today_archive=today_archive)
    agent_file = AGENTS_DIR / "monitor-checker.md"
    dbg = load_debug_config("monitor")

    monitor_data = None
    total_cost = 0.0
    last_error = ""

    for attempt in range(1, MAX_MONITOR_RETRIES + 1):
        if attempt > 1:
            print(f"  [{ticker}] リトライ {attempt}/{MAX_MONITOR_RETRIES}")

        try:
            result = await call_agent(
                prompt, file_path=str(agent_file), **{**dbg, "show_cost": True},
            )
        except Exception as e:
            last_error = f"エージェント呼び出し例外: {e}"
            print(f"  [{ticker}] {last_error}")
            continue

        cost = result.cost if result else None
        if cost:
            total_cost += cost
            print(f"  [{ticker}] コスト: ${cost:.4f}")

        if not result or not result.text:
            last_error = "エージェント応答なし"
            print(f"  [{ticker}] 警告: {last_error}")
            continue

        parsed = parse_monitor_result(result.text)
        if not parsed:
            last_error = "結果パース失敗"
            print(f"  [{ticker}] 警告: {last_error}")
            continue

        if parsed.get("result") == "ERROR":
            last_error = parsed.get("summary", "result=ERROR")
            print(f"  [{ticker}] 警告: {last_error}")
            continue

        monitor_data = parsed
        break

    if monitor_data is None:
        print(f"  [{ticker}] リトライ上限到達 — ERROR として記録")
        plan_price = _extract_plan_price(newplan_full)
        error_record = {
            "checked_at": now.isoformat(),
            "result": "ERROR",
            "current_price": None,
            "plan_price": plan_price,
            "price_change_pct": None,
            "summary": f"リトライ {MAX_MONITOR_RETRIES} 回失敗: {last_error}",
            "risk_flags": [],
            "cost_usd": total_cost or None,
            "retries_exhausted": True,
            "error_detail": last_error,
        }
        if target_archive_id:
            safe_db(update_archivelog, target_archive_id,
                    MotivationID=0, active=False,
                    monitor=error_record, status="error")
        else:
            new_record = safe_db(create_archivelog, ticker,
                                 archivelog.get("mode", "buy"),
                                 archivelog.get("span", "中期"))
            if new_record:
                safe_db(update_archivelog, new_record["id"],
                        MotivationID=0, active=False,
                        monitor=error_record, status="error")
        return error_record

    plan_price = _extract_plan_price(newplan_full)

    tech_price = None
    if target_archive_id:
        try:
            _tech_rec = get_client().from_("archive").select("technical").eq("id", target_archive_id).limit(1).execute()
            if _tech_rec.data:
                _tech = _tech_rec.data[0].get("technical") or {}
                tech_price = _tech.get("latest_price") if isinstance(_tech, dict) else None
        except Exception:
            pass
    current_price = tech_price if tech_price is not None else monitor_data.get("current_price")

    price_change_pct = None
    if plan_price and current_price and plan_price > 0:
        price_change_pct = round((current_price - plan_price) / plan_price * 100, 2)

    monitor_record = {
        "checked_at": now.isoformat(),
        "result": monitor_data.get("result", "ERROR"),
        "current_price": current_price,
        "plan_price": plan_price,
        "price_change_pct": price_change_pct,
        "summary": monitor_data.get("summary", ""),
        "risk_flags": monitor_data.get("risk_flags", []),
        "cost_usd": total_cost or None,
    }

    if monitor_data.get("result") == "NG":
        monitor_record["ng_reason"] = monitor_data.get("ng_reason", "")
        if target_archive_id:
            safe_db(update_archivelog, target_archive_id,
                    MotivationID=1,
                    motivation_full=monitor_data.get("ng_reason", ""),
                    active=True, monitor=monitor_record)
        else:
            safe_db(update_archivelog, archivelog["id"], active=False)
            new_record = safe_db(create_archivelog, ticker,
                                 archivelog.get("mode", "buy"),
                                 archivelog.get("span", "中期"))
            if new_record:
                safe_db(update_archivelog, new_record["id"],
                        MotivationID=1,
                        motivation_full=monitor_data.get("ng_reason", ""),
                        active=True, monitor=monitor_record)
    else:
        if target_archive_id:
            safe_db(update_archivelog, target_archive_id,
                    MotivationID=0, active=False,
                    monitor=monitor_record, status="completed")
        else:
            new_record = safe_db(create_archivelog, ticker,
                                 archivelog.get("mode", "buy"),
                                 archivelog.get("span", "中期"))
            if new_record:
                safe_db(update_archivelog, new_record["id"],
                        MotivationID=0, active=False,
                        monitor=monitor_record, status="completed")

    status = monitor_record["result"]
    print(f"  [{ticker}] 結果: {status}")
    if status == "NG":
        print(f"  [{ticker}] 理由: {monitor_record.get('ng_reason', '')}")

    return monitor_record


def _run_important_indicators(ticker: str, archive_id: str):
    """重要指標ブロックを subprocess で呼び出す。"""
    ii_dir = Path(__file__).resolve().parent.parent.parent / "ImportantIndicators"
    venv_python = ii_dir / ".venv" / "Scripts" / "python.exe"
    if not venv_python.exists():
        venv_python = ii_dir / ".venv" / "bin" / "python"
    script = ii_dir / "src" / "main.py"
    if not script.exists():
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


async def run_single(ticker: str):
    """1銘柄の Monitor チェックを実行する。"""
    ticker = ticker.upper()

    target_archive_id = None
    try:
        resp = (
            get_client()
            .from_("archive")
            .select("id")
            .eq("ticker", ticker)
            .not_.is_("technical", "null")
            .is_("monitor", "null")
            .order("created_at", desc=True)
            .limit(1)
            .execute()
        )
        if resp.data:
            target_archive_id = resp.data[0]["id"]
    except Exception:
        pass

    if target_archive_id:
        _run_important_indicators(ticker, target_archive_id)

    result = await check_one_ticker(ticker, archivelog=None, target_archive_id=target_archive_id)

    if result and result.get("result") != "ERROR":
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    _ticker = None
    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--ticker" and i + 1 < len(args):
            _ticker = args[i + 1]
            i += 2
        else:
            _ticker = args[i]
            i += 1

    if not _ticker:
        print("エラー: --ticker は必須です。バッチ実行は monitor_batch.py を使用してください。")
        sys.exit(1)

    asyncio.run(run_single(_ticker))
