#!/usr/bin/env python3
"""Run the Mag7 relative-strength 1:2 RR rotation backtest."""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import timedelta
import json
from pathlib import Path
import sys
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROOT_PATH = str(PROJECT_ROOT)
if ROOT_PATH not in sys.path:
    sys.path.insert(0, ROOT_PATH)

from backtests.backtesting_py.isolated_backtest_engine import (
    build_single_buy_and_hold_equity,
    fetch_ohlcv_for_tickers_sync,
    parse_date_range_utc,
    print_symbol_stats,
    resolve_tickers,
    run_isolated_backtests_from_data,
)
from backtests.backtesting_py.mag7_relative_strength_rr_strategy import (
    MAG7_TICKERS,
    Mag7RelativeStrengthRRStrategy,
    SharedRotationMetrics,
    compute_mag7_relative_strength_features,
)
from common.models.period import Period


DEFAULT_OUTPUT_PATH = (
    PROJECT_ROOT / "backtests" / "results" / "mag7_relative_strength_rr_backtest.json"
)


@dataclass(frozen=True)
class PortfolioTrade:
    ticker: str
    entry_time: str
    exit_time: str
    entry_price: float
    exit_price: float
    shares: int
    net_return_pct: float
    exit_reason: str


@dataclass(frozen=True)
class SharedPortfolioRun:
    metrics: SharedRotationMetrics
    equity_curve: pd.Series
    monthly_returns_pct: pd.Series
    trades: tuple[PortfolioTrade, ...]


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Mag7 relative-strength rotation with fixed 1:2 RR exits."
    )
    parser.add_argument(
        "--tickers",
        nargs="+",
        default=None,
        help="Ticker symbols (comma or space separated). Defaults to Mag7.",
    )
    parser.add_argument("--start-date", default="2021-06-21")
    parser.add_argument("--end-date", default="2026-06-20")
    parser.add_argument("--holdout-start-date", default="2025-01-01")
    parser.add_argument("--warmup-days", type=int, default=385)
    parser.add_argument("--initial-capital", type=float, default=10_000.0)
    parser.add_argument("--leverage", type=float, default=2.0)
    parser.add_argument("--round-trip-commission", type=float, default=1.0)
    parser.add_argument("--target-monthly-return-pct", type=float, default=6.0)
    parser.add_argument("--benchmark-ticker", default="SPY")
    parser.add_argument(
        "--output-json",
        default=str(DEFAULT_OUTPUT_PATH),
        help="Path for compact JSON summary. Use an empty value to skip writing.",
    )
    parser.add_argument("--no-isolated", action="store_true")
    return parser.parse_args(argv)


def _validate_args(args: argparse.Namespace) -> None:
    if float(args.initial_capital) <= 0:
        raise SystemExit("Invalid --initial-capital. Value must be > 0.")
    if float(args.leverage) <= 0:
        raise SystemExit("Invalid --leverage. Value must be > 0.")
    if float(args.round_trip_commission) < 0:
        raise SystemExit("Invalid --round-trip-commission. Value must be >= 0.")
    if int(args.warmup_days) < 0:
        raise SystemExit("Invalid --warmup-days. Value must be >= 0.")
    if float(args.target_monthly_return_pct) <= 0:
        raise SystemExit("Invalid --target-monthly-return-pct. Value must be > 0.")


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


def _strategy_kwargs(start_time: pd.Timestamp) -> dict[str, Any]:
    return {
        "entry_rank_threshold": 3,
        "exit_rank_threshold": 5,
        "min_score": -0.1,
        "require_positive_fast_momentum": False,
        "atr_stop_multiplier": 4.0,
        "min_stop_pct": 0.04,
        "max_stop_pct": 0.2,
        "risk_reward_ratio": 2.0,
        "max_holding_bars": 63,
        "use_full_equity_sizing": True,
        "full_equity_fraction": 1.0,
        "activation_time_utc": start_time.isoformat(),
    }


