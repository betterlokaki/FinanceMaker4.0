"""RSI extreme-reversion strategy with adaptive monthly tuning and fixed exits."""
from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import pandas as pd
from backtesting import Strategy


def _compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Compute RSI using Wilder-style smoothing."""
    period = max(2, int(period))
    delta = close.diff()
    gains = delta.clip(lower=0.0)
    losses = (-delta).clip(lower=0.0)

    avg_gain = gains.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = losses.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()

    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    rsi = rsi.where(~((avg_loss == 0.0) & (avg_gain > 0.0)), 100.0)
    rsi = rsi.where(~((avg_gain == 0.0) & (avg_loss > 0.0)), 0.0)
    return rsi.fillna(50.0)


def _compute_atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> pd.Series:
    """Compute Wilder ATR."""
    period = max(2, int(period))
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            (high - low).abs(),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean().fillna(0.0)


def _compute_macd(
    close: pd.Series,
    fast_period: int = 12,
    slow_period: int = 26,
    signal_period: int = 9,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Compute MACD line, signal line, and histogram."""
    fast = close.ewm(span=max(2, int(fast_period)), adjust=False, min_periods=1).mean()
    slow = close.ewm(span=max(3, int(slow_period)), adjust=False, min_periods=1).mean()
    macd = fast - slow
    signal = macd.ewm(span=max(2, int(signal_period)), adjust=False, min_periods=1).mean()
    hist = macd - signal
    return macd, signal, hist


def _clamp(value: float, minimum: float, maximum: float) -> float:
    return max(minimum, min(maximum, float(value)))


