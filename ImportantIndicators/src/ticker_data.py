"""
個別銘柄データ取得モジュール

EPS実績vs予想、売上実績vs予想、Relative Strength、出来高異常を取得する。
"""

import time
import random

import yfinance as yf
from curl_cffi import requests as cffi_requests

from retry_util import retry_with_backoff

# セクター → ETF マッピング（米国株）
SECTOR_ETF_MAP = {
    "Technology": "XLK",
    "Healthcare": "XLV",
    "Financial Services": "XLF",
    "Financials": "XLF",
    "Consumer Cyclical": "XLY",
    "Consumer Discretionary": "XLY",
    "Consumer Defensive": "XLP",
    "Consumer Staples": "XLP",
    "Energy": "XLE",
    "Industrials": "XLI",
    "Basic Materials": "XLB",
    "Materials": "XLB",
    "Utilities": "XLU",
    "Real Estate": "XLRE",
    "Communication Services": "XLC",
}


def _create_session():
    return cffi_requests.Session(impersonate="chrome")


def _detect_market(ticker: str) -> str:
    """ティッカーから市場を推定。"""
    if ticker.endswith(".T") or ticker.replace(".T", "").isdigit():
        return "JP"
    return "US"


def fetch_earnings(ticker_obj: yf.Ticker) -> dict:
    """EPS実績vs予想、売上実績vs予想を取得。"""
    result = {
        "latest_quarter": None,
        "eps_actual": None,
        "eps_estimate": None,
        "eps_surprise_pct": None,
        "revenue_actual": None,
        "revenue_estimate": None,
        "revenue_surprise_pct": None,
    }

    # EPS
    try:
        hist = ticker_obj.get_earnings_history()
        if hist is not None and len(hist) > 0:
            # DataFrame の場合（yfinance 1.x）
            import pandas as pd
            if isinstance(hist, pd.DataFrame):
                row = hist.iloc[0]
                result["eps_actual"] = float(row["epsActual"]) if pd.notna(row.get("epsActual")) else None
                result["eps_estimate"] = float(row["epsEstimate"]) if pd.notna(row.get("epsEstimate")) else None
                surprise = row.get("surprisePercent")
                if pd.notna(surprise):
                    result["eps_surprise_pct"] = round(float(surprise) * 100, 2)
                result["latest_quarter"] = str(hist.index[0].date())
            else:
                # list of dict の場合（旧バージョン）
                latest = hist[0]
                result["eps_actual"] = latest.get("epsActual")
                result["eps_estimate"] = latest.get("epsEstimate")
                surprise = latest.get("surprisePercent")
                if surprise is not None:
                    result["eps_surprise_pct"] = round(float(surprise) * 100, 2)
                quarter = latest.get("quarter")
                if quarter:
                    result["latest_quarter"] = str(quarter)
    except Exception as e:
        print(f"    [警告] EPS取得失敗: {e}")

    # 売上
    try:
        rev_est = ticker_obj.revenue_estimate
        if rev_est is not None and not rev_est.empty:
            if "avg" in rev_est.index:
                result["revenue_estimate"] = float(rev_est.loc["avg"].iloc[0])
    except Exception as e:
        print(f"    [警告] 売上予想取得失敗: {e}")

    return result


