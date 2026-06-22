"""Mag7 5-minute opening-range/VWAP long-short strategy."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Iterable
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from backtesting import Strategy

from backtests.backtesting_py.mag7_adaptive_long_short_strategy import (
    MAG7_TICKERS,
    SleevePortfolioResult,
    SleeveTrade,
)


NY_TZ = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class IntradayOrbParams:
    """Frozen parameters for the 5-minute ORB/VWAP strategy."""

    signal_style: str = "breakout"
    opening_range_bars: int = 6
    breakout_buffer_pct: float = 0.0005
    vwap_buffer_pct: float = 0.0
    min_opening_range_pct: float = 0.002
    max_opening_range_pct: float = 0.04
    stop_range_fraction: float = 0.75
    min_stop_pct: float = 0.004
    max_stop_pct: float = 0.025
    risk_reward_ratio: float = 2.0
    leverage: float = 1.0
    exposure_fraction: float = 1.0
    max_trades_per_day: int = 2
    allow_failed_breakout_flip: bool = True
    require_relative_volume: bool = False
    relative_volume_min: float = 1.0
    flatten_minutes_before_close: int = 5

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def prepare_intraday_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Normalize a 5-minute frame and add session/date features."""
    if frame is None or frame.empty:
        return pd.DataFrame()
    out = frame.copy()
    rename = {column: str(column).strip().title() for column in out.columns}
    out = out.rename(columns=rename)
    required = ["Open", "High", "Low", "Close", "Volume"]
    if not all(column in out.columns for column in required):
        return pd.DataFrame(columns=required)
    if all(column in out.columns for column in ("Sessiondate", "Barinsession", "Vwap", "Relvolume")):
        out = out.rename(
            columns={
                "Sessiondate": "SessionDate",
                "Barinsession": "BarInSession",
                "Vwap": "VWAP",
                "Relvolume": "RelVolume",
            }
        )
    if all(column in out.columns for column in ("SessionDate", "BarInSession", "VWAP", "RelVolume")):
        out = out.loc[:, required + ["SessionDate", "BarInSession", "VWAP", "RelVolume"]].copy()
        out.index = pd.to_datetime(out.index, utc=True, errors="coerce")
        out = out[~out.index.isna()].sort_index()
        out.index = pd.DatetimeIndex(out.index).tz_convert("UTC").tz_localize(None)
        return out.dropna(subset=required + ["SessionDate", "VWAP"])

    out = out.loc[:, required].copy()
    for column in required:
        out[column] = pd.to_numeric(out[column], errors="coerce")
    out = out.dropna(subset=required)
    out.index = pd.to_datetime(out.index, utc=True, errors="coerce")
    out = out[~out.index.isna()].sort_index()
    if out.empty:
        return out
    out.index = pd.DatetimeIndex(out.index).tz_convert("UTC").tz_localize(None)

    index_ny = pd.DatetimeIndex(out.index).tz_localize("UTC").tz_convert(NY_TZ)
    out["SessionDate"] = [ts.date().isoformat() for ts in index_ny]
    out["BarInSession"] = out.groupby("SessionDate").cumcount()
    typical = (out["High"] + out["Low"] + out["Close"]) / 3.0
    pv = typical * out["Volume"].clip(lower=0.0)
    cum_pv = pv.groupby(out["SessionDate"]).cumsum()
    cum_vol = out["Volume"].clip(lower=0.0).groupby(out["SessionDate"]).cumsum()
    out["VWAP"] = cum_pv / cum_vol.replace(0.0, np.nan)
    out["RelVolume"] = _relative_volume_by_bar(out)
    return out.dropna(subset=["VWAP"])


