"""
Market data via yfinance.
Fetches price, volume, and (approximate) short interest for a list of tickers.
All calls are cached for 5 minutes to avoid hammering yfinance.
"""
from __future__ import annotations

import logging
import time
from functools import lru_cache
from typing import Optional

import yfinance as yf

logger = logging.getLogger(__name__)

_CACHE: dict[str, tuple[float, dict]] = {}   # ticker → (timestamp, data)
CACHE_TTL = 300  # seconds


def _from_cache(ticker: str) -> Optional[dict]:
    if ticker in _CACHE:
        ts, data = _CACHE[ticker]
        if time.time() - ts < CACHE_TTL:
            return data
    return None


def _to_cache(ticker: str, data: dict):
    _CACHE[ticker] = (time.time(), data)


def get_market_data(ticker: str) -> dict:
    """
    Returns:
      price           float
      price_change_1d float  (%)
      volume          int
      avg_volume      int
      volume_ratio    float
      short_interest  float  (% of float short — may be 0 if unavailable)
      market_cap      int
    """
    cached = _from_cache(ticker)
    if cached:
        return cached

    try:
        t = yf.Ticker(ticker)
        info = t.info

        price = info.get("currentPrice") or info.get("regularMarketPrice") or 0.0
        prev_close = info.get("previousClose") or info.get("regularMarketPreviousClose") or price
        price_change_1d = ((price - prev_close) / prev_close * 100) if prev_close else 0.0

        volume = info.get("regularMarketVolume") or info.get("volume") or 0
        avg_volume = info.get("averageDailyVolume10Day") or info.get("averageVolume") or 1
        volume_ratio = volume / max(avg_volume, 1)

        # Short interest: yfinance returns sharesShort and floatShares
        shares_short = info.get("sharesShort") or 0
        float_shares = info.get("floatShares") or 1
        short_interest = (shares_short / float_shares * 100) if float_shares else 0.0

        data = {
            "price": round(price, 2),
            "price_change_1d": round(price_change_1d, 2),
            "volume": int(volume),
            "avg_volume": int(avg_volume),
            "volume_ratio": round(volume_ratio, 2),
            "short_interest": round(short_interest, 2),
            "market_cap": info.get("marketCap") or 0,
        }

        _to_cache(ticker, data)
        return data

    except Exception as e:
        logger.warning("yfinance error for %s: %s", ticker, e)
        return {
            "price": 0.0,
            "price_change_1d": 0.0,
            "volume": 0,
            "avg_volume": 1,
            "volume_ratio": 0.0,
            "short_interest": 0.0,
            "market_cap": 0,
        }


def get_batch_market_data(tickers: list[str]) -> dict[str, dict]:
    """Fetch multiple tickers. Uses yfinance batch download for efficiency."""
    # Check cache first
    result = {}
    missing = []
    for t in tickers:
        cached = _from_cache(t)
        if cached:
            result[t] = cached
        else:
            missing.append(t)

    if not missing:
        return result

    try:
        # Batch download 1-day history for volume/price
        raw = yf.download(
            missing,
            period="5d",
            interval="1d",
            group_by="ticker",
            auto_adjust=True,
            progress=False,
        )

        for ticker in missing:
            try:
                if len(missing) == 1:
                    df = raw
                else:
                    df = raw[ticker]

                if df.empty:
                    result[ticker] = get_market_data(ticker)  # fallback to individual
                    continue

                latest = df.iloc[-1]
                prev = df.iloc[-2] if len(df) > 1 else df.iloc[-1]

                price = float(latest["Close"])
                prev_close = float(prev["Close"])
                price_change_1d = (price - prev_close) / prev_close * 100 if prev_close else 0

                volume = int(latest["Volume"])
                avg_volume = int(df["Volume"].mean())
                volume_ratio = volume / max(avg_volume, 1)

                # Short interest still needs individual call — no batch endpoint
                si = 0.0
                try:
                    info = yf.Ticker(ticker).info
                    shares_short = info.get("sharesShort") or 0
                    float_shares = info.get("floatShares") or 1
                    si = shares_short / float_shares * 100
                except Exception:
                    pass

                data = {
                    "price": round(price, 2),
                    "price_change_1d": round(price_change_1d, 2),
                    "volume": volume,
                    "avg_volume": avg_volume,
                    "volume_ratio": round(volume_ratio, 2),
                    "short_interest": round(si, 2),
                    "market_cap": 0,
                }
                _to_cache(ticker, data)
                result[ticker] = data

            except Exception as e:
                logger.warning("Batch parse error for %s: %s", ticker, e)
                result[ticker] = get_market_data(ticker)

    except Exception as e:
        logger.warning("Batch download failed: %s — falling back to individual", e)
        for t in missing:
            result[t] = get_market_data(t)

    return result
