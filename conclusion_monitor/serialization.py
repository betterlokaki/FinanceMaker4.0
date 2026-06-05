"""Serialization helpers for conclusion report payloads."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from common.models.order_response import OrderResponse
from common.models.pnl_summary import PnlSummary
from common.models.portfolio import Portfolio
from common.models.position import Position


def json_default(value: Any) -> str:
    """JSON fallback for dates, datetimes, enums, and unknown SDK values."""
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    enum_value = getattr(value, "value", None)
    if enum_value is not None:
        return str(enum_value)
    return str(value)


def serialize_order(order: OrderResponse) -> dict[str, Any]:
    """Serialize a normalized broker order."""
    return {
        "order_id": order.order_id,
        "ticker": order.ticker.upper(),
        "quantity": order.quantity,
        "filled_quantity": order.filled_quantity,
        "remaining_quantity": order.remaining_quantity,
        "side": order.side.value,
        "order_type": order.order_type.value,
        "status": order.status.value,
        "limit_price": order.limit_price,
        "stop_price": order.stop_price,
        "average_fill_price": order.average_fill_price,
        "time_in_force": order.time_in_force.value,
        "created_at": _dt(order.created_at),
        "updated_at": _dt(order.updated_at),
        "filled_at": _dt(order.filled_at),
    }


def serialize_position(position: Position) -> dict[str, Any]:
    """Serialize a portfolio position."""
    return {
        "ticker": position.ticker.upper(),
        "quantity": position.quantity,
        "average_cost": position.average_cost,
        "current_price": position.current_price,
        "market_value": position.market_value,
        "unrealized_pnl": position.unrealized_pnl,
        "realized_pnl": position.realized_pnl,
        "is_long": position.is_long,
        "is_short": position.is_short,
        "cost_basis": position.cost_basis,
        "unrealized_pnl_percent": position.unrealized_pnl_percent,
    }


def serialize_portfolio(portfolio: Portfolio) -> dict[str, Any]:
    """Serialize portfolio account state."""
    return {
        "cash_balance": portfolio.cash_balance,
        "total_market_value": portfolio.total_market_value,
        "total_equity": portfolio.total_equity,
        "buying_power": portfolio.buying_power,
        "unrealized_pnl": portfolio.unrealized_pnl,
        "realized_pnl": portfolio.realized_pnl,
        "position_count": portfolio.position_count,
        "positions": [serialize_position(position) for position in portfolio.positions],
        "open_orders": [serialize_order(order) for order in portfolio.open_orders],
    }


def serialize_pnl(summary: PnlSummary) -> dict[str, Any]:
    """Serialize P/L summary state."""
    return {
        "as_of_date": summary.as_of_date.isoformat(),
        "since_date": summary.since_date.isoformat(),
        "currency": summary.currency,
        "daily_pnl": summary.daily_pnl,
        "pnl_since_date": summary.pnl_since_date,
        "baseline_date": summary.baseline_date.isoformat() if summary.baseline_date else None,
        "baseline_nav": summary.baseline_nav,
        "current_nav": summary.current_nav,
    }


def dataframe_to_candles(frame: Any) -> list[dict[str, Any]]:
    """Serialize an OHLCV DataFrame returned by a market provider."""
    if frame is None or getattr(frame, "empty", True):
        return []

    candles: list[dict[str, Any]] = []
    for timestamp, row in frame.sort_index().iterrows():
        period = row.get("period")
        period_value = getattr(period, "value", period)
        candles.append(
            {
                "time": _dt(_to_datetime(timestamp)),
                "open": _float_or_none(row.get("open")),
                "high": _float_or_none(row.get("high")),
                "low": _float_or_none(row.get("low")),
                "close": _float_or_none(row.get("close")),
                "volume": _int_or_none(row.get("volume")),
                "period": str(period_value) if period_value is not None else None,
            }
        )
    return candles


def _to_datetime(value: Any) -> datetime | None:
    to_pydatetime = getattr(value, "to_pydatetime", None)
    if callable(to_pydatetime):
        return to_pydatetime()
    return value if isinstance(value, datetime) else None


def _dt(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _float_or_none(value: Any) -> float | None:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def _int_or_none(value: Any) -> int | None:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None
