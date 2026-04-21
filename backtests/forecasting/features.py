"""Feature engineering for next-3-candle OHLC forecasting."""
from __future__ import annotations

import numpy as np
import pandas as pd

try:
    import pandas_ta as ta
except Exception as exc:  # pragma: no cover - runtime dependency gate
    raise RuntimeError("Missing dependency `pandas_ta`.") from exc


def _series_or_nan(series: pd.Series | None, index: pd.Index) -> pd.Series:
    if series is None:
        return pd.Series(np.nan, index=index, dtype=float)
    out = pd.to_numeric(series, errors="coerce")
    out.index = index
    return out.astype(float)


def _first_col(df: pd.DataFrame | None, prefix: str, index: pd.Index) -> pd.Series:
    if df is None or df.empty:
        return pd.Series(np.nan, index=index, dtype=float)
    cols = [c for c in df.columns if str(c).startswith(prefix)]
    if not cols:
        return pd.Series(np.nan, index=index, dtype=float)
    out = pd.to_numeric(df[cols[0]], errors="coerce")
    out.index = index
    return out.astype(float)


def compute_feature_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Compute leakage-safe technical feature set using current/past bars only."""
    if df.empty:
        return pd.DataFrame(index=df.index)

    required = ["Open", "High", "Low", "Close", "Volume"]
    if not all(col in df.columns for col in required):
        raise ValueError("Expected OHLCV columns: Open, High, Low, Close, Volume")

    frame = df.copy()
    idx = frame.index
    open_ = frame["Open"].astype(float)
    high = frame["High"].astype(float)
    low = frame["Low"].astype(float)
    close = frame["Close"].astype(float)
    volume = frame["Volume"].astype(float)

    feat = pd.DataFrame(index=idx)

    # Core price/volume features.
    feat["ret_1"] = close.pct_change(1)
    feat["log_ret_1"] = np.log(close).diff()
    feat["ret_3"] = close.pct_change(3)
    feat["ret_6"] = close.pct_change(6)
    feat["ret_12"] = close.pct_change(12)
    feat["range_pct"] = (high - low) / close.replace(0.0, np.nan)
    feat["body_pct"] = (close - open_) / open_.replace(0.0, np.nan)
    feat["upper_wick_pct"] = (high - np.maximum(open_, close)) / close.replace(0.0, np.nan)
    feat["lower_wick_pct"] = (np.minimum(open_, close) - low) / close.replace(0.0, np.nan)
    feat["volume_z20"] = (volume - volume.rolling(20, min_periods=20).mean()) / (
        volume.rolling(20, min_periods=20).std().replace(0.0, np.nan)
    )

    # Requested baseline indicators.
    feat["ema_21"] = _series_or_nan(ta.ema(close=close, length=21), idx)
    feat["ema_89"] = _series_or_nan(ta.ema(close=close, length=89), idx)
    feat["ema_dist_21"] = (close - feat["ema_21"]) / close.replace(0.0, np.nan)
    feat["rsi_14"] = _series_or_nan(ta.rsi(close=close, length=14), idx)

    macd = ta.macd(close=close, fast=12, slow=26, signal=9)
    feat["macd_line"] = _first_col(macd, "MACD_", idx)
    feat["macd_signal"] = _first_col(macd, "MACDs_", idx)
    feat["macd_hist"] = _first_col(macd, "MACDh_", idx)

    # Additional indicators (>=12, sourced from pandas_ta catalog).
    adx = ta.adx(high=high, low=low, close=close, length=14)
    feat["adx_14"] = _first_col(adx, "ADX_", idx)
    feat["dmp_14"] = _first_col(adx, "DMP_", idx)
    feat["dmn_14"] = _first_col(adx, "DMN_", idx)
    feat["cci_20"] = _series_or_nan(ta.cci(high=high, low=low, close=close, length=20), idx)

    ppo = ta.ppo(close=close)
    feat["ppo"] = _first_col(ppo, "PPO_", idx)
    feat["ppo_signal"] = _first_col(ppo, "PPOs_", idx)
    feat["ppo_hist"] = _first_col(ppo, "PPOh_", idx)

    feat["roc_10"] = _series_or_nan(ta.roc(close=close, length=10), idx)

    stoch = ta.stoch(high=high, low=low, close=close)
    feat["stoch_k"] = _first_col(stoch, "STOCHk_", idx)
    feat["stoch_d"] = _first_col(stoch, "STOCHd_", idx)

    stochrsi = ta.stochrsi(close=close)
    feat["stochrsi_k"] = _first_col(stochrsi, "STOCHRSIk_", idx)
    feat["stochrsi_d"] = _first_col(stochrsi, "STOCHRSId_", idx)

    feat["willr_14"] = _series_or_nan(ta.willr(high=high, low=low, close=close, length=14), idx)
    feat["ultosc"] = _series_or_nan(ta.uo(high=high, low=low, close=close), idx)
    feat["atr_14"] = _series_or_nan(ta.atr(high=high, low=low, close=close, length=14), idx)
    feat["natr_14"] = _series_or_nan(ta.natr(high=high, low=low, close=close, length=14), idx)

    bbands = ta.bbands(close=close, length=20, std=2.0)
    feat["bb_low"] = _first_col(bbands, "BBL_", idx)
    feat["bb_mid"] = _first_col(bbands, "BBM_", idx)
    feat["bb_up"] = _first_col(bbands, "BBU_", idx)
    feat["bb_width"] = _first_col(bbands, "BBB_", idx)

    feat["obv"] = _series_or_nan(ta.obv(close=close, volume=volume), idx)
    feat["adosc"] = _series_or_nan(
        ta.adosc(high=high, low=low, close=close, volume=volume),
        idx,
    )
    feat["cmf_20"] = _series_or_nan(ta.cmf(high=high, low=low, close=close, volume=volume, length=20), idx)

    psar = ta.psar(high=high, low=low, close=close)
    feat["psar_long"] = _first_col(psar, "PSARl_", idx)
    feat["psar_short"] = _first_col(psar, "PSARs_", idx)

    supertrend = ta.supertrend(high=high, low=low, close=close, length=7, multiplier=3.0)
    feat["supertrend"] = _first_col(supertrend, "SUPERT_", idx)
    feat["supertrend_dir"] = _first_col(supertrend, "SUPERTd_", idx)

    # Lag features for high-signal columns.
    lag_cols = [
        "ret_1",
        "ret_3",
        "ret_6",
        "ema_dist_21",
        "rsi_14",
        "macd_hist",
        "adx_14",
        "atr_14",
        "bb_width",
        "obv",
        "cmf_20",
        "supertrend_dir",
    ]
    lag_frames: list[pd.DataFrame] = []
    for lag in (1, 2, 3, 6, 12):
        shifted = feat[lag_cols].shift(lag)
        shifted = shifted.rename(columns={col: f"{col}_lag{lag}" for col in lag_cols})
        lag_frames.append(shifted)
    if lag_frames:
        feat = pd.concat([feat] + lag_frames, axis=1)

    # Keep feature frame numeric and finite where possible.
    feat = feat.replace([np.inf, -np.inf], np.nan)
    return feat


def feature_columns_from_frame(frame: pd.DataFrame) -> list[str]:
    """Return stable sorted feature list for training/inference schema."""
    return sorted(str(col) for col in frame.columns)
