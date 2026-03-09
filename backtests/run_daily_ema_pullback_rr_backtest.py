#!/usr/bin/env python3
"""Daily EMA pullback swing strategy backtest (long-only, multi-ticker).

This script is intentionally explicit and transparent:
- Fetches daily OHLCV with YahooMarketProvider
- Calculates indicators with pandas_ta
- Runs a bar-by-bar loop with one global open position max
- Sizes each trade to risk 1% of current equity
- Exits on TP / SL or close-below-SMA200 rule
"""
from __future__ import annotations

import asyncio
import math
from datetime import timedelta
from dataclasses import dataclass
from typing import Optional

import httpx
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import pandas_ta as ta

from common.models.period import Period
from common.settings import settings
from pullers.market.yahoo.yahoo_market_provider import YahooMarketProvider


# =========================
# User-configurable params
# =========================
TICKERS = ["A", "MSFT", "NVDA", "AMZN", "META", "TSLA", "GOOGL", "NFLX", "AMD", "PLTR"]  # Add more symbols here, e.g. ["AAPL", "MSFT", "NVDA"]
START_DATE = "2018-01-01"
END_DATE = "2026-03-01"
INITIAL_CAPITAL = 30000
RISK_PER_TRADE = 0.02
MAX_EQUITY_ALLOC_PER_TRADE = 1.0 / 3.0
RR_RATIO = 6.0
COMMISSION = 1.0
BENCHMARK_TICKER = "SPY"

# Strategy and model parameters
SMA_LENGTH = 200
EMA_FAST_LENGTH = 20
EMA_SLOW_LENGTH = 50
ATR_LENGTH = 14
ATR_STOP_MULTIPLIER = 1.5
EMA_TOUCH_BUFFER = 1.015
USE_RECENT_SWING_LOW = False
SWING_LOOKBACK = 10
ASSUME_SL_FIRST_IF_BOTH_HIT = True
INDICATOR_WARMUP_DAYS = 300

# Output controls
SHOW_TRADE_ROWS = 20
PLOT_EQUITY = True


@dataclass
class SignalInfo:
    """Container for a valid entry signal."""

    ema_name: str
    ema_value: float
    pattern: str


@dataclass
class OpenTrade:
    """State of the currently open trade."""

    ticker: str
    entry_date: pd.Timestamp
    entry_price: float
    shares: int
    stop_price: float
    take_profit: float
    entry_pattern: str
    entry_ema: str
    pending_exit_next_open: bool = False
    pending_exit_reason: str = ""


@dataclass(frozen=True)
class EntryCandidate:
    """Potential entry on current date, used for unbiased ticker selection."""

    ticker: str
    entry_date: pd.Timestamp
    entry_price: float
    stop_price: float
    take_profit: float
    shares: int
    signal_pattern: str
    ema_touched: str
    ema_distance_pct: float


def _unique_tickers(tickers: list[str]) -> list[str]:
    """Normalize ticker list while preserving order."""
    out: list[str] = []
    seen: set[str] = set()
    for raw in tickers:
        ticker = str(raw).strip().upper()
        if ticker and ticker not in seen:
            out.append(ticker)
            seen.add(ticker)
    return out


