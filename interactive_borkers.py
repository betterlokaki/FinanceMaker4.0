"""IBKR order adapter for DAY limit entry with GTC bracket exits.

Intentionally named ``interactive_borkers.py`` (as requested).
"""
from __future__ import annotations

from dataclasses import dataclass

from common.models.order import OrderSide, OrderType, TimeInForce
from common.models.order_request import OrderRequest
from common.models.order_response import OrderResponse
from publishers.abstracts.i_broker import IBroker


@dataclass(frozen=True)
class BracketOrderPlan:
    """Normalized order plan used by live strategy."""

    ticker: str
    quantity: int
    entry_price: float
    take_profit_price: float
    stop_price: float


class InteractiveBorkersOrderAdapter:
    """Places IBKR-compatible DAY + GTC bracket orders."""

    def __init__(
        self,
        buy_limit_rth: bool = True,
        take_profit_rth: bool = True,
        stop_loss_rth: bool = True,
    ) -> None:
        self._buy_limit_rth = buy_limit_rth
        self._take_profit_rth = take_profit_rth
        self._stop_loss_rth = stop_loss_rth

    async def place_day_limit_with_gtc_exits(
        self,
        broker: IBroker,
        plan: BracketOrderPlan,
    ) -> OrderResponse:
        """Submit a parent limit buy + TP + stop exits."""
        request = OrderRequest(
            ticker=plan.ticker.upper(),
            quantity=plan.quantity,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            limit_price=round(plan.entry_price, 2),
            take_profit_price=round(plan.take_profit_price, 2),
            stop_price=round(plan.stop_price, 2),
            time_in_force=TimeInForce.DAY,
            buy_limit_rth=self._buy_limit_rth,
            take_profit_rth=self._take_profit_rth,
            stop_loss_rth=self._stop_loss_rth,
        )
        return await broker.place_order(request)
