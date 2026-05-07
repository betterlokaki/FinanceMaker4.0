"""Alpaca order request converter."""
from __future__ import annotations

import logging
import time

from alpaca.trading.enums import (
    OrderClass as AlpacaOrderClass,
    OrderSide as AlpacaOrderSide,
    TimeInForce as AlpacaTimeInForce,
)
from alpaca.trading.requests import (
    LimitOrderRequest,
    MarketOrderRequest,
    StopLimitOrderRequest,
    StopLossRequest,
    StopOrderRequest,
    TakeProfitRequest,
    TrailingStopOrderRequest,
)

from common.models.order import OrderSide, OrderType, TimeInForce
from common.models.order_request import OrderRequest

logger = logging.getLogger(__name__)


class AlpacaOrderRequestConverter:
    """Convert normalized order requests into alpaca-py request models."""

    SIDE_MAP: dict[OrderSide, AlpacaOrderSide] = {
        OrderSide.BUY: AlpacaOrderSide.BUY,
        OrderSide.SELL: AlpacaOrderSide.SELL,
    }

    TIF_MAP: dict[TimeInForce, AlpacaTimeInForce] = {
        TimeInForce.DAY: AlpacaTimeInForce.DAY,
        TimeInForce.GTC: AlpacaTimeInForce.GTC,
        TimeInForce.IOC: AlpacaTimeInForce.IOC,
        TimeInForce.FOK: AlpacaTimeInForce.FOK,
    }

    BRACKET_TIFS: set[TimeInForce] = {TimeInForce.DAY, TimeInForce.GTC}

    @classmethod
    def to_alpaca(cls, order_request: OrderRequest) -> object:
        """Convert an app order request into an alpaca-py order request."""
        if cls._is_bracket(order_request):
            return cls._to_bracket(order_request)

        if order_request.take_profit_price is not None or order_request.stop_loss_price is not None:
            raise ValueError(
                "Alpaca advanced orders require both take_profit_price and stop_loss_price"
            )

        side = cls.SIDE_MAP[order_request.side]
        tif = cls._to_tif(order_request.time_in_force)
        client_order_id = cls._build_client_order_id(order_request.ticker)
        extended_hours = cls._simple_extended_hours(order_request)

        if order_request.order_type == OrderType.MARKET:
            return MarketOrderRequest(
                symbol=order_request.ticker.upper(),
                qty=order_request.quantity,
                side=side,
                time_in_force=tif,
                client_order_id=client_order_id,
                extended_hours=False,
            )
        if order_request.order_type == OrderType.LIMIT:
            return LimitOrderRequest(
                symbol=order_request.ticker.upper(),
                qty=order_request.quantity,
                side=side,
                time_in_force=tif,
                limit_price=order_request.limit_price,
                client_order_id=client_order_id,
                extended_hours=extended_hours,
            )
        if order_request.order_type == OrderType.STOP:
            return StopOrderRequest(
                symbol=order_request.ticker.upper(),
                qty=order_request.quantity,
                side=side,
                time_in_force=tif,
                stop_price=order_request.stop_price,
                client_order_id=client_order_id,
                extended_hours=False,
            )
        if order_request.order_type == OrderType.STOP_LIMIT:
            return StopLimitOrderRequest(
                symbol=order_request.ticker.upper(),
                qty=order_request.quantity,
                side=side,
                time_in_force=tif,
                stop_price=order_request.stop_price,
                limit_price=order_request.limit_price,
                client_order_id=client_order_id,
                extended_hours=False,
            )
        if order_request.order_type == OrderType.TRAILING_STOP:
            return cls._to_trailing_stop(order_request, side, tif, client_order_id)

        raise ValueError(f"Unsupported Alpaca order type: {order_request.order_type}")

    @classmethod
    def _to_bracket(cls, order_request: OrderRequest) -> object:
        if order_request.trailing_stop_amt is not None:
            raise ValueError("Alpaca native bracket orders do not support trailing stop legs")
        if order_request.order_type not in (OrderType.MARKET, OrderType.LIMIT):
            raise ValueError("Alpaca bracket parent must be a market or limit order")
        if order_request.time_in_force not in cls.BRACKET_TIFS:
            raise ValueError("Alpaca bracket orders require DAY or GTC time in force")
        if order_request.take_profit_rth is False:
            raise ValueError(
                "Alpaca native bracket orders do not support extended-hours take-profit legs"
            )
        if order_request.stop_loss_rth is not None:
            logger.info(
                "Ignoring stop_loss_rth=%s for Alpaca bracket order; Alpaca has no separate "
                "stop-loss RTH toggle on native brackets.",
                order_request.stop_loss_rth,
            )

        stop_price = cls._resolve_stop_loss_price(order_request)
        if stop_price is None or order_request.take_profit_price is None:
            raise ValueError("Alpaca bracket orders require stop_loss_price and take_profit_price")

        kwargs = {
            "symbol": order_request.ticker.upper(),
            "qty": order_request.quantity,
            "side": cls.SIDE_MAP[order_request.side],
            "time_in_force": cls._to_tif(order_request.time_in_force),
            "order_class": AlpacaOrderClass.BRACKET,
            "take_profit": TakeProfitRequest(limit_price=order_request.take_profit_price),
            "stop_loss": StopLossRequest(stop_price=stop_price),
            "client_order_id": cls._build_client_order_id(order_request.ticker),
            "extended_hours": False,
        }

        if order_request.order_type == OrderType.MARKET:
            return MarketOrderRequest(**kwargs)

        return LimitOrderRequest(
            **kwargs,
            limit_price=order_request.limit_price,
        )

    @classmethod
    def _to_trailing_stop(
        cls,
        order_request: OrderRequest,
        side: AlpacaOrderSide,
        tif: AlpacaTimeInForce,
        client_order_id: str,
    ) -> TrailingStopOrderRequest:
        if order_request.trailing_stop_amt is None:
            raise ValueError("Trailing stop orders require trailing_stop_amt")

        kwargs: dict[str, object] = {
            "symbol": order_request.ticker.upper(),
            "qty": order_request.quantity,
            "side": side,
            "time_in_force": tif,
            "client_order_id": client_order_id,
            "extended_hours": False,
        }
        if order_request.trailing_stop_type == "amt":
            kwargs["trail_price"] = order_request.trailing_stop_amt
        else:
            kwargs["trail_percent"] = order_request.trailing_stop_amt

        return TrailingStopOrderRequest(**kwargs)

    @classmethod
    def _to_tif(cls, time_in_force: TimeInForce) -> AlpacaTimeInForce:
        try:
            return cls.TIF_MAP[time_in_force]
        except KeyError as exc:
            raise ValueError(f"Unsupported Alpaca time in force: {time_in_force}") from exc

    @staticmethod
    def _is_bracket(order_request: OrderRequest) -> bool:
        return (
            order_request.take_profit_price is not None
            and (
                order_request.stop_loss_price is not None
                or order_request.stop_price is not None
                or order_request.trailing_stop_amt is not None
            )
        )

    @staticmethod
    def _resolve_stop_loss_price(order_request: OrderRequest) -> float | None:
        if order_request.stop_loss_price is not None:
            return order_request.stop_loss_price
        return order_request.stop_price

    @staticmethod
    def _simple_extended_hours(order_request: OrderRequest) -> bool:
        if order_request.order_type != OrderType.LIMIT:
            return False
        return order_request.buy_limit_rth is False

    @staticmethod
    def _build_client_order_id(ticker: str) -> str:
        return f"{ticker.upper()}-{time.time_ns()}"
