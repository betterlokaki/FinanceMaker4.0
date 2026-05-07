"""Alpaca portfolio converter."""
from __future__ import annotations

from typing import Any

from common.converters.alpaca.order_response_converter import AlpacaOrderResponseConverter
from common.models.order_response import OrderResponse
from common.models.portfolio import Portfolio
from common.models.position import Position


class AlpacaPortfolioConverter:
    """Convert Alpaca account and position payloads into Portfolio."""

    @classmethod
    def from_alpaca(
        cls,
        account: Any,
        positions_data: list[Any],
        open_orders: list[OrderResponse] | None = None,
    ) -> Portfolio:
        positions = [cls._convert_position(position) for position in positions_data]
        long_market_value = cls._safe_float(cls._get(account, "long_market_value", None)) or 0.0
        short_market_value = cls._safe_float(cls._get(account, "short_market_value", None)) or 0.0

        return Portfolio(
            positions=positions,
            open_orders=open_orders or [],
            cash_balance=cls._safe_float(cls._get(account, "cash", None)) or 0.0,
            total_market_value=long_market_value + abs(short_market_value),
            total_equity=(
                cls._safe_float(cls._get(account, "equity", None))
                or cls._safe_float(cls._get(account, "portfolio_value", None))
                or 0.0
            ),
            buying_power=cls._safe_float(cls._get(account, "buying_power", None)) or 0.0,
            unrealized_pnl=sum(position.unrealized_pnl or 0.0 for position in positions),
            realized_pnl=0.0,
        )

    @classmethod
    def _convert_position(cls, position_data: Any) -> Position:
        side = str(cls._enum_value(cls._get(position_data, "side", "long"))).lower()
        raw_quantity = int(float(cls._get(position_data, "qty", 0) or 0))
        quantity = -abs(raw_quantity) if side == "short" else abs(raw_quantity)

        return Position(
            ticker=str(cls._get(position_data, "symbol", "") or ""),
            quantity=quantity,
            average_cost=cls._safe_float(cls._get(position_data, "avg_entry_price", None)) or 0.0,
            current_price=cls._safe_float(cls._get(position_data, "current_price", None)),
            market_value=cls._safe_float(cls._get(position_data, "market_value", None)),
            unrealized_pnl=cls._safe_float(cls._get(position_data, "unrealized_pl", None)),
            realized_pnl=None,
        )

    @staticmethod
    def _get(item: Any, key: str, default: Any = None) -> Any:
        return AlpacaOrderResponseConverter._get(item, key, default)

    @staticmethod
    def _enum_value(value: Any) -> str:
        return AlpacaOrderResponseConverter._enum_value(value)

    @staticmethod
    def _safe_float(value: Any) -> float | None:
        return AlpacaOrderResponseConverter._safe_float(value)
