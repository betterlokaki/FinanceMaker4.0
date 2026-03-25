#!/usr/bin/env python3
"""Run the tuned Big7 EMA+slope long/short strategy with $1 pair commission."""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

VENV_EXEC_HINT = (
    "Use the project virtualenv, e.g. "
    "`.venv/bin/python backtests/run_big7_last_year_ema_slope_backtest.py --no-plot`."
)


def _missing_dependency_message(package_name: str) -> str:
    return f"Missing dependency `{package_name}`. {VENV_EXEC_HINT}"


try:
    import httpx
except ImportError as exc:  # pragma: no cover - runtime dependency gate
    raise SystemExit(_missing_dependency_message("httpx")) from exc

try:
    import matplotlib.pyplot as plt
except ImportError as exc:  # pragma: no cover - runtime dependency gate
    raise SystemExit(_missing_dependency_message("matplotlib")) from exc

try:
    import pandas as pd
except ImportError as exc:  # pragma: no cover - runtime dependency gate
    raise SystemExit(_missing_dependency_message("pandas")) from exc

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
from backtests.backtesting_py.portfolio_orchestrator import (
    SharedPortfolioResult,
    run_shared_capital_portfolio,
)
from common.models.period import Period
from common.settings import settings
from pullers.market.yahoo.yahoo_market_provider import YahooMarketProvider

try:
    from backtesting import Backtest
except ImportError as exc:  # pragma: no cover - runtime dependency gate
    raise SystemExit(_missing_dependency_message("backtesting")) from exc


