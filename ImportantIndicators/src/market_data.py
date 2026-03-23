"""
市場全体データ取得モジュール

VIX、米10年債利回りを yfinance で、FRB政策金利・日銀政策金利を FRED API で取得する。
全銘柄共通のデータを1回取得して返す。
"""

import os
from datetime import datetime, timezone, timedelta
from pathlib import Path

import yfinance as yf
from curl_cffi import requests as cffi_requests
from dotenv import load_dotenv

from retry_util import retry_with_backoff

JST = timezone(timedelta(hours=9))
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

# FRED API の遅延インポート（APIキーがない環境でも market_data の他の関数は使えるように）
_fred = None


def _load_env():
    env_path = _PROJECT_ROOT / ".env.local"
    if env_path.exists():
        load_dotenv(env_path, override=False)


def _get_fred():
    global _fred
    if _fred is not None:
        return _fred
    _load_env()
    api_key = os.environ.get("FRED_API_KEY")
    if not api_key:
        print("  [警告] FRED_API_KEY が設定されていません。政策金利は取得できません。")
        return None
    from fredapi import Fred
    _fred = Fred(api_key=api_key)
    return _fred


def _create_session():
    return cffi_requests.Session(impersonate="chrome")


def fetch_market_data() -> dict:
    """
    市場全体データを取得して dict で返す。
    取得できなかった項目は None。
    """
    result = {
        "vix": None,
        "us_10y_yield": None,
        "ffr": None,
        "boj_rate": None,
    }

    # yfinance でバッチ取得（VIX + 米10年債利回り）
    session = _create_session()

    def _fetch_yf_market():
        return yf.download(
            ["^VIX", "^TNX"],
            period="5d",
            session=session,
            progress=False,
        )

    data = retry_with_backoff(_fetch_yf_market)
    if data is not None and not data.empty:
        if "Close" in data.columns:
            close = data["Close"]
            if "^VIX" in close.columns:
                vix_series = close["^VIX"].dropna()
                if not vix_series.empty:
                    result["vix"] = round(float(vix_series.iloc[-1]), 2)
            if "^TNX" in close.columns:
                tnx_series = close["^TNX"].dropna()
                if not tnx_series.empty:
                    result["us_10y_yield"] = round(float(tnx_series.iloc[-1]), 3)

    # FRED API で政策金利取得
    fred = _get_fred()
    if fred:
        # FRB 政策金利（日次実効レート）
        try:
            ffr_series = fred.get_series("DFF")
            if ffr_series is not None and not ffr_series.empty:
                result["ffr"] = round(float(ffr_series.iloc[-1]), 2)
        except Exception as e:
            print(f"  [警告] FRED FRB政策金利取得失敗: {e}")

        # 日銀政策金利
        try:
            boj_series = fred.get_series("IRSTCI01JPM156N")
            if boj_series is not None and not boj_series.empty:
                result["boj_rate"] = round(float(boj_series.iloc[-1]), 2)
        except Exception as e:
            print(f"  [警告] FRED 日銀政策金利取得失敗: {e}")

    return result


if __name__ == "__main__":
    import json
    data = fetch_market_data()
    print(json.dumps(data, indent=2, ensure_ascii=False))
