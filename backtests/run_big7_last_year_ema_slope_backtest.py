#!/usr/bin/env python3
"""Run the tuned Big7 EMA+slope long/short strategy with $1 pair commission."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

import httpx
import matplotlib.pyplot as plt
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROOT_PATH = str(PROJECT_ROOT)
if ROOT_PATH not in sys.path:
    sys.path.insert(0, ROOT_PATH)

from backtests.backtesting_py.config import PortfolioConfig
from backtests.backtesting_py.cost_model import make_commission_callable
from backtests.backtesting_py.data_adapter import (
    fetch_ohlcv_from_yahoo_provider,
    infer_tick_size,
)
from backtests.backtesting_py.mag7_ema_slope_regime_strategy import (
    Mag7EmaSlopeRegimeStrategy,
)
from backtests.backtesting_py.portfolio_orchestrator import run_shared_capital_portfolio
from common.models.period import Period
from common.settings import settings
from pullers.market.yahoo.yahoo_market_provider import YahooMarketProvider

try:
    from backtesting import Backtest
except ImportError as exc:  # pragma: no cover - runtime dependency gate
    raise SystemExit(
        "Missing dependency `backtesting`. Install requirements first."
    ) from exc


DEFAULT_TICKERS: list[str] = [
    "AAPL",
    "MSFT",
    "NVDA",
    "AMZN",
    "META",
    "TSLA",
    "GOOGL",
]
NY_TZ = ZoneInfo("America/New_York")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run Big7 EMA+slope long/short strategy (last-year profile)."
    )
    parser.add_argument(
        "--tickers",
        nargs="+",
        default=None,
        help="Ticker symbols (comma or space separated). Defaults to Big 7.",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=60,
        help="Lookback window in days from now.",
    )
    parser.add_argument(
        "--period",
        choices=["minute", "hour", "daily"],
        default="hour",
        help="Data interval.",
    )
    parser.add_argument(
        "--initial-capital",
        type=float,
        default=10_000.0,
        help="Shared portfolio starting cash.",
    )
    parser.add_argument(
        "--leverage",
        type=float,
        default=6.0,
        help="Leverage used in both per-symbol and shared-portfolio execution.",
    )
    parser.add_argument(
        "--notional-per-trade",
        type=float,
        default=30_000.0,
        help="Target notional per position.",
    )
    parser.add_argument(
        "--ema-period",
        type=int,
        default=20,
        help="EMA lookback for regime filter.",
    )
    parser.add_argument(
        "--slope-len",
        type=int,
        default=36,
        help="Bars used for EMA slope direction check.",
    )
    parser.add_argument(
        "--band",
        type=float,
        default=0.0,
        help="Dead-zone band around EMA (fraction).",
    )
    parser.add_argument(
        "--stop-loss-pct",
        type=float,
        default=0.03,
        help="Fixed stop loss fraction (live parity).",
    )
    parser.add_argument(
        "--take-profit-pct",
        type=float,
        default=0.06,
        help="Fixed take profit fraction (live parity).",
    )
    parser.add_argument(
        "--trade-direction",
        choices=["Both", "Long Only", "Short Only"],
        default="Both",
        help="Direction filter.",
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
        help="Ticker to use for single-symbol buy-and-hold comparison.",
    )
    parser.add_argument(
        "--no-plot",
        action="store_true",
        help="Disable comparison chart display.",
    )
    return parser.parse_args()


def _resolve_tickers(arg_values: list[str] | None) -> list[str]:
    if not arg_values:
        return DEFAULT_TICKERS

    resolved: list[str] = []
    seen: set[str] = set()
    for raw in arg_values:
        for item in raw.split(","):
            ticker = item.strip().upper()
            if ticker and ticker not in seen:
                resolved.append(ticker)
                seen.add(ticker)
    return resolved or DEFAULT_TICKERS


def _period_from_arg(value: str) -> Period:
    if value == "minute":
        return Period.MINUTE
    if value == "daily":
        return Period.DAILY
    return Period.HOUR


async def _fetch_all_data(
    tickers: list[str],
    start_time: datetime,
    end_time: datetime,
    period: Period,
) -> dict[str, pd.DataFrame]:
    client = httpx.AsyncClient(
        timeout=settings.http.timeout,
        follow_redirects=settings.http.follow_redirects,
        limits=httpx.Limits(
            max_connections=settings.http.max_connections,
            max_keepalive_connections=settings.http.max_keepalive_connections,
        ),
    )
    provider = YahooMarketProvider(http_client=client)
    data: dict[str, pd.DataFrame] = {}
    try:
        for ticker in tickers:
            df = await fetch_ohlcv_from_yahoo_provider(
                provider=provider,
                ticker=ticker,
                start_time=start_time,
                end_time=end_time,
                period=period,
            )
            if not df.empty:
                data[ticker] = df
    finally:
        await client.aclose()
    return data


def _print_symbol_stats(ticker: str, stats: pd.Series) -> None:
    trades = int(stats.get("# Trades", 0))
    ret = float(stats.get("Return [%]", 0.0))
    win = float(stats.get("Win Rate [%]", 0.0))
    equity = float(stats.get("Equity Final [$]", 0.0))
    print(
        f"{ticker:>6} | trades={trades:>4} | return={ret:>8.2f}% | "
        f"win={win:>6.2f}% | equity=${equity:>11.2f}"
    )


def _build_strategy_equity_series(
    shared_equity_curve: tuple[tuple[pd.Timestamp, float], ...],
    index: pd.DatetimeIndex,
    initial_capital: float,
) -> pd.Series:
    if not shared_equity_curve:
        return pd.Series(initial_capital, index=index, dtype=float)

    points = pd.DataFrame(shared_equity_curve, columns=["time", "equity"])
    points["time"] = pd.to_datetime(points["time"], errors="coerce")
    points = points.dropna(subset=["time"]).sort_values("time")
    if points.empty:
        return pd.Series(initial_capital, index=index, dtype=float)

    # Multiple trades can update equity at the same timestamp.
    # Collapse duplicates to the latest equity snapshot to make reindex safe.
    strategy = (
        points.groupby("time", sort=True)["equity"]
        .last()
        .astype(float)
        .sort_index()
    )
    return strategy.reindex(index).ffill().fillna(initial_capital)


def _build_buy_and_hold_basket_equity(
    data_by_ticker: dict[str, pd.DataFrame],
    tickers: list[str],
    index: pd.DatetimeIndex,
    initial_capital: float,
) -> pd.Series:
    close_table = pd.DataFrame(index=index)
    for ticker in tickers:
        if ticker in data_by_ticker:
            close_table[ticker] = data_by_ticker[ticker]["Close"].reindex(index)
    close_table = close_table.ffill()

    normalized = pd.DataFrame(index=index)
    for ticker in close_table.columns:
        series = close_table[ticker].dropna()
        if series.empty:
            continue
        first_price = float(series.iloc[0])
        if first_price <= 0:
            continue
        normalized[ticker] = close_table[ticker] / first_price

    if normalized.empty:
        return pd.Series(initial_capital, index=index, dtype=float)

    basket_norm = normalized.mean(axis=1, skipna=True)
    return (initial_capital * basket_norm).ffill().fillna(initial_capital)


def _build_single_buy_and_hold_equity(
    df: pd.DataFrame,
    index: pd.DatetimeIndex,
    initial_capital: float,
) -> pd.Series:
    close = df["Close"].reindex(index).ffill()
    valid = close.dropna()
    if valid.empty:
        return pd.Series(initial_capital, index=index, dtype=float)

    first_price = float(valid.iloc[0])
    if first_price <= 0:
        return pd.Series(initial_capital, index=index, dtype=float)

    return (initial_capital * (close / first_price)).ffill().fillna(initial_capital)


def _filter_regular_session(df: pd.DataFrame) -> pd.DataFrame:
    """Match live strategy regular-session filtering for intraday bars."""
    if df.empty:
        return df
    index_utc = pd.to_datetime(df.index, utc=True, errors="coerce")
    valid = ~index_utc.isna()
    if not valid.any():
        return df.iloc[0:0].copy()

    frame = df.loc[valid].copy()
    frame.index = index_utc[valid].tz_convert(NY_TZ)
    frame = frame[frame.index.dayofweek < 5]
    frame = frame.between_time("09:30", "16:00", inclusive="left")
    if frame.empty:
        return frame

    frame.index = frame.index.tz_convert("UTC").tz_localize(None)
    return frame.sort_index()


def _plot_three_pnl_graphs(
    strategy_equity: pd.Series,
    basket_bh_equity: pd.Series,
    benchmark_bh_equity: pd.Series,
    initial_capital: float,
    tickers: list[str],
    benchmark_ticker: str,
) -> None:
    strategy_pnl = strategy_equity - initial_capital
    basket_pnl = basket_bh_equity - initial_capital
    benchmark_pnl = benchmark_bh_equity - initial_capital

    plt.figure(figsize=(14, 7))
    plt.plot(
        basket_pnl.index,
        basket_pnl.values,
        color="tab:blue",
        linewidth=1.8,
        label=f"Big7 Buy & Hold ({', '.join(tickers)})",
    )
    plt.plot(
        strategy_pnl.index,
        strategy_pnl.values,
        color="tab:green",
        linewidth=2.0,
        label="Strategy Portfolio P&L",
    )
    plt.plot(
        benchmark_pnl.index,
        benchmark_pnl.values,
        color="tab:orange",
        linewidth=1.8,
        label=f"{benchmark_ticker} Buy & Hold",
    )
    plt.axhline(0.0, color="black", linewidth=0.9, alpha=0.6)
    plt.title("P&L Comparison (Strategy vs Big7 B&H vs SPY B&H)")
    plt.xlabel("Date")
    plt.ylabel("P&L ($)")
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.tight_layout()
    plt.show()


def main() -> None:
    args = _parse_args()
    tickers = _resolve_tickers(args.tickers)
    benchmark_ticker = args.benchmark_ticker.strip().upper() or "SPY"

    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=max(1, args.lookback_days))
    period = _period_from_arg(args.period)
    leverage = max(1.0, float(args.leverage))
    commission_per_side = max(0.0, float(args.round_trip_commission) / 2.0)

    print("=" * 92)
    print("BIG7 EMA+SLOPE REGIME BACKTEST (LONG+SHORT, SHARED CAPITAL)")
    print("=" * 92)
    print(f"Tickers: {', '.join(tickers)}")
    print(f"Benchmark: {benchmark_ticker}")
    print(
        f"Date range: {start_time.date()} to {end_time.date()} | Interval: {period.value} | "
        f"Trade direction: {args.trade_direction}"
    )
    print(
        f"Initial capital=${args.initial_capital:,.2f} | Leverage={leverage:.2f}x | "
        f"Notional/trade=${args.notional_per_trade:,.2f}"
    )
    print(
        f"EMA={args.ema_period} | slope_len={args.slope_len} | band={args.band:.4f} | "
        f"SL={args.stop_loss_pct:.4f} | TP={args.take_profit_pct:.4f} | "
        f"Commission=${args.round_trip_commission:.2f}/pair"
    )
    print("=" * 92)

    fetch_tickers = list(tickers)
    if benchmark_ticker not in fetch_tickers:
        fetch_tickers.append(benchmark_ticker)

    fetched_data = asyncio.run(
        _fetch_all_data(
            tickers=fetch_tickers,
            start_time=start_time,
            end_time=end_time,
            period=period,
        )
    )
    if not fetched_data:
        print("No data fetched for any ticker.")
        return

    data_by_ticker = {ticker: fetched_data[ticker] for ticker in tickers if ticker in fetched_data}
    benchmark_df = fetched_data.get(benchmark_ticker, pd.DataFrame())
    if period in (Period.MINUTE, Period.HOUR):
        data_by_ticker = {
            ticker: _filter_regular_session(df)
            for ticker, df in data_by_ticker.items()
        }
        if benchmark_df is not None and not benchmark_df.empty:
            benchmark_df = _filter_regular_session(benchmark_df)

    if not data_by_ticker:
        print("No strategy-ticker data fetched.")
        return

    trades_by_ticker: dict[str, pd.DataFrame] = {}
    tick_size_by_ticker: dict[str, float] = {}

    print("\nPer-symbol results:")
    for ticker, df in data_by_ticker.items():
        if len(df) < 100:
            print(f"{ticker:>6} | skipped (insufficient bars: {len(df)})")
            continue

        tick_size = infer_tick_size(df, fallback=0.01)
        tick_size_by_ticker[ticker] = tick_size

        bt = Backtest(
            data=df,
            strategy=Mag7EmaSlopeRegimeStrategy,
            cash=float(args.initial_capital),
            commission=make_commission_callable(
                commission_rate=0.0,
                tick_size=tick_size,
                slippage_ticks=0.0,
                fixed_commission_per_side=0.0,
            ),
            margin=1.0 / leverage,
            trade_on_close=False,
            hedging=False,
            exclusive_orders=False,
            finalize_trades=True,
        )
        stats = bt.run(
            trade_direction=args.trade_direction,
            notional_per_trade=float(args.notional_per_trade),
            ema_period=int(args.ema_period),
            slope_len=int(args.slope_len),
            band=float(args.band),
            stop_loss_pct=max(0.0, float(args.stop_loss_pct)),
            take_profit_pct=max(0.0, float(args.take_profit_pct)),
            use_limit_entry=True,
        )

        trades = stats.get("_trades", pd.DataFrame())
        if isinstance(trades, pd.DataFrame) and not trades.empty:
            trades_by_ticker[ticker] = trades

        _print_symbol_stats(ticker, stats)

    shared = run_shared_capital_portfolio(
        trades_by_ticker=trades_by_ticker,
        portfolio_config=PortfolioConfig(
            initial_capital=float(args.initial_capital),
            max_leverage=leverage,
            commission_rate=0.0,
            slippage_ticks=0.0,
            fixed_commission_per_side=commission_per_side,
            default_tick_size=0.01,
        ),
        tick_size_by_ticker=tick_size_by_ticker,
    )

    long_trades = sum(1 for trade in shared.executed_trades if trade.direction == "Long")
    short_trades = sum(1 for trade in shared.executed_trades if trade.direction == "Short")

    print("\n" + "=" * 92)
    print("SHARED PORTFOLIO RESULT")
    print("=" * 92)
    print(f"Initial Capital: ${shared.initial_capital:,.2f}")
    print(f"Final Equity:    ${shared.final_equity:,.2f}")
    print(f"Return:          {shared.total_return_pct:+.2f}%")
    print(f"Max Drawdown:    {shared.max_drawdown_pct:.2f}%")
    print(f"Trades:          {shared.total_trades} (Long={long_trades}, Short={short_trades})")
    print(f"Wins / Losses:   {shared.winning_trades} / {shared.losing_trades}")
    print(f"Skipped Entries: {len(shared.skipped_trades)}")
    print("=" * 92)

    master_index = pd.DatetimeIndex([])
    for ticker in tickers:
        df = data_by_ticker.get(ticker)
        if df is not None and not df.empty:
            master_index = master_index.union(df.index)
    if benchmark_df is not None and not benchmark_df.empty:
        master_index = master_index.union(benchmark_df.index)
    if shared.equity_curve:
        master_index = master_index.union(
            pd.DatetimeIndex([timestamp for timestamp, _ in shared.equity_curve])
        )
    master_index = master_index.sort_values()

    if not master_index.empty:
        strategy_equity = _build_strategy_equity_series(
            shared_equity_curve=shared.equity_curve,
            index=master_index,
            initial_capital=float(args.initial_capital),
        )
        basket_bh_equity = _build_buy_and_hold_basket_equity(
            data_by_ticker=data_by_ticker,
            tickers=tickers,
            index=master_index,
            initial_capital=float(args.initial_capital),
        )
        if benchmark_df is not None and not benchmark_df.empty:
            benchmark_bh_equity = _build_single_buy_and_hold_equity(
                df=benchmark_df,
                index=master_index,
                initial_capital=float(args.initial_capital),
            )
        else:
            benchmark_bh_equity = pd.Series(
                float(args.initial_capital), index=master_index, dtype=float
            )

        basket_return_pct = (
            ((float(basket_bh_equity.iloc[-1]) / float(args.initial_capital)) - 1.0) * 100.0
            if float(args.initial_capital) > 0
            else 0.0
        )
        benchmark_return_pct = (
            ((float(benchmark_bh_equity.iloc[-1]) / float(args.initial_capital)) - 1.0) * 100.0
            if float(args.initial_capital) > 0
            else 0.0
        )
        print(f"Big7 Buy&Hold:   {basket_return_pct:+.2f}%")
        print(f"{benchmark_ticker} Buy&Hold: {benchmark_return_pct:+.2f}%")

        if args.no_plot:
            print("Comparison chart: skipped (--no-plot).")
        else:
            _plot_three_pnl_graphs(
                strategy_equity=strategy_equity,
                basket_bh_equity=basket_bh_equity,
                benchmark_bh_equity=benchmark_bh_equity,
                initial_capital=float(args.initial_capital),
                tickers=tickers,
                benchmark_ticker=benchmark_ticker,
            )
    else:
        print("Comparison chart: skipped (empty index).")

    target = 250.0
    status = "HIT" if shared.total_return_pct >= target else "MISS"
    print(f"Target {target:.2f}% => {status}")


if __name__ == "__main__":
    main()
