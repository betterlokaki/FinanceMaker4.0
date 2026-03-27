"""Shared-capital portfolio orchestration for multi-symbol backtests."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from backtests.backtesting_py.config import PortfolioConfig
from backtests.backtesting_py.cost_model import calculate_side_cost


@dataclass(frozen=True)
class ExecutedPortfolioTrade:
    """Executed trade under shared-capital constraints."""

    ticker: str
    direction: str
    size: int
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_price: float
    exit_price: float
    gross_pnl: float
    net_pnl: float
    entry_cost: float
    exit_cost: float
    short_borrow_fee: float


@dataclass(frozen=True)
class SkippedPortfolioTrade:
    """Candidate trade rejected by portfolio constraints."""

    ticker: str
    entry_time: pd.Timestamp
    reason: str


@dataclass(frozen=True)
class SharedPortfolioResult:
    """Aggregated result across symbols with shared capital."""

    initial_capital: float
    final_equity: float
    total_return_pct: float
    max_drawdown_pct: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    executed_trades: tuple[ExecutedPortfolioTrade, ...] = field(default_factory=tuple)
    skipped_trades: tuple[SkippedPortfolioTrade, ...] = field(default_factory=tuple)
    equity_curve: tuple[tuple[pd.Timestamp, float], ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class _CandidateTrade:
    ticker: str
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    size: int
    direction: int
    entry_price: float
    exit_price: float

    @property
    def notional(self) -> float:
        return self.entry_price * self.size


def _iter_candidates(trades_by_ticker: dict[str, pd.DataFrame]) -> list[_CandidateTrade]:
    candidates: list[_CandidateTrade] = []
    for ticker, trades in trades_by_ticker.items():
        if trades is None or trades.empty:
            continue
        for _, row in trades.iterrows():
            entry_time = pd.Timestamp(row.get("EntryTime"))
            exit_time = pd.Timestamp(row.get("ExitTime"))
            entry_price = float(row.get("EntryPrice", 0.0))
            exit_price = float(row.get("ExitPrice", 0.0))
            raw_size = row.get("Size", 0)
            size = int(abs(raw_size))
            direction = 1 if float(raw_size) > 0 else -1

            if (
                size < 1
                or entry_price <= 0
                or exit_price <= 0
                or pd.isna(entry_time)
                or pd.isna(exit_time)
                or exit_time < entry_time
            ):
                continue

            candidates.append(
                _CandidateTrade(
                    ticker=ticker,
                    entry_time=entry_time,
                    exit_time=exit_time,
                    size=size,
                    direction=direction,
                    entry_price=entry_price,
                    exit_price=exit_price,
                )
            )
    return candidates


def _max_drawdown_pct(equity_curve: list[tuple[pd.Timestamp, float]]) -> float:
    if not equity_curve:
        return 0.0
    series = pd.Series(
        data=[value for _, value in equity_curve],
        index=pd.DatetimeIndex([ts for ts, _ in equity_curve]),
    ).sort_index()
    running_max = series.cummax()
    drawdown = (series / running_max) - 1.0
    return abs(float(drawdown.min())) * 100.0 if not drawdown.empty else 0.0


def _year_fraction(entry_time: pd.Timestamp, exit_time: pd.Timestamp) -> float:
    seconds = max(0.0, float((exit_time - entry_time).total_seconds()))
    return seconds / (365.0 * 24.0 * 60.0 * 60.0)


def run_shared_capital_portfolio(
    trades_by_ticker: dict[str, pd.DataFrame],
    portfolio_config: PortfolioConfig,
    tick_size_by_ticker: dict[str, float] | None = None,
) -> SharedPortfolioResult:
    """Allocate multi-symbol candidate trades under shared capital constraints."""
    tick_sizes = tick_size_by_ticker or {}
    candidates = _iter_candidates(trades_by_ticker)
    allocation_fraction = float(portfolio_config.position_size_cash_fraction)
    allocation_fraction = max(0.0, min(1.0, allocation_fraction))
    short_borrow_fee_apr = max(0.0, float(portfolio_config.short_borrow_fee_apr))

    events: list[tuple[pd.Timestamp, int, str, str, _CandidateTrade]] = []
    for candidate in candidates:
        # Exits first (priority 0), entries second (priority 1)
        events.append((candidate.exit_time, 0, candidate.ticker, "exit", candidate))
        events.append((candidate.entry_time, 1, candidate.ticker, "entry", candidate))
    events.sort(key=lambda item: (item[0], item[1], item[2]))

    cash = float(portfolio_config.initial_capital)
    used_notional = 0.0
    open_positions: dict[_CandidateTrade, dict[str, Any]] = {}
    executed: list[ExecutedPortfolioTrade] = []
    skipped: list[SkippedPortfolioTrade] = []
    equity_curve: list[tuple[pd.Timestamp, float]] = []

    for event_time, _, ticker, event_type, candidate in events:
        tick_size = float(tick_sizes.get(ticker, portfolio_config.default_tick_size))

        if event_type == "entry":
            if candidate in open_positions:
                continue

            capacity = cash * portfolio_config.max_leverage
            size = candidate.size
            notional = candidate.notional
            if portfolio_config.dynamic_position_sizing:
                remaining_capacity = max(0.0, capacity - used_notional)
                target_notional = min(
                    remaining_capacity,
                    cash * portfolio_config.max_leverage * allocation_fraction,
                )
                size = int(target_notional / candidate.entry_price)
                if size < 1:
                    skipped.append(
                        SkippedPortfolioTrade(
                            ticker=ticker,
                            entry_time=event_time,
                            reason="insufficient_effective_size",
                        )
                    )
                    continue
                notional = candidate.entry_price * size

            if used_notional + notional > capacity:
                skipped.append(
                    SkippedPortfolioTrade(
                        ticker=ticker,
                        entry_time=event_time,
                        reason="insufficient_shared_capacity",
                    )
                )
                continue

            entry_cost = calculate_side_cost(
                order_size=size * candidate.direction,
                price=candidate.entry_price,
                commission_rate=portfolio_config.commission_rate,
                tick_size=tick_size,
                slippage_ticks=portfolio_config.slippage_ticks,
                fixed_commission_per_side=portfolio_config.fixed_commission_per_side,
            )
            if cash - entry_cost <= 0:
                skipped.append(
                    SkippedPortfolioTrade(
                        ticker=ticker,
                        entry_time=event_time,
                        reason="insufficient_cash_for_entry_cost",
                    )
                )
                continue

            cash -= entry_cost
            used_notional += notional
            open_positions[candidate] = {
                "entry_cost": entry_cost,
                "tick_size": tick_size,
                "size": size,
                "direction": candidate.direction,
                "entry_price": candidate.entry_price,
                "entry_time": candidate.entry_time,
                "notional": notional,
            }
            equity_curve.append((event_time, cash))
            continue

        # exit event
        position = open_positions.pop(candidate, None)
        if position is None:
            continue

        size = int(position["size"])
        direction = int(position["direction"])
        entry_price = float(position["entry_price"])
        gross_pnl = (
            (candidate.exit_price - entry_price)
            * size
            * direction
        )
        exit_cost = calculate_side_cost(
            order_size=-size * direction,
            price=candidate.exit_price,
            commission_rate=portfolio_config.commission_rate,
            tick_size=float(position["tick_size"]),
            slippage_ticks=portfolio_config.slippage_ticks,
            fixed_commission_per_side=portfolio_config.fixed_commission_per_side,
        )
        entry_cost = float(position["entry_cost"])
        short_borrow_fee = 0.0
        if direction < 0 and short_borrow_fee_apr > 0.0:
            short_borrow_fee = float(position["notional"]) * short_borrow_fee_apr * _year_fraction(
                pd.Timestamp(position["entry_time"]),
                candidate.exit_time,
            )
        net_pnl = gross_pnl - entry_cost - exit_cost - short_borrow_fee

        cash += gross_pnl - exit_cost - short_borrow_fee
        used_notional = max(0.0, used_notional - float(position["notional"]))

        executed.append(
            ExecutedPortfolioTrade(
                ticker=ticker,
                direction="Long" if direction > 0 else "Short",
                size=size,
                entry_time=pd.Timestamp(position["entry_time"]),
                exit_time=candidate.exit_time,
                entry_price=entry_price,
                exit_price=candidate.exit_price,
                gross_pnl=gross_pnl,
                net_pnl=net_pnl,
                entry_cost=entry_cost,
                exit_cost=exit_cost,
                short_borrow_fee=short_borrow_fee,
            )
        )
        equity_curve.append((event_time, cash))

    wins = sum(1 for trade in executed if trade.net_pnl > 0)
    losses = sum(1 for trade in executed if trade.net_pnl <= 0)
    total_return_pct = (
        ((cash - portfolio_config.initial_capital) / portfolio_config.initial_capital) * 100.0
        if portfolio_config.initial_capital > 0
        else 0.0
    )

    return SharedPortfolioResult(
        initial_capital=portfolio_config.initial_capital,
        final_equity=cash,
        total_return_pct=total_return_pct,
        max_drawdown_pct=_max_drawdown_pct(equity_curve),
        total_trades=len(executed),
        winning_trades=wins,
        losing_trades=losses,
        executed_trades=tuple(executed),
        skipped_trades=tuple(skipped),
        equity_curve=tuple(equity_curve),
    )
