"""Mag7 5-minute cross-sectional long/short sleeve strategy."""
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
class CrossSectionParams:
    """Frozen parameters for cross-sectional intraday Mag7 ranking."""

    style: str = "momentum"
    fast_bars: int = 3
    mid_bars: int = 12
    slow_bars: int = 39
    fast_weight: float = 1.0
    mid_weight: float = 1.0
    slow_weight: float = 0.5
    vwap_weight: float = 0.5
    open_weight: float = 0.5
    vol_adjust: bool = True
    long_count: int = 2
    short_count: int = 2
    min_abs_score: float = 0.0005
    use_vwap_side_filter: bool = True
    use_dispersion_filter: bool = True
    dispersion_lookback_bars: int = 12
    dispersion_window_bars: int = 240
    min_dispersion_quantile: float = 0.7
    entry_start_bar: int = 6
    leverage: float = 1.0
    exposure_fraction: float = 1.0
    stop_pct: float = 0.006
    risk_reward_ratio: float = 2.0
    max_holding_bars: int = 12
    max_trades_per_day: int = 4
    flatten_minutes_before_close: int = 5

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def compute_cross_section_features(
    data_by_ticker: dict[str, pd.DataFrame],
    *,
    params: CrossSectionParams,
) -> dict[str, pd.DataFrame]:
    """Prepare ticker frames with no-lookahead cross-sectional scores."""
    prepared: dict[str, pd.DataFrame] = {}
    close_table = pd.DataFrame()
    vwap_table = pd.DataFrame()
    open_ret_table = pd.DataFrame()
    vol_table = pd.DataFrame()

    for ticker, raw in data_by_ticker.items():
        ticker_key = str(ticker).strip().upper()
        frame = prepare_intraday_frame(raw)
        if frame.empty:
            continue
        prepared[ticker_key] = frame
        close = frame["Close"].astype(float)
        close_table[ticker_key] = close
        vwap_table[ticker_key] = (close - frame["VWAP"].astype(float)) / close.replace(0.0, np.nan)
        open_ret_table[ticker_key] = close.groupby(frame["SessionDate"]).transform(
            lambda series: (series / series.iloc[0]) - 1.0
        )
        vol_table[ticker_key] = close.pct_change().rolling(20, min_periods=10).std()

    if close_table.empty:
        return {}

    fast = close_table.pct_change(max(1, int(params.fast_bars)), fill_method=None)
    mid = close_table.pct_change(max(1, int(params.mid_bars)), fill_method=None)
    slow = close_table.pct_change(max(1, int(params.slow_bars)), fill_method=None)
    score = (
        fast * float(params.fast_weight)
        + mid * float(params.mid_weight)
        + slow * float(params.slow_weight)
        + vwap_table * float(params.vwap_weight)
        + open_ret_table * float(params.open_weight)
    )
    if bool(params.vol_adjust):
        score = score / vol_table.replace(0.0, np.nan)
    if str(params.style).strip().lower() == "reversal":
        score = -score

    rank = score.rank(axis=1, ascending=False, method="first")
    dispersion_returns = close_table.pct_change(
        max(1, int(params.dispersion_lookback_bars)),
        fill_method=None,
    )
    dispersion = dispersion_returns.std(axis=1, skipna=True)
    dispersion_window = max(20, int(params.dispersion_window_bars))
    dispersion_threshold = (
        dispersion.rolling(
            dispersion_window,
            min_periods=max(20, dispersion_window // 4),
        )
        .quantile(float(params.min_dispersion_quantile))
        .shift(1)
    )
    dispersion_ok = dispersion >= dispersion_threshold
    if not bool(params.use_dispersion_filter):
        dispersion_ok = pd.Series(True, index=dispersion.index)

    out: dict[str, pd.DataFrame] = {}
    for ticker, frame in prepared.items():
        enriched = frame.copy()
        enriched["CrossScore"] = score[ticker].reindex(enriched.index)
        enriched["CrossRank"] = rank[ticker].reindex(enriched.index)
        enriched["CrossVwapDist"] = vwap_table[ticker].reindex(enriched.index)
        enriched["CrossDispersion"] = dispersion.reindex(enriched.index)
        enriched["CrossDispersionOk"] = (
            dispersion_ok.reindex(enriched.index, fill_value=False).astype(bool)
        )
        out[ticker] = enriched.replace([np.inf, -np.inf], np.nan)
    return out


def run_cross_section_sleeve_portfolio(
    *,
    data_by_ticker: dict[str, pd.DataFrame],
    tickers: Iterable[str] = MAG7_TICKERS,
    params: CrossSectionParams,
    initial_capital: float = 100_000.0,
    round_trip_commission: float = 1.0,
    target_monthly_return_pct: float = 6.0,
    short_borrow_fee_apr: float = 0.03,
    start_time: pd.Timestamp | None = None,
    end_time: pd.Timestamp | None = None,
) -> SleevePortfolioResult:
    """Run independent equal-capital sleeves from cross-sectional signals."""
    ticker_list = [str(ticker).strip().upper() for ticker in tickers if str(ticker).strip()]
    featured = compute_cross_section_features(data_by_ticker, params=params)
    common_index = _common_index({ticker: featured[ticker] for ticker in ticker_list if ticker in featured})
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
        frame = featured[ticker].reindex(common_index)
        equity, trades = _run_single_ticker(
            ticker=ticker,
            frame=frame.dropna(subset=["Open", "High", "Low", "Close", "SessionDate", "CrossScore", "CrossRank"]),
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


class Mag7IntradayCrossSectionStrategy(Strategy):
    """Minimal backtesting.py shell; portfolio runner is authoritative."""

    def init(self) -> None:
        return None

    def next(self) -> None:
        return None


def _run_single_ticker(
    *,
    ticker: str,
    frame: pd.DataFrame,
    params: CrossSectionParams,
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
    scores = frame["CrossScore"].to_numpy(dtype=float)
    ranks = frame["CrossRank"].to_numpy(dtype=float)
    vwap_dist = frame.get("CrossVwapDist", pd.Series(0.0, index=frame.index)).to_numpy(dtype=float)
    dispersion_ok = (
        frame.get("CrossDispersionOk", pd.Series(True, index=frame.index))
        .fillna(False)
        .astype(bool)
        .to_numpy()
    )
    sessions = frame["SessionDate"].astype(str).to_numpy()
    bars = frame["BarInSession"].astype(float).to_numpy()
    flatten_from = 78 - max(1, int(params.flatten_minutes_before_close // 5))

    for i in range(1, len(frame)):
        timestamp = pd.Timestamp(times[i])
        session = str(sessions[i])
        if session not in trades_by_session:
            trades_by_session[session] = 0

        desired = _desired_from_previous(
            score=float(scores[i - 1]),
            rank=float(ranks[i - 1]),
            vwap_dist=float(vwap_dist[i - 1]),
            dispersion_ok=bool(dispersion_ok[i - 1]),
            bar_in_session=float(bars[i - 1]),
            params=params,
        )
        if bars[i] >= flatten_from or sessions[i] != sessions[i - 1]:
            desired = 0

        if position is not None:
            held = i - int(position["entry_index"]) + 1
            current = int(position["direction"])
            if desired != current or held >= max(1, int(params.max_holding_bars)):
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
            shares = int(
                (
                    cash
                    * max(0.0, float(params.leverage))
                    * max(0.0, min(1.0, float(params.exposure_fraction)))
                )
                / max(1e-9, entry)
            )
            if shares >= 1 and entry > 0.0:
                risk = entry * max(0.0001, float(params.stop_pct))
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


def _desired_from_previous(
    *,
    score: float,
    rank: float,
    vwap_dist: float,
    dispersion_ok: bool,
    bar_in_session: float,
    params: CrossSectionParams,
) -> int:
    if not np.isfinite(score) or not np.isfinite(rank):
        return 0
    if bool(params.use_dispersion_filter) and not bool(dispersion_ok):
        return 0
    if bar_in_session < max(3, int(params.entry_start_bar)):
        return 0
    if abs(score) < float(params.min_abs_score):
        return 0
    long_ok = rank <= max(1, int(params.long_count)) and score > 0.0
    short_ok = rank > (len(MAG7_TICKERS) - max(1, int(params.short_count))) and score < 0.0
    if bool(params.use_vwap_side_filter):
        long_ok = long_ok and vwap_dist >= 0.0
        short_ok = short_ok and vwap_dist <= 0.0
    if long_ok:
        return 1
    if short_ok:
        return -1
    return 0


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
