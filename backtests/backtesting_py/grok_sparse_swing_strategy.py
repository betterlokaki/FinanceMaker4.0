"""Sparse Grok-driven swing backtest engine (event-driven, no leakage)."""
from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd
from tqdm import tqdm

from gpt.grok.grok_base import GrokClient

SYSTEM_MESSAGE = (
    "You are a professional swing trader... "
    "You are extremely disciplined and never use future information."
)


@dataclass(frozen=True)
class SwingSetup:
    action: str
    entry_price: float | None
    stop_loss: float | None
    target_price: float | None
    confidence: int
    reasoning: str
    generated_on: pd.Timestamp
    raw_response: str


@dataclass(frozen=True)
class TradeRecord:
    entry_date: pd.Timestamp
    entry_price: float
    exit_date: pd.Timestamp
    exit_price: float
    shares: float
    pnl_dollars: float
    return_pct: float
    commission_dollars: float
    exit_reason: str
    grok_confidence: int
    grok_reasoning: str
    setup_generated_on: pd.Timestamp


@dataclass(frozen=True)
class SparseBacktestResult:
    ticker: str
    initial_capital: float
    final_equity: float
    total_return_pct: float
    win_rate_pct: float
    profit_factor: float
    max_drawdown_pct: float
    sharpe: float
    num_trades: int
    entries_triggered: int
    setup_buy_count: int
    setup_skip_count: int
    low_confidence_rejections: int
    grok_api_calls: int
    total_commission_dollars: float
    trades: tuple[TradeRecord, ...]
    trade_log_df: pd.DataFrame
    setup_log: tuple[dict[str, Any], ...]
    strategy_equity: pd.Series
    drawdown: pd.Series
    pending_untriggered_setup: SwingSetup | None


