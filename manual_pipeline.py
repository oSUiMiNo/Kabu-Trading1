"""
手動銘柄入力パイプライン

ユーザーが銘柄を指定して分析を直接起動する。
Admission（対話 + watchlist + archive 作成）を行い、
main_pipeline.py --manual --ticker を呼び出す。

Usage:
    python manual_pipeline.py NVDA
    python manual_pipeline.py 3038 --market JP --display-name 神戸物産
"""
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_JST = timezone(timedelta(hours=9))
PROJECT_ROOT = Path(__file__).resolve().parent

sys.path.insert(0, str(PROJECT_ROOT / "shared"))
from supabase_client import (
    safe_db,
    list_watchlist,
    upsert_watchlist,
    create_archivelog,
    update_archivelog,
)
from discord_notifier import send_webhook
from notification_types import NotifyLabel, LABEL_COLOR


# ── ユーティリティ ───────────────────────────────────────

def _ask(prompt: str, choices: list[str] | None = None, default: str | None = None) -> str:
    if choices:
        options = "/".join(choices)
        if default:
            prompt_text = f"{prompt} [{options}] (デフォルト: {default}): "
        else:
            prompt_text = f"{prompt} [{options}]: "
    else:
        prompt_text = f"{prompt}: "

    while True:
        answer = input(prompt_text).strip()
        if not answer and default:
            return default
        if choices and answer not in choices:
            print(f"  '{answer}' は選択肢にありません。{'/'.join(choices)} から選んでください。")
            continue
        if answer:
            return answer
        print("  入力してください。")


def _confirm(prompt: str) -> bool:
    answer = input(f"{prompt} [y/n]: ").strip().lower()
    return answer in ("y", "yes")


# ── Admission + main_pipeline.py 呼び出し ──────────────

def run_manual_pipeline(
    ticker: str,
    market: str | None = None,
    display_name: str | None = None,
):
    ticker = ticker.upper()
    print(f"\n{'='*60}")
    print(f"=== 手動分析パイプライン: {ticker} ===")
    print(f"{'='*60}\n")

    # ── 対話: mode と span ──
    mode = _ask("投資モード", ["buy", "sell"], default="buy")
    span = _ask("投資期間", ["short", "mid", "long"], default="mid")

    # ── watchlist チェック ──
    wl = safe_db(list_watchlist, active_only=False) or []
    wl_tickers = {w["ticker"] for w in wl}
    is_new = ticker not in wl_tickers

    if not is_new:
        print(f"\n  {ticker} は既に watchlist に登録されています。")
        if not _confirm("  この銘柄の分析を続行しますか？"):
            print("  中止しました。")
            return

    if is_new:
        add_to_wl = _confirm(f"\n  {ticker} を watchlist に追加して今後の定期チェック対象にしますか？")
        if add_to_wl:
            wl_fields = {"market": (market or "US").upper()}
            if display_name:
                wl_fields["display_name"] = display_name
            safe_db(upsert_watchlist, ticker, **wl_fields)
            print(f"  watchlist に追加しました: {ticker} (market={wl_fields['market']})")
        else:
            print(f"  watchlist には追加せず、分析のみ実行します。")
            safe_db(upsert_watchlist, ticker, market=(market or "US").upper(), active=False)
            if display_name:
                safe_db(upsert_watchlist, ticker, display_name=display_name)
            print(f"  (Watch 連携のため watchlist に非アクティブで仮登録)")

    # ── Admission: archive 作成 + active/monitor 設定 ──
    print(f"\n{'='*60}")
    print(f"=== Admission ===")
    print(f"{'='*60}")

    record = safe_db(create_archivelog, ticker, mode, span)
    if not record:
        print(f"  archive レコード作成失敗")
        sys.exit(1)
    archive_id = record["id"]
    print(f"  archive 作成: {archive_id}")

    safe_db(
        update_archivelog,
        archive_id,
        active=True,
        MotivationID=2,
        motivation_full="手動入力による新規分析",
        monitor={
            "result": "NG",
            "checked_at": datetime.now(_JST).isoformat(),
            "summary": "手動入力による分析依頼",
            "ng_reason": "手動入力による新規分析",
            "current_price": None,
            "plan_price": None,
            "price_change_pct": None,
            "risk_flags": [],
            "manual_entry": True,
        },
    )
    print(f"  active=True, MotivationID=2 設定完了")

    # ── main_pipeline.py --manual に委譲 ──
    print(f"\n  main_pipeline.py --manual --ticker {ticker} を起動します。\n")
    cmd = [sys.executable, str(PROJECT_ROOT / "main_pipeline.py"), "--manual", "--ticker", ticker]
    if market:
        cmd.extend(["--market", market.upper()])
    result = subprocess.run(cmd, cwd=str(PROJECT_ROOT))
    sys.exit(result.returncode)


if __name__ == "__main__":
    from dotenv import load_dotenv
    env_path = PROJECT_ROOT / ".env.local"
    if env_path.exists():
        load_dotenv(env_path, override=False)

    _ticker = None
    _market = None
    _display_name = None

    args = sys.argv[1:]
    i = 0
    while i < len(args):
        if args[i] == "--market" and i + 1 < len(args):
            _market = args[i + 1].upper()
            i += 2
        elif args[i] == "--display-name" and i + 1 < len(args):
            _display_name = args[i + 1]
            i += 2
        elif args[i].startswith("--"):
            i += 1
        else:
            _ticker = args[i]
            i += 1

    if not _ticker:
        print("使い方: python manual_pipeline.py <TICKER> [--market US|JP] [--display-name 名前]")
        print("例: python manual_pipeline.py NVDA")
        print("例: python manual_pipeline.py 3038 --market JP --display-name 神戸物産")
        sys.exit(1)

    try:
        run_manual_pipeline(_ticker, _market, _display_name)
    except KeyboardInterrupt:
        print("\n中断されました。")
        sys.exit(1)
    except Exception as e:
        print(f"\n[FATAL] 手動パイプライン異常終了: {e}", flush=True)
        try:
            embed = {
                "title": "[ エラー ] 手動分析パイプライン異常終了",
                "description": str(e)[:2000],
                "color": LABEL_COLOR.get(NotifyLabel.ERROR, 0x808080),
            }
            send_webhook(embed)
        except Exception:
            pass
        sys.exit(1)
