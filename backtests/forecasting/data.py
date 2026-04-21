"""Data access and time-window helpers for forecasting pipeline."""
from __future__ import annotations

from datetime import timedelta
from typing import Sequence

import pandas as pd

from backtests.backtesting_py.isolated_backtest_engine import fetch_ohlcv_for_tickers_sync
from common.models.period import Period


def _as_utc_timestamp(value: pd.Timestamp) -> pd.Timestamp:
    ts = pd.Timestamp(value)
    if ts.tzinfo is None:
        return ts.tz_localize("UTC")
    return ts.tz_convert("UTC")


def normalize_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize DataFrame into canonical OHLCV schema with UTC-naive index."""
    if df.empty:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])

    frame = df.copy()
    rename_map = {
        "open": "Open",
        "high": "High",
        "low": "Low",
        "close": "Close",
        "volume": "Volume",
    }
    frame = frame.rename(columns=rename_map)
    required = ["Open", "High", "Low", "Close", "Volume"]
    if not all(col in frame.columns for col in required):
        return pd.DataFrame(columns=required)

    frame = frame[required]
    for col in required:
        frame[col] = pd.to_numeric(frame[col], errors="coerce")

    frame = frame.dropna(subset=required)
    if frame.empty:
        return pd.DataFrame(columns=required)

    index = pd.to_datetime(frame.index, utc=True, errors="coerce")
    frame = frame.loc[~index.isna()].copy()
    index = pd.to_datetime(frame.index, utc=True, errors="coerce")
    frame.index = index.tz_convert("UTC").tz_localize(None)
    frame = frame[~frame.index.duplicated(keep="last")]
    frame = frame.sort_index()
    return frame


def fetch_hourly_ohlcv(
    *,
    tickers: Sequence[str],
    start: pd.Timestamp,
    end: pd.Timestamp,
    warmup_days: int = 0,
) -> dict[str, pd.DataFrame]:
    """Fetch hourly OHLCV bars (including pre/post market) for provided tickers."""
    start_utc = _as_utc_timestamp(start)
    end_utc = _as_utc_timestamp(end)
    fetch_start = (start_utc - timedelta(days=max(0, int(warmup_days)))).to_pydatetime()
    fetch_end = (end_utc + timedelta(days=1)).to_pydatetime()

    raw = fetch_ohlcv_for_tickers_sync(
        tickers=list(tickers),
        start_time=fetch_start,
        end_time=fetch_end,
        period=Period.HOUR,
    )
    out: dict[str, pd.DataFrame] = {}
    for ticker, df in raw.items():
        normalized = normalize_ohlcv(df)
        if not normalized.empty:
            out[ticker.upper()] = normalized
    return out


def slice_time_window(
    df: pd.DataFrame,
    *,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DataFrame:
    """Slice inclusive time window using UTC-naive index."""
    if df.empty:
        return df
    start_naive = _as_utc_timestamp(start).tz_localize(None)
    end_naive = _as_utc_timestamp(end).tz_localize(None)
    return df.loc[(df.index >= start_naive) & (df.index <= end_naive)].copy()


def build_supervised_panel(
    *,
    data_by_ticker: dict[str, pd.DataFrame],
    horizon: int,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> tuple[pd.DataFrame, list[str], list[str]]:
    """Build aligned panel containing features + targets + ticker + time."""
    from backtests.forecasting.features import compute_feature_frame, feature_columns_from_frame
    from backtests.forecasting.targets import make_ohlc_return_targets, target_columns_for_horizon

    rows: list[pd.DataFrame] = []
    feature_cols: list[str] | None = None
    target_cols = target_columns_for_horizon(int(horizon))
    start_naive = _as_utc_timestamp(start).tz_localize(None)
    end_naive = _as_utc_timestamp(end).tz_localize(None)

    for ticker, bars in sorted(data_by_ticker.items()):
        if bars.empty:
            continue
        features = compute_feature_frame(bars)
        targets = make_ohlc_return_targets(bars, horizon=int(horizon))
        if feature_cols is None:
            feature_cols = feature_columns_from_frame(features)

        merged = pd.concat([features, targets], axis=1)
        merged["ticker"] = str(ticker).upper()
        merged["time"] = pd.DatetimeIndex(merged.index)
        merged = merged.loc[(merged.index >= start_naive) & (merged.index <= end_naive)]
        if merged.empty:
            continue
        rows.append(merged)

    if not rows:
        return pd.DataFrame(), (feature_cols or []), target_cols
    panel = pd.concat(rows, axis=0, ignore_index=True).sort_values(["ticker", "time"]).reset_index(drop=True)

    # Some optional indicators may be unavailable depending on installed backends.
    effective_features = [
        col
        for col in (feature_cols or [])
        if col in panel.columns and pd.to_numeric(panel[col], errors="coerce").notna().any()
    ]
    if effective_features:
        panel[effective_features] = panel.groupby("ticker", group_keys=False)[effective_features].ffill()
        panel[effective_features] = panel[effective_features].fillna(0.0)
    panel = panel.dropna(subset=list(target_cols))
    return panel, effective_features, target_cols
