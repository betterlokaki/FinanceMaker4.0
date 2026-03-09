"""Adapters that normalize data from YahooMarketProvider for backtesting.py."""
from __future__ import annotations

from datetime import datetime

import numpy as np
import pandas as pd

from common.models.period import Period
from pullers.market.abstracts.i_market_provider import IMarketProvider


def _empty_ohlcv() -> pd.DataFrame:
    return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])


def infer_tick_size(df: pd.DataFrame, fallback: float = 0.01) -> float:
    """Infer minimum price increment from OHLC data."""
    if df.empty:
        return fallback

    prices = pd.concat(
        [df["Open"], df["High"], df["Low"], df["Close"]],
        axis=0,
        ignore_index=True,
    ).dropna()
    if prices.empty:
        return fallback

    unique_prices = np.sort(prices.unique())
    if unique_prices.size < 2:
        return fallback

    diffs = np.diff(unique_prices)
    positive_diffs = diffs[diffs > 0]
    if positive_diffs.size == 0:
        return fallback

    tick = float(positive_diffs.min())
    return tick if np.isfinite(tick) and tick > 0 else fallback


async def fetch_ohlcv_from_yahoo_provider(
    provider: IMarketProvider,
    ticker: str,
    start_time: datetime,
    end_time: datetime,
    period: Period = Period.DAILY,
) -> pd.DataFrame:
    """Fetch and normalize OHLCV data to backtesting.py schema."""
    raw = await provider.get_prices(
        ticker=ticker,
        start_time=start_time,
        end_time=end_time,
        period=period,
    )
    if raw is None or raw.empty:
        return _empty_ohlcv()

    df = raw.copy()
    rename_map = {
        "open": "Open",
        "high": "High",
        "low": "Low",
        "close": "Close",
        "volume": "Volume",
    }
    df = df.rename(columns=rename_map)

    required = ["Open", "High", "Low", "Close", "Volume"]
    if not all(col in df.columns for col in required):
        return _empty_ohlcv()

    df = df[required]
    for col in required:
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna(subset=required)
    if df.empty:
        return _empty_ohlcv()

    index = pd.to_datetime(df.index, utc=True, errors="coerce")
    if isinstance(index, pd.DatetimeIndex):
        df.index = index.tz_convert(None)

    df = df[~df.index.isna()]
    df = df.sort_index()
    df = df[~df.index.duplicated(keep="last")]
    return df