def _feature_kwargs() -> dict[str, Any]:
    return {
        "fast_momentum_bars": 21,
        "mid_momentum_bars": 63,
        "slow_momentum_bars": 126,
        "fast_weight": 1.0,
        "mid_weight": 0.5,
        "slow_weight": 2.0,
        "trend_ema_period": 30,
        "atr_period": 20,
    }


def _common_index(data_by_ticker: dict[str, pd.DataFrame]) -> pd.DatetimeIndex:
    common: pd.DatetimeIndex | None = None
    for frame in data_by_ticker.values():
        if frame is None or frame.empty:
            continue
        index = pd.DatetimeIndex(frame.index).sort_values()
        common = index if common is None else common.intersection(index)
    return pd.DatetimeIndex([]) if common is None else common.sort_values()


def run_shared_rotation_portfolio(
    *,
    data_by_ticker: dict[str, pd.DataFrame],
    tickers: list[str],
    start_time: pd.Timestamp,
    end_time: pd.Timestamp,
    holdout_start_time: pd.Timestamp,
    initial_capital: float,
    leverage: float,
    round_trip_commission: float,
    strategy_kwargs: dict[str, Any],
) -> SharedPortfolioRun:
    """Run a shared-account daily simulation from close-known signals.

    Signals are evaluated after a completed daily candle. Entries and
    rank/trend exits execute at the next open. Bracket exits are checked with
    daily high/low, and if stop and target are both touched on one bar, the
    stop is assumed first.
    """
    common_index = _common_index({ticker: data_by_ticker[ticker] for ticker in tickers})
    common_index = common_index[(common_index >= start_time) & (common_index <= end_time)]
    if len(common_index) < 2:
        empty = pd.Series(dtype=float)
        metrics = SharedRotationMetrics(
            initial_capital=initial_capital,
            final_equity=initial_capital,
            return_pct=0.0,
            mean_monthly_return_pct=0.0,
            dev_mean_monthly_return_pct=0.0,
            holdout_mean_monthly_return_pct=0.0,
            max_drawdown_pct=0.0,
            trades=0,
            win_rate_pct=0.0,
            profit_factor=0.0,
            months=0,
            months_at_or_above_target=0,
        )
        return SharedPortfolioRun(metrics, empty, empty, tuple())

    pending_entries: list[dict[str, Any]] = []
    pending_exits: set[str] = set()
    open_positions: dict[str, dict[str, Any]] = {}
    trades: list[PortfolioTrade] = []
    trade_returns: list[float] = []
    curve: list[tuple[pd.Timestamp, float]] = []
    cash = float(initial_capital)
    commission_per_side = max(0.0, float(round_trip_commission) / 2.0)
    atr_stop_multiplier = float(strategy_kwargs["atr_stop_multiplier"])
    min_stop_pct = float(strategy_kwargs["min_stop_pct"])
    max_stop_pct = float(strategy_kwargs["max_stop_pct"])
    risk_reward_ratio = float(strategy_kwargs["risk_reward_ratio"])
    max_holding_bars = int(strategy_kwargs["max_holding_bars"])
    exit_rank_threshold = int(strategy_kwargs["exit_rank_threshold"])

    for bar, timestamp in enumerate(common_index):
        # Execute exits requested by the prior close.
        for ticker in sorted(pending_exits):
            position = open_positions.pop(ticker, None)
            if position is None:
                continue
            exit_price = float(data_by_ticker[ticker].at[timestamp, "Open"])
            cash += (int(position["shares"]) * exit_price) - commission_per_side
            net_return = (exit_price / float(position["entry_price"])) - 1.0
            net_return -= round_trip_commission / max(1.0, float(position["notional"]))
            trade_returns.append(net_return)
            trades.append(
                _portfolio_trade(
                    ticker=ticker,
                    position=position,
                    exit_time=timestamp,
                    exit_price=exit_price,
                    net_return=net_return,
                    exit_reason="rank_or_trend_exit",
                )
            )
        pending_exits.clear()

        # Execute entries requested by the prior close, using the current open.
        if pending_entries:
            equity_at_open = _portfolio_equity(
                cash=cash,
                open_positions=open_positions,
                data_by_ticker=data_by_ticker,
                timestamp=timestamp,
                price_column="Open",
            )
            gross_at_open = _portfolio_gross_notional(
                open_positions=open_positions,
                data_by_ticker=data_by_ticker,
                timestamp=timestamp,
                price_column="Open",
            )
            remaining_capacity = max(0.0, (equity_at_open * float(leverage)) - gross_at_open)

            for pending_entry in pending_entries:
                ticker = str(pending_entry["ticker"])
                if ticker in open_positions:
                    continue
                if remaining_capacity <= 0.0:
                    break
                frame = data_by_ticker[ticker]
                entry_price = float(frame.at[timestamp, "Open"])
                atr_value = float(pending_entry["atr_value"])
                if not np.isfinite(entry_price) or entry_price <= 0.0:
                    continue
                if not np.isfinite(atr_value) or atr_value <= 0.0:
                    continue

                target_notional = min(remaining_capacity, equity_at_open)
                shares = int(target_notional / entry_price)
                if shares < 1:
                    continue

                stop_price, take_profit_price = Mag7RelativeStrengthRRStrategy.compute_exit_prices(
                    entry_price=entry_price,
                    atr_value=atr_value,
                    atr_stop_multiplier=atr_stop_multiplier,
                    min_stop_pct=min_stop_pct,
                    max_stop_pct=max_stop_pct,
                    risk_reward_ratio=risk_reward_ratio,
                )
                notional = shares * entry_price
                cash -= notional + commission_per_side
                remaining_capacity -= notional
                open_positions[ticker] = {
                    "ticker": ticker,
                    "entry_time": timestamp,
                    "entry_price": entry_price,
                    "shares": shares,
                    "stop_price": stop_price,
                    "take_profit_price": take_profit_price,
                    "entry_bar": bar,
                    "notional": notional,
                }
        pending_entries.clear()

        # Check bracket and time exits during the current bar.
        for ticker, position in list(open_positions.items()):
            frame = data_by_ticker[ticker]
            high = float(frame.at[timestamp, "High"])
            low = float(frame.at[timestamp, "Low"])
            close = float(frame.at[timestamp, "Close"])
            stop_price = float(position["stop_price"])
            take_profit_price = float(position["take_profit_price"])
            exit_price: float | None = None
            exit_reason: str | None = None

            hit_stop = low <= stop_price
            hit_target = high >= take_profit_price
            if hit_stop and hit_target:
                exit_price = stop_price
                exit_reason = "stop_first_same_bar"
            elif hit_stop:
                exit_price = stop_price
                exit_reason = "stop"
            elif hit_target:
                exit_price = take_profit_price
                exit_reason = "take_profit"
            elif (bar - int(position["entry_bar"]) + 1) >= max_holding_bars:
                exit_price = close
                exit_reason = "time_exit"

            if exit_price is None:
                continue

            open_positions.pop(ticker, None)
            cash += (int(position["shares"]) * exit_price) - commission_per_side
            net_return = (exit_price / float(position["entry_price"])) - 1.0
            net_return -= round_trip_commission / max(1.0, float(position["notional"]))
            trade_returns.append(net_return)
            trades.append(
                _portfolio_trade(
                    ticker=ticker,
                    position=position,
                    exit_time=timestamp,
                    exit_price=exit_price,
                    net_return=net_return,
                    exit_reason=str(exit_reason),
                )
            )

        equity_at_close = _portfolio_equity(
            cash=cash,
            open_positions=open_positions,
            data_by_ticker=data_by_ticker,
            timestamp=timestamp,
            price_column="Close",
        )
        curve.append((timestamp, equity_at_close))

        if bar >= len(common_index) - 1:
            continue

        # Generate next-open exits from this completed close.
        for ticker, position in list(open_positions.items()):
            frame = data_by_ticker[ticker]
            rank = float(frame.at[timestamp, "Mag7Rank"])
            close = float(frame.at[timestamp, "Close"])
            trend_ema = float(frame.at[timestamp, "Mag7TrendEma"])
            if np.isfinite(rank) and rank > exit_rank_threshold:
                pending_exits.add(ticker)
            elif np.isfinite(close) and np.isfinite(trend_ema) and close <= trend_ema:
                pending_exits.add(ticker)

        ranked_candidates = _ranked_entry_candidates(
            data_by_ticker=data_by_ticker,
            tickers=tickers,
            timestamp=timestamp,
            open_positions=open_positions,
            pending_exits=pending_exits,
            strategy_kwargs=strategy_kwargs,
        )
        pending_entries.extend(ranked_candidates)

    equity_curve = pd.Series(
        data=[equity for _, equity in curve],
        index=pd.DatetimeIndex([timestamp for timestamp, _ in curve]),
        dtype=float,
    )
    monthly_returns = _monthly_returns_pct(equity_curve)
    dev_monthly = monthly_returns[monthly_returns.index < holdout_start_time]
    holdout_monthly = monthly_returns[monthly_returns.index >= holdout_start_time]
    wins = [value for value in trade_returns if value > 0.0]
    losses = [value for value in trade_returns if value <= 0.0]
    profit_factor = (
        float(sum(wins) / abs(sum(losses)))
        if losses and abs(sum(losses)) > 0.0
        else (999.0 if wins else 0.0)
    )
    final_equity = float(equity_curve.iloc[-1]) if not equity_curve.empty else initial_capital
    metrics = SharedRotationMetrics(
        initial_capital=float(initial_capital),
        final_equity=final_equity,
        return_pct=((final_equity / float(initial_capital)) - 1.0) * 100.0,
        mean_monthly_return_pct=float(monthly_returns.mean()) if not monthly_returns.empty else 0.0,
        dev_mean_monthly_return_pct=float(dev_monthly.mean()) if not dev_monthly.empty else 0.0,
        holdout_mean_monthly_return_pct=(
            float(holdout_monthly.mean()) if not holdout_monthly.empty else 0.0
        ),
        max_drawdown_pct=_max_drawdown_pct(equity_curve),
        trades=len(trade_returns),
        win_rate_pct=(len(wins) / len(trade_returns) * 100.0) if trade_returns else 0.0,
        profit_factor=profit_factor,
        months=len(monthly_returns),
        months_at_or_above_target=int((monthly_returns >= 6.0).sum()),
    )
    return SharedPortfolioRun(
        metrics=metrics,
        equity_curve=equity_curve,
        monthly_returns_pct=monthly_returns,
        trades=tuple(trades),
    )