def fetch_daily_data(
    ticker: str,
    start_date: str,
    end_date: str,
    warmup_days: int = 0,
) -> pd.DataFrame:
    """Fetch daily OHLCV using internal YahooMarketProvider and normalize schema."""

    start_ts_raw = pd.to_datetime(start_date, errors="raise", utc=True).to_pydatetime()
    fetch_start_ts = start_ts_raw - timedelta(days=max(0, int(warmup_days)))
    end_ts = pd.to_datetime(end_date, errors="raise", utc=True).to_pydatetime() + timedelta(days=1)

    async def _fetch() -> pd.DataFrame:
        client = httpx.AsyncClient(
            timeout=settings.http.timeout,
            follow_redirects=settings.http.follow_redirects,
            limits=httpx.Limits(
                max_connections=settings.http.max_connections,
                max_keepalive_connections=settings.http.max_keepalive_connections,
            ),
        )
        provider = YahooMarketProvider(http_client=client)
        try:
            return await provider.get_prices(
                ticker=ticker,
                start_time=fetch_start_ts,
                end_time=end_ts,
                period=Period.DAILY,
            )
        finally:
            await client.aclose()

    raw = asyncio.run(_fetch())
    if raw is None or raw.empty:
        raise ValueError(f"No data downloaded for {ticker} via YahooMarketProvider.")

    rename_map = {
        "open": "Open",
        "high": "High",
        "low": "Low",
        "close": "Close",
        "volume": "Volume",
    }
    df = raw.rename(columns=rename_map)
    required = ["Open", "High", "Low", "Close", "Volume"]
    if not all(col in df.columns for col in required):
        raise ValueError(f"Provider data missing required OHLCV columns for {ticker}.")

    out = df[required].copy()
    for col in required:
        out[col] = pd.to_numeric(out[col], errors="coerce")

    out = out.dropna(subset=required)
    index = pd.to_datetime(out.index, utc=True, errors="coerce")
    if isinstance(index, pd.DatetimeIndex):
        out.index = index.tz_convert(None)
    out = out[~out.index.isna()].sort_index()
    out = out[~out.index.duplicated(keep="last")]
    if warmup_days <= 0:
        start_cutoff = pd.to_datetime(start_date, errors="raise").tz_localize(None)
        out = out[out.index >= start_cutoff]
    if out.empty:
        raise ValueError(f"Data for {ticker} is empty after cleaning.")
    return out


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Compute all required indicators using pandas_ta."""
    out = df.copy()
    out["SMA200"] = ta.sma(out["Close"], length=SMA_LENGTH)
    out["EMA20"] = ta.ema(out["Close"], length=EMA_FAST_LENGTH)
    out["EMA50"] = ta.ema(out["Close"], length=EMA_SLOW_LENGTH)
    out["ATR14"] = ta.atr(out["High"], out["Low"], out["Close"], length=ATR_LENGTH)
    return out


def fetch_data_for_tickers(tickers: list[str]) -> dict[str, pd.DataFrame]:
    """Fetch and prepare all configured strategy tickers."""
    data_by_ticker: dict[str, pd.DataFrame] = {}
    start_cutoff = pd.to_datetime(START_DATE, errors="raise").tz_localize(None)
    for ticker in tickers:
        try:
            raw = fetch_daily_data(
                ticker=ticker,
                start_date=START_DATE,
                end_date=END_DATE,
                warmup_days=INDICATOR_WARMUP_DAYS,
            )
            prepared = add_indicators(raw)
            prepared = prepared[prepared.index >= start_cutoff]
            if prepared.empty:
                print(f"Skipping {ticker}: no bars after start cutoff.")
                continue
            data_by_ticker[ticker] = prepared
        except Exception as exc:
            print(f"Skipping {ticker}: {exc}")

    if not data_by_ticker:
        raise ValueError("No strategy ticker data was loaded.")
    return data_by_ticker


def is_hammer(open_price: float, high_price: float, low_price: float, close_price: float) -> bool:
    """Hammer rule: lower wick >= 2x body and upper wick <= 0.3x body."""
    body = abs(close_price - open_price)
    safe_body = max(body, 1e-12)
    lower_wick = min(open_price, close_price) - low_price
    upper_wick = high_price - max(open_price, close_price)
    return (lower_wick >= 2.0 * safe_body) and (upper_wick <= 0.3 * safe_body)


def is_bullish_engulfing(
    prev_open: float,
    prev_close: float,
    curr_open: float,
    curr_close: float,
) -> bool:
    """Bullish engulfing: current green body fully engulfs previous body."""
    prev_red = prev_close < prev_open
    curr_green = curr_close > curr_open
    body_engulfs = (curr_open <= prev_close) and (curr_close >= prev_open)
    return prev_red and curr_green and body_engulfs


def resolve_pullback_ema(low_price: float, ema20: float, ema50: float) -> tuple[Optional[str], Optional[float]]:
    """Return first touched EMA in a pullback (higher EMA touched first)."""
    touched: list[tuple[str, float]] = []

    if np.isfinite(ema20) and low_price <= ema20 * EMA_TOUCH_BUFFER:
        touched.append(("EMA20", float(ema20)))
    if np.isfinite(ema50) and low_price <= ema50 * EMA_TOUCH_BUFFER:
        touched.append(("EMA50", float(ema50)))

    if not touched:
        return None, None

    touched.sort(key=lambda item: item[1], reverse=True)
    return touched[0]


def most_recent_swing_low(df: pd.DataFrame, bar_index: int, lookback: int) -> float:
    """Approximate recent swing low from rolling lookback window."""
    start = max(0, bar_index - lookback + 1)
    return float(df["Low"].iloc[start : bar_index + 1].min())


def generate_signal(df: pd.DataFrame, bar_index: int) -> Optional[SignalInfo]:
    """Generate long signal for a single bar."""
    row = df.iloc[bar_index]

    close_price = float(row["Close"])
    if not np.isfinite(row["SMA200"]) or close_price <= float(row["SMA200"]):
        return None

    ema_name, ema_value = resolve_pullback_ema(
        low_price=float(row["Low"]),
        ema20=float(row["EMA20"]) if np.isfinite(row["EMA20"]) else np.nan,
        ema50=float(row["EMA50"]) if np.isfinite(row["EMA50"]) else np.nan,
    )
    if ema_name is None or ema_value is None:
        return None

    hammer = is_hammer(
        open_price=float(row["Open"]),
        high_price=float(row["High"]),
        low_price=float(row["Low"]),
        close_price=close_price,
    )

    bullish_engulfing = False
    if bar_index > 0:
        prev = df.iloc[bar_index - 1]
        bullish_engulfing = is_bullish_engulfing(
            prev_open=float(prev["Open"]),
            prev_close=float(prev["Close"]),
            curr_open=float(row["Open"]),
            curr_close=close_price,
        )

    strong_bullish = (close_price > float(row["Open"])) and (close_price > ema_value)

    if hammer:
        return SignalInfo(ema_name=ema_name, ema_value=ema_value, pattern="hammer")
    if bullish_engulfing:
        return SignalInfo(ema_name=ema_name, ema_value=ema_value, pattern="bullish_engulfing")
    if strong_bullish:
        return SignalInfo(ema_name=ema_name, ema_value=ema_value, pattern="strong_bullish_close")
    return None


def _pattern_priority(pattern: str) -> int:
    priorities = {
        "bullish_engulfing": 3,
        "hammer": 2,
        "strong_bullish_close": 1,
    }
    return priorities.get(pattern, 0)


def _ema_priority(ema_name: str) -> int:
    priorities = {
        "EMA20": 2,
        "EMA50": 1,
    }
    return priorities.get(ema_name, 0)


def _pick_best_candidate(candidates: list[EntryCandidate]) -> EntryCandidate:
    """Pick best candidate without ticker-list order bias."""
    return sorted(
        candidates,
        key=lambda c: (
            -_pattern_priority(c.signal_pattern),
            -_ema_priority(c.ema_touched),
            c.ema_distance_pct,
            c.ticker,
        ),
    )[0]


def calculate_position_size(equity: float, entry_price: float, stop_price: float) -> int:
    """Risk-based sizing with a hard cash cap (no implicit leverage)."""
    risk_per_share = entry_price - stop_price
    if risk_per_share <= 0 or entry_price <= 0:
        return 0

    dollars_to_risk = equity * RISK_PER_TRADE
    shares_by_risk = math.floor(dollars_to_risk / risk_per_share)
    max_cash_available = max(0.0, (equity * MAX_EQUITY_ALLOC_PER_TRADE) - COMMISSION)
    shares_by_cash = math.floor(max_cash_available / entry_price)
    return max(0, min(shares_by_risk, 100000))


def _close_trade(
    open_trade: OpenTrade,
    exit_date: pd.Timestamp,
    exit_price: float,
    reason: str,
    equity_before_exit: float,
) -> tuple[float, dict[str, object]]:
    """Close an open trade and return updated equity plus trade record."""
    gross_pnl = (exit_price - open_trade.entry_price) * open_trade.shares
    net_pnl = gross_pnl - COMMISSION
    new_equity = equity_before_exit + net_pnl
    invested = open_trade.entry_price * open_trade.shares
    pnl_pct = (net_pnl / invested) * 100.0 if invested > 0 else 0.0

    trade_record = {
        "ticker": open_trade.ticker,
        "entry_date": open_trade.entry_date,
        "exit_date": exit_date,
        "entry_price": open_trade.entry_price,
        "exit_price": exit_price,
        "shares": open_trade.shares,
        "pnl": net_pnl,
        "pnl_pct": pnl_pct,
        "reason": reason,
        "signal_pattern": open_trade.entry_pattern,
        "ema_touched": open_trade.entry_ema,
    }
    return new_equity, trade_record


def simulate_trades_multi(
    data_by_ticker: dict[str, pd.DataFrame],
    ticker_priority: list[str],
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, int]]:
    """Bar-by-bar simulation across multiple tickers with one global open trade."""
    active_tickers = [ticker for ticker in ticker_priority if ticker in data_by_ticker]
    all_dates = sorted(
        {
            dt
            for ticker in active_tickers
            for dt in data_by_ticker[ticker].index
        }
    )
    if not all_dates:
        return pd.DataFrame(), pd.DataFrame()

    date_to_pos: dict[str, dict[pd.Timestamp, int]] = {
        ticker: {ts: i for i, ts in enumerate(data_by_ticker[ticker].index)}
        for ticker in active_tickers
    }

    equity = float(INITIAL_CAPITAL)
    open_trade: Optional[OpenTrade] = None
    trades: list[dict[str, object]] = []
    equity_points: list[dict[str, object]] = []
    last_close_by_ticker: dict[str, float] = {}
    diagnostics: dict[str, int] = {
        "entry_candidates": 0,
        "selected_entries": 0,
        "candidates_rejected_by_selection": 0,
        "multi_candidate_days": 0,
        "blocked_entry_candidates_while_open": 0,
    }

    for current_date in all_dates:
        for ticker in active_tickers:
            pos = date_to_pos[ticker].get(current_date)
            if pos is not None:
                last_close_by_ticker[ticker] = float(data_by_ticker[ticker].iloc[pos]["Close"])

        if open_trade and open_trade.pending_exit_next_open:
            df_open = data_by_ticker[open_trade.ticker]
            pos_open = date_to_pos[open_trade.ticker].get(current_date)
            if pos_open is not None:
                open_price = float(df_open.iloc[pos_open]["Open"])
                equity, trade = _close_trade(
                    open_trade=open_trade,
                    exit_date=current_date,
                    exit_price=open_price,
                    reason=open_trade.pending_exit_reason,
                    equity_before_exit=equity,
                )
                trades.append(trade)
                open_trade = None

        if open_trade:
            df_pos = data_by_ticker[open_trade.ticker]
            pos = date_to_pos[open_trade.ticker].get(current_date)
            if pos is not None:
                row = df_pos.iloc[pos]
                high_price = float(row["High"])
                low_price = float(row["Low"])
                close_price = float(row["Close"])
                sma200 = float(row["SMA200"]) if np.isfinite(row["SMA200"]) else np.nan

                sl_hit = low_price <= open_trade.stop_price
                tp_hit = high_price >= open_trade.take_profit

                if sl_hit and tp_hit:
                    if ASSUME_SL_FIRST_IF_BOTH_HIT:
                        exit_price = open_trade.stop_price
                        reason = "sl_hit_same_bar_tp_hit"
                    else:
                        exit_price = open_trade.take_profit
                        reason = "tp_hit_same_bar_sl_hit"
                    equity, trade = _close_trade(
                        open_trade=open_trade,
                        exit_date=current_date,
                        exit_price=exit_price,
                        reason=reason,
                        equity_before_exit=equity,
                    )
                    trades.append(trade)
                    open_trade = None
                elif sl_hit:
                    equity, trade = _close_trade(
                        open_trade=open_trade,
                        exit_date=current_date,
                        exit_price=open_trade.stop_price,
                        reason="sl_hit",
                        equity_before_exit=equity,
                    )
                    trades.append(trade)
                    open_trade = None
                elif tp_hit:
                    equity, trade = _close_trade(
                        open_trade=open_trade,
                        exit_date=current_date,
                        exit_price=open_trade.take_profit,
                        reason="tp_hit",
                        equity_before_exit=equity,
                    )
                    trades.append(trade)
                    open_trade = None
                elif np.isfinite(sma200) and close_price < sma200:
                    if pos < len(df_pos) - 1:
                        open_trade.pending_exit_next_open = True
                        open_trade.pending_exit_reason = "close_below_sma200_next_open"
                    else:
                        equity, trade = _close_trade(
                            open_trade=open_trade,
                            exit_date=current_date,
                            exit_price=close_price,
                            reason="close_below_sma200_last_bar_close",
                            equity_before_exit=equity,
                        )
                        trades.append(trade)
                        open_trade = None

        if open_trade is None:
            candidates: list[EntryCandidate] = []
            for ticker in active_tickers:
                df = data_by_ticker[ticker]
                pos = date_to_pos[ticker].get(current_date)
                if pos is None:
                    continue

                row = df.iloc[pos]
                signal = generate_signal(df, pos)
                atr_value = float(row["ATR14"]) if np.isfinite(row["ATR14"]) else np.nan
                if not signal or not np.isfinite(atr_value) or atr_value <= 0:
                    continue

                signal_low = float(row["Low"])
                if USE_RECENT_SWING_LOW:
                    signal_low = min(
                        signal_low,
                        most_recent_swing_low(df=df, bar_index=pos, lookback=SWING_LOOKBACK),
                    )

                stop_price = signal_low - (ATR_STOP_MULTIPLIER * atr_value)
                entry_price = float(row["Close"])
                if stop_price >= entry_price:
                    continue

                shares = calculate_position_size(
                    equity=equity,
                    entry_price=entry_price,
                    stop_price=stop_price,
                )
                if shares < 1:
                    continue

                risk_per_share = entry_price - stop_price
                take_profit = entry_price + (RR_RATIO * risk_per_share)
                ema_distance_pct = (
                    abs(entry_price - signal.ema_value) / entry_price if entry_price > 0 else float("inf")
                )
                candidates.append(
                    EntryCandidate(
                        ticker=ticker,
                        entry_date=current_date,
                        entry_price=entry_price,
                        stop_price=stop_price,
                        take_profit=take_profit,
                        shares=shares,
                        signal_pattern=signal.pattern,
                        ema_touched=signal.ema_name,
                        ema_distance_pct=ema_distance_pct,
                    )
                )

            diagnostics["entry_candidates"] += len(candidates)
            if len(candidates) > 1:
                diagnostics["multi_candidate_days"] += 1

            if candidates:
                chosen = _pick_best_candidate(candidates)
                diagnostics["selected_entries"] += 1
                diagnostics["candidates_rejected_by_selection"] += max(0, len(candidates) - 1)
                open_trade = OpenTrade(
                    ticker=chosen.ticker,
                    entry_date=chosen.entry_date,
                    entry_price=chosen.entry_price,
                    shares=chosen.shares,
                    stop_price=chosen.stop_price,
                    take_profit=chosen.take_profit,
                    entry_pattern=chosen.signal_pattern,
                    entry_ema=chosen.ema_touched,
                )
        else:
            # Visibility into how often one-open-trade rule blocks other entries.
            blocked = 0
            for ticker in active_tickers:
                if ticker == open_trade.ticker:
                    continue
                df = data_by_ticker[ticker]
                pos = date_to_pos[ticker].get(current_date)
                if pos is None:
                    continue

                row = df.iloc[pos]
                signal = generate_signal(df, pos)
                atr_value = float(row["ATR14"]) if np.isfinite(row["ATR14"]) else np.nan
                if not signal or not np.isfinite(atr_value) or atr_value <= 0:
                    continue

                signal_low = float(row["Low"])
                if USE_RECENT_SWING_LOW:
                    signal_low = min(
                        signal_low,
                        most_recent_swing_low(df=df, bar_index=pos, lookback=SWING_LOOKBACK),
                    )

                stop_price = signal_low - (ATR_STOP_MULTIPLIER * atr_value)
                entry_price = float(row["Close"])
                if stop_price >= entry_price:
                    continue

                shares = calculate_position_size(
                    equity=equity,
                    entry_price=entry_price,
                    stop_price=stop_price,
                )
                if shares > 0:
                    blocked += 1
            diagnostics["blocked_entry_candidates_while_open"] += blocked

        if open_trade:
            mark_price = last_close_by_ticker.get(open_trade.ticker, open_trade.entry_price)
            marked_equity = equity + ((mark_price - open_trade.entry_price) * open_trade.shares)
        else:
            marked_equity = equity
        equity_points.append({"date": current_date, "equity": marked_equity})

    if open_trade:
        df_final = data_by_ticker[open_trade.ticker]
        final_date = df_final.index[-1]
        final_close = float(df_final["Close"].iloc[-1])
        equity, trade = _close_trade(
            open_trade=open_trade,
            exit_date=final_date,
            exit_price=final_close,
            reason="end_of_data_close",
            equity_before_exit=equity,
        )
        trades.append(trade)
        if equity_points:
            equity_points[-1]["equity"] = equity

    trades_df = pd.DataFrame(trades)
    if not trades_df.empty:
        trades_df = trades_df.sort_values(["entry_date", "ticker"]).reset_index(drop=True)

    equity_df = pd.DataFrame(equity_points)
    if not equity_df.empty:
        equity_df = equity_df.set_index("date").sort_index()

    return trades_df, equity_df, diagnostics


def build_buy_and_hold_basket_equity(
    data_by_ticker: dict[str, pd.DataFrame],
    tickers: list[str],
    index: pd.DatetimeIndex,
) -> pd.Series:
    """Equal-weight buy-and-hold benchmark across the full ticker basket."""
    close_table = pd.DataFrame(index=index)
    for ticker in tickers:
        close_table[ticker] = data_by_ticker[ticker]["Close"].reindex(index)
    close_table = close_table.ffill()

    normalized = pd.DataFrame(index=index)
    for ticker in tickers:
        series = close_table[ticker].dropna()
        if series.empty:
            continue
        first_price = float(series.iloc[0])
        if first_price <= 0:
            continue
        normalized[ticker] = close_table[ticker] / first_price

    if normalized.empty:
        return pd.Series(INITIAL_CAPITAL, index=index, dtype=float)

    basket_norm = normalized.mean(axis=1, skipna=True)
    basket_equity = (INITIAL_CAPITAL * basket_norm).ffill().fillna(INITIAL_CAPITAL)
    return basket_equity


def build_single_buy_and_hold_equity(df: pd.DataFrame, index: pd.DatetimeIndex) -> pd.Series:
    """Single-symbol buy-and-hold equity curve."""
    close = df["Close"].reindex(index).ffill()
    valid = close.dropna()
    if valid.empty:
        return pd.Series(INITIAL_CAPITAL, index=index, dtype=float)

    first_price = float(valid.iloc[0])
    if first_price <= 0:
        return pd.Series(INITIAL_CAPITAL, index=index, dtype=float)

    equity = (INITIAL_CAPITAL * (close / first_price)).ffill().fillna(INITIAL_CAPITAL)
    return equity


def calculate_performance(trades_df: pd.DataFrame, equity_df: pd.DataFrame) -> dict[str, float]:
    """Compute full performance report metrics."""
    if equity_df.empty:
        final_equity = float(INITIAL_CAPITAL)
        max_drawdown_pct = 0.0
        cagr_pct = 0.0
    else:
        final_equity = float(equity_df["equity"].iloc[-1])
        running_max = equity_df["equity"].cummax()
        drawdown = (equity_df["equity"] / running_max) - 1.0
        max_drawdown_pct = abs(float(drawdown.min())) * 100.0

        total_days = (equity_df.index[-1] - equity_df.index[0]).days
        years = max(total_days / 365.25, 0.0)
        if years > 0 and final_equity > 0 and INITIAL_CAPITAL > 0:
            cagr_pct = ((final_equity / INITIAL_CAPITAL) ** (1.0 / years) - 1.0) * 100.0
        else:
            cagr_pct = 0.0

    total_return_pct = ((final_equity / INITIAL_CAPITAL) - 1.0) * 100.0 if INITIAL_CAPITAL > 0 else 0.0
    num_trades = int(len(trades_df))

    if num_trades == 0:
        return {
            "total_return_pct": total_return_pct,
            "cagr_pct": cagr_pct,
            "max_drawdown_pct": max_drawdown_pct,
            "win_rate_pct": 0.0,
            "profit_factor": 0.0,
            "num_trades": 0,
            "avg_win": 0.0,
            "avg_loss": 0.0,
            "expectancy": 0.0,
            "final_equity": final_equity,
        }

    wins = trades_df[trades_df["pnl"] > 0]
    losses = trades_df[trades_df["pnl"] < 0]

    win_rate_pct = (len(wins) / num_trades) * 100.0
    gross_profit = float(wins["pnl"].sum()) if not wins.empty else 0.0
    gross_loss = float(losses["pnl"].sum()) if not losses.empty else 0.0
    profit_factor = (gross_profit / abs(gross_loss)) if gross_loss < 0 else float("inf")

    avg_win = float(wins["pnl"].mean()) if not wins.empty else 0.0
    avg_loss = float(losses["pnl"].mean()) if not losses.empty else 0.0
    expectancy = float(trades_df["pnl"].mean())

    return {
        "total_return_pct": total_return_pct,
        "cagr_pct": cagr_pct,
        "max_drawdown_pct": max_drawdown_pct,
        "win_rate_pct": win_rate_pct,
        "profit_factor": profit_factor,
        "num_trades": num_trades,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "expectancy": expectancy,
        "final_equity": final_equity,
    }


def print_performance_report(metrics: dict[str, float], tickers: list[str]) -> None:
    """Print strategy-level performance summary."""
    print("\n" + "=" * 80)
    print(f"Backtest Report | {START_DATE} -> {END_DATE}")
    print("=" * 80)
    print(f"Strategy Tickers: {', '.join(tickers)}")
    print(f"Initial Capital : ${INITIAL_CAPITAL:,.2f}")
    print(f"Risk / Trade    : {RISK_PER_TRADE * 100:.2f}%")
    print(f"Max Alloc/Trade : {MAX_EQUITY_ALLOC_PER_TRADE * 100:.2f}% of equity")
    print(f"Final Equity    : ${metrics['final_equity']:,.2f}")
    print(f"Total Return    : {metrics['total_return_pct']:.2f}%")
    print(f"CAGR            : {metrics['cagr_pct']:.2f}%")
    print(f"Max Drawdown    : {metrics['max_drawdown_pct']:.2f}%")
    print(f"Win Rate        : {metrics['win_rate_pct']:.2f}%")
    print(f"Profit Factor   : {metrics['profit_factor']:.2f}")
    print(f"# Trades        : {int(metrics['num_trades'])}")
    print(f"Avg Win         : ${metrics['avg_win']:.2f}")
    print(f"Avg Loss        : ${metrics['avg_loss']:.2f}")
    print(f"Expectancy      : ${metrics['expectancy']:.2f} per trade")
    print("=" * 80)


def print_trade_table(trades_df: pd.DataFrame, max_rows: int = 20) -> None:
    """Print first N trades and a short summary."""
    if trades_df.empty:
        print("\nNo trades were generated.")
        return

    display = trades_df.copy()
    for col in ["entry_date", "exit_date"]:
        display[col] = pd.to_datetime(display[col]).dt.strftime("%Y-%m-%d")
    for col in ["entry_price", "exit_price", "pnl", "pnl_pct"]:
        display[col] = display[col].astype(float).round(4)

    print(f"\nTrade List (first {min(max_rows, len(display))} rows)")
    print("-" * 120)
    print(
        display[
            [
                "ticker",
                "entry_date",
                "exit_date",
                "entry_price",
                "exit_price",
                "shares",
                "pnl",
                "pnl_pct",
                "reason",
                "signal_pattern",
                "ema_touched",
            ]
        ]
        .head(max_rows)
        .to_string(index=False)
    )

    total_pnl = float(trades_df["pnl"].sum())
    wins = int((trades_df["pnl"] > 0).sum())
    losses = int((trades_df["pnl"] < 0).sum())
    breakeven = int((trades_df["pnl"] == 0).sum())

    print("-" * 120)
    print(
        f"Trade Summary: total={len(trades_df)}, wins={wins}, losses={losses}, "
        f"breakeven={breakeven}, net_pnl=${total_pnl:.2f}"
    )


def print_execution_diagnostics(diagnostics: dict[str, int]) -> None:
    """Print diagnostics for multi-ticker entry routing behavior."""
    print("\nExecution Diagnostics")
    print("-" * 80)
    print(f"Entry candidates found            : {diagnostics.get('entry_candidates', 0)}")
    print(f"Selected entries                 : {diagnostics.get('selected_entries', 0)}")
    print(f"Multi-candidate days             : {diagnostics.get('multi_candidate_days', 0)}")
    print(
        "Rejected by same-day selection   : "
        f"{diagnostics.get('candidates_rejected_by_selection', 0)}"
    )
    print(
        "Blocked while position already open: "
        f"{diagnostics.get('blocked_entry_candidates_while_open', 0)}"
    )
    print("-" * 80)


def plot_three_pnl_graphs(
    strategy_equity: pd.Series,
    basket_bh_equity: pd.Series,
    spy_bh_equity: pd.Series,
    tickers: list[str],
) -> None:
    """Plot all requested P&L lines on one shared chart for comparison."""
    basket_pnl = basket_bh_equity - INITIAL_CAPITAL
    strategy_pnl = strategy_equity - INITIAL_CAPITAL
    spy_pnl = spy_bh_equity - INITIAL_CAPITAL

    plt.figure(figsize=(14, 7))
    plt.plot(
        basket_pnl.index,
        basket_pnl.values,
        color="tab:blue",
        linewidth=1.8,
        label=f"Buy & Hold Basket ({', '.join(tickers)})",
    )
    plt.plot(
        strategy_pnl.index,
        strategy_pnl.values,
        color="tab:green",
        linewidth=2.0,
        label="Strategy P&L",
    )
    plt.plot(
        spy_pnl.index,
        spy_pnl.values,
        color="tab:orange",
        linewidth=1.8,
        label=f"{BENCHMARK_TICKER} Buy & Hold",
    )
    plt.axhline(0.0, color="black", linewidth=0.9, alpha=0.6)
    plt.title("P&L Comparison (Same Chart)")
    plt.xlabel("Date")
    plt.ylabel("P&L ($)")
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.tight_layout()
    plt.show()


def main() -> None:
    """Run full backtest pipeline."""
    tickers = _unique_tickers(TICKERS)
    if not tickers:
        raise ValueError("TICKERS is empty. Add at least one symbol.")

    data_by_ticker = fetch_data_for_tickers(tickers)
    active_tickers = [ticker for ticker in tickers if ticker in data_by_ticker]
    if not active_tickers:
        raise ValueError("No valid ticker data was loaded for strategy simulation.")

    trades_df, strategy_equity_df, diagnostics = simulate_trades_multi(data_by_ticker, active_tickers)
    metrics = calculate_performance(trades_df, strategy_equity_df)

    print_performance_report(metrics, active_tickers)
    print_trade_table(trades_df, max_rows=SHOW_TRADE_ROWS)
    print_execution_diagnostics(diagnostics)

    benchmark_df = fetch_daily_data(BENCHMARK_TICKER, START_DATE, END_DATE)
    master_index = strategy_equity_df.index.union(benchmark_df.index).sort_values()
    if master_index.empty:
        return

    strategy_equity = (
        strategy_equity_df["equity"].reindex(master_index).ffill().fillna(INITIAL_CAPITAL)
    )
    basket_bh_equity = build_buy_and_hold_basket_equity(
        data_by_ticker=data_by_ticker,
        tickers=active_tickers,
        index=master_index,
    )
    spy_bh_equity = build_single_buy_and_hold_equity(df=benchmark_df, index=master_index)

    if PLOT_EQUITY:
        plot_three_pnl_graphs(
            strategy_equity=strategy_equity,
            basket_bh_equity=basket_bh_equity,
            spy_bh_equity=spy_bh_equity,
            tickers=active_tickers,
        )


if __name__ == "__main__":
    main()
