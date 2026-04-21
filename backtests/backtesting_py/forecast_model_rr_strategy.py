"""Forecast-model strategy (backtesting.py Strategy) using thresholded 3-candle high/low logic."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
from backtesting import Strategy

from backtests.forecasting.features import compute_feature_frame
from backtests.forecasting.io import load_model_bundle
from backtests.forecasting.models import predict_with_bundle


@lru_cache(maxsize=16)
def _load_bundle_cached(model_dir: str):
    return load_model_bundle(model_dir)


_PREDICTION_CACHE: dict[tuple, tuple[np.ndarray, np.ndarray]] = {}


def _frame_fingerprint(df: pd.DataFrame) -> tuple[int, int, int, float]:
    if df.empty:
        return (0, 0, 0, 0.0)
    idx = pd.DatetimeIndex(df.index)
    first_ns = int(pd.Timestamp(idx[0]).value)
    last_ns = int(pd.Timestamp(idx[-1]).value)
    close_sum = float(np.nansum(pd.to_numeric(df["Close"], errors="coerce").to_numpy(dtype=float)))
    return (len(df), first_ns, last_ns, round(close_sum, 6))


class ForecastModelRRStrategy(Strategy):
    """Model-driven strategy:

    - Build features from the last `lookback_bars` candles (+ indicators).
    - Predict next 3-candle path using trained forecast model.
    - Long entry when predicted high >= +target move and predicted low >= -max adverse.
    - Short entry when predicted low <= -target move and predicted high <= +max adverse.
    - Execute with fixed 1:R risk-reward exits (default R=3).
    """

    model_dir: str = ""
    ticker: str = ""
    trade_direction: str = "Both"

    lookback_bars: int = 70
    prediction_target_pct: float = 0.03
    max_adverse_pct: float = 0.01

    stop_loss_pct: float = 0.01
    risk_reward_ratio: float = 3.0
    max_hold_bars: int = 3

    use_full_equity_sizing: bool = True
    full_equity_fraction: float = 1.0
    notional_per_trade: float = 10_000.0

    def init(self) -> None:
        model_dir = str(self.model_dir or "").strip()
        if not model_dir:
            raise ValueError("`model_dir` is required.")

        resolved_model_dir = str(Path(model_dir).expanduser().resolve())
        if not Path(resolved_model_dir).exists():
            raise ValueError(f"model_dir does not exist: {resolved_model_dir}")

        self._bundle = _load_bundle_cached(resolved_model_dir)
        self._ticker = str(self.ticker or "").strip().upper()
        if not self._ticker:
            raise ValueError("`ticker` is required.")

        self._lookback = max(20, int(self.lookback_bars))
        self._target_move = max(0.0001, float(self.prediction_target_pct))
        self._max_adverse = max(0.0001, float(self.max_adverse_pct))

        frame = pd.DataFrame(
            {
                "Open": np.asarray(self.data.Open, dtype=float),
                "High": np.asarray(self.data.High, dtype=float),
                "Low": np.asarray(self.data.Low, dtype=float),
                "Close": np.asarray(self.data.Close, dtype=float),
                "Volume": np.asarray(self.data.Volume, dtype=float),
            },
            index=pd.DatetimeIndex(self.data.index),
        )
        cache_key = (resolved_model_dir, self._ticker, self._lookback, *_frame_fingerprint(frame))

        high_cols = sorted(
            [col for col in self._bundle.target_columns if str(col).startswith("target_h")],
            key=lambda c: int(str(c).replace("target_h", "")),
        )
        low_cols = sorted(
            [col for col in self._bundle.target_columns if str(col).startswith("target_l")],
            key=lambda c: int(str(c).replace("target_l", "")),
        )
        if not high_cols or not low_cols:
            raise ValueError("Model bundle missing target_h* / target_l* columns.")

        cached = _PREDICTION_CACHE.get(cache_key)
        if cached is None:
            features = compute_feature_frame(frame)
            features = features.reindex(columns=self._bundle.feature_columns)
            features = features.replace([np.inf, -np.inf], np.nan).ffill().fillna(0.0)

            pred = predict_with_bundle(self._bundle, X=features, ticker=self._ticker)
            pred_df = pd.DataFrame(pred, index=features.index, columns=self._bundle.target_columns)
            pred_high = pd.to_numeric(pred_df[high_cols].max(axis=1), errors="coerce").to_numpy(dtype=float)
            pred_low = pd.to_numeric(pred_df[low_cols].min(axis=1), errors="coerce").to_numpy(dtype=float)

            _PREDICTION_CACHE[cache_key] = (pred_high, pred_low)
        else:
            pred_high, pred_low = cached

        self._pred_max_high = pred_high
        self._pred_min_low = pred_low
        self._entry_bar: int | None = None
        self._was_in_position = False

    def next(self) -> None:
        bar = len(self.data) - 1

        current_in_position = bool(self.position)
        if current_in_position and not self._was_in_position:
            self._entry_bar = bar
        elif (not current_in_position) and self._was_in_position:
            self._entry_bar = None
        self._was_in_position = current_in_position

        if current_in_position and self._entry_bar is not None:
            hold_len = bar - int(self._entry_bar) + 1
            if hold_len >= max(1, int(self.max_hold_bars)):
                self.position.close()
                return

        if bar < (self._lookback - 1):
            return
        if bar >= len(self._pred_max_high):
            return
        if self.position:
            return
        if self._active_entry_orders():
            return

        pred_high = float(self._pred_max_high[bar])
        pred_low = float(self._pred_min_low[bar])
        if not np.isfinite(pred_high) or not np.isfinite(pred_low):
            return

        can_long = str(self.trade_direction) in ("Both", "Long Only")
        can_short = str(self.trade_direction) in ("Both", "Short Only")

        long_signal = can_long and (pred_high >= self._target_move) and (pred_low >= -self._max_adverse)
        short_signal = can_short and (pred_low <= -self._target_move) and (pred_high <= self._max_adverse)

        if not long_signal and not short_signal:
            return

        side = "LONG"
        if long_signal and short_signal:
            long_margin = (pred_high - self._target_move) + max(0.0, pred_low + self._max_adverse)
            short_margin = ((-pred_low) - self._target_move) + max(0.0, self._max_adverse - pred_high)
            side = "LONG" if long_margin >= short_margin else "SHORT"
        elif short_signal:
            side = "SHORT"

        price = float(self.data.Close[-1])
        if not np.isfinite(price) or price <= 0.0:
            return

        stop_pct = max(0.0001, float(self.stop_loss_pct))
        rr = max(0.1, float(self.risk_reward_ratio))

        size: int | float
        if bool(self.use_full_equity_sizing):
            fraction = max(0.0, min(1.0, float(self.full_equity_fraction)))
            if fraction >= 1.0:
                fraction = 0.999999
            if fraction <= 0.0:
                return
            size = fraction
        else:
            shares = int(float(self.notional_per_trade) / price)
            if shares < 1:
                return
            size = shares

        if side == "LONG":
            sl = price * (1.0 - stop_pct)
            tp = price * (1.0 + (stop_pct * rr))
            self.buy(size=size, sl=sl, tp=tp)
            return

        sl = price * (1.0 + stop_pct)
        tp = price * (1.0 - (stop_pct * rr))
        self.sell(size=size, sl=sl, tp=tp)

    def _active_entry_orders(self) -> list:
        orders = []
        for order in self.orders:
            if bool(getattr(order, "is_contingent", False)):
                continue
            orders.append(order)
        return orders
