"""Target construction utilities for next-N OHLC return forecasting."""
from __future__ import annotations

import pandas as pd


def make_ohlc_return_targets(df: pd.DataFrame, horizon: int = 3) -> pd.DataFrame:
    """Build next-N OHLC targets as returns relative to close at time t."""
    if df.empty:
        return pd.DataFrame(index=df.index)
    if horizon < 1:
        raise ValueError("horizon must be >= 1")

    close = pd.to_numeric(df["Close"], errors="coerce")
    targets = pd.DataFrame(index=df.index)

    for step in range(1, horizon + 1):
        for price_col, short in (("Open", "o"), ("High", "h"), ("Low", "l"), ("Close", "c")):
            future = pd.to_numeric(df[price_col], errors="coerce").shift(-step)
            targets[f"target_{short}{step}"] = (future / close) - 1.0

    return targets


def target_columns_for_horizon(horizon: int) -> list[str]:
    """Return stable target column order for next-N OHLC returns."""
    if horizon < 1:
        raise ValueError("horizon must be >= 1")
    cols: list[str] = []
    for step in range(1, horizon + 1):
        cols.extend([f"target_o{step}", f"target_h{step}", f"target_l{step}", f"target_c{step}"])
    return cols
