"""Filled-order outcome classification."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from common.models.order import OrderSide
from common.models.order_response import OrderResponse
from common.models.position import Position

from conclusion_monitor.serialization import serialize_order, serialize_position


@dataclass
class _OpenLot:
    ticker: str
    quantity: int
    price: float
    order_id: str
    opened_at: datetime | None


class TradeOutcomeClassifier:
    """Pair filled orders FIFO and classify realized/open outcomes."""

    def classify(
        self,
        filled_orders: list[OrderResponse],
        positions: list[Position],
    ) -> dict[str, Any]:
        """Return realized and open-position outcome buckets."""
        closed_trades, unpaired_orders = self._pair_orders(filled_orders)
        successful = [trade for trade in closed_trades if trade["realized_pnl"] > 0]
        unsuccessful = [trade for trade in closed_trades if trade["realized_pnl"] <= 0]
        open_positions = [self._open_position_outcome(position) for position in positions]

        return {
            "closed_trades": closed_trades,
            "successful_trades": successful,
            "unsuccessful_trades": unsuccessful,
            "unpaired_filled_orders": unpaired_orders,
            "open_position_outcomes": open_positions,
            "summary": {
                "closed_trade_count": len(closed_trades),
                "successful_trade_count": len(successful),
                "unsuccessful_trade_count": len(unsuccessful),
                "unpaired_filled_order_count": len(unpaired_orders),
                "realized_trade_pnl": round(
                    sum(float(trade["realized_pnl"]) for trade in closed_trades),
                    2,
                ),
                "open_position_unrealized_pnl": round(
                    sum(float(item.get("unrealized_pnl") or 0.0) for item in open_positions),
                    2,
                ),
            },
        }

    def _pair_orders(
        self,
        filled_orders: list[OrderResponse],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        lots_by_ticker: dict[str, list[_OpenLot]] = {}
        closed_trades: list[dict[str, Any]] = []
        unpriced_orders: list[dict[str, Any]] = []

        for order in sorted(filled_orders, key=self._order_sort_key):
            quantity = self._filled_quantity(order)
            price = self._fill_price(order)
            if quantity <= 0:
                continue
            if price is None or price <= 0:
                unpriced_orders.append(
                    {
                        "reason": "missing_fill_price",
                        "order": serialize_order(order),
                    }
                )
                continue

            signed_quantity = quantity if order.side == OrderSide.BUY else -quantity
            remaining = abs(signed_quantity)
            side_sign = 1 if signed_quantity > 0 else -1
            ticker = order.ticker.upper()
            lots = lots_by_ticker.setdefault(ticker, [])

            while remaining > 0 and lots and lots[0].quantity * side_sign < 0:
                lot = lots[0]
                close_quantity = min(remaining, abs(lot.quantity))
                closed_trades.append(
                    self._closed_trade(lot, order, price, close_quantity)
                )
                lot.quantity += close_quantity if lot.quantity < 0 else -close_quantity
                remaining -= close_quantity
                if lot.quantity == 0:
                    lots.pop(0)

            if remaining > 0:
                lots.append(
                    _OpenLot(
                        ticker=ticker,
                        quantity=remaining * side_sign,
                        price=price,
                        order_id=order.order_id,
                        opened_at=self._order_time(order),
                    )
                )

        unpaired = [
            self._unpaired_lot(lot)
            for lots in lots_by_ticker.values()
            for lot in lots
            if lot.quantity != 0
        ]
        return closed_trades, [*unpriced_orders, *unpaired]

    def _closed_trade(
        self,
        lot: _OpenLot,
        closing_order: OrderResponse,
        close_price: float,
        quantity: int,
    ) -> dict[str, Any]:
        is_long = lot.quantity > 0
        pnl = (
            (close_price - lot.price) * quantity
            if is_long
            else (lot.price - close_price) * quantity
        )
        pnl_pct = (
            ((close_price - lot.price) / lot.price) * 100.0
            if is_long
            else ((lot.price - close_price) / lot.price) * 100.0
        )
        return {
            "ticker": lot.ticker,
            "direction": "long" if is_long else "short",
            "quantity": quantity,
            "entry_order_id": lot.order_id,
            "exit_order_id": closing_order.order_id,
            "entry_price": round(lot.price, 4),
            "exit_price": round(close_price, 4),
            "opened_at": lot.opened_at.isoformat() if lot.opened_at else None,
            "closed_at": _iso(self._order_time(closing_order)),
            "realized_pnl": round(pnl, 2),
            "realized_pnl_percent": round(pnl_pct, 2),
            "result": "successful" if pnl > 0 else "unsuccessful",
        }

    def _open_position_outcome(self, position: Position) -> dict[str, Any]:
        payload = serialize_position(position)
        pnl = position.unrealized_pnl
        if pnl is None:
            result = "unknown"
        elif pnl > 0:
            result = "successful"
        elif pnl < 0:
            result = "unsuccessful"
        else:
            result = "flat"
        payload["result"] = result
        return payload

    @staticmethod
    def _unpaired_lot(lot: _OpenLot) -> dict[str, Any]:
        return {
            "reason": "not_closed_within_report_day",
            "ticker": lot.ticker,
            "side": "BUY" if lot.quantity > 0 else "SELL",
            "remaining_quantity": abs(lot.quantity),
            "average_fill_price": lot.price,
            "order_id": lot.order_id,
            "opened_at": lot.opened_at.isoformat() if lot.opened_at else None,
        }

    @staticmethod
    def _filled_quantity(order: OrderResponse) -> int:
        if order.filled_quantity > 0:
            return int(order.filled_quantity)
        return int(order.quantity) if order.is_filled else 0

    @staticmethod
    def _fill_price(order: OrderResponse) -> float | None:
        return order.average_fill_price or order.limit_price or order.stop_price

    @staticmethod
    def _order_time(order: OrderResponse) -> datetime | None:
        return order.filled_at or order.updated_at or order.created_at

    def _order_sort_key(self, order: OrderResponse) -> tuple[float, str]:
        timestamp = self._order_time(order)
        if timestamp is None:
            return 0.0, order.order_id
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        return timestamp.timestamp(), order.order_id


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None
