"""Position sizing helpers driven by StrategyInputModel."""
from __future__ import annotations

import logging

from common.models.portfolio import Portfolio
from common.models.strategy_input import StrategyInputModel

logger = logging.getLogger(__name__)


class PositionSizer:
    """Calculate integer share quantities from portfolio percentage inputs."""

    @staticmethod
    def quantity_for_entry(
        portfolio: Portfolio,
        entry_price: float,
        strategy_input: StrategyInputModel,
        reserved_notional: float = 0.0,
    ) -> int:
        if entry_price <= 0:
            logger.warning("Cannot size entry with non-positive price %.4f", entry_price)
            return 0

        base_value = PositionSizer._portfolio_base_value(portfolio)
        target_notional = base_value * strategy_input.portfolio_pct_per_trade
        if strategy_input.max_notional_per_trade is not None:
            target_notional = min(target_notional, strategy_input.max_notional_per_trade)

        available_notional = PositionSizer._available_notional(portfolio, reserved_notional)
        notional = min(target_notional, available_notional)
        quantity = int(notional / max(entry_price, 0.01))
        if quantity < 1:
            logger.warning(
                "Quantity < 1 for entry %.2f: base=%.2f target=%.2f available=%.2f reserved=%.2f",
                entry_price,
                base_value,
                target_notional,
                available_notional,
                reserved_notional,
            )
        return quantity

    @staticmethod
    def _portfolio_base_value(portfolio: Portfolio) -> float:
        for value in (
            portfolio.total_equity,
            portfolio.cash_balance,
            portfolio.buying_power,
        ):
            value = max(0.0, float(value))
            if value > 0:
                return value
        return 0.0

    @staticmethod
    def _available_notional(portfolio: Portfolio, reserved_notional: float) -> float:
        cash_or_buying_power = max(
            max(0.0, float(portfolio.cash_balance)),
            max(0.0, float(portfolio.buying_power)),
        )
        return max(0.0, cash_or_buying_power - max(0.0, reserved_notional))
