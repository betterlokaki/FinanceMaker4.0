"""EMA+slope regime strategy for Mag7 long/short backtests."""
from __future__ import annotations

import numpy as np
import pandas as pd
from backtesting import Strategy


class Mag7EmaSlopeRegimeStrategy(Strategy):
    """Trend-regime strategy with reversible long/short positioning.

    Rules:
    - Go long when price is above EMA and EMA slope is positive.
    - Go short when price is below EMA and EMA slope is negative.
    - Hold until regime changes.
    """

    trade_direction: str = "Both"
    notional_per_trade: float = 30_000.0
    ema_period: int = 20
    slope_len: int = 36
    band: float = 0.0

    def init(self) -> None:
        close = pd.Series(np.asarray(self.data.Close, dtype=float))
        ema = close.ewm(span=max(2, self.ema_period), adjust=False, min_periods=1).mean()
        slope = ema - ema.shift(max(1, self.slope_len))

        self._ema = ema.to_numpy(dtype=float)
        self._slope = slope.fillna(0.0).to_numpy(dtype=float)

    def next(self) -> None:
        bar = len(self.data) - 1
        warmup = self.ema_period + self.slope_len + 2
        if bar < warmup:
            return

        price = float(self.data.Close[-1])
        if not np.isfinite(price) or price <= 0:
            return

        ema_value = float(self._ema[bar])
        slope_value = float(self._slope[bar])
        band = max(0.0, float(self.band))

        long_signal = (price > (ema_value * (1.0 + band))) and (slope_value > 0.0)
        short_signal = (price < (ema_value * (1.0 - band))) and (slope_value < 0.0)

        can_long = self.trade_direction in ("Both", "Long Only")
        can_short = self.trade_direction in ("Both", "Short Only")

        shares = int(self.notional_per_trade / price)
        if shares < 1:
            return

        if long_signal and can_long:
            if self.position.is_short:
                self.position.close()
            if not self.position:
                self.buy(size=shares)
            return

        if short_signal and can_short:
            if self.position.is_long:
                self.position.close()
            if not self.position:
                self.sell(size=shares)
