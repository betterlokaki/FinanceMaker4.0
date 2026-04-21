"""Forecast-driven trade simulation with ATR-based 1:4 RR logic."""
from __future__ import annotations

from dataclasses import dataclass
from math import erf, sqrt
from typing import Any

import numpy as np
import pandas as pd

from backtests.forecasting.config import TradeLogicConfig


@dataclass(slots=True)
class TradeState:
    ticker: str
    side: str
    entry_time: pd.Timestamp
    entry_idx: int
    entry_price: float
    stop_price: float
    take_profit_price: float
    quantity: int
    max_exit_idx: int
    pair_fee: float
    expected_edge: float


def _normal_cdf(x: float, mu: float, sigma: float) -> float:
    sigma = max(1e-9, float(sigma))
    z = (x - mu) / (sigma * sqrt(2.0))
    return 0.5 * (1.0 + erf(z))


def _compute_side_edge(
    *,
    side: str,
    mu_ret: float,
    sigma_ret: float,
    tp_ret: float,
    sl_ret: float,
    cost_ret: float,
) -> tuple[float, float, float]:
    if side == "LONG":
        p_tp = 1.0 - _normal_cdf(tp_ret, mu_ret, sigma_ret)
        p_sl = _normal_cdf(-sl_ret, mu_ret, sigma_ret)
    elif side == "SHORT":
        p_tp = _normal_cdf(-tp_ret, mu_ret, sigma_ret)
        p_sl = 1.0 - _normal_cdf(sl_ret, mu_ret, sigma_ret)
    else:
        raise ValueError(f"Unsupported side {side}")

    p_tp = float(np.clip(p_tp, 0.0, 1.0))
    p_sl = float(np.clip(p_sl, 0.0, 1.0))
    edge = (p_tp * tp_ret) - (p_sl * sl_ret) - cost_ret
    return edge, p_tp, p_sl


def _candidate_signal(
    *,
    pred_row: pd.Series,
    atr_value: float,
    entry_price: float,
    sigma_c3: float,
    cfg: TradeLogicConfig,
) -> dict[str, float] | None:
    if not np.isfinite(entry_price) or entry_price <= 0.0:
        return None
    if not np.isfinite(atr_value) or atr_value <= 0.0:
        return None

    stop_dist = max(1e-9, float(atr_value) * float(cfg.atr_multiplier))
    tp_dist = stop_dist * float(cfg.rr_ratio)

    sl_ret = stop_dist / entry_price
    tp_ret = tp_dist / entry_price
    if sl_ret <= 0.0 or tp_ret <= 0.0:
        return None

    mu_ret = float(pred_row.get("pred_target_c3", np.nan))
    sigma_ret = max(1e-6, float(sigma_c3))

    cost_long = (float(cfg.long_round_trip_fee) / entry_price)
    cost_short = (float(cfg.short_round_trip_fee) / entry_price)

    long_edge, long_p_tp, long_p_sl = _compute_side_edge(
        side="LONG",
        mu_ret=mu_ret,
        sigma_ret=sigma_ret,
        tp_ret=tp_ret,
        sl_ret=sl_ret,
        cost_ret=cost_long,
    )
    short_edge, short_p_tp, short_p_sl = _compute_side_edge(
        side="SHORT",
        mu_ret=mu_ret,
        sigma_ret=sigma_ret,
        tp_ret=tp_ret,
        sl_ret=sl_ret,
        cost_ret=cost_short,
    )

    candidates = [
        {
            "side": "LONG",
            "edge": long_edge,
            "p_tp": long_p_tp,
            "p_sl": long_p_sl,
            "pair_fee": float(cfg.long_round_trip_fee),
            "stop_dist": stop_dist,
            "tp_dist": tp_dist,
        },
        {
            "side": "SHORT",
            "edge": short_edge,
            "p_tp": short_p_tp,
            "p_sl": short_p_sl,
            "pair_fee": float(cfg.short_round_trip_fee),
            "stop_dist": stop_dist,
            "tp_dist": tp_dist,
        },
    ]

    valid = [
        c
        for c in candidates
        if c["edge"] >= float(cfg.min_edge) and c["p_tp"] >= float(cfg.min_tp_prob)
    ]
    if not valid:
        return None

    valid.sort(key=lambda row: float(row["edge"]), reverse=True)
    return valid[0]