def _portfolio_equity(
    *,
    cash: float,
    open_positions: dict[str, dict[str, Any]],
    data_by_ticker: dict[str, pd.DataFrame],
    timestamp: pd.Timestamp,
    price_column: str,
) -> float:
    value = float(cash)
    for ticker, position in open_positions.items():
        value += int(position["shares"]) * float(data_by_ticker[ticker].at[timestamp, price_column])
    return value


def _portfolio_gross_notional(
    *,
    open_positions: dict[str, dict[str, Any]],
    data_by_ticker: dict[str, pd.DataFrame],
    timestamp: pd.Timestamp,
    price_column: str,
) -> float:
    return sum(
        int(position["shares"]) * float(data_by_ticker[ticker].at[timestamp, price_column])
        for ticker, position in open_positions.items()
    )


def _portfolio_trade(
    *,
    ticker: str,
    position: dict[str, Any],
    exit_time: pd.Timestamp,
    exit_price: float,
    net_return: float,
    exit_reason: str,
) -> PortfolioTrade:
    return PortfolioTrade(
        ticker=ticker,
        entry_time=pd.Timestamp(position["entry_time"]).isoformat(),
        exit_time=pd.Timestamp(exit_time).isoformat(),
        entry_price=round(float(position["entry_price"]), 6),
        exit_price=round(float(exit_price), 6),
        shares=int(position["shares"]),
        net_return_pct=round(float(net_return) * 100.0, 6),
        exit_reason=exit_reason,
    )