DEFAULT_TICKERS: list[str] = [
  "NVDA",
  
]
NY_TZ = ZoneInfo("America/New_York")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
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
        default=475,
        help="Lookback window in days from now.",
    )
    parser.add_argument(
        "--period",
        choices=["minute", "hour", "daily"],
        default="hour",
        help="Data interval.",
    )
    parser.add_argument(
        "--regular-session-only",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "If enabled for intraday periods, keep only regular US session "
            "(09:30-16:00 ET)."
        ),
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
        default=1.0,
        help="Leverage used in both per-symbol and shared-portfolio execution.",
    )
    parser.add_argument(
        "--notional-per-trade",
        type=float,
        default=10_000.0,
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
        default=24,
        help="Bars used for EMA slope direction check.",
    )
    parser.add_argument(
        "--band",
        type=float,
        default=0.016,
        help="Dead-zone band around EMA (fraction).",
    )
    parser.add_argument(
        "--stop-loss-pct",
        type=float,
        default=0.0,
        help="Fixed stop loss fraction (live parity).",
    )
    parser.add_argument(
        "--take-profit-pct",
        type=float,
        default=0.12,
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
        "--short-borrow-fee-apr",
        type=float,
        default=0.03,
        help="Annualized borrow fee applied to short positions in shared portfolio.",
    )
    parser.add_argument(
        "--compounding-position-sizing",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "If enabled, each new trade is sized from current portfolio cash "
            "(no leverage increase; capped by shared capacity)."
        ),
    )
    parser.add_argument(
        "--position-size-cash-fraction",
        type=float,
        default=0.85,
        help="Fraction of current portfolio cash allocated to each new entry (0-1).",
    )
    parser.add_argument(
        "--run-mode",
        choices=["shared", "isolated"],
        default="shared",
        help=(
            "shared: one shared-capital portfolio across all tickers. "
            "isolated: run each ticker independently and compare standalone equity curves."
        ),
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
    parser.add_argument(
        "--target-return-pct",
        type=float,
        default=170.0,
        help="Target portfolio return percentage used for HIT/MISS status.",
    )
    return parser.parse_args(argv)


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


def _validate_backtest_inputs(
    *,
    lookback_days: int,
    initial_capital: float,
    leverage: float,
    notional_per_trade: float,
    target_return_pct: float,
    run_mode: str = "shared",
) -> None:
    if lookback_days < 1:
        raise SystemExit("Invalid --lookback-days. Value must be >= 1.")
    if initial_capital <= 0:
        raise SystemExit("Invalid --initial-capital. Value must be > 0.")
    if leverage <= 0:
        raise SystemExit("Invalid --leverage. Value must be > 0.")
    if notional_per_trade <= 0:
        raise SystemExit("Invalid --notional-per-trade. Value must be > 0.")
    if target_return_pct <= 0:
        raise SystemExit("Invalid --target-return-pct. Value must be > 0.")

    buying_power = initial_capital * leverage
    if run_mode == "shared" and notional_per_trade > buying_power:
        raise SystemExit(
            "Invalid sizing: --notional-per-trade "
            f"(${notional_per_trade:,.2f}) exceeds available buying power "
            f"(${buying_power:,.2f} = --initial-capital * --leverage). "
            "Lower --notional-per-trade or increase --initial-capital/--leverage."
        )


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


def _build_equity_series_from_stats(
    stats: pd.Series,
    fallback_index: pd.DatetimeIndex,
    initial_capital: float,
) -> pd.Series:
    equity_curve = stats.get("_equity_curve", pd.DataFrame())
    if not isinstance(equity_curve, pd.DataFrame) or equity_curve.empty:
        return pd.Series(initial_capital, index=fallback_index, dtype=float)
    if "Equity" not in equity_curve.columns:
        return pd.Series(initial_capital, index=fallback_index, dtype=float)

    values = pd.to_numeric(equity_curve["Equity"], errors="coerce")
    series = pd.Series(values.values, index=pd.DatetimeIndex(equity_curve.index), dtype=float)
    series = series.dropna()
    if series.empty:
        return pd.Series(initial_capital, index=fallback_index, dtype=float)
    series = series[~series.index.duplicated(keep="last")].sort_index()
    return series.reindex(fallback_index).ffill().fillna(initial_capital)


def _plot_isolated_ticker_equity_curves(
    *,
    equity_by_ticker: dict[str, pd.Series],
    initial_capital: float,
    benchmark_bh_equity: pd.Series | None = None,
    benchmark_ticker: str = "SPY",
) -> None:
    if not equity_by_ticker:
        return

    plt.figure(figsize=(14, 7))
    for ticker in sorted(equity_by_ticker):
        equity = equity_by_ticker[ticker]
        if equity.empty:
            continue
        plt.plot(
            equity.index,
            equity.values,
            linewidth=1.8,
            label=ticker,
        )

    if benchmark_bh_equity is not None and not benchmark_bh_equity.empty:
        plt.plot(
            benchmark_bh_equity.index,
            benchmark_bh_equity.values,
            color="black",
            linewidth=2.0,
            linestyle="--",
            label=f"{benchmark_ticker} Buy & Hold",
        )

    plt.axhline(initial_capital, color="black", linewidth=1.0, alpha=0.6, label="Initial Capital")
    plt.title("Standalone Strategy Equity by Ticker (All-In Sizing)")
    plt.xlabel("Date")
    plt.ylabel("Equity ($)")
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.tight_layout()
    plt.show()


def run_isolated_backtests_from_data(
    *,
    data_by_ticker: dict[str, pd.DataFrame],
    initial_capital: float,
    leverage: float,
    notional_per_trade: float,
    ema_period: int,
    slope_len: int,
    band: float,
    stop_loss_pct: float,
    take_profit_pct: float,
    trade_direction: str,
    commission_per_side: float,
    use_limit_entry: bool = False,
    close_on_neutral_signal: bool = True,
    print_symbol_results: bool = True,
) -> tuple[dict[str, pd.Series], dict[str, pd.Series]]:
    stats_by_ticker: dict[str, pd.Series] = {}
    equity_by_ticker: dict[str, pd.Series] = {}

    if print_symbol_results:
        print("\nPer-ticker standalone results (all-in sizing):")

    for ticker, df in data_by_ticker.items():
        if len(df) < 100:
            if print_symbol_results:
                print(f"{ticker:>6} | skipped (insufficient bars: {len(df)})")
            continue

        tick_size = infer_tick_size(df, fallback=0.01)
        bt = Backtest(
            data=df,
            strategy=Mag7EmaSlopeRegimeStrategy,
            cash=initial_capital,
            commission=make_commission_callable(
                commission_rate=0.0,
                tick_size=tick_size,
                slippage_ticks=0.0,
                fixed_commission_per_side=commission_per_side,
            ),
            margin=1.0 / leverage,
            trade_on_close=False,
            hedging=False,
            exclusive_orders=False,
            finalize_trades=True,
        )
        stats = bt.run(
            trade_direction=trade_direction,
            notional_per_trade=notional_per_trade,
            ema_period=ema_period,
            slope_len=slope_len,
            band=band,
            stop_loss_pct=max(0.0, stop_loss_pct),
            take_profit_pct=max(0.0, take_profit_pct),
            use_limit_entry=bool(use_limit_entry),
            close_on_neutral_signal=bool(close_on_neutral_signal),
            use_full_equity_sizing=True,
            full_equity_fraction=1.0,
        )

        stats_by_ticker[ticker] = stats
        equity_by_ticker[ticker] = _build_equity_series_from_stats(
            stats=stats,
            fallback_index=pd.DatetimeIndex(df.index).sort_values(),
            initial_capital=initial_capital,
        )

        if print_symbol_results:
            _print_symbol_stats(ticker, stats)

    return stats_by_ticker, equity_by_ticker


def run_shared_backtest_from_data(
    *,
    data_by_ticker: dict[str, pd.DataFrame],
    initial_capital: float,
    leverage: float,
    notional_per_trade: float,
    ema_period: int,
    slope_len: int,
    band: float,
    stop_loss_pct: float,
    take_profit_pct: float,
    trade_direction: str,
    commission_per_side: float,
    use_limit_entry: bool = True,
    close_on_neutral_signal: bool = False,
    use_compounding_position_sizing: bool = True,
    position_size_cash_fraction: float = 1.0,
    short_borrow_fee_apr: float = 0.03,
    print_symbol_results: bool = True,
) -> tuple[SharedPortfolioResult, dict[str, pd.DataFrame], dict[str, float]]:
    trades_by_ticker: dict[str, pd.DataFrame] = {}
    tick_size_by_ticker: dict[str, float] = {}

    if print_symbol_results:
        print("\nPer-symbol results:")

    for ticker, df in data_by_ticker.items():
        if len(df) < 100:
            if print_symbol_results:
                print(f"{ticker:>6} | skipped (insufficient bars: {len(df)})")
            continue

        tick_size = infer_tick_size(df, fallback=0.01)
        tick_size_by_ticker[ticker] = tick_size

        bt = Backtest(
            data=df,
            strategy=Mag7EmaSlopeRegimeStrategy,
            cash=initial_capital,
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
            trade_direction=trade_direction,
            notional_per_trade=notional_per_trade,
            ema_period=ema_period,
            slope_len=slope_len,
            band=band,
            stop_loss_pct=max(0.0, stop_loss_pct),
            take_profit_pct=max(0.0, take_profit_pct),
            use_limit_entry=bool(use_limit_entry),
            close_on_neutral_signal=bool(close_on_neutral_signal),
        )

        trades = stats.get("_trades", pd.DataFrame())
        if isinstance(trades, pd.DataFrame) and not trades.empty:
            trades_by_ticker[ticker] = trades

        if print_symbol_results:
            _print_symbol_stats(ticker, stats)

    shared = run_shared_capital_portfolio(
        trades_by_ticker=trades_by_ticker,
        portfolio_config=PortfolioConfig(
            initial_capital=initial_capital,
            max_leverage=leverage,
            commission_rate=0.0,
            slippage_ticks=0.0,
            fixed_commission_per_side=commission_per_side,
            default_tick_size=0.01,
            dynamic_position_sizing=bool(use_compounding_position_sizing),
            position_size_cash_fraction=max(0.0, float(position_size_cash_fraction)),
            short_borrow_fee_apr=max(0.0, float(short_borrow_fee_apr)),
        ),
        tick_size_by_ticker=tick_size_by_ticker,
    )
    return shared, trades_by_ticker, tick_size_by_ticker


def main() -> None:
    args = _parse_args()
    run_mode = str(args.run_mode).strip().lower()
    _validate_backtest_inputs(
        lookback_days=int(args.lookback_days),
        initial_capital=float(args.initial_capital),
        leverage=float(args.leverage),
        notional_per_trade=float(args.notional_per_trade),
        target_return_pct=float(args.target_return_pct),
        run_mode=run_mode,
    )
    tickers = _resolve_tickers(args.tickers)
    benchmark_ticker = args.benchmark_ticker.strip().upper() or "SPY"

    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=max(1, args.lookback_days))
    period = _period_from_arg(args.period)
    leverage = float(args.leverage)
    initial_capital = float(args.initial_capital)
    notional_per_trade = float(args.notional_per_trade)
    commission_per_side = max(0.0, float(args.round_trip_commission) / 2.0)
    short_borrow_fee_apr = max(0.0, float(args.short_borrow_fee_apr))
    position_size_cash_fraction = max(0.0, min(1.0, float(args.position_size_cash_fraction)))

    print("=" * 92)
    if run_mode == "isolated":
        print("BIG7 EMA+SLOPE REGIME BACKTEST (ISOLATED TICKER RUNS)")
    else:
        print("BIG7 EMA+SLOPE REGIME BACKTEST (LONG+SHORT, SHARED CAPITAL)")
    print("=" * 92)
    print(f"Tickers: {', '.join(tickers)}")
    print(f"Benchmark: {benchmark_ticker}")
    print(
        f"Date range: {start_time.date()} to {end_time.date()} | Interval: {period.value} | "
        f"Trade direction: {args.trade_direction}"
    )
    print(f"Regular session only: {bool(args.regular_session_only)}")
    if run_mode == "isolated":
        print(
            f"Initial capital=${initial_capital:,.2f} | Leverage={leverage:.2f}x | "
            "Standalone sizing=ALL-IN (100% each trade)"
        )
        print("Entry model: market entry | Exit model: close on neutral/opposite regime")
    else:
        print(
            f"Initial capital=${initial_capital:,.2f} | Leverage={leverage:.2f}x | "
            f"Notional/trade=${notional_per_trade:,.2f}"
        )
    print(
        f"EMA={args.ema_period} | slope_len={args.slope_len} | band={args.band:.4f} | "
        f"SL={args.stop_loss_pct:.4f} | TP={args.take_profit_pct:.4f} | "
        f"Commission=${args.round_trip_commission:.2f}/pair | "
        f"ShortBorrowAPR={short_borrow_fee_apr:.2%}"
    )
    print(
        f"CompoundingSizing={bool(args.compounding_position_sizing)} | "
        f"CashFraction/Trade={position_size_cash_fraction:.2f}"
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
    if bool(args.regular_session_only) and period in (Period.MINUTE, Period.HOUR):
        data_by_ticker = {
            ticker: _filter_regular_session(df)
            for ticker, df in data_by_ticker.items()
        }
        if benchmark_df is not None and not benchmark_df.empty:
            benchmark_df = _filter_regular_session(benchmark_df)

    if not data_by_ticker:
        print("No strategy-ticker data fetched.")
        return

    if run_mode == "isolated":
        stats_by_ticker, equity_by_ticker = run_isolated_backtests_from_data(
            data_by_ticker=data_by_ticker,
            initial_capital=initial_capital,
            leverage=leverage,
            notional_per_trade=notional_per_trade,
            ema_period=int(args.ema_period),
            slope_len=int(args.slope_len),
            band=float(args.band),
            stop_loss_pct=float(args.stop_loss_pct),
            take_profit_pct=float(args.take_profit_pct),
            trade_direction=args.trade_direction,
            commission_per_side=commission_per_side,
            print_symbol_results=True,
        )
        if not stats_by_ticker:
            print("No standalone ticker results to report.")
            return

        ranked = sorted(
            stats_by_ticker.items(),
            key=lambda item: float(item[1].get("Return [%]", 0.0)),
            reverse=True,
        )
        print("\n" + "=" * 92)
        print("STANDALONE TICKER SUMMARY")
        print("=" * 92)
        for ticker, stats in ranked:
            _print_symbol_stats(ticker, stats)
        print("=" * 92)

        if args.no_plot:
            print("Comparison chart: skipped (--no-plot).")
        else:
            master_index = pd.DatetimeIndex([])
            for equity in equity_by_ticker.values():
                if equity is not None and not equity.empty:
                    master_index = master_index.union(pd.DatetimeIndex(equity.index))
            if benchmark_df is not None and not benchmark_df.empty:
                master_index = master_index.union(pd.DatetimeIndex(benchmark_df.index))
            master_index = master_index.sort_values()

            benchmark_bh_equity = None
            if benchmark_df is not None and not benchmark_df.empty and not master_index.empty:
                benchmark_bh_equity = _build_single_buy_and_hold_equity(
                    df=benchmark_df,
                    index=master_index,
                    initial_capital=initial_capital,
                )

            _plot_isolated_ticker_equity_curves(
                equity_by_ticker=equity_by_ticker,
                initial_capital=initial_capital,
                benchmark_bh_equity=benchmark_bh_equity,
                benchmark_ticker=benchmark_ticker,
            )
        return

    shared, _, _ = run_shared_backtest_from_data(
        data_by_ticker=data_by_ticker,
        initial_capital=initial_capital,
        leverage=leverage,
        notional_per_trade=notional_per_trade,
        ema_period=int(args.ema_period),
        slope_len=int(args.slope_len),
        band=float(args.band),
        stop_loss_pct=float(args.stop_loss_pct),
        take_profit_pct=float(args.take_profit_pct),
        trade_direction=args.trade_direction,
        commission_per_side=commission_per_side,
        use_compounding_position_sizing=bool(args.compounding_position_sizing),
        position_size_cash_fraction=position_size_cash_fraction,
        short_borrow_fee_apr=short_borrow_fee_apr,
        print_symbol_results=True,
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
            initial_capital=initial_capital,
        )
        basket_bh_equity = _build_buy_and_hold_basket_equity(
            data_by_ticker=data_by_ticker,
            tickers=tickers,
            index=master_index,
            initial_capital=initial_capital,
        )
        if benchmark_df is not None and not benchmark_df.empty:
            benchmark_bh_equity = _build_single_buy_and_hold_equity(
                df=benchmark_df,
                index=master_index,
                initial_capital=initial_capital,
            )
        else:
            benchmark_bh_equity = pd.Series(
                initial_capital, index=master_index, dtype=float
            )

        basket_return_pct = (
            ((float(basket_bh_equity.iloc[-1]) / initial_capital) - 1.0) * 100.0
            if initial_capital > 0
            else 0.0
        )
        benchmark_return_pct = (
            ((float(benchmark_bh_equity.iloc[-1]) / initial_capital) - 1.0) * 100.0
            if initial_capital > 0
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
                initial_capital=initial_capital,
                tickers=tickers,
                benchmark_ticker=benchmark_ticker,
            )
    else:
        print("Comparison chart: skipped (empty index).")

    target = float(args.target_return_pct)
    status = "HIT" if shared.total_return_pct >= target else "MISS"
    print(f"Target {target:.2f}% => {status}")


if __name__ == "__main__":
    main()