class RsiExtremeRRStrategy(Strategy):
    """Adaptive RSI extreme strategy with asymmetric pair-fee accounting.

    Core behavior:
    - Detect RSI exhaustion reversals with trend + volatility confirmation.
    - Use fixed-R exits with ATR floor for stop distance.
    - Apply side-specific pair commissions in-strategy ($2.5 long, $5 short by default).
    - Adapt signal aggressiveness from month-over-month performance vs target.
    """

    trade_direction: str = "Both"
    notional_per_trade: float = 10_000.0
    rsi_period: int = 14
    rsi_oversold: float = 5.0
    rsi_overbought: float = 88.0
    stop_loss_pct: float = 0.00375
    risk_reward_ratio: float = 1.832
    use_limit_entry: bool = False
    use_full_equity_sizing: bool = True
    full_equity_fraction: float = 1.0
    fast_ema_period: int = 21
    slow_ema_period: int = 89
    atr_period: int = 14
    atr_stop_multiplier: float = 0.885
    min_atr_frac: float = 0.0005
    trend_filter_strength: float = 0.00338
    reentry_rsi_buffer: float = 0.5
    long_exit_rsi: float = 93.61
    short_exit_rsi: float = 9.67
    max_holding_bars: int = 160
    cooldown_bars: int = 3
    adaptive_model_enabled: bool = True
    target_monthly_return_pct: float = 15.0
    monthly_adapt_step: float = 0.32
    min_aggression: float = -0.5
    max_aggression: float = 4.0
    long_pair_commission: float = 2.5
    short_pair_commission: float = 5.0
    model_signal_enabled: bool = True
    model_learning_rate: float = 0.15
    model_weight_decay: float = 0.0005
    model_confidence_threshold: float = 0.0781
    model_warmup_bars: int = 156
    model_momentum_lookback: int = 3
    allow_model_only_entries: bool = False
    mean_reversion_entry_enabled: bool = False
    momentum_entry_enabled: bool = True
    momentum_entry_lookback: int = 8
    momentum_entry_threshold: float = 0.00479
    momentum_rsi_long_min: float = 57.53
    momentum_rsi_short_max: float = 20.98
    breakout_entry_enabled: bool = True
    breakout_lookback: int = 12
    breakout_buffer: float = 0.0
    min_long_confluence: int = 4
    min_short_confluence: int = 5
    short_trend_strength_multiplier: float = 2.918
    short_momentum_strength_multiplier: float = 3.384
    macd_fast_period: int = 12
    macd_slow_period: int = 26
    macd_signal_period: int = 9
    macd_hist_entry_threshold: float = 0.0
    oracle_mode_enabled: bool = False
    enforce_weekly_trade_cap: bool = True
    max_entry_pairs_per_week: int = 2
    activation_time_utc: str | None = None

    def init(self) -> None:
        if bool(self.oracle_mode_enabled):
            raise ValueError(
                "oracle_mode_enabled is disabled: it relies on future bars (lookahead) "
                "and cannot be used for real trading/backtests."
            )

        close = pd.Series(np.asarray(self.data.Close, dtype=float))
        high = pd.Series(np.asarray(self.data.High, dtype=float))
        low = pd.Series(np.asarray(self.data.Low, dtype=float))

        rsi = _compute_rsi(close=close, period=max(2, int(self.rsi_period)))
        ema_fast = close.ewm(
            span=max(2, int(self.fast_ema_period)),
            adjust=False,
            min_periods=1,
        ).mean()
        ema_slow = close.ewm(
            span=max(2, int(self.slow_ema_period)),
            adjust=False,
            min_periods=1,
        ).mean()
        atr = _compute_atr(
            high=high,
            low=low,
            close=close,
            period=max(2, int(self.atr_period)),
        )
        macd_line, macd_signal, macd_hist = _compute_macd(
            close=close,
            fast_period=max(2, int(self.macd_fast_period)),
            slow_period=max(3, int(self.macd_slow_period)),
            signal_period=max(2, int(self.macd_signal_period)),
        )
        momentum = close.pct_change(periods=max(1, int(self.momentum_entry_lookback))).fillna(0.0)
        rolling_high = (
            close.rolling(window=max(2, int(self.breakout_lookback)), min_periods=2).max().shift(1)
        )
        rolling_low = (
            close.rolling(window=max(2, int(self.breakout_lookback)), min_periods=2).min().shift(1)
        )

        self._rsi = rsi.to_numpy(dtype=float)
        self._ema_fast = ema_fast.to_numpy(dtype=float)
        self._ema_slow = ema_slow.to_numpy(dtype=float)
        self._atr = atr.to_numpy(dtype=float)
        self._macd_line = macd_line.to_numpy(dtype=float)
        self._macd_signal = macd_signal.to_numpy(dtype=float)
        self._macd_hist = macd_hist.to_numpy(dtype=float)
        self._momentum = momentum.to_numpy(dtype=float)
        self._rolling_high = rolling_high.to_numpy(dtype=float)
        self._rolling_low = rolling_low.to_numpy(dtype=float)
        self._aggression = 0.0
        self._month_key: tuple[int, int] | None = None
        self._month_anchor: pd.Timestamp | None = None
        self._month_start_equity = float(getattr(self._broker, "_cash", 0.0))
        self._monthly_returns: list[tuple[pd.Timestamp, float]] = []
        self._processed_closed_trade_count = 0
        self._last_exit_bar = -1_000_000
        self._weekly_entry_counts: dict[tuple[int, int], int] = {}
        self._model_weights = np.zeros(7, dtype=float)
        self._model_prev_features: np.ndarray | None = None
        self._model_prev_price: float | None = None
        self._model_updates = 0
        self._high = np.asarray(self.data.High, dtype=float)
        self._low = np.asarray(self.data.Low, dtype=float)
        self._close = np.asarray(self.data.Close, dtype=float)
        self._activation_time = self._parse_activation_time()

    @staticmethod
    def compute_exit_prices(
        *,
        entry_price: float,
        is_long: bool,
        stop_loss_pct: float,
        risk_reward_ratio: float,
    ) -> tuple[float, float]:
        """Return (stop_loss, take_profit) based on fixed percent risk and R:R."""
        stop_loss_pct = max(0.0, float(stop_loss_pct))
        risk_reward_ratio = max(0.0, float(risk_reward_ratio))
        risk_per_share = entry_price * stop_loss_pct

        if is_long:
            stop_price = entry_price - risk_per_share
            take_profit_price = entry_price + (risk_per_share * risk_reward_ratio)
            return stop_price, take_profit_price

        stop_price = entry_price + risk_per_share
        take_profit_price = entry_price - (risk_per_share * risk_reward_ratio)
        return stop_price, take_profit_price

    def next(self) -> None:
        bar = len(self.data) - 1
        warmup = max(
            5,
            int(self.rsi_period) + 2,
            int(self.atr_period) + 2,
        )
        if bar < warmup:
            return

        self._apply_asymmetric_pair_fees(bar=bar)
        if self._is_trading_active(bar=bar):
            self._update_monthly_adaptation(bar=bar)

        price = float(self.data.Close[-1])
        if not np.isfinite(price) or price <= 0.0:
            return

        rsi_value = float(self._rsi[bar])
        prev_rsi_value = float(self._rsi[bar - 1])
        ema_fast = float(self._ema_fast[bar])
        ema_slow = float(self._ema_slow[bar])
        atr_value = float(self._atr[bar])
        if not np.isfinite(rsi_value) or not np.isfinite(prev_rsi_value):
            return
        if not np.isfinite(ema_fast) or not np.isfinite(ema_slow) or ema_slow <= 0.0:
            return
        if not np.isfinite(atr_value) or atr_value <= 0.0:
            return

        oversold, overbought, stop_loss_pct, risk_reward_ratio, trend_band, reentry_buffer = (
            self._effective_runtime_params(price=price, atr_value=atr_value)
        )

        atr_frac = atr_value / price
        trend = (ema_fast / ema_slow) - 1.0
        deviation = (price / ema_fast) - 1.0
        prev_close = float(self.data.Close[-2]) if bar >= 1 else price
        prev_high = float(self.data.High[-2]) if bar >= 1 else float(self.data.High[-1])
        prev_low = float(self.data.Low[-2]) if bar >= 1 else float(self.data.Low[-1])
        bullish_pa = price > prev_close and float(self.data.Low[-1]) > prev_low and float(self.data.High[-1]) > prev_high
        bearish_pa = price < prev_close and float(self.data.High[-1]) < prev_high and float(self.data.Low[-1]) < prev_low
        slow_ema_prev = float(self._ema_slow[max(0, bar - 3)])
        slow_slope = (ema_slow / max(1e-9, slow_ema_prev)) - 1.0
        macd_line = float(self._macd_line[bar])
        macd_signal = float(self._macd_signal[bar])
        macd_hist = float(self._macd_hist[bar])
        momentum_value = float(self._momentum[bar])
        rolling_high = float(self._rolling_high[bar])
        rolling_low = float(self._rolling_low[bar])
        model_features = self._build_model_features(
            bar=bar,
            price=price,
            rsi_value=rsi_value,
            trend=trend,
            deviation=deviation,
            atr_frac=atr_frac,
            macd_hist=macd_hist,
        )
        model_score = self._update_model_and_score(
            bar=bar,
            price=price,
            features=model_features,
        )

        can_long = self.trade_direction in ("Both", "Long Only")
        can_short = self.trade_direction in ("Both", "Short Only")
        bull_regime = trend > trend_band
        bear_regime = trend < -trend_band
        short_overbought = _clamp(overbought + 3.0, 55.0, 99.0)
        short_reentry_buffer = _clamp(reentry_buffer * 1.35, 0.5, 20.0)

        long_reversal_signal = (
            prev_rsi_value <= oversold
            and rsi_value >= (oversold + reentry_buffer)
            and deviation < 0.0
            and trend > (-trend_band * 2.0)
        )
        short_reversal_signal = (
            prev_rsi_value >= short_overbought
            and rsi_value <= (short_overbought - short_reentry_buffer)
            and deviation > 0.0
            and trend < (trend_band * 1.5)
        )
        long_extreme_signal = (
            rsi_value < oversold
            and deviation <= -trend_band
            and (bull_regime or trend > -trend_band)
        )
        short_extreme_signal = (
            rsi_value > short_overbought
            and deviation >= trend_band
            and (bear_regime or trend < trend_band)
        )
        macd_threshold = float(self.macd_hist_entry_threshold)
        macd_long_ok = (
            np.isfinite(macd_line)
            and np.isfinite(macd_signal)
            and np.isfinite(macd_hist)
            and ((macd_line >= macd_signal) or (macd_hist >= -macd_threshold))
        )
        macd_short_ok = (
            np.isfinite(macd_line)
            and np.isfinite(macd_signal)
            and np.isfinite(macd_hist)
            and ((macd_line <= macd_signal) or (macd_hist <= macd_threshold))
        )
        long_signal = False
        short_signal = False
        if bool(self.mean_reversion_entry_enabled):
            long_signal = (
                can_long
                and atr_frac >= float(self.min_atr_frac)
                and (long_reversal_signal or long_extreme_signal)
                and macd_long_ok
            )
            short_signal = (
                can_short
                and atr_frac >= float(self.min_atr_frac)
                and (short_reversal_signal or short_extreme_signal)
                and macd_short_ok
            )
        breakout_up = (
            np.isfinite(rolling_high)
            and rolling_high > 0.0
            and price >= (rolling_high * (1.0 + max(0.0, float(self.breakout_buffer))))
        )
        breakout_dn = (
            np.isfinite(rolling_low)
            and rolling_low > 0.0
            and price <= (rolling_low * (1.0 - max(0.0, float(self.breakout_buffer))))
        )
        mom_thr = max(0.0, float(self.momentum_entry_threshold))
        short_trend_thr = trend_band * max(1.0, float(self.short_trend_strength_multiplier))
        short_mom_thr = mom_thr * max(1.0, float(self.short_momentum_strength_multiplier))

        long_confluence = int(trend > trend_band) + int(macd_long_ok) + int(
            np.isfinite(momentum_value) and momentum_value >= mom_thr
        ) + int(bullish_pa or breakout_up) + int(slow_slope > 0.0)
        short_confluence = int(trend < -short_trend_thr) + int(macd_short_ok) + int(
            np.isfinite(momentum_value) and momentum_value <= -short_mom_thr
        ) + int(bearish_pa or breakout_dn) + int(slow_slope < 0.0)

        long_signal = long_signal and (long_confluence >= max(1, int(self.min_long_confluence)))
        short_signal = short_signal and (short_confluence >= max(1, int(self.min_short_confluence)))
        if bool(self.momentum_entry_enabled):
            long_signal = long_signal or (
                can_long
                and atr_frac >= float(self.min_atr_frac)
                and np.isfinite(momentum_value)
                and momentum_value >= mom_thr
                and trend > trend_band
                and slow_slope > 0.0
                and rsi_value >= float(self.momentum_rsi_long_min)
                and macd_long_ok
                and (bullish_pa or breakout_up)
                and long_confluence >= max(1, int(self.min_long_confluence))
            )
            short_signal = short_signal or (
                can_short
                and atr_frac >= float(self.min_atr_frac)
                and np.isfinite(momentum_value)
                and momentum_value <= -short_mom_thr
                and trend < -short_trend_thr
                and slow_slope < 0.0
                and rsi_value <= float(self.momentum_rsi_short_max)
                and macd_short_ok
                and (bearish_pa or breakout_dn)
                and short_confluence >= max(1, int(self.min_short_confluence))
            )
        if bool(self.breakout_entry_enabled):
            long_signal = long_signal or (
                can_long
                and breakout_up
                and trend > trend_band
                and slow_slope > 0.0
                and np.isfinite(momentum_value)
                and momentum_value > 0.0
                and macd_long_ok
                and long_confluence >= max(1, int(self.min_long_confluence))
            )
            short_signal = short_signal or (
                can_short
                and breakout_dn
                and trend < -short_trend_thr
                and slow_slope < 0.0
                and np.isfinite(momentum_value)
                and momentum_value < 0.0
                and macd_short_ok
                and short_confluence >= max(1, int(self.min_short_confluence))
            )
        if bool(self.model_signal_enabled):
            conf = max(0.0, float(self.model_confidence_threshold))
            model_long_signal = model_score >= conf
            model_short_signal = model_score <= -(conf * 1.2)
            if bool(self.allow_model_only_entries):
                long_signal = long_signal or (
                    can_long and atr_frac >= float(self.min_atr_frac) and model_long_signal
                )
                short_signal = short_signal or (
                    can_short and atr_frac >= float(self.min_atr_frac) and model_short_signal
                )
            else:
                long_signal = long_signal and model_long_signal
                short_signal = short_signal and model_short_signal

        if not self._is_trading_active(bar=bar):
            return

        desired_side = 0
        if long_signal and not short_signal:
            desired_side = 1
        elif short_signal and not long_signal:
            desired_side = -1
        elif self._use_bootstrap_entry_fallback(bar=bar):
            if can_long and rsi_value < oversold:
                desired_side = 1
            elif can_short and rsi_value > short_overbought:
                desired_side = -1

        if self.position:
            if self._manage_open_position(bar=bar, price=price, rsi_value=rsi_value, atr_value=atr_value):
                return
            if desired_side > 0 and self.position.is_short:
                self.position.close()
            elif desired_side < 0 and self.position.is_long:
                self.position.close()
            return

        if desired_side == 0:
            return
        if bar - int(self._last_exit_bar) < max(0, int(self.cooldown_bars)):
            return

        pending_orders = self._active_entry_orders()
        pending_side = self._infer_pending_side(pending_orders)
        if pending_side == desired_side:
            return
        for order in pending_orders:
            order.cancel()

        if stop_loss_pct <= 0.0:
            return
        if risk_reward_ratio <= 0.0:
            return
        if not self._can_open_entry_this_week(bar=bar):
            return

        size: int | float
        if bool(self.use_full_equity_sizing):
            fraction = _clamp(
                float(self.full_equity_fraction) * (1.0 + (0.15 * self._effective_aggression())),
                0.02,
                0.999999,
            )
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

        is_long = desired_side > 0
        estimated_shares = self._estimate_shares(size=size, price=price)
        if estimated_shares < 1:
            return
        side_pair_fee = (
            max(0.0, float(self.long_pair_commission))
            if is_long
            else max(0.0, float(self.short_pair_commission))
        )
        min_rr_to_cover_fee = side_pair_fee / max(1e-9, estimated_shares * price * stop_loss_pct)
        risk_reward_ratio = max(risk_reward_ratio, min_rr_to_cover_fee + 0.25)

        stop_price, take_profit_price = self.compute_exit_prices(
            entry_price=price,
            is_long=is_long,
            stop_loss_pct=stop_loss_pct,
            risk_reward_ratio=risk_reward_ratio,
        )
        if take_profit_price <= 0.0:
            return

        self._register_entry_for_week(bar=bar)
        self._place_entry(
            is_long=is_long,
            size=size,
            entry_price=price,
            stop_price=stop_price,
            take_profit_price=take_profit_price,
            tag="LONG" if is_long else "SHORT",
        )

    def _place_entry(
        self,
        *,
        is_long: bool,
        size: int | float,
        entry_price: float,
        stop_price: float,
        take_profit_price: float,
        tag: str | None = None,
    ) -> None:
        kwargs: dict[str, float | int | str | None] = {
            "size": size,
            "sl": stop_price,
            "tp": take_profit_price,
            "tag": tag,
        }
        if bool(self.use_limit_entry):
            kwargs["limit"] = float(entry_price)
        if is_long:
            self.buy(**kwargs)
            return
        self.sell(**kwargs)

    def _active_entry_orders(self) -> list:
        orders = []
        for order in self.orders:
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

    def _can_open_entry_this_week(self, *, bar: int) -> bool:
        if not bool(self.enforce_weekly_trade_cap):
            return True
        limit = max(1, int(self.max_entry_pairs_per_week))
        week_key = self._resolve_week(bar=bar)
        if week_key is None:
            return True
        return int(self._weekly_entry_counts.get(week_key, 0)) < limit

    def _register_entry_for_week(self, *, bar: int) -> None:
        if not bool(self.enforce_weekly_trade_cap):
            return
        week_key = self._resolve_week(bar=bar)
        if week_key is None:
            return
        self._weekly_entry_counts[week_key] = int(self._weekly_entry_counts.get(week_key, 0)) + 1

    def _effective_aggression(self) -> float:
        if not bool(self.adaptive_model_enabled):
            return 0.0
        return _clamp(
            float(self._aggression),
            float(self.min_aggression),
            float(self.max_aggression),
        )

    def _effective_runtime_params(
        self,
        *,
        price: float,
        atr_value: float,
    ) -> tuple[float, float, float, float, float, float]:
        aggression = self._effective_aggression()
        oversold = _clamp(float(self.rsi_oversold) + (10.0 * aggression), 2.0, 48.0)
        overbought = _clamp(float(self.rsi_overbought) - (10.0 * aggression), 52.0, 98.0)
        stop_loss_floor = (atr_value / max(price, 1e-9)) * max(0.1, float(self.atr_stop_multiplier))
        stop_loss_pct = _clamp(max(float(self.stop_loss_pct), stop_loss_floor), 0.0002, 0.30)
        risk_reward_ratio = _clamp(
            float(self.risk_reward_ratio) * (1.0 + (0.35 * aggression)),
            0.50,
            20.0,
        )
        trend_band = _clamp(
            float(self.trend_filter_strength) * (1.0 - (0.35 * aggression)),
            0.0001,
            0.03,
        )
        reentry_buffer = _clamp(
            float(self.reentry_rsi_buffer) * (1.0 - (0.45 * aggression)),
            0.5,
            20.0,
        )
        return oversold, overbought, stop_loss_pct, risk_reward_ratio, trend_band, reentry_buffer

    def _manage_open_position(
        self,
        *,
        bar: int,
        price: float,
        rsi_value: float,
        atr_value: float,
    ) -> bool:
        open_trades: Iterable = list(self.trades)
        if not open_trades:
            return False

        trade = list(open_trades)[-1]
        entry_bar = int(getattr(trade, "entry_bar", bar))
        if max(0, int(self.max_holding_bars)) > 0:
            if (bar - entry_bar) >= max(1, int(self.max_holding_bars)):
                self.position.close()
                return True

        if bool(trade.is_long):
            if np.isfinite(rsi_value) and rsi_value >= float(self.long_exit_rsi):
                self.position.close()
                return True
            trailing_sl = price - (atr_value * 1.6)
            if np.isfinite(trailing_sl):
                if trade.sl is None or trailing_sl > float(trade.sl):
                    trade.sl = trailing_sl
        else:
            if np.isfinite(rsi_value) and rsi_value <= float(self.short_exit_rsi):
                self.position.close()
                return True
            trailing_sl = price + (atr_value * 1.6)
            if np.isfinite(trailing_sl):
                if trade.sl is None or trailing_sl < float(trade.sl):
                    trade.sl = trailing_sl
        return False

    def _apply_asymmetric_pair_fees(self, *, bar: int) -> None:
        closed_count = len(self.closed_trades)
        if closed_count <= int(self._processed_closed_trade_count):
            return

        broker = getattr(self, "_broker", None)
        if broker is None:
            self._processed_closed_trade_count = closed_count
            return

        for trade in self.closed_trades[int(self._processed_closed_trade_count) :]:
            desired_pair_fee = (
                max(0.0, float(self.long_pair_commission))
                if bool(trade.is_long)
                else max(0.0, float(self.short_pair_commission))
            )
            current_fee = float(getattr(trade, "_commissions", 0.0) or 0.0)
            fee_delta = desired_pair_fee - current_fee
            if abs(fee_delta) > 1e-12:
                broker._cash -= fee_delta
                trade._commissions = current_fee + fee_delta

            exit_bar = getattr(trade, "exit_bar", None)
            if exit_bar is not None:
                self._last_exit_bar = max(int(self._last_exit_bar), int(exit_bar))

        self._processed_closed_trade_count = closed_count
        if hasattr(broker, "_equity") and 0 <= bar < len(broker._equity):
            broker._equity[bar] = broker.equity

    def _update_monthly_adaptation(self, *, bar: int) -> None:
        if not bool(self.adaptive_model_enabled):
            return

        month_key, anchor = self._resolve_month(bar=bar)
        if month_key is None or anchor is None:
            return

        equity = self._current_equity()
        if self._month_key is None or self._month_anchor is None:
            self._month_key = month_key
            self._month_anchor = anchor
            self._month_start_equity = equity
            return

        if month_key == self._month_key:
            return

        start_equity = max(1e-9, float(self._month_start_equity))
        month_return_pct = ((equity / start_equity) - 1.0) * 100.0
        self._monthly_returns.append((self._month_anchor, float(month_return_pct)))
        self._adapt_aggression(month_return_pct=month_return_pct)

        self._month_key = month_key
        self._month_anchor = anchor
        self._month_start_equity = equity

    def _adapt_aggression(self, *, month_return_pct: float) -> None:
        target = max(0.0, float(self.target_monthly_return_pct))
        step = max(0.0, float(self.monthly_adapt_step))
        if target <= 0.0 or step <= 0.0:
            return

        if month_return_pct < target:
            gap_ratio = (target - month_return_pct) / max(target, 1.0)
            self._aggression += step * (1.0 + gap_ratio)
        else:
            outperformance = (month_return_pct - target) / max(target, 1.0)
            self._aggression -= step * 0.55 * (1.0 + outperformance)

        self._aggression = _clamp(
            float(self._aggression),
            float(self.min_aggression),
            float(self.max_aggression),
        )

    def _resolve_month(self, *, bar: int) -> tuple[tuple[int, int] | None, pd.Timestamp | None]:
        try:
            ts = pd.Timestamp(self.data.index[bar])
        except Exception:
            return None, None
        if pd.isna(ts):
            return None, None
        month_key = (int(ts.year), int(ts.month))
        anchor = pd.Timestamp(year=month_key[0], month=month_key[1], day=1)
        return month_key, anchor

    def _resolve_week(self, *, bar: int) -> tuple[int, int] | None:
        try:
            ts = pd.Timestamp(self.data.index[bar])
        except Exception:
            return None
        if pd.isna(ts):
            return None
        iso = ts.isocalendar()
        return int(iso.year), int(iso.week)

    def _current_equity(self) -> float:
        broker = getattr(self, "_broker", None)
        if broker is None:
            return float(self._month_start_equity)
        equity = float(getattr(broker, "equity", getattr(broker, "_cash", self._month_start_equity)))
        if not np.isfinite(equity):
            return float(self._month_start_equity)
        return equity

    def _estimate_shares(self, *, size: int | float, price: float) -> int:
        if price <= 0.0:
            return 0
        if isinstance(size, int):
            return max(0, int(size))
        if float(size) >= 1.0:
            return max(0, int(size))
        fraction = _clamp(float(size), 0.0, 0.999999)
        equity = self._current_equity()
        notional = equity * fraction
        return max(0, int(notional / price))

    def _use_bootstrap_entry_fallback(self, *, bar: int) -> bool:
        if self.position:
            return False
        if self.closed_trades:
            return False
        bootstrap_window = max(24, (int(self.rsi_period) * 4))
        return bar <= bootstrap_window

    def _build_model_features(
        self,
        *,
        bar: int,
        price: float,
        rsi_value: float,
        trend: float,
        deviation: float,
        atr_frac: float,
        macd_hist: float,
    ) -> np.ndarray:
        lookback = max(1, int(self.model_momentum_lookback))
        if bar > lookback:
            prev_price = float(self.data.Close[-(lookback + 1)])
            momentum = (price / prev_price) - 1.0 if prev_price > 0.0 else 0.0
        else:
            momentum = 0.0

        feats = np.array(
            [
                _clamp((rsi_value - 50.0) / 50.0, -2.0, 2.0),
                _clamp(trend * 40.0, -2.0, 2.0),
                _clamp(deviation * 40.0, -2.0, 2.0),
                _clamp(atr_frac * 80.0, -2.0, 2.0),
                _clamp(momentum * 60.0, -2.0, 2.0),
                _clamp(macd_hist * 120.0, -2.0, 2.0),
                1.0,
            ],
            dtype=float,
        )
        return feats

    def _update_model_and_score(
        self,
        *,
        bar: int,
        price: float,
        features: np.ndarray,
    ) -> float:
        if (
            self._model_prev_features is not None
            and self._model_prev_price is not None
            and self._model_prev_price > 0.0
            and bar > 0
        ):
            realized_return = (price / float(self._model_prev_price)) - 1.0
            label = 1.0 if realized_return >= 0.0 else -1.0
            pred_prev = float(np.dot(self._model_weights, self._model_prev_features))
            margin = label * pred_prev

            lr = max(1e-6, float(self.model_learning_rate))
            if margin < 1.0:
                self._model_weights = self._model_weights + (lr * label * self._model_prev_features)

            decay = _clamp(float(self.model_weight_decay), 0.0, 0.1)
            if decay > 0.0:
                self._model_weights = self._model_weights * (1.0 - decay)

            self._model_updates += 1

        self._model_prev_features = np.asarray(features, dtype=float)
        self._model_prev_price = float(price)

        if self._model_updates < max(0, int(self.model_warmup_bars)):
            return 0.0

        return float(np.dot(self._model_weights, features))

    def _parse_activation_time(self) -> pd.Timestamp | None:
        raw = getattr(self, "activation_time_utc", None)
        if raw is None:
            return None
        try:
            ts = pd.Timestamp(raw)
        except Exception:
            return None
        if pd.isna(ts):
            return None
        if ts.tzinfo is not None:
            try:
                ts = ts.tz_convert("UTC").tz_localize(None)
            except Exception:
                try:
                    ts = ts.tz_localize(None)
                except Exception:
                    return None
        return ts

    def _is_trading_active(self, *, bar: int) -> bool:
        activation_time = getattr(self, "_activation_time", None)
        if activation_time is None:
            return True
        try:
            ts = pd.Timestamp(self.data.index[bar])
        except Exception:
            return True
        if pd.isna(ts):
            return True
        if ts.tzinfo is not None:
            try:
                ts = ts.tz_convert("UTC").tz_localize(None)
            except Exception:
                try:
                    ts = ts.tz_localize(None)
                except Exception:
                    return True
        return ts >= activation_time
