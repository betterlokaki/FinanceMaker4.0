"""Mag7 5-minute volatility-compression breakout/flip sleeve strategy."""
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
class CompressionFlipParams:
    """Frozen parameters for compression expansion with one failure flip."""

    compression_bars: int = 24
    width_window_bars: int = 1560
    compression_quantile: float = 0.2
    atr_bars: int = 14
    breakout_atr_fraction: float = 0.1
    use_volume_filter: bool = True
    volume_lookback: int = 20
    volume_multiple: float = 1.2
    stop_mode: str = "range"
    stop_atr_mult: float = 1.0
    min_stop_pct: float = 0.004
    max_stop_pct: float = 0.025
    risk_reward_ratio: float = 2.0
    leverage: float = 1.0
    exposure_fraction: float = 1.0
    max_holding_bars: int = 18
    flip_timeout_bars: int = 6
    allow_failure_flip: bool = True
    max_trades_per_day: int = 2
    entry_start_bar: int = 9
    entry_end_bar: int = 66
    flatten_minutes_before_close: int = 5

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def prepare_compression_frame(frame: pd.DataFrame, *, params: CompressionFlipParams) -> pd.DataFrame:
    """Add no-lookahead compression and breakout features."""
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
    atr_bars = max(2, int(params.atr_bars))
    compression_bars = max(3, int(params.compression_bars))
    width_window = max(40, int(params.width_window_bars))
    out["ATR"] = true_range.rolling(atr_bars, min_periods=max(4, atr_bars // 2)).mean()
    out["CompressionHigh"] = high.shift(1).rolling(compression_bars, min_periods=compression_bars).max()
    out["CompressionLow"] = low.shift(1).rolling(compression_bars, min_periods=compression_bars).min()
    width = (out["CompressionHigh"] - out["CompressionLow"]) / close.replace(0.0, np.nan)
    out["CompressionWidth"] = width
    out["CompressionWidthThreshold"] = (
        width.rolling(width_window, min_periods=max(40, width_window // 4))
        .quantile(float(params.compression_quantile))
        .shift(1)
    )
    out["CompressionOk"] = width <= out["CompressionWidthThreshold"]
    grouped = out.groupby("SessionDate", sort=False)
    out["VolumeAvg"] = grouped["Volume"].transform(
        lambda series: series.shift(1).rolling(max(2, int(params.volume_lookback)), min_periods=5).mean()
    )
    return out.replace([np.inf, -np.inf], np.nan)


def run_compression_flip_portfolio(
    *,
    data_by_ticker: dict[str, pd.DataFrame],
    tickers: Iterable[str] = MAG7_TICKERS,
    params: CompressionFlipParams,
    initial_capital: float = 100_000.0,
    round_trip_commission: float = 1.0,
    target_monthly_return_pct: float = 6.0,
    short_borrow_fee_apr: float = 0.03,
    start_time: pd.Timestamp | None = None,
    end_time: pd.Timestamp | None = None,
) -> SleevePortfolioResult:
    """Run independent equal-capital sleeves and combine them."""
    ticker_list = [str(ticker).strip().upper() for ticker in tickers if str(ticker).strip()]
    prepared = {
        ticker: prepare_compression_frame(data_by_ticker[ticker], params=params)
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
                    "CompressionHigh",
                    "CompressionLow",
                    "CompressionOk",
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


class Mag7IntradayCompressionFlipStrategy(Strategy):
    """Minimal backtesting.py shell; sleeve simulator is authoritative."""

    def init(self) -> None:
        return None

    def next(self) -> None:
        return None


def _run_single_ticker(
    *,
    ticker: str,
    frame: pd.DataFrame,
    params: CompressionFlipParams,
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
    sessions = frame["SessionDate"].astype(str).to_numpy()
    bars = frame["BarInSession"].astype(float).to_numpy()
    flatten_from = 78 - max(1, int(params.flatten_minutes_before_close // 5))

    for i in range(1, len(frame)):
        timestamp = pd.Timestamp(times[i])
        session = str(sessions[i])
        trades_by_session.setdefault(session, 0)
        previous = frame.iloc[i - 1]
        desired = _breakout_signal(previous, params=params)
        is_flip_entry = False

        if bars[i] >= flatten_from or sessions[i] != sessions[i - 1]:
            desired = 0

        if position is not None:
            held = i - int(position["entry_index"]) + 1
            flip_signal = _failure_flip_signal(previous, position=position, params=params)
            should_flip = (
                bool(params.allow_failure_flip)
                and not bool(position.get("flipped", False))
                and held <= max(1, int(params.flip_timeout_bars))
                and flip_signal != 0
            )
            should_time_exit = held >= max(1, int(params.max_holding_bars))
            if should_flip or should_time_exit:
                cash, trade = _close_position(
                    ticker=ticker,
                    position=position,
                    cash=cash,
                    exit_time=timestamp,
                    exit_price=float(opens[i]),
                    exit_reason="failure_flip" if should_flip else "time_exit",
                    commission_per_side=commission_per_side,
                    short_borrow_fee_apr=short_borrow_fee_apr,
                )
                trades.append(trade)
                desired = flip_signal if should_flip else 0
                is_flip_entry = should_flip
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
            risk = _entry_risk(entry=entry, direction=desired, signal=previous, params=params)
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
                    "compression_high": float(previous["CompressionHigh"]),
                    "compression_low": float(previous["CompressionLow"]),
                    "flipped": bool(is_flip_entry),
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


def _breakout_signal(row: pd.Series, *, params: CompressionFlipParams) -> int:
    if not bool(row["CompressionOk"]):
        return 0
    atr = float(row["ATR"])
    bar = float(row["BarInSession"])
    high = float(row["CompressionHigh"])
    low = float(row["CompressionLow"])
    close = float(row["Close"])
    if not all(np.isfinite(value) for value in (atr, high, low, close)) or atr <= 0.0:
        return 0
    if bar < int(params.entry_start_bar) or bar > int(params.entry_end_bar):
        return 0
    if bool(params.use_volume_filter):
        volume_avg = float(row["VolumeAvg"])
        if not np.isfinite(volume_avg) or volume_avg <= 0.0:
            return 0
        if float(row["Volume"]) < volume_avg * float(params.volume_multiple):
            return 0
    buffer = atr * max(0.0, float(params.breakout_atr_fraction))
    if close > high + buffer:
        return 1
    if close < low - buffer:
        return -1
    return 0


def _failure_flip_signal(
    row: pd.Series,
    *,
    position: dict[str, Any],
    params: CompressionFlipParams,
) -> int:
    close = float(row["Close"])
    high = float(position["compression_high"])
    low = float(position["compression_low"])
    if not all(np.isfinite(value) for value in (close, high, low)):
        return 0
    if int(position["direction"]) > 0 and close <= high:
        return -1
    if int(position["direction"]) < 0 and close >= low:
        return 1
    return 0


def _entry_risk(
    *,
    entry: float,
    direction: int,
    signal: pd.Series,
    params: CompressionFlipParams,
) -> float:
    atr = float(signal["ATR"])
    high = float(signal["CompressionHigh"])
    low = float(signal["CompressionLow"])
    if str(params.stop_mode).strip().lower() == "atr":
        raw_risk = atr * max(0.1, float(params.stop_atr_mult))
    elif direction > 0:
        raw_risk = entry - low
    else:
        raw_risk = high - entry
    min_risk = entry * max(0.0001, float(params.min_stop_pct))
    max_risk = entry * max(float(params.min_stop_pct), float(params.max_stop_pct))
    if not np.isfinite(raw_risk) or raw_risk <= 0.0:
        raw_risk = min_risk
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