def fetch_relative_strength(symbol: str, session) -> dict:
    """ベンチマーク指数・セクターETF に対する相対強度を計算。"""
    result = {
        "vs_index_3m_pct": None,
        "benchmark": None,
        "vs_sector_3m_pct": None,
        "sector": None,
        "sector_etf": None,
    }

    market = _detect_market(symbol)
    benchmark = "^GSPC" if market == "US" else "^N225"
    result["benchmark"] = benchmark

    try:
        # 銘柄 + ベンチマークの3ヶ月リターンを取得
        data = yf.download(
            [symbol, benchmark],
            period="3mo",
            session=session,
            progress=False,
        )
        if data is not None and not data.empty and "Close" in data.columns:
            close = data["Close"]
            for col in [symbol, benchmark]:
                if col in close.columns:
                    series = close[col].dropna()
                    if len(series) >= 2:
                        ret = (series.iloc[-1] / series.iloc[0] - 1) * 100
                        if col == symbol:
                            ticker_return = ret
                        else:
                            bench_return = ret

            if "ticker_return" in dir() and "bench_return" in dir():
                result["vs_index_3m_pct"] = round(float(ticker_return - bench_return), 2)
    except Exception as e:
        print(f"    [警告] ベンチマーク比較失敗: {e}")

    # セクターETF 比較（米国株のみ）
    if market == "US":
        try:
            ticker_obj = yf.Ticker(symbol, session=session)
            info = ticker_obj.info or {}
            sector = info.get("sector", "")
            result["sector"] = sector
            etf = SECTOR_ETF_MAP.get(sector)
            if etf:
                result["sector_etf"] = etf
                # 銘柄とセクターETF をまとめて取得
                pair_data = yf.download(
                    [symbol, etf],
                    period="3mo",
                    session=session,
                    progress=False,
                )
                if pair_data is not None and not pair_data.empty and "Close" in pair_data.columns:
                    pair_close = pair_data["Close"]
                    t_close = pair_close[symbol].dropna() if symbol in pair_close.columns else None
                    e_close = pair_close[etf].dropna() if etf in pair_close.columns else None
                    if t_close is not None and e_close is not None and len(t_close) >= 2 and len(e_close) >= 2:
                        t_ret = (float(t_close.iloc[-1]) / float(t_close.iloc[0]) - 1) * 100
                        e_ret = (float(e_close.iloc[-1]) / float(e_close.iloc[0]) - 1) * 100
                        result["vs_sector_3m_pct"] = round(float(t_ret - e_ret), 2)
        except Exception as e:
            print(f"    [警告] セクター比較失敗: {e}")

    return result


def fetch_volume_anomaly(ticker_obj: yf.Ticker, symbol: str) -> dict:
    """5日平均出来高比とドル出来高を計算。"""
    result = {
        "volume_ratio_5d": None,
        "dollar_volume": None,
        "currency": "USD" if _detect_market(symbol) == "US" else "JPY",
    }

    try:
        hist = ticker_obj.history(period="1mo")
        if hist is not None and not hist.empty and "Volume" in hist.columns:
            volumes = hist["Volume"].dropna()
            if len(volumes) >= 6:
                latest_vol = float(volumes.iloc[-1])
                avg_5d = float(volumes.iloc[-6:-1].mean())
                if avg_5d > 0:
                    result["volume_ratio_5d"] = round(latest_vol / avg_5d, 2)

            closes = hist["Close"].dropna()
            if not closes.empty and not volumes.empty:
                result["dollar_volume"] = round(
                    float(closes.iloc[-1]) * float(volumes.iloc[-1])
                )
    except Exception as e:
        print(f"    [警告] 出来高データ取得失敗: {e}")

    return result


def fetch_ticker_data(symbol: str) -> dict:
    """
    1銘柄分の全個別データを取得して返す。
    """
    session = _create_session()
    ticker_obj = yf.Ticker(symbol, session=session)

    earnings = fetch_earnings(ticker_obj)

    time.sleep(random.uniform(1, 2))

    relative_strength = fetch_relative_strength(symbol, session)

    time.sleep(random.uniform(1, 2))

    volume = fetch_volume_anomaly(ticker_obj, symbol)

    return {
        "earnings": earnings,
        "relative_strength": relative_strength,
        "volume": volume,
    }


if __name__ == "__main__":
    import json
    import sys

    symbol = sys.argv[1] if len(sys.argv) > 1 else "NVDA"
    print(f"Fetching data for {symbol}...")
    data = fetch_ticker_data(symbol)
    print(json.dumps(data, indent=2, ensure_ascii=False, default=str))
