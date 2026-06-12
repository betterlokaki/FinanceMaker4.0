"""Unit tests for Alpaca broker behavior with a fake client."""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from types import SimpleNamespace
from typing import Any

import requests
from alpaca.trading.enums import QueryOrderStatus
from alpaca.trading.enums import OrderClass

from common.models.order import OrderSide, OrderStatus, OrderType, TimeInForce
from common.models.order_request import OrderRequest
from common.settings import AlpacaConfig
from publishers.alpaca import AlpacaBroker


def _position(
    symbol: str,
    qty: str = "1",
    side: str = "long",
    avg_entry_price: str = "100",
) -> SimpleNamespace:
    signed_market_value = str(float(qty) * float(avg_entry_price))
    if side == "short":
        signed_market_value = f"-{abs(float(signed_market_value))}"
    return SimpleNamespace(
        symbol=symbol,
        qty=qty,
        side=side,
        avg_entry_price=avg_entry_price,
        current_price=avg_entry_price,
        market_value=signed_market_value,
        unrealized_pl="0",
    )


def _order(
    order_id: str,
    symbol: str,
    qty: str,
    side: str,
    order_type: str,
    limit_price: str | None = None,
    stop_price: str | None = None,
    time_in_force: str = "gtc",
    status: str = "new",
    legs: list[SimpleNamespace] | None = None,
    created_at: datetime | None = None,
    updated_at: datetime | None = None,
    filled_at: datetime | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=order_id,
        symbol=symbol,
        qty=qty,
        filled_qty="0",
        side=side,
        type=order_type,
        status=status,
        limit_price=limit_price,
        stop_price=stop_price,
        filled_avg_price=None,
        time_in_force=time_in_force,
        created_at=created_at,
        updated_at=updated_at,
        filled_at=filled_at,
        legs=legs or [],
    )


def _protected_exit_orders(
    symbol: str = "AAPL",
    qty: str = "1",
    side: str = "sell",
    take_profit: str = "999",
    stop_loss: str = "1",
) -> list[SimpleNamespace]:
    stop = _order(
        order_id=f"{symbol}-sl",
        symbol=symbol,
        qty=qty,
        side=side,
        order_type="stop",
        stop_price=stop_loss,
    )
    take = _order(
        order_id=f"{symbol}-tp",
        symbol=symbol,
        qty=qty,
        side=side,
        order_type="limit",
        limit_price=take_profit,
        legs=[stop],
    )
    return [take]