def run_intraday_orb_portfolio(
    *,
    data_by_ticker: dict[str, pd.DataFrame],
    tickers: Iterable[str] = MAG7_TICKERS,
    params: IntradayOrbParams,
    initial_capital: float = 100_000.0,
    round_trip_commission: float = 1.0,
    target_monthly_return_pct: float = 6.0,
    short_borrow_fee_apr: float = 0.03,
) -> SleevePortfolioResult:
    """Run independent 1/7 sleeves and combine them into a portfolio."""
    ticker_list = [str(ticker).strip().upper() for ticker in tickers if str(ticker).strip()]
    common_index = _common_index({ticker: data_by_ticker[ticker] for ticker in ticker_list if ticker in data_by_ticker})
    if len(common_index) < 10:
        empty = pd.Series(dtype=float)
        return _empty_result(initial_capital, empty)

    sleeve_capital = float(initial_capital) / float(len(ticker_list))
    commission_per_side = max(0.0, float(round_trip_commission) / 2.0)
    sleeve_equity: dict[str, pd.Series] = {}
    trades_by_ticker: dict[str, tuple[SleeveTrade, ...]] = {}

    for ticker in ticker_list:
        prepared = prepare_intraday_frame(data_by_ticker[ticker])
        frame = prepared.reindex(common_index)
        equity, trades = _run_single_ticker(
            ticker=ticker,
            frame=frame.dropna(subset=["Open", "High", "Low", "Close", "VWAP", "SessionDate"]),
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
    final_equity = float(portfolio_equity.iloc[-1]) if not portfolio_equity.empty else initial_capital

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


def run_intraday_orb_per_ticker_portfolio(
    *,
    data_by_ticker: dict[str, pd.DataFrame],
    params_by_ticker: dict[str, IntradayOrbParams],
    tickers: Iterable[str] = MAG7_TICKERS,
    initial_capital: float = 100_000.0,
    round_trip_commission: float = 1.0,
    target_monthly_return_pct: float = 6.0,
    short_borrow_fee_apr: float = 0.03,
) -> SleevePortfolioResult:
    """Run equal sleeves with independently frozen per-ticker parameters."""
    ticker_list = [str(ticker).strip().upper() for ticker in tickers if str(ticker).strip()]
    common_index = _common_index({ticker: data_by_ticker[ticker] for ticker in ticker_list if ticker in data_by_ticker})
    if len(common_index) < 10:
        empty = pd.Series(dtype=float)
        return _empty_result(initial_capital, empty)

    sleeve_capital = float(initial_capital) / float(len(ticker_list))
    commission_per_side = max(0.0, float(round_trip_commission) / 2.0)
    sleeve_equity: dict[str, pd.Series] = {}
    trades_by_ticker: dict[str, tuple[SleeveTrade, ...]] = {}

    for ticker in ticker_list:
        params = params_by_ticker[ticker]
        prepared = prepare_intraday_frame(data_by_ticker[ticker])
        frame = prepared.reindex(common_index)
        equity, trades = _run_single_ticker(
            ticker=ticker,
            frame=frame.dropna(subset=["Open", "High", "Low", "Close", "VWAP", "SessionDate"]),
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
    final_equity = float(portfolio_equity.iloc[-1]) if not portfolio_equity.empty else initial_capital

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


class Mag7IntradayOpeningRangeStrategy(Strategy):
    """Backtesting.py wrapper for precomputed ORB/VWAP features."""

    breakout_buffer_pct: float = 0.0005
    vwap_buffer_pct: float = 0.0
    min_stop_pct: float = 0.004
    max_stop_pct: float = 0.025
    risk_reward_ratio: float = 2.0
    exposure_fraction: float = 1.0

    def init(self) -> None:
        return None

    def next(self) -> None:
        if len(self.data) < 2:
            return
        signal = int(float(self.data.Signal[-2]))
        if self.position:
            if signal == 0 or (signal > 0 and self.position.is_short) or (signal < 0 and self.position.is_long):
                self.position.close()
            return
        if signal == 0:
            return
        price = float(self.data.Open[-1])
        risk_pct = max(float(self.min_stop_pct), min(float(self.max_stop_pct), float(self.data.RiskPct[-2])))
        risk = price * risk_pct
        size = max(0.0, min(0.999999, float(self.exposure_fraction)))
        if signal > 0:
            self.buy(size=size, sl=price - risk, tp=price + risk * float(self.risk_reward_ratio))
        else:
            self.sell(size=size, sl=price + risk, tp=price - risk * float(self.risk_reward_ratio))


def _run_single_ticker(
    *,
    ticker: str,
    frame: pd.DataFrame,
    params: IntradayOrbParams,
    initial_capital: float,
    commission_per_side: float,
    short_borrow_fee_apr: float,
) -> tuple[pd.Series, list[SleeveTrade]]:
    cash = float(initial_capital)
    position: dict[str, Any] | None = None
    trades: list[SleeveTrade] = []
    equity_points: list[tuple[pd.Timestamp, float]] = []

    for session_date, day in frame.groupby("SessionDate", sort=True):
        day = day.sort_index()
        if len(day) <= int(params.opening_range_bars) + 2:
            continue
        times = pd.DatetimeIndex(day.index)
        opens = day["Open"].to_numpy(dtype=float)
        highs = day["High"].to_numpy(dtype=float)
        lows = day["Low"].to_numpy(dtype=float)
        closes = day["Close"].to_numpy(dtype=float)
        vwaps = day["VWAP"].to_numpy(dtype=float)
        rel_volumes = day["RelVolume"].to_numpy(dtype=float)

        opening_range_bars = int(params.opening_range_bars)
        or_high = float(np.nanmax(highs[:opening_range_bars]))
        or_low = float(np.nanmin(lows[:opening_range_bars]))
        or_mid = (or_high + or_low) / 2.0
        day_open = float(opens[0])
        if day_open <= 0.0:
            continue
        or_range_pct = (or_high - or_low) / day_open
        if or_range_pct < float(params.min_opening_range_pct):
            continue
        if or_range_pct > float(params.max_opening_range_pct):
            continue

        trades_today = 0
        long_break_seen = False
        short_break_seen = False
        flatten_from = max(0, len(day) - max(1, int(params.flatten_minutes_before_close // 5)))

        for idx in range(opening_range_bars + 1, len(day)):
            timestamp = times[idx]
            signal = _signal_from_values(
                close=float(closes[idx - 1]),
                vwap=float(vwaps[idx - 1]),
                rel_volume=float(rel_volumes[idx - 1]),
                params=params,
                or_high=or_high,
                or_low=or_low,
                long_break_seen=long_break_seen,
                short_break_seen=short_break_seen,
            )
            prev_close = float(closes[idx - 1])
            if prev_close > or_high * (1.0 + float(params.breakout_buffer_pct)):
                long_break_seen = True
            if prev_close < or_low * (1.0 - float(params.breakout_buffer_pct)):
                short_break_seen = True

            if idx >= flatten_from:
                signal = 0

            if position is not None:
                current_side = int(position["direction"])
                if signal != current_side and (signal != 0 or trades_today < int(params.max_trades_per_day)):
                    cash, trade = _close_position(
                        ticker=ticker,
                        position=position,
                        cash=cash,
                        exit_time=pd.Timestamp(timestamp),
                        exit_price=float(opens[idx]),
                        exit_reason="flip_or_flatten",
                        commission_per_side=commission_per_side,
                        short_borrow_fee_apr=short_borrow_fee_apr,
                    )
                    trades.append(trade)
                    position = None

            if position is not None:
                exit_price, exit_reason = _bracket_exit_for_values(
                    high=float(highs[idx]),
                    low=float(lows[idx]),
                    position=position,
                )
                if exit_price is not None:
                    cash, trade = _close_position(
                        ticker=ticker,
                        position=position,
                        cash=cash,
                        exit_time=pd.Timestamp(timestamp),
                        exit_price=exit_price,
                        exit_reason=exit_reason,
                        commission_per_side=commission_per_side,
                        short_borrow_fee_apr=short_borrow_fee_apr,
                    )
                    trades.append(trade)
                    position = None

            if position is None and signal != 0 and trades_today < int(params.max_trades_per_day):
                entry_price = float(opens[idx])
                if np.isfinite(entry_price) and entry_price > 0.0:
                    risk_pct = _risk_pct(
                        entry_price=entry_price,
                        or_high=or_high,
                        or_low=or_low,
                        or_mid=or_mid,
                        direction=signal,
                        params=params,
                    )
                    target_notional = (
                        cash
                        * max(0.0, float(params.leverage))
                        * max(0.0, min(1.0, float(params.exposure_fraction)))
                    )
                    shares = int(target_notional / entry_price)
                    if shares >= 1:
                        risk = entry_price * risk_pct
                        if signal > 0:
                            cash -= shares * entry_price + commission_per_side
                            stop = entry_price - risk
                            target = entry_price + risk * float(params.risk_reward_ratio)
                        else:
                            cash += shares * entry_price - commission_per_side
                            stop = entry_price + risk
                            target = entry_price - risk * float(params.risk_reward_ratio)
                        position = {
                            "direction": signal,
                            "shares": shares,
                            "entry_time": pd.Timestamp(timestamp),
                            "entry_price": entry_price,
                            "stop_price": stop,
                            "target_price": target,
                            "entry_commission": commission_per_side,
                        }
                        trades_today += 1
                        exit_price, exit_reason = _bracket_exit_for_values(
                            high=float(highs[idx]),
                            low=float(lows[idx]),
                            position=position,
                        )
                        if exit_price is not None:
                            cash, trade = _close_position(
                                ticker=ticker,
                                position=position,
                                cash=cash,
                                exit_time=pd.Timestamp(timestamp),
                                exit_price=exit_price,
                                exit_reason=exit_reason,
                                commission_per_side=commission_per_side,
                                short_borrow_fee_apr=short_borrow_fee_apr,
                            )
                            trades.append(trade)
                            position = None

            equity_points.append(
                (
                    pd.Timestamp(timestamp),
                    _mark_to_market_equity(cash=cash, position=position, close=float(closes[idx])),
                )
            )

        if position is not None:
            timestamp = pd.Timestamp(times[-1])
            cash, trade = _close_position(
                ticker=ticker,
                position=position,
                cash=cash,
                exit_time=timestamp,
                exit_price=float(closes[-1]),
                exit_reason="eod_flatten",
                commission_per_side=commission_per_side,
                short_borrow_fee_apr=short_borrow_fee_apr,
            )
            trades.append(trade)
            position = None
            equity_points.append((timestamp, cash))

    equity = pd.Series(
        data=[point[1] for point in equity_points],
        index=pd.DatetimeIndex([point[0] for point in equity_points]),
        dtype=float,
    )
    return equity, trades


def _signal_from_values(
    *,
    close: float,
    vwap: float,
    rel_volume: float,
    params: IntradayOrbParams,
    or_high: float,
    or_low: float,
    long_break_seen: bool,
    short_break_seen: bool,
) -> int:
    if not np.isfinite(close) or not np.isfinite(vwap):
        return 0
    if bool(params.require_relative_volume) and rel_volume < float(params.relative_volume_min):
        return 0
    buffer = float(params.breakout_buffer_pct)
    vwap_buffer = float(params.vwap_buffer_pct)
    long_break = close > or_high * (1.0 + buffer) and close > vwap * (1.0 + vwap_buffer)
    short_break = close < or_low * (1.0 - buffer) and close < vwap * (1.0 - vwap_buffer)
    style = str(params.signal_style).strip().lower()
    if style == "vwap_fade":
        if close > vwap * (1.0 + buffer):
            return -1
        if close < vwap * (1.0 - buffer):
            return 1
        return 0
    if style == "vwap_trend":
        if close > vwap * (1.0 + buffer):
            return 1
        if close < vwap * (1.0 - buffer):
            return -1
        return 0
    if style == "fade":
        if long_break:
            return -1
        if short_break:
            return 1
        return 0

    if long_break:
        return 1
    if short_break:
        return -1
    if bool(params.allow_failed_breakout_flip):
        failed_long = long_break_seen and close < or_high and close < vwap
        failed_short = short_break_seen and close > or_low and close > vwap
        if failed_long:
            return -1
        if failed_short:
            return 1
    return 0


def _signal_from_completed_bar(
    *,
    previous: pd.Series,
    params: IntradayOrbParams,
    or_high: float,
    or_low: float,
    long_break_seen: bool,
    short_break_seen: bool,
) -> int:
    close = float(previous["Close"])
    vwap = float(previous["VWAP"])
    rel_volume = float(previous.get("RelVolume", 1.0))
    if not np.isfinite(close) or not np.isfinite(vwap):
        return 0
    if bool(params.require_relative_volume) and rel_volume < float(params.relative_volume_min):
        return 0
    buffer = float(params.breakout_buffer_pct)
    vwap_buffer = float(params.vwap_buffer_pct)
    long_break = close > or_high * (1.0 + buffer) and close > vwap * (1.0 + vwap_buffer)
    short_break = close < or_low * (1.0 - buffer) and close < vwap * (1.0 - vwap_buffer)
    if long_break:
        return 1
    if short_break:
        return -1
    if bool(params.allow_failed_breakout_flip):
        failed_long = long_break_seen and close < or_high and close < vwap
        failed_short = short_break_seen and close > or_low and close > vwap
        if failed_long:
            return -1
        if failed_short:
            return 1
    return 0


def _risk_pct(
    *,
    entry_price: float,
    or_high: float,
    or_low: float,
    or_mid: float,
    direction: int,
    params: IntradayOrbParams,
) -> float:
    range_risk = max(0.0, or_high - or_low) * max(0.0, float(params.stop_range_fraction))
    midpoint_risk = abs(float(entry_price) - or_mid)
    risk = max(range_risk, midpoint_risk, float(entry_price) * max(0.0, float(params.min_stop_pct)))
    max_risk = float(entry_price) * max(0.0, float(params.max_stop_pct))
    if max_risk > 0.0:
        risk = min(risk, max_risk)
    return max(0.0001, risk / float(entry_price))


def _relative_volume_by_bar(frame: pd.DataFrame, lookback_sessions: int = 20) -> pd.Series:
    pivot = frame.pivot_table(
        values="Volume",
        index="SessionDate",
        columns="BarInSession",
        aggfunc="first",
    )
    rolling = pivot.shift(1).rolling(max(2, int(lookback_sessions)), min_periods=5).mean()
    average = (
        rolling.stack()
        .rename("AverageVolume")
        .reset_index()
        .rename(columns={"BarInSession": "BarInSession"})
    )
    keyed = pd.DataFrame(
        {
            "RowIndex": frame.index,
            "SessionDate": frame["SessionDate"].to_numpy(),
            "BarInSession": frame["BarInSession"].astype(int).to_numpy(),
            "Volume": frame["Volume"].astype(float).to_numpy(),
        }
    )
    merged = keyed.merge(average, on=["SessionDate", "BarInSession"], how="left", sort=False)
    rel = merged["Volume"] / merged["AverageVolume"].replace(0.0, np.nan)
    rel = rel.replace([np.inf, -np.inf], np.nan).fillna(1.0)
    return pd.Series(rel.to_numpy(dtype=float), index=frame.index, dtype=float)


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
    borrow_fee = 0.0
    if direction < 0 and short_borrow_fee_apr > 0.0:
        years = max(0.0, (exit_time - entry_time).total_seconds()) / (365.0 * 24.0 * 60.0 * 60.0)
        borrow_fee = shares * entry_price * float(short_borrow_fee_apr) * years
    if direction > 0:
        cash += shares * float(exit_price) - commission_per_side
        gross_pnl = (float(exit_price) - entry_price) * shares
    else:
        cash -= shares * float(exit_price) + commission_per_side + borrow_fee
        gross_pnl = (entry_price - float(exit_price)) * shares
    net_pnl = gross_pnl - float(position["entry_commission"]) - commission_per_side - borrow_fee
    return cash, SleeveTrade(
        ticker=ticker,
        direction="Long" if direction > 0 else "Short",
        entry_time=entry_time.isoformat(),
        exit_time=pd.Timestamp(exit_time).isoformat(),
        entry_price=round(entry_price, 6),
        exit_price=round(float(exit_price), 6),
        shares=shares,
        net_pnl=round(float(net_pnl), 6),
        net_return_pct=round((net_pnl / max(1.0, shares * entry_price)) * 100.0, 6),
        exit_reason=exit_reason,
    )


def _bracket_exit_for_bar(row: pd.Series, position: dict[str, Any]) -> tuple[float | None, str]:
    return _bracket_exit_for_values(
        high=float(row["High"]),
        low=float(row["Low"]),
        position=position,
    )


def _bracket_exit_for_values(
    *,
    high: float,
    low: float,
    position: dict[str, Any],
) -> tuple[float | None, str]:
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
    drawdown = (equity / equity.cummax()) - 1.0
    return float(drawdown.min()) * 100.0


def _common_index(data_by_ticker: dict[str, pd.DataFrame]) -> pd.DatetimeIndex:
    common: pd.DatetimeIndex | None = None
    for frame in data_by_ticker.values():
        if frame is None or frame.empty:
            continue
        prepared = prepare_intraday_frame(frame)
        index = pd.DatetimeIndex(prepared.index).sort_values()
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