def _to_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_df_ohlcv(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        raise ValueError("Input DataFrame is empty.")

    out = df.copy()
    rename_map: dict[str, str] = {}
    for col in out.columns:
        key = str(col).strip().lower()
        if key == "open":
            rename_map[col] = "Open"
        elif key == "high":
            rename_map[col] = "High"
        elif key == "low":
            rename_map[col] = "Low"
        elif key == "close":
            rename_map[col] = "Close"
        elif key == "volume":
            rename_map[col] = "Volume"
    out = out.rename(columns=rename_map)

    required = ["Open", "High", "Low", "Close", "Volume"]
    if not all(col in out.columns for col in required):
        raise ValueError(f"Input DataFrame must include columns: {required}")

    out = out[required].copy()
    for col in required:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    out = out.dropna(subset=required)

    index = pd.to_datetime(out.index, utc=True, errors="coerce")
    valid = ~index.isna()
    if not valid.any():
        raise ValueError("Input DataFrame has no valid datetime index.")

    out = out.loc[valid].copy()
    out.index = index[valid].tz_convert(None)
    out = out.sort_index()
    out = out[~out.index.duplicated(keep="last")]

    if out.empty:
        raise ValueError("Input DataFrame is empty after normalization.")
    return out


def _try_parse_json_object(raw_response: str) -> dict[str, Any] | None:
    cleaned = (raw_response or "").strip()
    if not cleaned:
        return None

    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass

    fenced_blocks = re.findall(
        r"```(?:json)?\s*(.*?)```",
        cleaned,
        flags=re.IGNORECASE | re.DOTALL,
    )
    for block in fenced_blocks:
        try:
            parsed = json.loads(block.strip())
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue

    decoder = json.JSONDecoder()
    for idx, char in enumerate(cleaned):
        if char != "{":
            continue
        try:
            parsed, _ = decoder.raw_decode(cleaned[idx:])
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    return None


def _compute_rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    gains = delta.clip(lower=0)
    losses = (-delta).clip(lower=0)

    avg_gain = gains.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = losses.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()

    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    rsi = 100.0 - (100.0 / (1.0 + rs))
    rsi = rsi.where(~((avg_loss == 0) & (avg_gain > 0)), 100.0)
    rsi = rsi.where(~((avg_gain == 0) & (avg_loss > 0)), 0.0)
    return rsi.fillna(50.0)


def _describe_volume_trend(volume: pd.Series) -> str:
    series = pd.to_numeric(volume, errors="coerce").dropna()
    if len(series) < 40:
        return "insufficient history (<40 bars)"

    recent_avg = float(series.tail(20).mean())
    prior_avg = float(series.iloc[-40:-20].mean())
    if prior_avg <= 0:
        return "unavailable (invalid prior volume average)"

    change_pct = ((recent_avg / prior_avg) - 1.0) * 100.0
    if change_pct > 10.0:
        trend = "increasing"
    elif change_pct < -10.0:
        trend = "decreasing"
    else:
        trend = "stable"
    return f"{trend} ({change_pct:+.1f}% vs prior 20-day average)"


def _build_markdown_table(history_df: pd.DataFrame, table_candles: int) -> str:
    candles = history_df.tail(max(1, table_candles))
    lines = [
        "| Date | Open | High | Low | Close | Volume |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for ts, row in candles.iterrows():
        date_str = pd.Timestamp(ts).strftime("%Y-%m-%d")
        lines.append(
            f"| {date_str} | {float(row['Open']):.2f} | {float(row['High']):.2f} | "
            f"{float(row['Low']):.2f} | {float(row['Close']):.2f} | {int(round(float(row['Volume'])))} |"
        )
    return "\n".join(lines)


def _build_grok_prompt(history_df: pd.DataFrame, table_candles: int) -> str:
    current_date = pd.Timestamp(history_df.index[-1]).strftime("%Y-%m-%d")

    close = pd.to_numeric(history_df["Close"], errors="coerce")
    volume = pd.to_numeric(history_df["Volume"], errors="coerce")

    sma50 = float(close.rolling(50).mean().iloc[-1]) if len(close) >= 50 else np.nan
    sma200 = float(close.rolling(200).mean().iloc[-1]) if len(close) >= 200 else np.nan
    rsi14 = float(_compute_rsi(close, 14).iloc[-1]) if len(close) >= 14 else np.nan
    current_price = float(close.iloc[-1])
    volume_trend = _describe_volume_trend(volume)

    table = _build_markdown_table(history_df, table_candles=table_candles)

    return (
        f"System instruction: {SYSTEM_MESSAGE}\n\n"
        f"Current date: {current_date} (this is the LAST day you know about. You have NO future data. "
        "We are currently FLAT after previous trade or at start. Give us the next swing setup.)\n\n"
        "Last 150 daily candles up to current date:\n"
        f"{table}\n\n"
        "Technical summary:\n"
        f"- 50SMA: {sma50:.2f}\n"
        f"- 200SMA: {sma200:.2f}\n"
        f"- RSI(14): {rsi14:.2f}\n"
        f"- Current price: {current_price:.2f}\n"
        f"- Recent volume trend: {volume_trend}\n\n"
        "Return NOTHING except valid JSON in this exact schema:\n"
        "{\n"
        '  "action": "buy" | "skip",\n'
        '  "entry_price": number | null,\n'
        '  "stop_loss": number | null,\n'
        '  "target_price": number | null,\n'
        '  "confidence": integer 0-100,\n'
        '  "reasoning": "detailed explanation"\n'
        "}\n\n"
        "Rules:\n"
        "- Use only provided data.\n"
        "- If action is buy, enforce stop_loss < entry_price < target_price.\n"
        "- If action is skip, set entry_price/stop_loss/target_price to null."
    )


def _normalize_setup(
    parsed: dict[str, Any] | None,
    generated_on: pd.Timestamp,
    raw_response: str,
) -> SwingSetup:
    if not isinstance(parsed, dict):
        return SwingSetup(
            action="skip",
            entry_price=None,
            stop_loss=None,
            target_price=None,
            confidence=0,
            reasoning="Invalid JSON from Grok; treated as skip.",
            generated_on=generated_on,
            raw_response=raw_response,
        )

    action = str(parsed.get("action", "skip")).strip().lower()
    confidence = int(max(0, min(100, int(round(_to_float(parsed.get("confidence")) or 0.0)))))
    reasoning = str(parsed.get("reasoning", "")).strip() or "No reasoning provided."

    if action != "buy":
        return SwingSetup(
            action="skip",
            entry_price=None,
            stop_loss=None,
            target_price=None,
            confidence=confidence,
            reasoning=reasoning,
            generated_on=generated_on,
            raw_response=raw_response,
        )

    entry_price = _to_float(parsed.get("entry_price"))
    stop_loss = _to_float(parsed.get("stop_loss"))
    target_price = _to_float(parsed.get("target_price"))

    if (
        entry_price is None
        or stop_loss is None
        or target_price is None
        or entry_price <= 0
        or stop_loss <= 0
        or target_price <= 0
        or not (stop_loss < entry_price < target_price)
    ):
        return SwingSetup(
            action="skip",
            entry_price=None,
            stop_loss=None,
            target_price=None,
            confidence=confidence,
            reasoning="Invalid BUY levels returned; treated as skip.",
            generated_on=generated_on,
            raw_response=raw_response,
        )

    return SwingSetup(
        action="buy",
        entry_price=float(entry_price),
        stop_loss=float(stop_loss),
        target_price=float(target_price),
        confidence=confidence,
        reasoning=reasoning,
        generated_on=generated_on,
        raw_response=raw_response,
    )


async def _request_setup(
    grok_client: GrokClient,
    df: pd.DataFrame,
    current_idx: int,
    table_candles: int,
    pause_seconds: float,
    api_calls: dict[str, int],
    *,
    ticker: str,
    verbose_grok: bool,
) -> SwingSetup:
    # Strictly no leakage.
    history = df.iloc[: current_idx + 1].copy()
    generated_on = pd.Timestamp(history.index[-1])
    prompt = _build_grok_prompt(history, table_candles=table_candles)

    api_calls["count"] += 1
    raw = ""
    try:
        raw = await grok_client.generate_text(prompt)
        parsed = _try_parse_json_object(raw)
        setup = _normalize_setup(parsed, generated_on=generated_on, raw_response=raw)
        if verbose_grok:
            print(
                f"[{ticker}] Grok call #{api_calls['count']} @ {generated_on.strftime('%Y-%m-%d')} "
                f"-> action={setup.action}, entry={setup.entry_price}, sl={setup.stop_loss}, tp={setup.target_price}",
                flush=True,
            )
            print(f"[{ticker}] Grok raw response:\n{raw or '<EMPTY>'}\n", flush=True)
        return setup
    except Exception as exc:
        setup = SwingSetup(
            action="skip",
            entry_price=None,
            stop_loss=None,
            target_price=None,
            confidence=0,
            reasoning=f"Grok call failed; treated as skip: {exc}",
            generated_on=generated_on,
            raw_response=raw,
        )
        if verbose_grok:
            print(
                f"[{ticker}] Grok call #{api_calls['count']} failed at "
                f"{generated_on.strftime('%Y-%m-%d')}: {exc}",
                flush=True,
            )
        return setup
    finally:
        await asyncio.sleep(max(0.0, float(pause_seconds)))


def _build_drawdown(equity: pd.Series) -> pd.Series:
    peak = equity.cummax()
    dd = (equity / peak) - 1.0
    return dd.fillna(0.0)


def _profit_factor(trades: list[TradeRecord]) -> float:
    gross_profit = sum(max(t.pnl_dollars, 0.0) for t in trades)
    gross_loss = sum(-min(t.pnl_dollars, 0.0) for t in trades)
    if gross_loss > 0:
        return gross_profit / gross_loss
    if gross_profit > 0:
        return float("inf")
    return 0.0


async def run_sparse_grok_swing_backtest(
    *,
    ticker: str,
    df: pd.DataFrame,
    grok_client: GrokClient,
    initial_capital: float = 10_000.0,
    warmup_bars: int = 200,
    table_candles: int = 150,
    pause_seconds: float = 1.0,
    fixed_commission_per_pair: float = 2.5,
    assume_stop_first_if_both_hit: bool = True,
    verbose_grok: bool = False,
    entry_policy: str = "touch",
    skip_wait_bars: int = 20,
    low_conf_wait_bars: int = 1,
    min_entry_confidence: int = 70,
    test_start: pd.Timestamp | None = None,
    test_end: pd.Timestamp | None = None,
) -> SparseBacktestResult:
    if entry_policy not in {"touch", "next-bar-open"}:
        raise ValueError("entry_policy must be 'touch' or 'next-bar-open'.")

    data = _to_df_ohlcv(df)
    if len(data) <= warmup_bars:
        raise ValueError(f"Need > {warmup_bars} bars, got {len(data)}")

    index = pd.DatetimeIndex(data.index)
    if test_start is None:
        warmup_idx = warmup_bars - 1
        test_start_idx = warmup_idx + 1
    else:
        ts_start = pd.Timestamp(test_start)
        if ts_start.tzinfo is not None:
            ts_start = ts_start.tz_convert(None)

        start_candidates = np.where(index >= ts_start)[0]
        if start_candidates.size == 0:
            raise ValueError(f"test_start {ts_start.date()} is after last available bar.")

        test_start_idx = int(start_candidates[0])
        warmup_idx = test_start_idx - 1

    if test_end is None:
        test_end_idx = len(data) - 1
    else:
        ts_end = pd.Timestamp(test_end)
        if ts_end.tzinfo is not None:
            ts_end = ts_end.tz_convert(None)
        end_candidates = np.where(index <= ts_end)[0]
        if end_candidates.size == 0:
            raise ValueError(f"test_end {ts_end.date()} is before first available bar.")
        test_end_idx = int(end_candidates[-1])

    if test_start_idx > test_end_idx:
        raise ValueError("Resolved test_start is after test_end.")
    if warmup_idx < (warmup_bars - 1):
        raise ValueError(
            f"Not enough pre-test bars for warmup={warmup_bars}. "
            f"Need at least {warmup_bars} bars before test_start."
        )

    api_calls = {"count": 0}
    equity_cash = float(initial_capital)
    position: dict[str, Any] | None = None
    active_setup: SwingSetup | None = None
    pending_untriggered_setup: SwingSetup | None = None

    setup_log: list[dict[str, Any]] = []
    trades: list[TradeRecord] = []
    equity_points: list[tuple[pd.Timestamp, float]] = []
    entries_triggered = 0
    low_confidence_rejections = 0
    next_setup_request_idx: int | None = None

    test_start_ts = pd.Timestamp(data.index[test_start_idx])
    equity_points.append((test_start_ts, equity_cash))

    # First and only startup setup call.
    if position is not None:
        raise RuntimeError("Invariant violated: attempted Grok call while in position.")
    first_setup = await _request_setup(
        grok_client=grok_client,
        df=data,
        current_idx=warmup_idx,
        table_candles=table_candles,
        pause_seconds=pause_seconds,
        api_calls=api_calls,
        ticker=ticker,
        verbose_grok=verbose_grok,
    )
    last_setup_request_idx = warmup_idx
    setup_log.append(
        {
            "generated_on": first_setup.generated_on,
            "action": first_setup.action,
            "entry_price": first_setup.entry_price,
            "stop_loss": first_setup.stop_loss,
            "target_price": first_setup.target_price,
            "confidence": first_setup.confidence,
            "reasoning": first_setup.reasoning,
            "raw_response": first_setup.raw_response,
            "eligible_for_entry": (
                first_setup.action == "buy" and int(first_setup.confidence) > int(min_entry_confidence)
            ),
        }
    )
    if first_setup.action == "buy" and int(first_setup.confidence) <= int(min_entry_confidence):
        low_confidence_rejections += 1
        next_setup_request_idx = warmup_idx + max(1, int(low_conf_wait_bars))
    elif first_setup.action == "skip":
        next_setup_request_idx = warmup_idx + max(1, int(skip_wait_bars))
    else:
        next_setup_request_idx = None
    active_setup = (
        first_setup
        if (first_setup.action == "buy" and int(first_setup.confidence) > int(min_entry_confidence))
        else None
    )

    for idx in tqdm(range(test_start_idx, test_end_idx + 1), desc="Backtesting", unit="bar"):
        ts = pd.Timestamp(data.index[idx])
        row = data.iloc[idx]
        low = float(row["Low"])
        high = float(row["High"])
        close = float(row["Close"])

        # In position: check exits; no Grok calls while in position.
        if position is not None:
            sl_hit = low <= float(position["stop_loss"])
            tp_hit = high >= float(position["target_price"])

            if sl_hit or tp_hit:
                if sl_hit and tp_hit:
                    if assume_stop_first_if_both_hit:
                        exit_price = float(position["stop_loss"])
                        exit_reason = "BOTH_HIT_SAME_BAR_STOP_FIRST"
                    else:
                        exit_price = float(position["target_price"])
                        exit_reason = "BOTH_HIT_SAME_BAR_TARGET_FIRST"
                elif sl_hit:
                    exit_price = float(position["stop_loss"])
                    exit_reason = "STOP_LOSS_HIT"
                else:
                    exit_price = float(position["target_price"])
                    exit_reason = "TARGET_HIT"

                shares = float(position["shares"])
                entry_price = float(position["entry_price"])
                entry_commission = float(position["entry_commission"])
                exit_commission = max(0.0, float(fixed_commission_per_pair) / 2.0)
                price_pnl = shares * (exit_price - entry_price)
                net_pnl = price_pnl - exit_commission
                equity_cash += net_pnl
                trade_pnl = price_pnl - entry_commission - exit_commission
                invested = max(1e-12, (entry_price * shares) + entry_commission)
                ret_pct = (trade_pnl / invested) * 100.0

                trade = TradeRecord(
                    entry_date=pd.Timestamp(position["entry_date"]),
                    entry_price=entry_price,
                    exit_date=ts,
                    exit_price=exit_price,
                    shares=shares,
                    pnl_dollars=trade_pnl,
                    return_pct=ret_pct,
                    commission_dollars=entry_commission + exit_commission,
                    exit_reason=exit_reason,
                    grok_confidence=int(position["setup_confidence"]),
                    grok_reasoning=str(position["setup_reasoning"]),
                    setup_generated_on=pd.Timestamp(position["setup_generated_on"]),
                )
                trades.append(trade)
                position = None
                pending_untriggered_setup = None

                # Immediately request next setup only after a completed exit.
                if position is not None:
                    raise RuntimeError("Invariant violated: attempted Grok call while in position.")
                next_setup = await _request_setup(
                    grok_client=grok_client,
                    df=data,
                    current_idx=idx,
                    table_candles=table_candles,
                    pause_seconds=pause_seconds,
                    api_calls=api_calls,
                    ticker=ticker,
                    verbose_grok=verbose_grok,
                )
                last_setup_request_idx = idx
                setup_log.append(
                    {
                        "generated_on": next_setup.generated_on,
                        "action": next_setup.action,
                        "entry_price": next_setup.entry_price,
                        "stop_loss": next_setup.stop_loss,
                        "target_price": next_setup.target_price,
                        "confidence": next_setup.confidence,
                        "reasoning": next_setup.reasoning,
                        "raw_response": next_setup.raw_response,
                        "eligible_for_entry": (
                            next_setup.action == "buy" and int(next_setup.confidence) > int(min_entry_confidence)
                        ),
                    }
                )

                if next_setup.action == "buy" and int(next_setup.confidence) <= int(min_entry_confidence):
                    low_confidence_rejections += 1
                    next_setup_request_idx = idx + max(1, int(low_conf_wait_bars))
                elif next_setup.action == "skip":
                    next_setup_request_idx = idx + max(1, int(skip_wait_bars))
                else:
                    next_setup_request_idx = None
                active_setup = (
                    next_setup
                    if (next_setup.action == "buy" and int(next_setup.confidence) > int(min_entry_confidence))
                    else None
                )
                if active_setup is not None:
                    pending_untriggered_setup = active_setup

                equity_points.append((ts, equity_cash))
                continue

        # Never call Grok while a position is open.
        if (
            position is None
            and active_setup is None
            and next_setup_request_idx is not None
            and idx >= next_setup_request_idx
        ):
            if position is not None:
                raise RuntimeError("Invariant violated: attempted Grok call while in position.")
            recalled_setup = await _request_setup(
                grok_client=grok_client,
                df=data,
                current_idx=idx,
                table_candles=table_candles,
                pause_seconds=pause_seconds,
                api_calls=api_calls,
                ticker=ticker,
                verbose_grok=verbose_grok,
            )
            last_setup_request_idx = idx
            setup_log.append(
                {
                    "generated_on": recalled_setup.generated_on,
                    "action": recalled_setup.action,
                    "entry_price": recalled_setup.entry_price,
                    "stop_loss": recalled_setup.stop_loss,
                    "target_price": recalled_setup.target_price,
                    "confidence": recalled_setup.confidence,
                    "reasoning": recalled_setup.reasoning,
                    "raw_response": recalled_setup.raw_response,
                    "eligible_for_entry": (
                        recalled_setup.action == "buy"
                        and int(recalled_setup.confidence) > int(min_entry_confidence)
                    ),
                }
            )
            if recalled_setup.action == "buy" and int(recalled_setup.confidence) <= int(min_entry_confidence):
                low_confidence_rejections += 1
                next_setup_request_idx = idx + max(1, int(low_conf_wait_bars))
            elif recalled_setup.action == "skip":
                next_setup_request_idx = idx + max(1, int(skip_wait_bars))
            else:
                next_setup_request_idx = None
            active_setup = (
                recalled_setup
                if (recalled_setup.action == "buy" and int(recalled_setup.confidence) > int(min_entry_confidence))
                else None
            )

        # Flat + active setup => wait for entry touch; no new Grok calls.
        if position is None and active_setup is not None:
            setup_entry = float(active_setup.entry_price)
            should_enter = False
            fill_entry = setup_entry
            if entry_policy == "touch":
                should_enter = low <= setup_entry <= high
                fill_entry = setup_entry
            else:
                should_enter = True
                open_px = float(row["Open"])
                fill_entry = open_px if np.isfinite(open_px) and open_px > 0 else close

            if should_enter:
                # Preserve setup risk distances if fill price differs from suggested entry.
                risk_distance = setup_entry - float(active_setup.stop_loss)
                reward_distance = float(active_setup.target_price) - setup_entry
                adj_stop = fill_entry - risk_distance
                adj_target = fill_entry + reward_distance
                if not (adj_stop > 0 and adj_stop < fill_entry < adj_target):
                    mark_equity = equity_cash if position is None else float(position["shares"]) * close
                    equity_points.append((ts, mark_equity))
                    continue

                entry_commission = max(0.0, float(fixed_commission_per_pair) / 2.0)
                if equity_cash <= entry_commission:
                    continue
                available_equity = equity_cash - entry_commission
                shares = available_equity / fill_entry
                if shares > 0:
                    entries_triggered += 1
                    equity_cash -= entry_commission
                    position = {
                        "entry_date": ts,
                        "entry_price": fill_entry,
                        "shares": shares,
                        "entry_commission": entry_commission,
                        "stop_loss": adj_stop,
                        "target_price": adj_target,
                        "setup_confidence": int(active_setup.confidence),
                        "setup_reasoning": active_setup.reasoning,
                        "setup_generated_on": active_setup.generated_on,
                    }
                    active_setup = None
                    pending_untriggered_setup = None

        mark_equity = equity_cash if position is None else float(position["shares"]) * close
        equity_points.append((ts, mark_equity))

    # Close open position at last close.
    if position is not None:
        last_ts = pd.Timestamp(data.index[test_end_idx])
        last_close = float(data["Close"].iloc[test_end_idx])

        shares = float(position["shares"])
        entry_price = float(position["entry_price"])
        entry_commission = float(position["entry_commission"])
        exit_commission = max(0.0, float(fixed_commission_per_pair) / 2.0)
        price_pnl = shares * (last_close - entry_price)
        net_pnl = price_pnl - exit_commission
        equity_cash += net_pnl
        trade_pnl = price_pnl - entry_commission - exit_commission
        invested = max(1e-12, (entry_price * shares) + entry_commission)
        ret_pct = (trade_pnl / invested) * 100.0

        trades.append(
            TradeRecord(
                entry_date=pd.Timestamp(position["entry_date"]),
                entry_price=entry_price,
                exit_date=last_ts,
                exit_price=last_close,
                shares=shares,
                pnl_dollars=trade_pnl,
                return_pct=ret_pct,
                commission_dollars=entry_commission + exit_commission,
                exit_reason="FORCED_EXIT_END_OF_DATA",
                grok_confidence=int(position["setup_confidence"]),
                grok_reasoning=str(position["setup_reasoning"]),
                setup_generated_on=pd.Timestamp(position["setup_generated_on"]),
            )
        )

        if equity_points and equity_points[-1][0] == last_ts:
            equity_points[-1] = (last_ts, equity_cash)
        else:
            equity_points.append((last_ts, equity_cash))

    if pending_untriggered_setup is None and active_setup is not None:
        pending_untriggered_setup = active_setup

    equity_df = pd.DataFrame(equity_points, columns=["Date", "Equity"])
    equity_df = equity_df.drop_duplicates(subset="Date", keep="last").sort_values("Date")
    strategy_equity = equity_df.set_index("Date")["Equity"].astype(float)
    drawdown = _build_drawdown(strategy_equity)

    final_equity = float(strategy_equity.iloc[-1]) if not strategy_equity.empty else float(initial_capital)
    total_return_pct = ((final_equity / float(initial_capital)) - 1.0) * 100.0
    win_rate_pct = (
        (sum(1 for t in trades if t.pnl_dollars > 0) / len(trades)) * 100.0 if trades else 0.0
    )
    max_drawdown_pct = abs(float(drawdown.min())) * 100.0 if not drawdown.empty else 0.0

    returns = strategy_equity.pct_change().dropna()
    if len(returns) > 1 and float(returns.std(ddof=0)) > 0:
        sharpe = float(np.sqrt(252.0) * returns.mean() / returns.std(ddof=0))
    else:
        sharpe = 0.0

    trade_log_df = pd.DataFrame(
        [
            {
                "entry_date": t.entry_date,
                "entry_price": t.entry_price,
                "exit_date": t.exit_date,
                "exit_price": t.exit_price,
                "shares": t.shares,
                "pnl_dollars": t.pnl_dollars,
                "return_pct": t.return_pct,
                "commission_dollars": t.commission_dollars,
                "exit_reason": t.exit_reason,
                "grok_confidence": t.grok_confidence,
                "grok_reasoning": t.grok_reasoning,
                "setup_generated_on": t.setup_generated_on,
            }
            for t in trades
        ]
    )

    return SparseBacktestResult(
        ticker=ticker,
        initial_capital=float(initial_capital),
        final_equity=final_equity,
        total_return_pct=total_return_pct,
        win_rate_pct=win_rate_pct,
        profit_factor=_profit_factor(trades),
        max_drawdown_pct=max_drawdown_pct,
        sharpe=sharpe,
        num_trades=len(trades),
        entries_triggered=entries_triggered,
        setup_buy_count=sum(1 for s in setup_log if str(s.get("action", "")).lower() == "buy"),
        setup_skip_count=sum(1 for s in setup_log if str(s.get("action", "")).lower() != "buy"),
        low_confidence_rejections=low_confidence_rejections,
        grok_api_calls=int(api_calls["count"]),
        total_commission_dollars=float(sum(t.commission_dollars for t in trades)),
        trades=tuple(trades),
        trade_log_df=trade_log_df,
        setup_log=tuple(setup_log),
        strategy_equity=strategy_equity,
        drawdown=drawdown,
        pending_untriggered_setup=pending_untriggered_setup,
    )