def _apply_entry_slippage(side: str, raw_price: float, cfg: TradeLogicConfig) -> float:
    slip = float(cfg.slippage_ticks) * float(cfg.tick_size)
    if side == "LONG":
        return raw_price + slip
    return raw_price - slip


def _apply_exit_slippage(side: str, raw_price: float, cfg: TradeLogicConfig) -> float:
    slip = float(cfg.slippage_ticks) * float(cfg.tick_size)
    if side == "LONG":
        return raw_price - slip
    return raw_price + slip


def _exit_trade(
    *,
    trade: TradeState,
    exit_price_raw: float,
    exit_time: pd.Timestamp,
    exit_idx: int,
    exit_reason: str,
    cfg: TradeLogicConfig,
) -> dict[str, Any]:
    exit_price = _apply_exit_slippage(trade.side, float(exit_price_raw), cfg)
    qty = int(trade.quantity)

    if trade.side == "LONG":
        gross = (exit_price - trade.entry_price) * qty
    else:
        gross = (trade.entry_price - exit_price) * qty

    net = gross - float(trade.pair_fee)
    notional = max(1e-9, trade.entry_price * qty)
    return_pct = net / notional

    return {
        "ticker": trade.ticker,
        "side": trade.side,
        "entry_time": trade.entry_time,
        "exit_time": exit_time,
        "entry_idx": trade.entry_idx,
        "exit_idx": int(exit_idx),
        "entry_price": float(trade.entry_price),
        "exit_price": float(exit_price),
        "stop_price": float(trade.stop_price),
        "take_profit_price": float(trade.take_profit_price),
        "quantity": qty,
        "pair_fee": float(trade.pair_fee),
        "gross_pnl": float(gross),
        "net_pnl": float(net),
        "return_pct": float(return_pct),
        "expected_edge": float(trade.expected_edge),
        "exit_reason": str(exit_reason),
    }


