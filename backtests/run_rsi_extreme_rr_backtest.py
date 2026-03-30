#!/usr/bin/env python3
"""Run hourly RSI extreme strategy in isolated per-ticker mode."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from pathlib import Path
import sys

VENV_EXEC_HINT = (
    "Use the project virtualenv, e.g. "
    "`.venv/bin/python backtests/run_rsi_extreme_rr_backtest.py --start-date 2025-01-01 --end-date 2025-12-31 --no-plot`."
)


def _missing_dependency_message(package_name: str) -> str:
    return f"Missing dependency `{package_name}`. {VENV_EXEC_HINT}"


try:
    import pandas as pd
except ImportError as exc:  # pragma: no cover - runtime dependency gate
    raise SystemExit(_missing_dependency_message("pandas")) from exc

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROOT_PATH = str(PROJECT_ROOT)
if ROOT_PATH not in sys.path:
    sys.path.insert(0, ROOT_PATH)

from backtests.backtesting_py.isolated_backtest_engine import (
    build_single_buy_and_hold_equity,
    fetch_ohlcv_for_tickers_sync,
    filter_regular_session,
    parse_date_range_utc,
    plot_isolated_ticker_candlestick_trade_markers,
    plot_isolated_ticker_equity_curves,
    print_symbol_stats,
    resolve_tickers,
    run_isolated_backtests_from_data,
)
from backtests.backtesting_py.rsi_extreme_rr_strategy import RsiExtremeRRStrategy
from common.models.period import Period


DEFAULT_TICKERS: list[str] = [
    "AAPL",
    "MSFT",
    "NVDA",
    "AMZN",
    "META",
    "TSLA",
    "GOOGL",
]


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run isolated hourly RSI extreme strategy and compare to SPY buy-and-hold."
    )
    parser.add_argument(
        "--tickers",
        nargs="+",
        default=None,
        help="Ticker symbols (comma or space separated).",
    )
    parser.add_argument(
        "--start-date",
        type=str,
        required=True,
        help="Backtest start date (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--end-date",
        type=str,
        required=True,
        help="Backtest end date (YYYY-MM-DD, inclusive).",
    )
    parser.add_argument(
        "--initial-capital",
        type=float,
        default=10_000.0,
        help="Initial capital per ticker (full-capital isolated run).",
    )
    parser.add_argument(
        "--leverage",
        type=float,
        default=1.0,
        help="Backtest leverage.",
    )
    parser.add_argument(
        "--round-trip-commission",
        type=float,
        default=1.0,
        help="Fixed commission per completed trade pair (entry+exit).",
    )
    parser.add_argument(
        "--benchmark-ticker",
        type=str,
        default="SPY",
        help="Benchmark ticker (S&P 500 proxy).",
    )
    parser.add_argument(
        "--rsi-period",
        type=int,
        default=14,
        help="RSI lookback period.",
    )
    parser.add_argument(
        "--rsi-oversold",
        type=float,
        default=10.0,
        help="Long trigger threshold: RSI < oversold.",
    )
    parser.add_argument(
        "--rsi-overbought",
        type=float,
        default=90.0,
        help="Short trigger threshold: RSI > overbought.",
    )
    parser.add_argument(
        "--stop-loss-pct",
        type=float,
        default=0.02,
        help="Fixed stop-loss fraction used as 1R.",
    )
    parser.add_argument(
        "--risk-reward-ratio",
        type=float,
        default=3.0,
        help="Take-profit multiple of risk (TP = R * this value).",
    )
    parser.add_argument(
        "--trade-direction",
        choices=["Both", "Long Only", "Short Only"],
        default="Both",
        help="Direction filter.",
    )
    parser.add_argument(
        "--no-plot",
        action="store_true",
        help="Disable equity chart output.",
    )
    parser.add_argument(
        "--warmup-days",
        type=int,
        default=90,
        help="Calendar days fetched before --start-date for indicator/model warmup.",
    )
    return parser.parse_args(argv)


def _resolve_date_range(args: argparse.Namespace) -> tuple[datetime, datetime]:
    try:
        return parse_date_range_utc(
            start_date=str(args.start_date),
            end_date=str(args.end_date),
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


def _validate_backtest_inputs(
    *,
    start_time: datetime,
    end_time_exclusive: datetime,
    initial_capital: float,
    leverage: float,
    round_trip_commission: float,
    rsi_period: int,
    rsi_oversold: float,
    rsi_overbought: float,
    stop_loss_pct: float,
    risk_reward_ratio: float,
    warmup_days: int = 0,
) -> None:
    if start_time >= end_time_exclusive:
        raise SystemExit("Invalid date range. Start must be earlier than end.")
    if initial_capital <= 0:
        raise SystemExit("Invalid --initial-capital. Value must be > 0.")
    if leverage <= 0:
        raise SystemExit("Invalid --leverage. Value must be > 0.")
    if round_trip_commission < 0:
        raise SystemExit("Invalid --round-trip-commission. Value must be >= 0.")
    if rsi_period < 2:
        raise SystemExit("Invalid --rsi-period. Value must be >= 2.")
    if rsi_oversold >= rsi_overbought:
        raise SystemExit("Invalid RSI thresholds. --rsi-oversold must be < --rsi-overbought.")
    if stop_loss_pct <= 0:
        raise SystemExit("Invalid --stop-loss-pct. Value must be > 0.")
    if risk_reward_ratio <= 0:
        raise SystemExit("Invalid --risk-reward-ratio. Value must be > 0.")
    if warmup_days < 0:
        raise SystemExit("Invalid --warmup-days. Value must be >= 0.")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    start_time, end_time_exclusive = _resolve_date_range(args)
    _validate_backtest_inputs(
        start_time=start_time,
        end_time_exclusive=end_time_exclusive,
        initial_capital=float(args.initial_capital),
        leverage=float(args.leverage),
        round_trip_commission=float(args.round_trip_commission),
        rsi_period=int(args.rsi_period),
        rsi_oversold=float(args.rsi_oversold),
        rsi_overbought=float(args.rsi_overbought),
        stop_loss_pct=float(args.stop_loss_pct),
        risk_reward_ratio=float(args.risk_reward_ratio),
        warmup_days=int(args.warmup_days),
    )

    tickers = resolve_tickers(args.tickers, default_tickers=DEFAULT_TICKERS)
    benchmark_ticker = str(args.benchmark_ticker).strip().upper() or "SPY"
    initial_capital = float(args.initial_capital)
    leverage = float(args.leverage)
    commission_per_side = max(0.0, float(args.round_trip_commission) / 2.0)
    warmup_days = max(0, int(args.warmup_days))
    fetch_start_time = start_time - timedelta(days=warmup_days)
    start_time_utc_naive = pd.Timestamp(start_time).tz_convert("UTC").tz_localize(None)

    print("=" * 96)
    print("RSI EXTREME RR BACKTEST (ISOLATED TICKER RUNS, HOURLY)")
    print("=" * 96)
    print(f"Tickers: {', '.join(tickers)}")
    print(f"Benchmark: {benchmark_ticker}")
    print(
        f"Date range: {start_time.date()} to {(end_time_exclusive - pd.Timedelta(days=1)).date()} | "
        "Interval: hour | Session: regular (09:30-16:00 ET)"
    )
    print(
        f"Warmup: {warmup_days} day(s) | "
        f"Fetch start: {fetch_start_time.date()}"
    )
    print(
        f"Initial capital=${initial_capital:,.2f} | Leverage={leverage:.2f}x | "
        f"Commission=${float(args.round_trip_commission):.2f}/pair"
    )
    print(
        f"RSI period={int(args.rsi_period)} | Oversold<{float(args.rsi_oversold):.2f} | "
        f"Overbought>{float(args.rsi_overbought):.2f} | "
        f"SL={float(args.stop_loss_pct):.4f} | RR={float(args.risk_reward_ratio):.2f}"
    )
    print("=" * 96)

    fetch_tickers = list(tickers)
    if benchmark_ticker not in fetch_tickers:
        fetch_tickers.append(benchmark_ticker)

    fetched_data = fetch_ohlcv_for_tickers_sync(
        tickers=fetch_tickers,
        start_time=fetch_start_time,
        end_time=end_time_exclusive,
        period=Period.HOUR,
    )
    if not fetched_data:
        print("No data fetched for any ticker.")
        return 1

    data_by_ticker: dict[str, pd.DataFrame] = {}
    for ticker in tickers:
        df = fetched_data.get(ticker, pd.DataFrame())
        if df is None or df.empty:
            continue
        filtered = filter_regular_session(df)
        if not filtered.empty and (filtered.index >= start_time_utc_naive).any():
            data_by_ticker[ticker] = filtered
    if not data_by_ticker:
        print("No strategy-ticker data fetched after session filtering.")
        return 1

    benchmark_df = fetched_data.get(benchmark_ticker, pd.DataFrame())
    if benchmark_df is not None and not benchmark_df.empty:
        benchmark_df = filter_regular_session(benchmark_df)
        benchmark_df = benchmark_df[benchmark_df.index >= start_time_utc_naive]

    strategy_kwargs = {
        "trade_direction": str(args.trade_direction),
        "rsi_period": int(args.rsi_period),
        "rsi_oversold": float(args.rsi_oversold),
        "rsi_overbought": float(args.rsi_overbought),
        "stop_loss_pct": float(args.stop_loss_pct),
        "risk_reward_ratio": float(args.risk_reward_ratio),
        "use_full_equity_sizing": True,
        "full_equity_fraction": 1.0,
        "use_limit_entry": False,
        "activation_time_utc": start_time.isoformat(),
    }
    stats_by_ticker, equity_by_ticker = run_isolated_backtests_from_data(
        data_by_ticker=data_by_ticker,
        strategy_cls=RsiExtremeRRStrategy,
        strategy_kwargs=strategy_kwargs,
        initial_capital=initial_capital,
        leverage=leverage,
        commission_per_side=commission_per_side,
        print_symbol_results=True,
    )
    if not stats_by_ticker:
        print("No standalone ticker results to report.")
        return 1

    for ticker, equity in list(equity_by_ticker.items()):
        if equity is None or equity.empty:
            continue
        trimmed = equity[equity.index >= start_time_utc_naive]
        if not trimmed.empty:
            equity_by_ticker[ticker] = trimmed

    ranked = sorted(
        stats_by_ticker.items(),
        key=lambda item: float(item[1].get("Return [%]", 0.0)),
        reverse=True,
    )
    print("\n" + "=" * 96)
    print("STANDALONE TICKER SUMMARY")
    print("=" * 96)
    for ticker, stats in ranked:
        print_symbol_stats(ticker, stats)
    print("=" * 96)

    master_index = pd.DatetimeIndex([])
    for equity in equity_by_ticker.values():
        if equity is not None and not equity.empty:
            master_index = master_index.union(pd.DatetimeIndex(equity.index))
    if benchmark_df is not None and not benchmark_df.empty:
        master_index = master_index.union(pd.DatetimeIndex(benchmark_df.index))
    master_index = master_index.sort_values()

    benchmark_bh_equity: pd.Series | None = None
    if benchmark_df is not None and not benchmark_df.empty and not master_index.empty:
        benchmark_bh_equity = build_single_buy_and_hold_equity(
            df=benchmark_df,
            index=master_index,
            initial_capital=initial_capital,
        )
        benchmark_return_pct = (
            ((float(benchmark_bh_equity.iloc[-1]) / initial_capital) - 1.0) * 100.0
            if initial_capital > 0.0
            else 0.0
        )
        print(f"{benchmark_ticker} Buy&Hold: {benchmark_return_pct:+.2f}%")
    else:
        print(f"{benchmark_ticker} Buy&Hold: unavailable (no benchmark data)")

    if args.no_plot:
        print("Comparison chart: skipped (--no-plot).")
        return 0

    if master_index.empty:
        print("Comparison chart: skipped (empty index).")
        return 0

    plot_isolated_ticker_equity_curves(
        equity_by_ticker=equity_by_ticker,
        initial_capital=initial_capital,
        benchmark_bh_equity=benchmark_bh_equity,
        benchmark_ticker=benchmark_ticker,
    )
    strategy_chart_plotted = plot_isolated_ticker_candlestick_trade_markers(
        data_by_ticker={
            ticker: frame[frame.index >= start_time_utc_naive]
            for ticker, frame in data_by_ticker.items()
        },
        stats_by_ticker=stats_by_ticker,
        title="RSI Strategy Candlesticks with Long/Short/Sell/Cover",
    )
    if not strategy_chart_plotted:
        print(
            "Candlestick marker chart: skipped "
            "(no executed trades in strategy period)."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