class FakeAlpacaClient:
    def __init__(
        self,
        fail_next_account: bool = False,
        positions: list[SimpleNamespace] | None = None,
        orders: list[SimpleNamespace] | None = None,
        reject_oco: bool = False,
    ) -> None:
        self.fail_next_account = fail_next_account
        self.reject_oco = reject_oco
        self.submitted_orders: list[Any] = []
        self.order_filters: list[Any] = []
        self.cancelled_orders: list[str] = []
        self.closed_positions: list[str] = []
        self.positions = positions if positions is not None else [_position("AAPL")]
        self.orders = orders if orders is not None else _protected_exit_orders()

    def get_account(self) -> SimpleNamespace:
        if self.fail_next_account:
            self.fail_next_account = False
            raise requests.ConnectionError("temporary network failure")
        return SimpleNamespace(
            account_number="PA123",
            cash="1000",
            long_market_value="100",
            short_market_value="0",
            equity="1100",
            last_equity="1075",
            portfolio_value="1100",
            buying_power="5000",
        )

    def get_all_positions(self) -> list[SimpleNamespace]:
        return self.positions

    def get_orders(self, _filter: Any = None) -> list[SimpleNamespace]:
        self.order_filters.append(_filter)
        return self.orders

    def get_order_by_id(self, order_id: str, _filter: Any = None) -> SimpleNamespace:
        status = "canceled" if order_id in self.cancelled_orders else "new"
        return SimpleNamespace(
            id=order_id,
            symbol="AAPL",
            qty="10",
            filled_qty="0",
            side="buy",
            type="limit",
            status=status,
            limit_price="100",
            stop_price=None,
            filled_avg_price=None,
            time_in_force="day",
        )

    def submit_order(self, order_data: Any) -> SimpleNamespace:
        if getattr(getattr(order_data, "order_class", None), "value", None) == OrderClass.OCO.value:
            if self.reject_oco:
                raise ValueError("OCO rejected")
            self._add_oco_order(order_data)

        self.submitted_orders.append(order_data)
        return SimpleNamespace(
            id="submitted-1",
            symbol=order_data.symbol,
            qty=str(order_data.qty),
            filled_qty="0",
            side=order_data.side.value,
            type=order_data.type.value,
            status="new",
            limit_price=str(getattr(order_data, "limit_price", "") or ""),
            stop_price=None,
            filled_avg_price=None,
            time_in_force=order_data.time_in_force.value,
        )

    def cancel_order_by_id(self, order_id: str) -> None:
        self.cancelled_orders.append(order_id)
        self._set_order_status(order_id, "canceled")

    def close_position(self, symbol: str) -> SimpleNamespace:
        self.closed_positions.append(symbol)
        self.positions = [
            position for position in self.positions if position.symbol.upper() != symbol.upper()
        ]
        return SimpleNamespace(
            id=f"close-{symbol}",
            symbol=symbol,
            qty="1",
            filled_qty="0",
            side="sell",
            type="market",
            status="new",
            limit_price=None,
            stop_price=None,
            filled_avg_price=None,
            time_in_force="day",
        )

    def get_portfolio_history(self, _filter: Any = None) -> SimpleNamespace:
        ts = int(datetime(2026, 4, 1, tzinfo=timezone.utc).timestamp())
        return SimpleNamespace(timestamp=[ts], equity=[1000.0])

    def _add_oco_order(self, order_data: Any) -> None:
        stop = _order(
            order_id=f"{order_data.symbol}-oco-sl",
            symbol=order_data.symbol,
            qty=str(order_data.qty),
            side=order_data.side.value,
            order_type="stop",
            stop_price=str(order_data.stop_loss.stop_price),
        )
        take = _order(
            order_id=f"{order_data.symbol}-oco-tp",
            symbol=order_data.symbol,
            qty=str(order_data.qty),
            side=order_data.side.value,
            order_type="limit",
            limit_price=str(order_data.take_profit.limit_price),
            legs=[stop],
        )
        self.orders.append(take)

    def _set_order_status(self, order_id: str, status: str) -> None:
        for order in self._flatten_orders(self.orders):
            if order.id == order_id:
                order.status = status

    def _flatten_orders(self, orders: list[SimpleNamespace]) -> list[SimpleNamespace]:
        flattened: list[SimpleNamespace] = []
        for order in orders:
            flattened.append(order)
            flattened.extend(self._flatten_orders(order.legs))
        return flattened


def _config() -> AlpacaConfig:
    return AlpacaConfig(
        api_key="key",
        secret_key="secret",
        paper=True,
        request_retry_attempts=1,
        request_retry_delay_seconds=0.0,
        portfolio_refresh_interval_seconds=0,
    )


def test_broker_connects_fetches_portfolio_places_and_cancels_order() -> None:
    async def _run() -> None:
        client = FakeAlpacaClient()
        broker = AlpacaBroker(_config(), client_factory=lambda **_: client)

        await broker.connect()
        portfolio = await broker.get_portfolio()

        assert broker.is_connected
        assert portfolio.buying_power == 5000.0
        assert portfolio.position_count == 1
        assert len(portfolio.open_orders) == 2

        response = await broker.place_order(
            OrderRequest(
                ticker="AAPL",
                quantity=10,
                side=OrderSide.BUY,
                order_type=OrderType.LIMIT,
                limit_price=100.0,
                stop_loss_price=98.0,
                take_profit_price=106.0,
                time_in_force=TimeInForce.DAY,
            )
        )
        assert response.order_id == "submitted-1"
        assert len(client.submitted_orders) == 1

        cancelled = await broker.cancel_order("submitted-1")
        assert cancelled.status == OrderStatus.CANCELLED
        assert client.cancelled_orders == ["submitted-1"]
        await broker.disconnect()
        assert not broker.is_connected

    asyncio.run(_run())


def test_startup_keeps_existing_tp_sl_even_when_prices_differ_from_config() -> None:
    async def _run() -> None:
        client = FakeAlpacaClient(
            positions=[_position("AAPL", qty="1", side="long", avg_entry_price="100")],
            orders=_protected_exit_orders(
                symbol="AAPL",
                qty="1",
                side="sell",
                take_profit="500",
                stop_loss="50",
            ),
        )
        broker = AlpacaBroker(_config(), client_factory=lambda **_: client)

        await broker.connect()

        assert client.submitted_orders == []
        assert client.cancelled_orders == []
        assert client.closed_positions == []
        await broker.disconnect()

    asyncio.run(_run())


