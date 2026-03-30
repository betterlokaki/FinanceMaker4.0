"""Reusable isolated backtest engine for multi-ticker strategy runs."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Any, Sequence
from zoneinfo import ZoneInfo

import httpx
import pandas as pd

from backtests.backtesting_py.cost_model import make_commission_callable
from backtests.backtesting_py.data_adapter import (
    fetch_ohlcv_from_yahoo_provider,
    infer_tick_size,
)
from common.models.period import Period
from common.settings import settings
from pullers.market.yahoo.yahoo_market_provider import YahooMarketProvider

NY_TZ = ZoneInfo("America/New_York")


def resolve_tickers(
    arg_values: list[str] | None,
    *,
    default_tickers: Sequence[str] | None = None,
) -> list[str]:
    """Normalize ticker list while preserving user order."""
    if not arg_values:
        return [str(item).strip().upper() for item in (default_tickers or []) if str(item).strip()]

    resolved: list[str] = []
    seen: set[str] = set()
    for raw in arg_values:
        for part in str(raw).split(","):
            ticker = part.strip().upper()
            if ticker and ticker not in seen:
                resolved.append(ticker)
                seen.add(ticker)

    if resolved:
        return resolved
    return [str(item).strip().upper() for item in (default_tickers or []) if str(item).strip()]


def _parse_date_utc(date_str: str) -> datetime:
    ts = pd.to_datetime(date_str, utc=True, errors="raise")
    if pd.isna(ts):
        raise ValueError(f"Invalid date: {date_str}")
    return ts.to_pydatetime()


def parse_date_range_utc(*, start_date: str, end_date: str) -> tuple[datetime, datetime]:
    """Resolve inclusive [start_date, end_date] into [start, end_exclusive) UTC."""
    start_raw = str(start_date).strip()
    end_raw = str(end_date).strip()
    if not start_raw:
        raise ValueError("Missing start date.")
    if not end_raw:
        raise ValueError("Missing end date.")

    start_time = _parse_date_utc(start_raw)
    end_time_exclusive = _parse_date_utc(end_raw) + timedelta(days=1)
    if start_time >= end_time_exclusive:
        raise ValueError("Resolved start date must be earlier than end date.")
    return start_time, end_time_exclusive


async def fetch_ohlcv_for_tickers(
    *,
    tickers: Sequence[str],
    start_time: datetime,
    end_time: datetime,
    period: Period,
) -> dict[str, pd.DataFrame]:
    """Fetch OHLCV data for all requested tickers."""
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


def fetch_ohlcv_for_tickers_sync(
    *,
    tickers: Sequence[str],
    start_time: datetime,
    end_time: datetime,
    period: Period,
) -> dict[str, pd.DataFrame]:
    """Synchronous wrapper around async data fetch."""
    return asyncio.run(
        fetch_ohlcv_for_tickers(
            tickers=tickers,
            start_time=start_time,
            end_time=end_time,
            period=period,
        )
    )


def filter_regular_session(df: pd.DataFrame) -> pd.DataFrame:
    """Keep only regular US market hours for intraday bars."""
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


def build_equity_series_from_stats(
    *,
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


def build_single_buy_and_hold_equity(
    *,
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


def print_symbol_stats(ticker: str, stats: pd.Series) -> None:
    trades = int(stats.get("# Trades", 0))
    ret = float(stats.get("Return [%]", 0.0))
    win = float(stats.get("Win Rate [%]", 0.0))
    equity = float(stats.get("Equity Final [$]", 0.0))
    print(
        f"{ticker:>6} | trades={trades:>4} | return={ret:>8.2f}% | "
        f"win={win:>6.2f}% | equity=${equity:>11.2f}"
    )


def run_isolated_backtests_from_data(
    *,
    data_by_ticker: dict[str, pd.DataFrame],
    strategy_cls: type,
    strategy_kwargs: dict[str, Any],
    initial_capital: float,
    leverage: float,
    commission_per_side: float,
    min_bars: int = 100,
    default_tick_size: float = 0.01,
    print_symbol_results: bool = True,
) -> tuple[dict[str, pd.Series], dict[str, pd.Series]]:
    """Run per-ticker isolated backtests for an arbitrary strategy class."""
    try:
        from backtesting import Backtest
    except ImportError as exc:  # pragma: no cover - runtime dependency gate
        raise RuntimeError("Missing dependency `backtesting`.") from exc

    stats_by_ticker: dict[str, pd.Series] = {}
    equity_by_ticker: dict[str, pd.Series] = {}

    if print_symbol_results:
        print("\nPer-ticker standalone results (all-in sizing):")

    for ticker, df in data_by_ticker.items():
        if len(df) < max(2, int(min_bars)):
            if print_symbol_results:
                print(f"{ticker:>6} | skipped (insufficient bars: {len(df)})")
            continue

        tick_size = infer_tick_size(df, fallback=default_tick_size)
        bt = Backtest(
            data=df,
            strategy=strategy_cls,
            cash=initial_capital,
            commission=make_commission_callable(
                commission_rate=0.0,
                tick_size=tick_size,
                slippage_ticks=0.0,
                fixed_commission_per_side=max(0.0, float(commission_per_side)),
            ),
            margin=1.0 / float(leverage),
            trade_on_close=False,
            hedging=False,
            exclusive_orders=False,
            finalize_trades=True,
        )
        stats = bt.run(**strategy_kwargs)
        stats_by_ticker[ticker] = stats
        equity_by_ticker[ticker] = build_equity_series_from_stats(
            stats=stats,
            fallback_index=pd.DatetimeIndex(df.index).sort_values(),
            initial_capital=initial_capital,
        )

        if print_symbol_results:
            print_symbol_stats(ticker, stats)

    return stats_by_ticker, equity_by_ticker


def plot_isolated_ticker_equity_curves(
    *,
    equity_by_ticker: dict[str, pd.Series],
    initial_capital: float,
    benchmark_bh_equity: pd.Series | None = None,
    benchmark_ticker: str = "SPY",
) -> None:
    """Plot isolated strategy equity per ticker and benchmark buy-and-hold."""
    if not equity_by_ticker:
        return

    import matplotlib.pyplot as plt

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


def plot_isolated_ticker_candlestick_trade_markers(
    *,
    data_by_ticker: dict[str, pd.DataFrame],
    stats_by_ticker: dict[str, pd.Series],
    title: str = "Strategy Candlesticks with Long/Short/Sell/Cover",
) -> bool:
    """Plot per-ticker candlesticks with long/short/sell/cover markers."""
    from backtests.backtesting_py.plotting import (
        plot_candlestick_trade_markers,
        trade_markers_from_stats_by_ticker,
    )

    return plot_candlestick_trade_markers(
        data_by_ticker=data_by_ticker,
        trade_markers=trade_markers_from_stats_by_ticker(stats_by_ticker),
        title=title,
    )