def _ranked_entry_candidates(
    *,
    data_by_ticker: dict[str, pd.DataFrame],
    tickers: list[str],
    timestamp: pd.Timestamp,
    open_positions: dict[str, dict[str, Any]],
    pending_exits: set[str],
    strategy_kwargs: dict[str, Any],
) -> list[dict[str, Any]]:
    entry_rank_threshold = int(strategy_kwargs["entry_rank_threshold"])
    min_score = float(strategy_kwargs["min_score"])
    require_positive_fast = bool(strategy_kwargs["require_positive_fast_momentum"])
    candidates: list[tuple[float, str, float]] = []
    for ticker in tickers:
        if ticker in open_positions or ticker in pending_exits:
            continue
        frame = data_by_ticker[ticker]
        rank = float(frame.at[timestamp, "Mag7Rank"])
        score = float(frame.at[timestamp, "Mag7Score"])
        fast_momentum = float(frame.at[timestamp, "Mag7FastMomentum"])
        close = float(frame.at[timestamp, "Close"])
        trend_ema = float(frame.at[timestamp, "Mag7TrendEma"])
        atr_value = float(frame.at[timestamp, "Mag7Atr"])
        if not all(
            np.isfinite(value)
            for value in (rank, score, fast_momentum, close, trend_ema, atr_value)
        ):
            continue
        if rank > entry_rank_threshold:
            continue
        if score < min_score:
            continue
        if require_positive_fast and fast_momentum <= 0.0:
            continue
        if close <= trend_ema:
            continue
        candidates.append((rank, ticker, atr_value))
    return [
        {"ticker": ticker, "atr_value": atr_value}
        for _, ticker, atr_value in sorted(candidates)
    ]


