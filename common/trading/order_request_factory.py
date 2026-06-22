"""Reusable factories for project OrderRequest models."""
from __future__ import annotations

from common.models.order import OrderSide, OrderType, TimeInForce
from common.models.order_request import OrderRequest
from common.models.strategy_input import StrategyInputModel


class OrderRequestFactory:
    """Build common order request shapes from StrategyInputModel."""

    @staticmethod
    def bracket_entry(
        ticker: str,
        quantity: int,
        entry_price: float,
        side: OrderSide,
        strategy_input: StrategyInputModel,
        *,
        time_in_force: TimeInForce = TimeInForce.GTC,
        buy_limit_rth: bool | None = True,
        take_profit_rth: bool | None = True,
        stop_loss_rth: bool | None = False,
    ) -> OrderRequest:
        entry = round(entry_price, 2)
        if side == OrderSide.BUY:
            stop_price = round(entry * (1.0 - strategy_input.risk_pct), 2)
            take_profit_price = round(entry * (1.0 + strategy_input.reward_pct), 2)
        else:
            stop_price = round(entry * (1.0 + strategy_input.risk_pct), 2)
            take_profit_price = round(entry * (1.0 - strategy_input.reward_pct), 2)

        return OrderRequest(
            ticker=ticker.upper(),
            quantity=quantity,
            side=side,
            order_type=OrderType.LIMIT,
            limit_price=entry,
            stop_loss_price=stop_price,
            take_profit_price=take_profit_price,
            time_in_force=time_in_force,
            buy_limit_rth=buy_limit_rth,
            take_profit_rth=take_profit_rth,
            stop_loss_rth=stop_loss_rth,
        )

    @staticmethod
    def plain_extended_hours_entry(
        ticker: str,
        quantity: int,
        entry_price: float,
    ) -> OrderRequest:
        return OrderRequest(
            ticker=ticker.upper(),
            quantity=quantity,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            limit_price=round(entry_price, 2),
            time_in_force=TimeInForce.DAY,
            extended_hours=True,
            buy_limit_rth=False,
        )

    @staticmethod
    def flatten_market(ticker: str, quantity: int, side: OrderSide) -> OrderRequest:
        return OrderRequest(
            ticker=ticker.upper(),
            quantity=quantity,
            side=side,
            order_type=OrderType.MARKET,
            time_in_force=TimeInForce.DAY,
        )
