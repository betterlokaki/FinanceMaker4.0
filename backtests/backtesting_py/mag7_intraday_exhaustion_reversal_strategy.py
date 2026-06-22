"""Mag7 5-minute exhaustion-reversal long/short sleeve strategy."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable

import numpy as np
import pandas as pd
from backtesting import Strategy

from backtests.backtesting_py.mag7_adaptive_long_short_strategy import (
    MAG7_TICKERS,
    SleevePortfolioResult,
    SleeveTrade,
)
from backtests.backtesting_py.mag7_intraday_orb_strategy import prepare_intraday_frame


@dataclass(frozen=True)
class ExhaustionReversalParams:
    """Frozen parameters for failed-extreme exhaustion reversals."""

    failure_bars: int = 3
    atr_bars: int = 14
    exhaustion_atr_mult: float = 1.5
    failure_atr_fraction: float = 0.15
    use_volume_filter: bool = True
    volume_lookback: int = 20
    volume_multiple: float = 1.2
    stop_atr_buffer: float = 0.25
    min_stop_pct: float = 0.004
    max_stop_pct: float = 0.025
    risk_reward_ratio: float = 2.0
    leverage: float = 1.0
    exposure_fraction: float = 1.0
    max_holding_bars: int = 12
    max_trades_per_day: int = 2
    entry_start_bar: int = 9
    entry_end_bar: int = 60
    flatten_minutes_before_close: int = 5

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def prepare_exhaustion_frame(frame: pd.DataFrame, *, params: ExhaustionReversalParams) -> pd.DataFrame:
    """Add completed-bar exhaustion/failure features."""
    out = prepare_intraday_frame(frame)
    if out.empty:
        return out
    high = out["High"].astype(float)
    low = out["Low"].astype(float)
    close = out["Close"].astype(float)
    volume = out["Volume"].astype(float)
    previous_close = close.shift(1)
    true_range = pd.concat(
        [
            (high - low).abs(),
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    out["ATR"] = true_range.rolling(max(2, int(params.atr_bars)), min_periods=4).mean()
    grouped = out.groupby("SessionDate", sort=False)
    out["SessionOpen"] = grouped["Open"].transform("first").astype(float)
    out["PriorSessionHigh"] = grouped["High"].transform(lambda series: series.shift(1).cummax())
    out["PriorSessionLow"] = grouped["Low"].transform(lambda series: series.shift(1).cummin())
    lookback = max(2, int(params.failure_bars))
    out["PriorRecentHigh"] = grouped["High"].transform(
        lambda series: series.shift(1).rolling(lookback, min_periods=1).max()
    )
    out["PriorRecentLow"] = grouped["Low"].transform(
        lambda series: series.shift(1).rolling(lookback, min_periods=1).min()
    )
    out["VolumeAvg"] = grouped["Volume"].transform(
        lambda series: series.shift(1).rolling(max(2, int(params.volume_lookback)), min_periods=5).mean()
    )
    return out.replace([np.inf, -np.inf], np.nan)


def run_exhaustion_reversal_portfolio(
    *,
    data_by_ticker: dict[str, pd.DataFrame],
    tickers: Iterable[str] = MAG7_TICKERS,
    params: ExhaustionReversalParams,
    initial_capital: float = 100_000.0,
    round_trip_commission: float = 1.0,
    target_monthly_return_pct: float = 6.0,
    short_borrow_fee_apr: float = 0.03,
    start_time: pd.Timestamp | None = None,
    end_time: pd.Timestamp | None = None,
) -> SleevePortfolioResult:
    """Run independent 1/7 sleeves and combine them into a portfolio."""
    ticker_list = [str(ticker).strip().upper() for ticker in tickers if str(ticker).strip()]
    prepared = {
        ticker: prepare_exhaustion_frame(data_by_ticker[ticker], params=params)
        for ticker in ticker_list
        if ticker in data_by_ticker
    }
    common_index = _common_index(prepared)
    if start_time is not None:
        common_index = common_index[common_index >= pd.Timestamp(start_time)]
    if end_time is not None:
        common_index = common_index[common_index <= pd.Timestamp(end_time)]
    if len(common_index) < 10:
        return _empty_result(float(initial_capital), pd.Series(dtype=float))

    sleeve_capital = float(initial_capital) / float(len(ticker_list))
    commission_per_side = max(0.0, float(round_trip_commission) / 2.0)
    sleeve_equity: dict[str, pd.Series] = {}
    trades_by_ticker: dict[str, tuple[SleeveTrade, ...]] = {}

    for ticker in ticker_list:
        frame = prepared[ticker].reindex(common_index)
        equity, trades = _run_single_ticker(
            ticker=ticker,
            frame=frame.dropna(
                subset=[
                    "Open",
                    "High",
                    "Low",
                    "Close",
                    "SessionDate",
                    "BarInSession",
                    "ATR",
                    "SessionOpen",
                    "PriorSessionHigh",
                    "PriorSessionLow",
                    "VolumeAvg",
                ]
            ),
            params=params,
            initial_capital=sleeve_capital,
            commission_per_side=commission_per_side,
            short_borrow_fee_apr=short_borrow_fee_apr,
        )
        if not equity.empty:
            equity = equity[~equity.index.duplicated(keep="last")].sort_index()
        sleeve_equity[ticker] = equity.reindex(common_index).ffill().fillna(sleeve_capital)
        trades_by_ticker[ticker] = tuple(trades)

    portfolio_equity = sum(sleeve_equity.values())
    monthly = _monthly_returns_pct(portfolio_equity)
    all_trades = [trade for trades in trades_by_ticker.values() for trade in trades]
    wins = [trade for trade in all_trades if trade.net_pnl > 0.0]
    losses = [trade for trade in all_trades if trade.net_pnl <= 0.0]
    gross_win = sum(trade.net_pnl for trade in wins)
    gross_loss = abs(sum(trade.net_pnl for trade in losses))
    profit_factor = (gross_win / gross_loss) if gross_loss > 0.0 else (999.0 if gross_win else 0.0)
    final_equity = float(portfolio_equity.iloc[-1]) if not portfolio_equity.empty else float(initial_capital)
    return SleevePortfolioResult(
        initial_capital=float(initial_capital),
        final_equity=final_equity,
        return_pct=((final_equity / float(initial_capital)) - 1.0) * 100.0,
        mean_monthly_return_pct=float(monthly.mean()) if not monthly.empty else 0.0,
        max_drawdown_pct=_max_drawdown_pct(portfolio_equity),
        trades=len(all_trades),
        winning_trades=len(wins),
        losing_trades=len(losses),
        win_rate_pct=(len(wins) / len(all_trades) * 100.0) if all_trades else 0.0,
        profit_factor=float(profit_factor),
        months=len(monthly),
        months_at_or_above_target=int((monthly >= float(target_monthly_return_pct)).sum()),
        monthly_returns_pct=monthly,
        equity_curve=portfolio_equity,
        sleeve_equity_curves=sleeve_equity,
        trades_by_ticker=trades_by_ticker,
    )


class Mag7IntradayExhaustionReversalStrategy(Strategy):
    """Minimal backtesting.py shell; sleeve simulator is authoritative."""

    def init(self) -> None:
        return None

    def next(self) -> None:
        return None


def _run_single_ticker(
    *,
    ticker: str,
    frame: pd.DataFrame,
    params: ExhaustionReversalParams,
    initial_capital: float,
    commission_per_side: float,
    short_borrow_fee_apr: float,
) -> tuple[pd.Series, list[SleeveTrade]]:
    cash = float(initial_capital)
    position: dict[str, Any] | None = None
    trades: list[SleeveTrade] = []
    equity_points: list[tuple[pd.Timestamp, float]] = []
    trades_by_session: dict[str, int] = {}

    times = pd.DatetimeIndex(frame.index)
    opens = frame["Open"].to_numpy(dtype=float)
    highs = frame["High"].to_numpy(dtype=float)
    lows = frame["Low"].to_numpy(dtype=float)
    closes = frame["Close"].to_numpy(dtype=float)
    atrs = frame["ATR"].to_numpy(dtype=float)
    sessions = frame["SessionDate"].astype(str).to_numpy()
    bars = frame["BarInSession"].astype(float).to_numpy()
    flatten_from = 78 - max(1, int(params.flatten_minutes_before_close // 5))

    for i in range(1, len(frame)):
        timestamp = pd.Timestamp(times[i])
        session = str(sessions[i])
        trades_by_session.setdefault(session, 0)

        desired = _signal_from_previous(frame.iloc[i - 1], params=params)
        if bars[i] >= flatten_from or sessions[i] != sessions[i - 1]:
            desired = 0

        if position is not None:
            held = i - int(position["entry_index"]) + 1
            current = int(position["direction"])
            if (desired != 0 and desired != current) or held >= max(1, int(params.max_holding_bars)):
                cash, trade = _close_position(
                    ticker=ticker,
                    position=position,
                    cash=cash,
                    exit_time=timestamp,
                    exit_price=float(opens[i]),
                    exit_reason="flip_or_time",
                    commission_per_side=commission_per_side,
                    short_borrow_fee_apr=short_borrow_fee_apr,
                )
                trades.append(trade)
                position = None

        if position is not None:
            exit_price, exit_reason = _bracket_exit(
                high=float(highs[i]),
                low=float(lows[i]),
                position=position,
            )
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

        if (
            position is None
            and desired != 0
            and trades_by_session[session] < int(params.max_trades_per_day)
        ):
            entry = float(opens[i])
            signal = frame.iloc[i - 1]
            risk = _entry_risk(entry=entry, direction=desired, signal=signal, params=params)
            shares = int(
                (
                    cash
                    * max(0.0, float(params.leverage))
                    * max(0.0, min(1.0, float(params.exposure_fraction)))
                )
                / max(1e-9, entry)
            )
            if shares >= 1 and entry > 0.0 and risk > 0.0:
                if desired > 0:
                    cash -= shares * entry + commission_per_side
                    stop = entry - risk
                    target = entry + risk * float(params.risk_reward_ratio)
                else:
                    cash += shares * entry - commission_per_side
                    stop = entry + risk
                    target = entry - risk * float(params.risk_reward_ratio)
                position = {
                    "direction": desired,
                    "shares": shares,
                    "entry_time": timestamp,
                    "entry_price": entry,
                    "entry_index": i,
                    "stop_price": stop,
                    "target_price": target,
                    "entry_commission": commission_per_side,
                }
                trades_by_session[session] += 1
                exit_price, exit_reason = _bracket_exit(
                    high=float(highs[i]),
                    low=float(lows[i]),
                    position=position,
                )
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

        equity_points.append((timestamp, _mark_to_market(cash=cash, position=position, close=float(closes[i]))))

    if position is not None:
        timestamp = pd.Timestamp(times[-1])
        cash, trade = _close_position(
            ticker=ticker,
            position=position,
            cash=cash,
            exit_time=timestamp,
            exit_price=float(closes[-1]),
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


def _signal_from_previous(row: pd.Series, *, params: ExhaustionReversalParams) -> int:
    atr = float(row["ATR"])
    bar = float(row["BarInSession"])
    if not np.isfinite(atr) or atr <= 0.0:
        return 0
    if bar < int(params.entry_start_bar) or bar > int(params.entry_end_bar):
        return 0
    volume_ok = True
    if bool(params.use_volume_filter):
        volume_avg = float(row["VolumeAvg"])
        volume_ok = np.isfinite(volume_avg) and volume_avg > 0.0 and float(row["Volume"]) >= volume_avg * float(
            params.volume_multiple
        )
    if not volume_ok:
        return 0

    session_open = float(row["SessionOpen"])
    high = float(row["High"])
    low = float(row["Low"])
    close = float(row["Close"])
    prior_high = max(float(row["PriorSessionHigh"]), float(row.get("PriorRecentHigh", np.nan)))
    prior_low = min(float(row["PriorSessionLow"]), float(row.get("PriorRecentLow", np.nan)))
    failure_buffer = atr * max(0.0, float(params.failure_atr_fraction))
    exhausted_up = (high - session_open) >= atr * float(params.exhaustion_atr_mult)
    exhausted_down = (session_open - low) >= atr * float(params.exhaustion_atr_mult)
    failed_high = np.isfinite(prior_high) and high > prior_high and close <= prior_high - failure_buffer
    failed_low = np.isfinite(prior_low) and low < prior_low and close >= prior_low + failure_buffer
    if exhausted_up and failed_high:
        return -1
    if exhausted_down and failed_low:
        return 1
    return 0


def _entry_risk(
    *,
    entry: float,
    direction: int,
    signal: pd.Series,
    params: ExhaustionReversalParams,
) -> float:
    atr = float(signal["ATR"])
    buffer = atr * max(0.0, float(params.stop_atr_buffer))
    if direction > 0:
        raw_risk = entry - (float(signal["Low"]) - buffer)
    else:
        raw_risk = (float(signal["High"]) + buffer) - entry
    min_risk = entry * max(0.0001, float(params.min_stop_pct))
    max_risk = entry * max(float(params.min_stop_pct), float(params.max_stop_pct))
    return max(min_risk, min(max_risk, raw_risk))


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
    entry = float(position["entry_price"])
    entry_time = pd.Timestamp(position["entry_time"])
    borrow_fee = 0.0
    if direction < 0 and short_borrow_fee_apr > 0.0:
        years = max(0.0, (pd.Timestamp(exit_time) - entry_time).total_seconds()) / (365.0 * 24.0 * 60.0 * 60.0)
        borrow_fee = shares * entry * float(short_borrow_fee_apr) * years
    if direction > 0:
        cash += shares * float(exit_price) - commission_per_side
        gross = (float(exit_price) - entry) * shares
    else:
        cash -= shares * float(exit_price) + commission_per_side + borrow_fee
        gross = (entry - float(exit_price)) * shares
    net = gross - float(position["entry_commission"]) - commission_per_side - borrow_fee
    return cash, SleeveTrade(
        ticker=ticker,
        direction="Long" if direction > 0 else "Short",
        entry_time=entry_time.isoformat(),
        exit_time=pd.Timestamp(exit_time).isoformat(),
        entry_price=round(entry, 6),
        exit_price=round(float(exit_price), 6),
        shares=shares,
        net_pnl=round(float(net), 6),
        net_return_pct=round((net / max(1.0, shares * entry)) * 100.0, 6),
        exit_reason=exit_reason,
    )


def _bracket_exit(*, high: float, low: float, position: dict[str, Any]) -> tuple[float | None, str]:
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


def _mark_to_market(*, cash: float, position: dict[str, Any] | None, close: float) -> float:
    if position is None:
        return float(cash)
    shares = int(position["shares"])
    if int(position["direction"]) > 0:
        return float(cash) + shares * float(close)
    return float(cash) - shares * float(close)


def _monthly_returns_pct(equity: pd.Series) -> pd.Series:
    if equity.empty:
        return pd.Series(dtype=float)
    return equity.resample("ME").last().pct_change().dropna() * 100.0


def _max_drawdown_pct(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    return float(((equity / equity.cummax()) - 1.0).min()) * 100.0


def _common_index(data_by_ticker: dict[str, pd.DataFrame]) -> pd.DatetimeIndex:
    common: pd.DatetimeIndex | None = None
    for frame in data_by_ticker.values():
        if frame is None or frame.empty:
            continue
        index = pd.DatetimeIndex(frame.index).sort_values()
        common = index if common is None else common.intersection(index)
    return pd.DatetimeIndex([]) if common is None else common.sort_values()


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