def _buy_and_hold_equity(
    *,
    data_by_ticker: dict[str, pd.DataFrame],
    tickers: list[str],
    index: pd.DatetimeIndex,
    initial_capital: float,
) -> pd.Series:
    if index.empty:
        return pd.Series(dtype=float)
    curves = []
    for ticker in tickers:
        frame = data_by_ticker.get(ticker)
        if frame is None or frame.empty:
            continue
        curves.append(
            build_single_buy_and_hold_equity(
                df=frame,
                index=index,
                initial_capital=initial_capital,
            )
        )
    if not curves:
        return pd.Series(dtype=float)
    return sum(curves) / len(curves)


def _series_return_pct(equity: pd.Series, initial_capital: float) -> float:
    if equity.empty or initial_capital <= 0:
        return 0.0
    return ((float(equity.iloc[-1]) / float(initial_capital)) - 1.0) * 100.0


def _write_json_summary(path: str, payload: dict[str, Any]) -> None:
    output = str(path or "").strip()
    if not output:
        return
    output_path = Path(output).expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    _validate_args(args)

    start_dt, end_dt = parse_date_range_utc(
        start_date=str(args.start_date),
        end_date=str(args.end_date),
    )
    holdout_dt, _ = parse_date_range_utc(
        start_date=str(args.holdout_start_date),
        end_date=str(args.holdout_start_date),
    )
    start_time = pd.Timestamp(start_dt).tz_convert(None)
    end_time = pd.Timestamp(end_dt - timedelta(days=1)).tz_convert(None)
    holdout_start_time = pd.Timestamp(holdout_dt).tz_convert(None)
    fetch_start_time = start_dt - timedelta(days=max(0, int(args.warmup_days)))

    tickers = resolve_tickers(args.tickers, default_tickers=MAG7_TICKERS)
    benchmark_ticker = str(args.benchmark_ticker).strip().upper() or "SPY"
    fetch_tickers = list(tickers)
    if benchmark_ticker not in fetch_tickers:
        fetch_tickers.append(benchmark_ticker)

    print("=" * 100)
    print("MAG7 RELATIVE STRENGTH 1:2 RR BACKTEST")
    print("=" * 100)
    print(f"Tickers: {', '.join(tickers)}")
    print(f"Benchmark: {benchmark_ticker}")
    print(f"Window: {args.start_date} to {args.end_date} | Holdout starts: {args.holdout_start_date}")
    print(f"Warmup days: {int(args.warmup_days)} | Period: daily")
    print(
        f"Initial capital=${float(args.initial_capital):,.2f} | "
        f"Shared leverage cap={float(args.leverage):.2f}x | "
        f"Commission=${float(args.round_trip_commission):.2f}/round-trip"
    )
    print("=" * 100)

    fetched = fetch_ohlcv_for_tickers_sync(
        tickers=fetch_tickers,
        start_time=fetch_start_time,
        end_time=end_dt,
        period=Period.DAILY,
    )
    strategy_raw = {ticker: fetched[ticker] for ticker in tickers if ticker in fetched}
    if len(strategy_raw) != len(tickers):
        missing = sorted(set(tickers) - set(strategy_raw))
        print(f"Missing strategy data for: {', '.join(missing)}")
        return 1

    featured = compute_mag7_relative_strength_features(strategy_raw, **_feature_kwargs())
    if len(featured) != len(tickers):
        print("Feature preparation failed for one or more tickers.")
        return 1

    strategy_kwargs = _strategy_kwargs(start_time)
    shared = run_shared_rotation_portfolio(
        data_by_ticker=featured,
        tickers=tickers,
        start_time=start_time,
        end_time=end_time,
        holdout_start_time=holdout_start_time,
        initial_capital=float(args.initial_capital),
        leverage=float(args.leverage),
        round_trip_commission=float(args.round_trip_commission),
        strategy_kwargs=strategy_kwargs,
    )
    metrics = shared.metrics
    target = float(args.target_monthly_return_pct)
    full_target_hit = metrics.mean_monthly_return_pct >= target
    holdout_target_hit = metrics.holdout_mean_monthly_return_pct >= target
    live_gate_passed = full_target_hit and holdout_target_hit

    print("\nSHARED ACCOUNT RESULT")
    print("-" * 100)
    print(f"Final equity: ${metrics.final_equity:,.2f}")
    print(f"Return: {metrics.return_pct:+.2f}%")
    print(f"Average monthly return: {metrics.mean_monthly_return_pct:+.2f}%")
    print(f"Development monthly return: {metrics.dev_mean_monthly_return_pct:+.2f}%")
    print(f"Holdout monthly return: {metrics.holdout_mean_monthly_return_pct:+.2f}%")
    print(f"Max drawdown: {metrics.max_drawdown_pct:+.2f}%")
    print(
        f"Trades: {metrics.trades} | Win rate: {metrics.win_rate_pct:.2f}% | "
        f"Profit factor: {metrics.profit_factor:.2f}"
    )
    print(
        f"Months >= {target:.2f}%: {metrics.months_at_or_above_target}/{metrics.months}"
    )
    print(f"Full-window target: {'HIT' if full_target_hit else 'MISS'}")
    print(f"Holdout target: {'HIT' if holdout_target_hit else 'MISS'}")
    print(f"Live conversion gate: {'PASS' if live_gate_passed else 'BLOCKED'}")

    benchmark_df = fetched.get(benchmark_ticker)
    benchmark_return_pct = 0.0
    benchmark_monthly_mean_pct = 0.0
    if benchmark_df is not None and not benchmark_df.empty and not shared.equity_curve.empty:
        benchmark_equity = build_single_buy_and_hold_equity(
            df=benchmark_df,
            index=pd.DatetimeIndex(shared.equity_curve.index),
            initial_capital=float(args.initial_capital),
        )
        benchmark_return_pct = _series_return_pct(benchmark_equity, float(args.initial_capital))
        benchmark_monthly = _monthly_returns_pct(benchmark_equity)
        benchmark_monthly_mean_pct = (
            float(benchmark_monthly.mean()) if not benchmark_monthly.empty else 0.0
        )

    universe_bh = _buy_and_hold_equity(
        data_by_ticker=featured,
        tickers=tickers,
        index=pd.DatetimeIndex(shared.equity_curve.index),
        initial_capital=float(args.initial_capital),
    )
    universe_bh_return_pct = _series_return_pct(universe_bh, float(args.initial_capital))
    universe_bh_monthly = _monthly_returns_pct(universe_bh)
    universe_bh_monthly_mean_pct = (
        float(universe_bh_monthly.mean()) if not universe_bh_monthly.empty else 0.0
    )
    print("\nBENCHMARKS")
    print("-" * 100)
    print(
        f"{benchmark_ticker} buy-and-hold: {benchmark_return_pct:+.2f}% | "
        f"avg monthly {benchmark_monthly_mean_pct:+.2f}%"
    )
    print(
        f"Equal-weight strategy universe buy-and-hold: {universe_bh_return_pct:+.2f}% | "
        f"avg monthly {universe_bh_monthly_mean_pct:+.2f}%"
    )

    isolated_summary: dict[str, Any] = {}
    if not bool(args.no_isolated):
        print("\nBACKTESTING.PY ISOLATED SMOKE")
        print("-" * 100)
        stats_by_ticker, equity_by_ticker = run_isolated_backtests_from_data(
            data_by_ticker=featured,
            strategy_cls=Mag7RelativeStrengthRRStrategy,
            strategy_kwargs=strategy_kwargs,
            initial_capital=float(args.initial_capital),
            leverage=float(args.leverage),
            commission_per_side=max(0.0, float(args.round_trip_commission) / 2.0),
            print_symbol_results=True,
        )
        for ticker, stats in sorted(stats_by_ticker.items()):
            print_symbol_stats(ticker, stats)
        isolated_summary = {
            ticker: {
                "return_pct": float(stats.get("Return [%]", 0.0)),
                "max_drawdown_pct": float(stats.get("Max. Drawdown [%]", 0.0)),
                "trades": int(stats.get("# Trades", 0)),
                "win_rate_pct": float(stats.get("Win Rate [%]", 0.0)),
            }
            for ticker, stats in stats_by_ticker.items()
        }

        _ = equity_by_ticker

    summary = {
        "strategy": "mag7_relative_strength_rr",
        "target_monthly_return_pct": target,
        "full_window_target_hit": full_target_hit,
        "holdout_target_hit": holdout_target_hit,
        "live_gate_passed": live_gate_passed,
        "tickers": tickers,
        "benchmark_ticker": benchmark_ticker,
        "start_date": str(args.start_date),
        "end_date": str(args.end_date),
        "holdout_start_date": str(args.holdout_start_date),
        "warmup_days": int(args.warmup_days),
        "feature_params": _feature_kwargs(),
        "strategy_params": {
            key: value
            for key, value in strategy_kwargs.items()
            if key != "activation_time_utc"
        },
        "shared_account": asdict(metrics),
        "benchmarks": {
            benchmark_ticker: {
                "return_pct": benchmark_return_pct,
                "mean_monthly_return_pct": benchmark_monthly_mean_pct,
            },
            "equal_weight_universe": {
                "return_pct": universe_bh_return_pct,
                "mean_monthly_return_pct": universe_bh_monthly_mean_pct,
            },
        },
        "isolated_backtesting_py": isolated_summary,
        "recent_trades": [asdict(trade) for trade in shared.trades[-25:]],
    }
    _write_json_summary(str(args.output_json), summary)
    if str(args.output_json).strip():
        print(f"\nSummary JSON: {Path(str(args.output_json)).expanduser().resolve()}")

    return 0 if full_target_hit else 2


if __name__ == "__main__":
    raise SystemExit(main())
