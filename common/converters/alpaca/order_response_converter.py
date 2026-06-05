"""Alpaca order response converter."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from common.models.order import OrderSide, OrderStatus, OrderType, TimeInForce
from common.models.order_response import OrderResponse


class AlpacaOrderResponseConverter:
    """Convert Alpaca order models into normalized order responses."""

    SIDE_MAP: dict[str, OrderSide] = {
        "buy": OrderSide.BUY,
        "sell": OrderSide.SELL,
        "BUY": OrderSide.BUY,
        "SELL": OrderSide.SELL,
    }

    ORDER_TYPE_MAP: dict[str, OrderType] = {
        "market": OrderType.MARKET,
        "limit": OrderType.LIMIT,
        "stop": OrderType.STOP,
        "stop_limit": OrderType.STOP_LIMIT,
        "trailing_stop": OrderType.TRAILING_STOP,
    }

    TIF_MAP: dict[str, TimeInForce] = {
        "day": TimeInForce.DAY,
        "gtc": TimeInForce.GTC,
        "ioc": TimeInForce.IOC,
        "fok": TimeInForce.FOK,
    }

    STATUS_MAP: dict[str, OrderStatus] = {
        "new": OrderStatus.SUBMITTED,
        "accepted": OrderStatus.SUBMITTED,
        "pending_new": OrderStatus.PENDING,
        "accepted_for_bidding": OrderStatus.SUBMITTED,
        "pending_review": OrderStatus.PENDING,
        "held": OrderStatus.PENDING,
        "pending_cancel": OrderStatus.PENDING,
        "pending_replace": OrderStatus.PENDING,
        "partially_filled": OrderStatus.PARTIALLY_FILLED,
        "filled": OrderStatus.FILLED,
        "done_for_day": OrderStatus.CANCELLED,
        "canceled": OrderStatus.CANCELLED,
        "cancelled": OrderStatus.CANCELLED,
        "replaced": OrderStatus.CANCELLED,
        "expired": OrderStatus.EXPIRED,
        "rejected": OrderStatus.REJECTED,
        "stopped": OrderStatus.REJECTED,
        "suspended": OrderStatus.REJECTED,
        "calculated": OrderStatus.SUBMITTED,
    }

    @classmethod
    def from_alpaca(cls, alpaca_order: Any) -> OrderResponse:
        """Convert an alpaca-py Order-like object or dict."""
        order_type = cls._enum_value(
            cls._get(alpaca_order, "type", cls._get(alpaca_order, "order_type", "market"))
        )
        side = cls._enum_value(cls._get(alpaca_order, "side", "buy"))
        status = cls._enum_value(cls._get(alpaca_order, "status", "new"))
        tif = cls._enum_value(cls._get(alpaca_order, "time_in_force", "day"))

        return OrderResponse(
            order_id=str(cls._get(alpaca_order, "id", cls._get(alpaca_order, "client_order_id", ""))),
            ticker=str(cls._get(alpaca_order, "symbol", "") or ""),
            quantity=int(float(cls._get(alpaca_order, "qty", 0) or 0)),
            filled_quantity=int(float(cls._get(alpaca_order, "filled_qty", 0) or 0)),
            side=cls.SIDE_MAP.get(side, OrderSide.BUY),
            order_type=cls.ORDER_TYPE_MAP.get(order_type, OrderType.MARKET),
            status=cls.STATUS_MAP.get(status, OrderStatus.PENDING),
            limit_price=cls._safe_float(cls._get(alpaca_order, "limit_price", None)),
            stop_price=cls._safe_float(cls._get(alpaca_order, "stop_price", None)),
            average_fill_price=cls._safe_float(cls._get(alpaca_order, "filled_avg_price", None)),
            time_in_force=cls.TIF_MAP.get(tif, TimeInForce.DAY),
            created_at=cls._safe_datetime(cls._get(alpaca_order, "created_at", None)) or datetime.now(),
            updated_at=cls._safe_datetime(cls._get(alpaca_order, "updated_at", None)) or datetime.now(),
            filled_at=cls._safe_datetime(cls._get(alpaca_order, "filled_at", None)),
        )

    @classmethod
    def flatten_orders(cls, orders: list[Any]) -> list[Any]:
        """Flatten nested Alpaca orders so child bracket legs are visible."""
        flattened: list[Any] = []
        for order in orders:
            flattened.append(order)
            legs = cls._get(order, "legs", None)
            if isinstance(legs, list):
                flattened.extend(cls.flatten_orders(legs))
        return flattened

    @staticmethod
    def _get(item: Any, key: str, default: Any = None) -> Any:
        if isinstance(item, dict):
            return item.get(key, default)
        return getattr(item, key, default)

    @staticmethod
    def _enum_value(value: Any) -> str:
        raw = getattr(value, "value", value)
        return str(raw)

    @staticmethod
    def _safe_float(value: Any) -> float | None:
        try:
            if value is None:
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _safe_datetime(value: Any) -> datetime | None:
        if isinstance(value, datetime):
            return value
        if not value:
            return None
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                return None
        return None
