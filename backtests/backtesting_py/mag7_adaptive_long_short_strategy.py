"""Mag7 adaptive long/short sleeve strategy and no-lookahead simulator."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable

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


@dataclass(frozen=True)
class AdaptiveLongShortParams:
    """Frozen parameters for the Mag7 adaptive long/short strategy."""

    fast_momentum_bars: int = 5
    mid_momentum_bars: int = 21
    slow_momentum_bars: int = 63
    fast_weight: float = 1.0
    mid_weight: float = 1.0
    slow_weight: float = 1.5
    trend_ema_period: int = 30
    trend_slope_bars: int = 10
    atr_period: int = 14
    long_rank_threshold: int = 3
    short_rank_threshold: int = 5
    min_long_score: float = 0.0
    max_short_score: float = 0.0
    min_trend_strength: float = 0.0
    atr_stop_multiplier: float = 3.0
    min_stop_pct: float = 0.025
    max_stop_pct: float = 0.12
    risk_reward_ratio: float = 2.0
    max_holding_bars: int = 42
    exposure_fraction: float = 1.0
    leverage: float = 1.0
    allow_market_regime_shorts: bool = True
    close_on_neutral: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SleeveTrade:
    """Executed trade from one ticker sleeve."""

    ticker: str
    direction: str
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    shares: int
    net_pnl: float
    net_return_pct: float
    exit_reason: str


@dataclass(frozen=True)
class SleevePortfolioResult:
    """Equal-weight sleeve portfolio metrics and evidence."""

    initial_capital: float
    final_equity: float
    return_pct: float
    mean_monthly_return_pct: float
    max_drawdown_pct: float
    trades: int
    winning_trades: int
    losing_trades: int
    win_rate_pct: float
    profit_factor: float
    months: int
    months_at_or_above_target: int
    monthly_returns_pct: pd.Series
    equity_curve: pd.Series
    sleeve_equity_curves: dict[str, pd.Series]
    trades_by_ticker: dict[str, tuple[SleeveTrade, ...]]


def compute_atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int,
) -> pd.Series:
    """Compute Wilder-style ATR."""
    period = max(2, int(period))
    previous_close = close.shift(1)
    true_range = pd.concat(
        [
            (high - low).abs(),
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return true_range.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def compute_mag7_adaptive_features(
    data_by_ticker: dict[str, pd.DataFrame],
    *,
    params: AdaptiveLongShortParams,
    benchmark: pd.DataFrame | None = None,
) -> dict[str, pd.DataFrame]:
    """Add no-lookahead cross-sectional features to Mag7 OHLCV frames."""
    normalized: dict[str, pd.DataFrame] = {}
    closes: dict[str, pd.Series] = {}

    for ticker, raw in data_by_ticker.items():
        ticker_key = str(ticker).strip().upper()
        if raw is None or raw.empty:
            continue
        frame = raw.copy()
        frame.index = pd.DatetimeIndex(frame.index)
        frame = frame[~frame.index.duplicated(keep="last")].sort_index()
        frame = _ensure_ohlcv(frame)
        if frame.empty:
            continue
        normalized[ticker_key] = frame
        closes[ticker_key] = frame["Close"].astype(float)

    if not closes:
        return {}

    close_table = pd.DataFrame(closes).sort_index()
    fast = close_table.pct_change(max(1, int(params.fast_momentum_bars)))
    mid = close_table.pct_change(max(1, int(params.mid_momentum_bars)))
    slow = close_table.pct_change(max(1, int(params.slow_momentum_bars)))
    score = (
        fast * float(params.fast_weight)
        + mid * float(params.mid_weight)
        + slow * float(params.slow_weight)
    )
    rank = score.rank(axis=1, ascending=False, method="first")

    market_bull = pd.Series(True, index=close_table.index, dtype=bool)
    market_bear = pd.Series(False, index=close_table.index, dtype=bool)
    if benchmark is not None and not benchmark.empty:
        bench = _ensure_ohlcv(benchmark.copy())
        if not bench.empty:
            bench_close = bench["Close"].astype(float).reindex(close_table.index).ffill()
            bench_ema = bench_close.ewm(
                span=max(2, int(params.trend_ema_period) * 2),
                adjust=False,
                min_periods=1,
            ).mean()
            bench_ret = bench_close.pct_change(max(2, int(params.mid_momentum_bars)))
            market_bull = ((bench_close >= bench_ema) & (bench_ret >= -0.02)).fillna(False)
            market_bear = ((bench_close < bench_ema) & (bench_ret < 0.0)).fillna(False)

    output: dict[str, pd.DataFrame] = {}
    for ticker, frame in normalized.items():
        out = frame.copy()
        close = out["Close"].astype(float)
        ema = close.ewm(
            span=max(2, int(params.trend_ema_period)),
            adjust=False,
            min_periods=1,
        ).mean()
        trend_strength = (close / ema.replace(0.0, np.nan)) - 1.0
        ema_slope = (ema / ema.shift(max(1, int(params.trend_slope_bars)))) - 1.0
        atr = compute_atr(
            high=out["High"].astype(float),
            low=out["Low"].astype(float),
            close=close,
            period=int(params.atr_period),
        )

        out["Mag7Score"] = score[ticker].reindex(out.index)
        out["Mag7Rank"] = rank[ticker].reindex(out.index)
        out["Mag7TrendEma"] = ema
        out["Mag7TrendStrength"] = trend_strength
        out["Mag7EmaSlope"] = ema_slope
        out["Mag7Atr"] = atr
        out["Mag7MarketBull"] = market_bull.reindex(out.index).ffill().fillna(False)
        out["Mag7MarketBear"] = market_bear.reindex(out.index).ffill().fillna(False)
        output[ticker] = out

    return output


def desired_direction_from_row(row: pd.Series, params: AdaptiveLongShortParams) -> int:
    """Return 1 for long, -1 for short, 0 for flat from completed-bar features."""
    rank = float(row.get("Mag7Rank", np.nan))
    score = float(row.get("Mag7Score", np.nan))
    trend_strength = float(row.get("Mag7TrendStrength", np.nan))
    ema_slope = float(row.get("Mag7EmaSlope", np.nan))
    close = float(row.get("Close", np.nan))
    ema = float(row.get("Mag7TrendEma", np.nan))
    market_bull = bool(row.get("Mag7MarketBull", True))
    market_bear = bool(row.get("Mag7MarketBear", False))

    if not all(np.isfinite(value) for value in (rank, score, trend_strength, ema_slope, close, ema)):
        return 0

    trend_min = max(0.0, float(params.min_trend_strength))
    long_ok = (
        rank <= max(1, int(params.long_rank_threshold))
        and score >= float(params.min_long_score)
        and trend_strength >= trend_min
        and ema_slope >= 0.0
        and close >= ema
        and market_bull
    )
    short_ok = (
        rank >= max(1, int(params.short_rank_threshold))
        and score <= float(params.max_short_score)
        and trend_strength <= -trend_min
        and ema_slope <= 0.0
        and close <= ema
        and (market_bear or bool(params.allow_market_regime_shorts))
    )
    if long_ok and not short_ok:
        return 1
    if short_ok and not long_ok:
        return -1
    return 0


def run_equal_weight_sleeve_portfolio(
    *,
    data_by_ticker: dict[str, pd.DataFrame],
    tickers: Iterable[str] = MAG7_TICKERS,
    params: AdaptiveLongShortParams,
    initial_capital: float = 100_000.0,
    round_trip_commission: float = 1.0,
    target_monthly_return_pct: float = 6.0,
    start_time: pd.Timestamp | None = None,
    end_time: pd.Timestamp | None = None,
    short_borrow_fee_apr: float = 0.03,
) -> SleevePortfolioResult:
    """Run independent equal-capital sleeves and combine their equity curves.

    Signals are computed from completed bar t-1 and executed at bar t open.
    Stops/targets are checked with bar t high/low, assuming the stop is hit
    first if both stop and target trade in the same bar.
    """
    ticker_list = [str(ticker).strip().upper() for ticker in tickers if str(ticker).strip()]
    if not ticker_list:
        empty = pd.Series(dtype=float)
        return _empty_result(initial_capital, empty)

    common_index = _common_index({ticker: data_by_ticker[ticker] for ticker in ticker_list if ticker in data_by_ticker})
    if start_time is not None:
        common_index = common_index[common_index >= pd.Timestamp(start_time)]
    if end_time is not None:
        common_index = common_index[common_index <= pd.Timestamp(end_time)]
    if len(common_index) < 3:
        empty = pd.Series(dtype=float)
        return _empty_result(initial_capital, empty)

    sleeve_capital = float(initial_capital) / float(len(ticker_list))
    commission_per_side = max(0.0, float(round_trip_commission) / 2.0)
    sleeve_equity: dict[str, pd.Series] = {}
    trades_by_ticker: dict[str, tuple[SleeveTrade, ...]] = {}

    for ticker in ticker_list:
        frame = data_by_ticker[ticker].reindex(common_index)
        equity, trades = _run_single_sleeve(
            ticker=ticker,
            frame=frame,
            params=params,
            initial_capital=sleeve_capital,
            commission_per_side=commission_per_side,
            short_borrow_fee_apr=short_borrow_fee_apr,
        )
        sleeve_equity[ticker] = equity
        trades_by_ticker[ticker] = tuple(trades)

    portfolio_equity = sum(sleeve_equity.values())
    monthly_returns = _monthly_returns_pct(portfolio_equity)
    all_trades = [trade for trades in trades_by_ticker.values() for trade in trades]
    wins = [trade for trade in all_trades if trade.net_pnl > 0.0]
    losses = [trade for trade in all_trades if trade.net_pnl <= 0.0]
    profit_factor = (
        float(sum(trade.net_pnl for trade in wins) / abs(sum(trade.net_pnl for trade in losses)))
        if losses and abs(sum(trade.net_pnl for trade in losses)) > 0.0
        else (999.0 if wins else 0.0)
    )
    final_equity = float(portfolio_equity.iloc[-1]) if not portfolio_equity.empty else initial_capital

    return SleevePortfolioResult(
        initial_capital=float(initial_capital),
        final_equity=final_equity,
        return_pct=((final_equity / float(initial_capital)) - 1.0) * 100.0,
        mean_monthly_return_pct=float(monthly_returns.mean()) if not monthly_returns.empty else 0.0,
        max_drawdown_pct=_max_drawdown_pct(portfolio_equity),
        trades=len(all_trades),
        winning_trades=len(wins),
        losing_trades=len(losses),
        win_rate_pct=(len(wins) / len(all_trades) * 100.0) if all_trades else 0.0,
        profit_factor=profit_factor,
        months=len(monthly_returns),
        months_at_or_above_target=int((monthly_returns >= float(target_monthly_return_pct)).sum()),
        monthly_returns_pct=monthly_returns,
        equity_curve=portfolio_equity,
        sleeve_equity_curves=sleeve_equity,
        trades_by_ticker=trades_by_ticker,
    )


class Mag7AdaptiveLongShortStrategy(Strategy):
    """Backtesting.py wrapper for one Mag7 adaptive long/short sleeve."""

    fast_momentum_bars: int = 5
    mid_momentum_bars: int = 21
    slow_momentum_bars: int = 63
    fast_weight: float = 1.0
    mid_weight: float = 1.0
    slow_weight: float = 1.5
    trend_ema_period: int = 30
    trend_slope_bars: int = 10
    atr_period: int = 14
    long_rank_threshold: int = 3
    short_rank_threshold: int = 5
    min_long_score: float = 0.0
    max_short_score: float = 0.0
    min_trend_strength: float = 0.0
    atr_stop_multiplier: float = 3.0
    min_stop_pct: float = 0.025
    max_stop_pct: float = 0.12
    risk_reward_ratio: float = 2.0
    max_holding_bars: int = 42
    exposure_fraction: float = 1.0
    leverage: float = 1.0
    allow_market_regime_shorts: bool = True
    close_on_neutral: bool = True

    def init(self) -> None:
        self._entry_bar: int | None = None

    def next(self) -> None:
        bar = len(self.data) - 1
        if bar < 2:
            return
        params = AdaptiveLongShortParams(
            fast_momentum_bars=int(self.fast_momentum_bars),
            mid_momentum_bars=int(self.mid_momentum_bars),
            slow_momentum_bars=int(self.slow_momentum_bars),
            fast_weight=float(self.fast_weight),
            mid_weight=float(self.mid_weight),
            slow_weight=float(self.slow_weight),
            trend_ema_period=int(self.trend_ema_period),
            trend_slope_bars=int(self.trend_slope_bars),
            atr_period=int(self.atr_period),
            long_rank_threshold=int(self.long_rank_threshold),
            short_rank_threshold=int(self.short_rank_threshold),
            min_long_score=float(self.min_long_score),
            max_short_score=float(self.max_short_score),
            min_trend_strength=float(self.min_trend_strength),
            atr_stop_multiplier=float(self.atr_stop_multiplier),
            min_stop_pct=float(self.min_stop_pct),
            max_stop_pct=float(self.max_stop_pct),
            risk_reward_ratio=float(self.risk_reward_ratio),
            max_holding_bars=int(self.max_holding_bars),
            exposure_fraction=float(self.exposure_fraction),
            leverage=float(self.leverage),
            allow_market_regime_shorts=bool(self.allow_market_regime_shorts),
            close_on_neutral=bool(self.close_on_neutral),
        )
        row = pd.Series(
            {
                "Open": float(self.data.Open[-1]),
                "High": float(self.data.High[-1]),
                "Low": float(self.data.Low[-1]),
                "Close": float(self.data.Close[-1]),
                "Mag7Rank": float(self.data.Mag7Rank[-1]),
                "Mag7Score": float(self.data.Mag7Score[-1]),
                "Mag7TrendStrength": float(self.data.Mag7TrendStrength[-1]),
                "Mag7EmaSlope": float(self.data.Mag7EmaSlope[-1]),
                "Mag7TrendEma": float(self.data.Mag7TrendEma[-1]),
                "Mag7MarketBull": bool(self.data.Mag7MarketBull[-1]),
                "Mag7MarketBear": bool(self.data.Mag7MarketBear[-1]),
            }
        )
        desired = desired_direction_from_row(row, params)

        if self.position:
            held_bars = 0 if self._entry_bar is None else bar - int(self._entry_bar) + 1
            if (
                held_bars >= max(1, int(self.max_holding_bars))
                or (desired > 0 and self.position.is_short)
                or (desired < 0 and self.position.is_long)
                or (desired == 0 and bool(self.close_on_neutral))
            ):
                self.position.close()
                self._entry_bar = None
            return

        if desired == 0 or self._active_entry_orders():
            return
        price = float(self.data.Close[-1])
        atr = float(self.data.Mag7Atr[-1])
        if not np.isfinite(price) or price <= 0.0 or not np.isfinite(atr) or atr <= 0.0:
            return
        risk_pct = _risk_pct(price=price, atr=atr, params=params)
        risk_amount = price * risk_pct
        size = max(0.0, min(0.999999, float(self.exposure_fraction)))
        if size <= 0.0:
            return
        if desired > 0:
            self._entry_bar = bar
            self.buy(
                size=size,
                sl=price - risk_amount,
                tp=price + (risk_amount * float(self.risk_reward_ratio)),
            )
            return
        self._entry_bar = bar
        self.sell(
            size=size,
            sl=price + risk_amount,
            tp=price - (risk_amount * float(self.risk_reward_ratio)),
        )

    def _active_entry_orders(self) -> list:
        return [order for order in self.orders if not bool(getattr(order, "is_contingent", False))]


def _run_single_sleeve(
    *,
    ticker: str,
    frame: pd.DataFrame,
    params: AdaptiveLongShortParams,
    initial_capital: float,
    commission_per_side: float,
    short_borrow_fee_apr: float,
) -> tuple[pd.Series, list[SleeveTrade]]:
    cash = float(initial_capital)
    position: dict[str, Any] | None = None
    equity_points: list[tuple[pd.Timestamp, float]] = []
    trades: list[SleeveTrade] = []

    for bar in range(1, len(frame)):
        timestamp = pd.Timestamp(frame.index[bar])
        signal_row = frame.iloc[bar - 1]
        row = frame.iloc[bar]
        desired = desired_direction_from_row(signal_row, params)

        if position is not None:
            current_side = int(position["direction"])
            held_bars = bar - int(position["entry_bar"]) + 1
            exit_at_open = False
            exit_reason = ""
            if desired != current_side and (desired != 0 or bool(params.close_on_neutral)):
                exit_at_open = True
                exit_reason = "flip_or_neutral"
            elif held_bars >= max(1, int(params.max_holding_bars)):
                exit_at_open = True
                exit_reason = "time_exit"
            if exit_at_open:
                cash, trade = _close_position(
                    ticker=ticker,
                    position=position,
                    cash=cash,
                    exit_time=timestamp,
                    exit_price=float(row["Open"]),
                    exit_reason=exit_reason,
                    commission_per_side=commission_per_side,
                    short_borrow_fee_apr=short_borrow_fee_apr,
                )
                trades.append(trade)
                position = None

        if position is not None:
            exit_price, exit_reason = _bracket_exit_for_bar(row=row, position=position)
            if exit_price is not None:
                cash, trade = _close_position(
                    ticker=ticker,
                    position=position,
                    cash=cash,
                    exit_time=timestamp,
                    exit_price=exit_price,
                    exit_reason=exit_reason,
                    commission_per_side=commission_per_side,
                    short_borrow_fee_apr=short_borrow_fee_apr,
                )
                trades.append(trade)
                position = None

        if position is None and desired != 0:
            entry_price = float(row["Open"])
            atr = float(signal_row.get("Mag7Atr", np.nan))
            if np.isfinite(entry_price) and entry_price > 0.0 and np.isfinite(atr) and atr > 0.0:
                equity_now = cash
                risk_pct = _risk_pct(price=entry_price, atr=atr, params=params)
                target_notional = (
                    equity_now
                    * max(0.0, float(params.leverage))
                    * max(0.0, min(1.0, float(params.exposure_fraction)))
                )
                shares = int(target_notional / entry_price)
                if shares >= 1:
                    risk_amount = entry_price * risk_pct
                    if desired > 0:
                        stop_price = entry_price - risk_amount
                        target_price = entry_price + risk_amount * float(params.risk_reward_ratio)
                        cash -= (shares * entry_price) + commission_per_side
                    else:
                        stop_price = entry_price + risk_amount
                        target_price = entry_price - risk_amount * float(params.risk_reward_ratio)
                        cash += (shares * entry_price) - commission_per_side
                    position = {
                        "ticker": ticker,
                        "direction": desired,
                        "shares": shares,
                        "entry_time": timestamp,
                        "entry_price": entry_price,
                        "entry_bar": bar,
                        "stop_price": stop_price,
                        "target_price": target_price,
                        "entry_commission": commission_per_side,
                    }
                    exit_price, exit_reason = _bracket_exit_for_bar(row=row, position=position)
                    if exit_price is not None:
                        cash, trade = _close_position(
                            ticker=ticker,
                            position=position,
                            cash=cash,
                            exit_time=timestamp,
                            exit_price=exit_price,
                            exit_reason=exit_reason,
                            commission_per_side=commission_per_side,
                            short_borrow_fee_apr=short_borrow_fee_apr,
                        )
                        trades.append(trade)
                        position = None

        equity_points.append((timestamp, _mark_to_market_equity(cash=cash, position=position, close=float(row["Close"]))))

    if position is not None and len(frame) > 0:
        timestamp = pd.Timestamp(frame.index[-1])
        cash, trade = _close_position(
            ticker=ticker,
            position=position,
            cash=cash,
            exit_time=timestamp,
            exit_price=float(frame.iloc[-1]["Close"]),
            exit_reason="finalize",
            commission_per_side=commission_per_side,
            short_borrow_fee_apr=short_borrow_fee_apr,
        )
        trades.append(trade)
        equity_points.append((timestamp, cash))

    equity = pd.Series(
        data=[point[1] for point in equity_points],
        index=pd.DatetimeIndex([point[0] for point in equity_points]),
        dtype=float,
    )
    return equity, trades


def _close_position(
    *,
    ticker: str,
    position: dict[str, Any],
    cash: float,
    exit_time: pd.Timestamp,
    exit_price: float,
    exit_reason: str,
    commission_per_side: float,
    short_borrow_fee_apr: float,
) -> tuple[float, SleeveTrade]:
    direction = int(position["direction"])
    shares = int(position["shares"])
    entry_price = float(position["entry_price"])
    entry_time = pd.Timestamp(position["entry_time"])
    exit_price = max(0.01, float(exit_price))
    borrow_fee = 0.0
    if direction < 0 and short_borrow_fee_apr > 0.0:
        years = max(0.0, (exit_time - entry_time).total_seconds()) / (365.0 * 24.0 * 60.0 * 60.0)
        borrow_fee = shares * entry_price * float(short_borrow_fee_apr) * years

    if direction > 0:
        cash += (shares * exit_price) - commission_per_side
        gross_pnl = (exit_price - entry_price) * shares
    else:
        cash -= (shares * exit_price) + commission_per_side + borrow_fee
        gross_pnl = (entry_price - exit_price) * shares
    net_pnl = gross_pnl - float(position["entry_commission"]) - commission_per_side - borrow_fee
    net_return_pct = (net_pnl / max(1.0, shares * entry_price)) * 100.0
    trade = SleeveTrade(
        ticker=ticker,
        direction="Long" if direction > 0 else "Short",
        entry_time=entry_time.isoformat(),
        exit_time=pd.Timestamp(exit_time).isoformat(),
        entry_price=round(entry_price, 6),
        exit_price=round(exit_price, 6),
        shares=shares,
        net_pnl=round(float(net_pnl), 6),
        net_return_pct=round(float(net_return_pct), 6),
        exit_reason=exit_reason,
    )
    return cash, trade


def _bracket_exit_for_bar(row: pd.Series, position: dict[str, Any]) -> tuple[float | None, str]:
    high = float(row["High"])
    low = float(row["Low"])
    direction = int(position["direction"])
    stop = float(position["stop_price"])
    target = float(position["target_price"])
    if direction > 0:
        hit_stop = low <= stop
        hit_target = high >= target
        if hit_stop and hit_target:
            return stop, "stop_first_same_bar"
        if hit_stop:
            return stop, "stop"
        if hit_target:
            return target, "take_profit"
        return None, ""

    hit_stop = high >= stop
    hit_target = low <= target
    if hit_stop and hit_target:
        return stop, "stop_first_same_bar"
    if hit_stop:
        return stop, "stop"
    if hit_target:
        return target, "take_profit"
    return None, ""


def _mark_to_market_equity(*, cash: float, position: dict[str, Any] | None, close: float) -> float:
    if position is None:
        return float(cash)
    direction = int(position["direction"])
    shares = int(position["shares"])
    if direction > 0:
        return float(cash) + shares * float(close)
    return float(cash) - shares * float(close)


def _risk_pct(*, price: float, atr: float, params: AdaptiveLongShortParams) -> float:
    risk = max(
        float(atr) * max(0.0, float(params.atr_stop_multiplier)),
        float(price) * max(0.0, float(params.min_stop_pct)),
    )
    max_risk = float(price) * max(0.0, float(params.max_stop_pct))
    if max_risk > 0.0:
        risk = min(risk, max_risk)
    return max(0.0001, risk / float(price))


def _monthly_returns_pct(equity: pd.Series) -> pd.Series:
    if equity.empty:
        return pd.Series(dtype=float)
    return equity.resample("ME").last().pct_change().dropna() * 100.0


def _max_drawdown_pct(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    running_max = equity.cummax()
    drawdown = (equity / running_max) - 1.0
    return float(drawdown.min()) * 100.0


def _common_index(data_by_ticker: dict[str, pd.DataFrame]) -> pd.DatetimeIndex:
    common: pd.DatetimeIndex | None = None
    for frame in data_by_ticker.values():
        if frame is None or frame.empty:
            continue
        index = pd.DatetimeIndex(frame.index).sort_values()
        common = index if common is None else common.intersection(index)
    return pd.DatetimeIndex([]) if common is None else common.sort_values()


def _ensure_ohlcv(frame: pd.DataFrame) -> pd.DataFrame:
    rename = {column: str(column).strip().title() for column in frame.columns}
    frame = frame.rename(columns=rename)
    required = ["Open", "High", "Low", "Close", "Volume"]
    if not all(column in frame.columns for column in required):
        return pd.DataFrame(columns=required)
    out = frame.loc[:, required].copy()
    for column in required:
        out[column] = pd.to_numeric(out[column], errors="coerce")
    return out.dropna(subset=required).sort_index()


def _empty_result(initial_capital: float, equity: pd.Series) -> SleevePortfolioResult:
    monthly = _monthly_returns_pct(equity)
    return SleevePortfolioResult(
        initial_capital=float(initial_capital),
        final_equity=float(initial_capital),
        return_pct=0.0,
        mean_monthly_return_pct=0.0,
        max_drawdown_pct=0.0,
        trades=0,
        winning_trades=0,
        losing_trades=0,
        win_rate_pct=0.0,
        profit_factor=0.0,
        months=len(monthly),
        months_at_or_above_target=0,
        monthly_returns_pct=monthly,
        equity_curve=equity,
        sleeve_equity_curves={},
        trades_by_ticker={},
    )