def test_startup_attaches_gtc_rth_oco_for_unprotected_long_position() -> None:
    async def _run() -> None:
        client = FakeAlpacaClient(
            positions=[_position("AAPL", qty="2", side="long", avg_entry_price="100")],
            orders=[],
        )
        broker = AlpacaBroker(_config(), client_factory=lambda **_: client)

        await broker.connect()

        assert len(client.submitted_orders) == 1
        oco = client.submitted_orders[0]
        assert oco.symbol == "AAPL"
        assert oco.qty == 2
        assert oco.side.value == "sell"
        assert oco.time_in_force.value == "gtc"
        assert oco.order_class.value == "oco"
        assert oco.extended_hours is False
        assert oco.take_profit.limit_price == 105.0
        assert oco.stop_loss.stop_price == 97.0
        assert client.closed_positions == []
        await broker.disconnect()

    asyncio.run(_run())


def test_startup_attaches_inverse_oco_prices_for_unprotected_short_position() -> None:
    async def _run() -> None:
        client = FakeAlpacaClient(
            positions=[_position("TSLA", qty="2", side="short", avg_entry_price="100")],
            orders=[],
        )
        broker = AlpacaBroker(_config(), client_factory=lambda **_: client)

        await broker.connect()

        assert len(client.submitted_orders) == 1
        oco = client.submitted_orders[0]
        assert oco.symbol == "TSLA"
        assert oco.qty == 2
        assert oco.side.value == "buy"
        assert oco.time_in_force.value == "gtc"
        assert oco.order_class.value == "oco"
        assert oco.extended_hours is False
        assert oco.take_profit.limit_price == 95.0
        assert oco.stop_loss.stop_price == 103.0
        assert client.closed_positions == []
        await broker.disconnect()

    asyncio.run(_run())


def test_startup_closes_only_unprotected_symbol_when_oco_attachment_fails() -> None:
    async def _run() -> None:
        client = FakeAlpacaClient(
            positions=[
                _position("AAPL", qty="1", side="long", avg_entry_price="100"),
                _position("MSFT", qty="1", side="long", avg_entry_price="200"),
            ],
            orders=_protected_exit_orders(
                symbol="MSFT",
                qty="1",
                side="sell",
                take_profit="300",
                stop_loss="150",
            ),
            reject_oco=True,
        )
        broker = AlpacaBroker(_config(), client_factory=lambda **_: client)

        await broker.connect()

        assert client.closed_positions == ["AAPL"]
        assert client.positions == [_position("MSFT", qty="1", side="long", avg_entry_price="200")]
        await broker.disconnect()

    asyncio.run(_run())


def test_broker_get_buying_power_reconnects_and_retries_transient_failure() -> None:
    async def _run() -> None:
        first_client = FakeAlpacaClient()
        clients = [first_client, FakeAlpacaClient()]

        def factory(**_: Any) -> FakeAlpacaClient:
            return clients.pop(0)

        broker = AlpacaBroker(_config(), client_factory=factory)
        await broker.connect()
        first_client.fail_next_account = True

        buying_power = await broker.get_buying_power()

        assert buying_power == 5000.0
        assert len(clients) == 0
        await broker.disconnect()

    asyncio.run(_run())


def test_pnl_summary_uses_daily_equity_delta_and_history_baseline() -> None:
    async def _run() -> None:
        client = FakeAlpacaClient()
        broker = AlpacaBroker(_config(), client_factory=lambda **_: client)
        await broker.connect()

        summary = await broker.get_pnl_summary(date(2026, 4, 1))

        assert summary.daily_pnl == 25.0
        assert summary.pnl_since_date == 100.0
        assert summary.baseline_date == date(2026, 4, 1)
        assert summary.baseline_nav == 1000.0
        assert summary.current_nav == 1100.0
        await broker.disconnect()

    asyncio.run(_run())


def test_get_orders_between_uses_all_status_and_time_bounds() -> None:
    async def _run() -> None:
        client = FakeAlpacaClient(
            positions=[],
            orders=[
                _order(
                    order_id="filled-1",
                    symbol="AAPL",
                    qty="2",
                    side="buy",
                    order_type="limit",
                    status="filled",
                    filled_at=datetime(2026, 4, 23, 14, 35, tzinfo=timezone.utc),
                )
            ],
        )
        broker = AlpacaBroker(_config(), client_factory=lambda **_: client)
        await broker.connect()
        client.order_filters.clear()

        after = datetime(2026, 4, 23, 4, 0, tzinfo=timezone.utc)
        until = datetime(2026, 4, 24, 0, 0, tzinfo=timezone.utc)
        orders = await broker.get_orders_between(after=after, until=until)

        assert len(orders) == 1
        assert orders[0].order_id == "filled-1"
        request = client.order_filters[-1]
        assert request.status == QueryOrderStatus.ALL
        assert request.after == after
        assert request.until == until
        assert request.nested is True
        await broker.disconnect()

    asyncio.run(_run())
