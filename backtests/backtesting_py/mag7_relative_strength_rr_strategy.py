"""Mag7 relative-strength rotation strategy with fixed 1:2 risk/reward exits."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from backtesting import Strategy


MAG7_TICKERS: tuple[str, ...] = (
    "AAPL",
    "MSFT",
    "NVDA",
    "AMZN",
    "META",
    "TSLA",
    "GOOGL",
)


def _compute_atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int,
) -> pd.Series:
    period = max(2, int(period))
    prev_close = close.shift(1)
    true_range = pd.concat(
        [
            (high - low).abs(),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def compute_mag7_relative_strength_features(
    data_by_ticker: dict[str, pd.DataFrame],
    *,
    fast_momentum_bars: int = 21,
    mid_momentum_bars: int = 63,
    slow_momentum_bars: int = 126,
    fast_weight: float = 1.0,
    mid_weight: float = 0.5,
    slow_weight: float = 2.0,
    trend_ema_period: int = 30,
    atr_period: int = 20,
) -> dict[str, pd.DataFrame]:
    """Add no-lookahead cross-sectional rank features to Mag7 OHLCV frames.

    The score at bar t uses only closes through bar t. The strategy places
    orders after bar t, so fills occur no earlier than the next bar.
    """
    normalized: dict[str, pd.DataFrame] = {}
    close_by_ticker: dict[str, pd.Series] = {}

    for ticker, raw in data_by_ticker.items():
        ticker_key = str(ticker).strip().upper()
        if raw is None or raw.empty:
            continue
        frame = raw.copy()
        frame.index = pd.DatetimeIndex(frame.index)
        frame = frame.sort_index()
        frame = frame[~frame.index.duplicated(keep="last")]
        normalized[ticker_key] = frame
        close_by_ticker[ticker_key] = pd.to_numeric(frame["Close"], errors="coerce")

    if not close_by_ticker:
        return {}

    close = pd.DataFrame(close_by_ticker).sort_index()
    fast = close.pct_change(periods=max(1, int(fast_momentum_bars)))
    mid = close.pct_change(periods=max(1, int(mid_momentum_bars)))
    slow = close.pct_change(periods=max(1, int(slow_momentum_bars)))
    score = (fast * float(fast_weight)) + (mid * float(mid_weight)) + (
        slow * float(slow_weight)
    )
    rank = score.rank(axis=1, ascending=False, method="first")

    output: dict[str, pd.DataFrame] = {}
    for ticker, frame in normalized.items():
        out = frame.copy()
        ticker_close = pd.to_numeric(out["Close"], errors="coerce")
        out["Mag7Score"] = score[ticker].reindex(out.index)
        out["Mag7Rank"] = rank[ticker].reindex(out.index)
        out["Mag7FastMomentum"] = fast[ticker].reindex(out.index)
        out["Mag7TrendEma"] = ticker_close.ewm(
            span=max(2, int(trend_ema_period)),
            adjust=False,
            min_periods=1,
        ).mean()
        out["Mag7Atr"] = _compute_atr(
            high=pd.to_numeric(out["High"], errors="coerce"),
            low=pd.to_numeric(out["Low"], errors="coerce"),
            close=ticker_close,
            period=max(2, int(atr_period)),
        )
        output[ticker] = out

    return output


@dataclass(frozen=True)
class SharedRotationMetrics:
    """Metrics from the shared-account Mag7 rotation simulation."""

    initial_capital: float
    final_equity: float
    return_pct: float
    mean_monthly_return_pct: float
    dev_mean_monthly_return_pct: float
    holdout_mean_monthly_return_pct: float
    max_drawdown_pct: float
    trades: int
    win_rate_pct: float
    profit_factor: float
    months: int
    months_at_or_above_target: int


class Mag7RelativeStrengthRRStrategy(Strategy):
    """Long-only Mag7 rank rotation with fixed 1:2 stop/target brackets."""

    entry_rank_threshold: int = 3
    exit_rank_threshold: int = 5
    min_score: float = -0.1
    require_positive_fast_momentum: bool = False
    atr_stop_multiplier: float = 4.0
    min_stop_pct: float = 0.04
    max_stop_pct: float = 0.2
    risk_reward_ratio: float = 2.0
    max_holding_bars: int = 63
    use_full_equity_sizing: bool = True
    full_equity_fraction: float = 1.0
    notional_per_trade: float = 10_000.0
    activation_time_utc: str | None = None

    def init(self) -> None:
        self._score = np.asarray(self.data.Mag7Score, dtype=float)
        self._rank = np.asarray(self.data.Mag7Rank, dtype=float)
        self._fast_momentum = np.asarray(self.data.Mag7FastMomentum, dtype=float)
        self._trend_ema = np.asarray(self.data.Mag7TrendEma, dtype=float)
        self._atr = np.asarray(self.data.Mag7Atr, dtype=float)
        self._entry_bar: int | None = None
        self._was_in_position = False
        self._activation_time = self._parse_activation_time()

    @staticmethod
    def compute_exit_prices(
        *,
        entry_price: float,
        atr_value: float,
        atr_stop_multiplier: float,
        min_stop_pct: float,
        max_stop_pct: float,
        risk_reward_ratio: float = 2.0,
    ) -> tuple[float, float]:
        stop_distance = max(
            float(atr_value) * max(0.0, float(atr_stop_multiplier)),
            float(entry_price) * max(0.0, float(min_stop_pct)),
        )
        max_stop_distance = float(entry_price) * max(0.0, float(max_stop_pct))
        if max_stop_distance > 0.0:
            stop_distance = min(stop_distance, max_stop_distance)

        stop_price = float(entry_price) - stop_distance
        take_profit_price = float(entry_price) + (
            stop_distance * max(0.0, float(risk_reward_ratio))
        )
        return stop_price, take_profit_price

    def next(self) -> None:
        bar = len(self.data) - 1
        if not self._is_trading_active(bar):
            return

        current_in_position = bool(self.position)
        if current_in_position and not self._was_in_position:
            self._entry_bar = bar
        elif (not current_in_position) and self._was_in_position:
            self._entry_bar = None
        self._was_in_position = current_in_position

        if self.position:
            if self._should_close_position(bar):
                self.position.close()
            return

        if self._active_entry_orders():
            return

        if not self._has_entry_signal(bar):
            return

        price = float(self.data.Close[-1])
        atr_value = float(self._atr[bar])
        if not np.isfinite(price) or price <= 0.0:
            return
        if not np.isfinite(atr_value) or atr_value <= 0.0:
            return

        stop_price, take_profit_price = self.compute_exit_prices(
            entry_price=price,
            atr_value=atr_value,
            atr_stop_multiplier=float(self.atr_stop_multiplier),
            min_stop_pct=float(self.min_stop_pct),
            max_stop_pct=float(self.max_stop_pct),
            risk_reward_ratio=float(self.risk_reward_ratio),
        )
        if stop_price <= 0.0 or take_profit_price <= price:
            return

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

        self.buy(size=size, sl=stop_price, tp=take_profit_price)

    def _has_entry_signal(self, bar: int) -> bool:
        if bar < 2:
            return False

        rank = float(self._rank[bar])
        score = float(self._score[bar])
        fast_momentum = float(self._fast_momentum[bar])
        trend_ema = float(self._trend_ema[bar])
        price = float(self.data.Close[-1])

        if not all(np.isfinite(value) for value in (rank, score, trend_ema, price)):
            return False
        if rank > max(1, int(self.entry_rank_threshold)):
            return False
        if score < float(self.min_score):
            return False
        if bool(self.require_positive_fast_momentum) and fast_momentum <= 0.0:
            return False
        return price > trend_ema

    def _should_close_position(self, bar: int) -> bool:
        rank = float(self._rank[bar])
        trend_ema = float(self._trend_ema[bar])
        price = float(self.data.Close[-1])
        if self._entry_bar is not None:
            hold_len = bar - int(self._entry_bar) + 1
            if hold_len >= max(1, int(self.max_holding_bars)):
                return True
        if not all(np.isfinite(value) for value in (rank, trend_ema, price)):
            return False
        if rank > max(1, int(self.exit_rank_threshold)):
            return True
        return price <= trend_ema

    def _active_entry_orders(self) -> list:
        return [
            order
            for order in self.orders
            if not bool(getattr(order, "is_contingent", False))
        ]

    def _is_trading_active(self, bar: int) -> bool:
        if self._activation_time is None:
            return True
        timestamp = pd.Timestamp(self.data.index[bar])
        return timestamp >= self._activation_time

    def _parse_activation_time(self) -> pd.Timestamp | None:
        raw = self.activation_time_utc
        if raw is None or str(raw).strip() == "":
            return None
        timestamp = pd.to_datetime(str(raw), utc=True, errors="coerce")
        if pd.isna(timestamp):
            raise ValueError(f"Invalid activation_time_utc: {raw}")
        return pd.Timestamp(timestamp).tz_convert(None)
