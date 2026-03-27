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
    - Use live-like fixed stop-loss / take-profit exits.
    - Reverse only when regime changes.
    """

    trade_direction: str = "Both"
    notional_per_trade: float = 5000.0
    ema_period: int = 20
    slope_len: int = 24
    band: float = 0.016
    stop_loss_pct: float = 0.02
    take_profit_pct: float = 0.06
    use_limit_entry: bool = True
    close_on_neutral_signal: bool = False
    use_full_equity_sizing: bool = False
    full_equity_fraction: float = 1.0

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

        desired_side = 0
        if long_signal and can_long:
            desired_side = 1
        elif short_signal and can_short:
            desired_side = -1

        # Match live behavior: keep current exposure if already aligned, reverse only
        # when the opposite regime is confirmed.
        if self.position:
            if desired_side > 0 and self.position.is_short:
                self.position.close()
            elif desired_side < 0 and self.position.is_long:
                self.position.close()
            elif desired_side == 0 and bool(self.close_on_neutral_signal):
                self.position.close()
            return

        pending_orders = self._active_entry_orders()
        pending_side = self._infer_pending_side(pending_orders)
        if desired_side == 0:
            return
        if pending_side == desired_side:
            return
        for order in pending_orders:
            order.cancel()

        size: int | float
        if bool(self.use_full_equity_sizing):
            # backtesting.py interprets size >= 1 as absolute units (shares/contracts).
            # Keep sizing strictly below 1.0 to represent "all available liquidity".
            fraction = max(0.0, min(1.0, float(self.full_equity_fraction)))
            if fraction >= 1.0:
                fraction = 0.999999
            if fraction <= 0.0:
                return
            size = fraction
        else:
            shares = int(self.notional_per_trade / price)
            if shares < 1:
                return
            size = shares

        entry_price = float(price)
        stop_loss_pct = max(0.0, float(self.stop_loss_pct))
        take_profit_pct = max(0.0, float(self.take_profit_pct))

        if desired_side > 0:
            sl = entry_price * (1.0 - stop_loss_pct) if stop_loss_pct > 0 else None
            tp = entry_price * (1.0 + take_profit_pct) if take_profit_pct > 0 else None
            self._place_entry(
                is_long=True,
                size=size,
                entry_price=entry_price,
                stop_price=sl,
                take_profit_price=tp,
            )
            return

        sl = entry_price * (1.0 + stop_loss_pct) if stop_loss_pct > 0 else None
        tp = entry_price * (1.0 - take_profit_pct) if take_profit_pct > 0 else None
        self._place_entry(
            is_long=False,
            size=size,
            entry_price=entry_price,
            stop_price=sl,
            take_profit_price=tp,
        )

    def _place_entry(
        self,
        *,
        is_long: bool,
        size: int | float,
        entry_price: float,
        stop_price: float | None,
        take_profit_price: float | None,
    ) -> None:
        kwargs: dict[str, float | int | None] = {
            "size": size,
            "sl": stop_price,
            "tp": take_profit_price,
        }
        if self.use_limit_entry:
            kwargs["limit"] = entry_price
        if is_long:
            self.buy(**kwargs)
            return
        self.sell(**kwargs)

    def _active_entry_orders(self) -> list:
        orders = []
        for order in self.orders:
            # Keep contingent SL/TP orders intact; only manage entry orders.
            if bool(getattr(order, "is_contingent", False)):
                continue
            orders.append(order)
        return orders

    @staticmethod
    def _infer_pending_side(orders: list) -> int:
        buy_count = 0
        sell_count = 0
        for order in orders:
            size = float(getattr(order, "size", 0.0) or 0.0)
            if size > 0:
                buy_count += 1
            elif size < 0:
                sell_count += 1
        if buy_count and not sell_count:
            return 1
        if sell_count and not buy_count:
            return -1
        return 0