def run_trade_simulation_for_ticker(
    *,
    ticker: str,
    bars: pd.DataFrame,
    predictions: pd.DataFrame,
    trade_cfg: TradeLogicConfig,
    sigma_c3: float,
    initial_capital: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Run one-position-at-a-time simulation for one ticker."""
    if bars.empty:
        return pd.DataFrame(), pd.DataFrame()

    idx = pd.DatetimeIndex(bars.index)
    open_ = pd.to_numeric(bars["Open"], errors="coerce").to_numpy(dtype=float)
    high = pd.to_numeric(bars["High"], errors="coerce").to_numpy(dtype=float)
    low = pd.to_numeric(bars["Low"], errors="coerce").to_numpy(dtype=float)
    close = pd.to_numeric(bars["Close"], errors="coerce").to_numpy(dtype=float)

    pred_map = predictions.copy()
    pred_map.index = pd.DatetimeIndex(pred_map.index)

    equity = float(initial_capital)
    active: TradeState | None = None
    signals: list[dict[str, Any]] = []
    trades: list[dict[str, Any]] = []

    for i, ts in enumerate(idx):
        # 1) Manage open trade on current candle.
        if active is not None and i >= active.entry_idx:
            bar_high = high[i]
            bar_low = low[i]
            tp_hit = False
            sl_hit = False
            if active.side == "LONG":
                tp_hit = bar_high >= active.take_profit_price
                sl_hit = bar_low <= active.stop_price
            else:
                tp_hit = bar_low <= active.take_profit_price
                sl_hit = bar_high >= active.stop_price

            if tp_hit and sl_hit:
                if str(trade_cfg.tie_break_rule).upper() == "SL_FIRST":
                    hit = "SL"
                else:
                    hit = "TP"
                exit_px = active.stop_price if hit == "SL" else active.take_profit_price
                trade_row = _exit_trade(
                    trade=active,
                    exit_price_raw=exit_px,
                    exit_time=ts,
                    exit_idx=i,
                    exit_reason=hit,
                    cfg=trade_cfg,
                )
                trades.append(trade_row)
                equity += float(trade_row["net_pnl"])
                active = None
            elif tp_hit:
                trade_row = _exit_trade(
                    trade=active,
                    exit_price_raw=active.take_profit_price,
                    exit_time=ts,
                    exit_idx=i,
                    exit_reason="TP",
                    cfg=trade_cfg,
                )
                trades.append(trade_row)
                equity += float(trade_row["net_pnl"])
                active = None
            elif sl_hit:
                trade_row = _exit_trade(
                    trade=active,
                    exit_price_raw=active.stop_price,
                    exit_time=ts,
                    exit_idx=i,
                    exit_reason="SL",
                    cfg=trade_cfg,
                )
                trades.append(trade_row)
                equity += float(trade_row["net_pnl"])
                active = None
            elif i >= active.max_exit_idx:
                trade_row = _exit_trade(
                    trade=active,
                    exit_price_raw=close[i],
                    exit_time=ts,
                    exit_idx=i,
                    exit_reason="MAX_HOLD",
                    cfg=trade_cfg,
                )
                trades.append(trade_row)
                equity += float(trade_row["net_pnl"])
                active = None

        # 2) Evaluate new signal from forecast at time t; enter at t+1 open.
        if active is not None:
            continue
        if ts not in pred_map.index:
            continue
        if (i + 1) >= len(idx):
            continue

        row = pred_map.loc[ts]
        row = row.iloc[0] if isinstance(row, pd.DataFrame) else row

        atr_val = float(row.get("atr_14", np.nan))
        next_open = float(open_[i + 1])
        candidate = _candidate_signal(
            pred_row=row,
            atr_value=atr_val,
            entry_price=next_open,
            sigma_c3=float(sigma_c3),
            cfg=trade_cfg,
        )
        signal_row = {
            "ticker": ticker,
            "time": ts,
            "atr_14": atr_val,
            "next_open": next_open,
            "mu_c3": float(row.get("pred_target_c3", np.nan)),
            "sigma_c3": float(sigma_c3),
            "signal": "HOLD",
            "selected_edge": np.nan,
            "selected_p_tp": np.nan,
            "selected_p_sl": np.nan,
            "selected_side": None,
            "entry_time": pd.NaT,
            "entry_price": np.nan,
            "sl_price": np.nan,
            "tp_price": np.nan,
        }
        if candidate is None:
            signals.append(signal_row)
            continue

        side = str(candidate["side"])
        entry_idx = i + 1
        entry_time = idx[entry_idx]
        entry_raw = float(open_[entry_idx])
        entry_price = _apply_entry_slippage(side, entry_raw, trade_cfg)
        qty = max(1, int(equity / max(1e-9, entry_price)))

        stop_dist = float(candidate["stop_dist"])
        tp_dist = float(candidate["tp_dist"])
        if side == "LONG":
            stop_price = entry_price - stop_dist
            tp_price = entry_price + tp_dist
        else:
            stop_price = entry_price + stop_dist
            tp_price = entry_price - tp_dist

        active = TradeState(
            ticker=ticker,
            side=side,
            entry_time=entry_time,
            entry_idx=entry_idx,
            entry_price=entry_price,
            stop_price=stop_price,
            take_profit_price=tp_price,
            quantity=qty,
            max_exit_idx=min(len(idx) - 1, entry_idx + int(trade_cfg.max_hold_candles) - 1),
            pair_fee=float(candidate["pair_fee"]),
            expected_edge=float(candidate["edge"]),
        )

        signal_row.update(
            {
                "signal": "ENTER",
                "selected_edge": float(candidate["edge"]),
                "selected_p_tp": float(candidate["p_tp"]),
                "selected_p_sl": float(candidate["p_sl"]),
                "selected_side": side,
                "entry_time": entry_time,
                "entry_price": entry_price,
                "sl_price": stop_price,
                "tp_price": tp_price,
            }
        )
        signals.append(signal_row)

    signals_df = pd.DataFrame(signals)
    trades_df = pd.DataFrame(trades)
    return signals_df, trades_df
