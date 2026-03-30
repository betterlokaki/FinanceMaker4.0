"""Reusable plotting helpers for strategy candlesticks and trade markers."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Sequence

import pandas as pd


@dataclass(frozen=True)
class CandlestickTradeMarker:
    """Normalized trade data used for candlestick marker overlays."""

    ticker: str
    direction: str
    entry_time: pd.Timestamp | datetime
    exit_time: pd.Timestamp | datetime
    entry_price: float
    exit_price: float


def _normalize_to_naive_utc_timestamp(value: pd.Timestamp | datetime) -> pd.Timestamp | None:
    ts = pd.Timestamp(value)
    if pd.isna(ts):
        return None
    if ts.tzinfo is not None:
        return ts.tz_convert("UTC").tz_localize(None)
    return ts


def _normalize_direction(value: Any) -> str | None:
    direction = str(value).strip().title()
    if direction in ("Long", "Short"):
        return direction
    return None


def trade_markers_from_shared_executed_trades(
    executed_trades: Sequence[Any],
) -> tuple[CandlestickTradeMarker, ...]:
    """Convert shared-portfolio executed trades into plot marker payload."""
    markers: list[CandlestickTradeMarker] = []
    for trade in executed_trades:
        ticker = str(getattr(trade, "ticker", "")).strip().upper()
        direction = _normalize_direction(getattr(trade, "direction", ""))
        entry_price = float(getattr(trade, "entry_price", 0.0) or 0.0)
        exit_price = float(getattr(trade, "exit_price", 0.0) or 0.0)
        entry_time = _normalize_to_naive_utc_timestamp(getattr(trade, "entry_time", pd.NaT))
        exit_time = _normalize_to_naive_utc_timestamp(getattr(trade, "exit_time", pd.NaT))
        if (
            not ticker
            or direction is None
            or entry_time is None
            or exit_time is None
            or entry_price <= 0.0
            or exit_price <= 0.0
            or exit_time < entry_time
        ):
            continue
        markers.append(
            CandlestickTradeMarker(
                ticker=ticker,
                direction=direction,
                entry_time=entry_time,
                exit_time=exit_time,
                entry_price=entry_price,
                exit_price=exit_price,
            )
        )
    return tuple(markers)


def trade_markers_from_backtesting_trades(
    *,
    ticker: str,
    trades: pd.DataFrame,
) -> tuple[CandlestickTradeMarker, ...]:
    """Convert backtesting.py `_trades` frame rows into marker payload."""
    if trades is None or trades.empty:
        return tuple()

    required_columns = ("EntryTime", "ExitTime", "EntryPrice", "ExitPrice", "Size")
    if not all(column in trades.columns for column in required_columns):
        return tuple()

    ticker_key = str(ticker).strip().upper()
    if not ticker_key:
        return tuple()

    markers: list[CandlestickTradeMarker] = []
    for _, row in trades.iterrows():
        entry_time = _normalize_to_naive_utc_timestamp(row.get("EntryTime", pd.NaT))
        exit_time = _normalize_to_naive_utc_timestamp(row.get("ExitTime", pd.NaT))
        entry_price = float(row.get("EntryPrice", 0.0) or 0.0)
        exit_price = float(row.get("ExitPrice", 0.0) or 0.0)
        size = int(float(row.get("Size", 0.0) or 0.0))
        direction = "Long" if size > 0 else "Short"
        if (
            entry_time is None
            or exit_time is None
            or entry_price <= 0.0
            or exit_price <= 0.0
            or size == 0
            or exit_time < entry_time
        ):
            continue
        markers.append(
            CandlestickTradeMarker(
                ticker=ticker_key,
                direction=direction,
                entry_time=entry_time,
                exit_time=exit_time,
                entry_price=entry_price,
                exit_price=exit_price,
            )
        )
    return tuple(markers)


def trade_markers_from_stats_by_ticker(
    stats_by_ticker: dict[str, pd.Series],
) -> tuple[CandlestickTradeMarker, ...]:
    """Extract marker payload from per-ticker backtesting.py stats objects."""
    markers: list[CandlestickTradeMarker] = []
    for ticker, stats in stats_by_ticker.items():
        trades = stats.get("_trades", pd.DataFrame()) if isinstance(stats, pd.Series) else pd.DataFrame()
        if isinstance(trades, pd.DataFrame) and not trades.empty:
            markers.extend(trade_markers_from_backtesting_trades(ticker=ticker, trades=trades))
    return tuple(markers)


def _prepare_ohlc_frame(df: pd.DataFrame) -> pd.DataFrame:
    required_cols = ("Open", "High", "Low", "Close")
    if not all(col in df.columns for col in required_cols):
        return pd.DataFrame(columns=required_cols)

    frame = df.loc[:, list(required_cols)].copy()
    frame.index = pd.to_datetime(frame.index, errors="coerce")
    frame = frame[~frame.index.isna()]
    if isinstance(frame.index, pd.DatetimeIndex) and frame.index.tz is not None:
        frame.index = frame.index.tz_convert("UTC").tz_localize(None)

    for column in required_cols:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=list(required_cols)).sort_index()
    return frame


def _candlestick_body_width(*, width_factor: float = 0.92) -> float:
    return max(0.1, min(1.0, float(width_factor)))


def _nearest_index_position(index: pd.DatetimeIndex, timestamp: pd.Timestamp) -> int | None:
    if index.empty:
        return None
    if timestamp < index[0] or timestamp > index[-1]:
        return None

    pos = int(index.searchsorted(timestamp, side="left"))
    if pos <= 0:
        return 0
    if pos >= len(index):
        return len(index) - 1

    prev_pos = pos - 1
    prev_distance = abs(int((index[prev_pos] - timestamp).value))
    next_distance = abs(int((index[pos] - timestamp).value))
    return prev_pos if prev_distance <= next_distance else pos


def _apply_compressed_time_ticks(ax, index: pd.DatetimeIndex, max_ticks: int = 8) -> None:
    if index.empty:
        return

    tick_count = max(1, min(int(max_ticks), len(index)))
    if tick_count == 1:
        positions = [0]
    else:
        step = (len(index) - 1) / float(tick_count - 1)
        positions = [int(round(i * step)) for i in range(tick_count)]
        positions = list(dict.fromkeys(positions))

    has_intraday = any((index[pos].hour != 0 or index[pos].minute != 0) for pos in positions)
    labels = [
        index[pos].strftime("%m-%d %H:%M") if has_intraday else index[pos].strftime("%Y-%m-%d")
        for pos in positions
    ]
    ax.set_xticks(positions)
    ax.set_xticklabels(labels, rotation=30, ha="right")


def plot_candlestick_trade_markers(
    *,
    data_by_ticker: dict[str, pd.DataFrame],
    trade_markers: Sequence[CandlestickTradeMarker],
    title: str = "Strategy Candlesticks with Long/Short/Sell/Cover",
    marker_size: float = 55.0,
    candle_width_factor: float = 0.92,
    min_body_fraction: float = 0.04,
) -> bool:
    """Plot candlesticks with long/short entry and sell/cover exit markers."""
    if not trade_markers:
        return False

    try:
        import matplotlib.pyplot as plt
        from matplotlib.patches import Rectangle
    except ImportError as exc:  # pragma: no cover - runtime dependency gate
        raise RuntimeError("Missing dependency `matplotlib`.") from exc

    strategy_start: pd.Timestamp | None = None
    strategy_end: pd.Timestamp | None = None
    markers_by_ticker: dict[str, list[CandlestickTradeMarker]] = {}
    for marker in trade_markers:
        entry_time = _normalize_to_naive_utc_timestamp(marker.entry_time)
        exit_time = _normalize_to_naive_utc_timestamp(marker.exit_time)
        if entry_time is None or exit_time is None:
            continue

        if strategy_start is None or entry_time < strategy_start:
            strategy_start = entry_time
        if strategy_end is None or exit_time > strategy_end:
            strategy_end = exit_time

        ticker = str(marker.ticker).strip().upper()
        if ticker:
            markers_by_ticker.setdefault(ticker, []).append(marker)

    if strategy_start is None or strategy_end is None or not markers_by_ticker:
        return False

    tickers = sorted(ticker for ticker in markers_by_ticker if ticker in data_by_ticker)
    if not tickers:
        return False

    plot_frames: dict[str, pd.DataFrame] = {}
    for ticker in tickers:
        frame = _prepare_ohlc_frame(data_by_ticker[ticker])
        if frame.empty:
            continue
        strategy_frame = frame.loc[(frame.index >= strategy_start) & (frame.index <= strategy_end)]
        if strategy_frame.empty:
            continue
        plot_frames[ticker] = strategy_frame

    if not plot_frames:
        return False

    tickers = sorted(plot_frames.keys())
    ncols = 1 if len(tickers) <= 3 else 2
    nrows = (len(tickers) + ncols - 1) // ncols

    max_bars = max(len(frame) for frame in plot_frames.values()) if plot_frames else 0
    fig_width = max(16.0, min(42.0, 8.0 + (float(max_bars) / 45.0)))
    fig, axes = plt.subplots(
        nrows=nrows,
        ncols=ncols,
        figsize=(fig_width, 4.2 * nrows),
        squeeze=False,
    )
    flat_axes = [axis for row in axes for axis in row]

    for idx, ticker in enumerate(tickers):
        ax = flat_axes[idx]
        frame = plot_frames[ticker]
        frame_index = pd.DatetimeIndex(frame.index)
        x = list(range(len(frame)))
        candle_width = _candlestick_body_width(width_factor=candle_width_factor)
        median_span = float((frame["High"] - frame["Low"]).median())
        min_body_scale = max(0.0, min(0.2, float(min_body_fraction)))
        min_body = max(1e-6, median_span * min_body_scale) if median_span > 0 else 1e-6

        for xpos, row in zip(x, frame.itertuples(index=False)):
            open_price = float(row.Open)
            high_price = float(row.High)
            low_price = float(row.Low)
            close_price = float(row.Close)

            is_up_candle = close_price >= open_price
            candle_color = "#2e8b57" if is_up_candle else "#c0392b"

            ax.vlines(xpos, low_price, high_price, color=candle_color, linewidth=0.9, alpha=0.9)

            body_low = min(open_price, close_price)
            body_height = abs(close_price - open_price)
            if body_height < min_body:
                body_low -= (min_body - body_height) / 2.0
                body_height = min_body

            ax.add_patch(
                Rectangle(
                    (xpos - (candle_width / 2.0), body_low),
                    candle_width,
                    body_height,
                    facecolor=candle_color,
                    edgecolor=candle_color,
                    linewidth=0.8,
                    alpha=0.95,
                )
            )

        long_x: list[float] = []
        long_y: list[float] = []
        short_x: list[float] = []
        short_y: list[float] = []
        sell_x: list[float] = []
        sell_y: list[float] = []
        cover_x: list[float] = []
        cover_y: list[float] = []

        for marker in markers_by_ticker[ticker]:
            entry_time = _normalize_to_naive_utc_timestamp(marker.entry_time)
            exit_time = _normalize_to_naive_utc_timestamp(marker.exit_time)
            if entry_time is None or exit_time is None:
                continue
            if entry_time < strategy_start or exit_time > strategy_end:
                continue

            entry_pos = _nearest_index_position(frame_index, entry_time)
            exit_pos = _nearest_index_position(frame_index, exit_time)
            if entry_pos is None or exit_pos is None:
                continue

            if marker.direction == "Long":
                long_x.append(float(entry_pos))
                long_y.append(float(marker.entry_price))
                sell_x.append(float(exit_pos))
                sell_y.append(float(marker.exit_price))
            else:
                short_x.append(float(entry_pos))
                short_y.append(float(marker.entry_price))
                cover_x.append(float(exit_pos))
                cover_y.append(float(marker.exit_price))

        marker_size = max(12.0, float(marker_size))
        marker_edge = "black"
        marker_width = 0.9
        if long_x:
            ax.scatter(
                long_x,
                long_y,
                marker="^",
                s=marker_size,
                c="white",
                edgecolors=marker_edge,
                linewidths=marker_width,
                zorder=4,
                label="Long",
            )
        if short_x:
            ax.scatter(
                short_x,
                short_y,
                marker="v",
                s=marker_size,
                c="white",
                edgecolors=marker_edge,
                linewidths=marker_width,
                zorder=4,
                label="Short",
            )
        if sell_x:
            ax.scatter(
                sell_x,
                sell_y,
                marker="v",
                s=marker_size,
                c="red",
                edgecolors=marker_edge,
                linewidths=marker_width,
                zorder=5,
                label="Sell",
            )
        if cover_x:
            ax.scatter(
                cover_x,
                cover_y,
                marker="^",
                s=marker_size,
                c="red",
                edgecolors=marker_edge,
                linewidths=marker_width,
                zorder=5,
                label="Cover",
            )

        ax.set_title(f"{ticker} | Candles + Strategy Markers")
        ax.set_ylabel("Price ($)")
        ax.grid(True, alpha=0.25)
        ax.margins(x=0.002)
        ax.set_xlim(-0.5, max(0.5, float(len(frame) - 0.5)))
        _apply_compressed_time_ticks(ax, frame_index, max_ticks=8)

        handles, labels = ax.get_legend_handles_labels()
        if handles:
            unique: dict[str, object] = {}
            for handle, label in zip(handles, labels):
                if label not in unique:
                    unique[label] = handle
            ax.legend(unique.values(), unique.keys(), loc="best", fontsize=8)

    for idx in range(len(tickers), len(flat_axes)):
        fig.delaxes(flat_axes[idx])

    fig.suptitle(title, fontsize=14)
    fig.tight_layout(rect=(0.0, 0.0, 1.0, 0.97))
    plt.show()
    return True
