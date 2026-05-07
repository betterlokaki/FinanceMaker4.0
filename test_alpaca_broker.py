"""Unit tests for Alpaca broker behavior with a fake client."""
from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from types import SimpleNamespace
from typing import Any

import requests

from common.models.order import OrderSide, OrderStatus, OrderType, TimeInForce
from common.models.order_request import OrderRequest
from common.settings import AlpacaConfig
from publishers.alpaca import AlpacaBroker


class FakeAlpacaClient:
    def __init__(self, fail_next_account: bool = False) -> None:
        self.fail_next_account = fail_next_account
        self.submitted_orders: list[Any] = []
        self.cancelled_orders: list[str] = []
        self.orders = [
            SimpleNamespace(
                id="open-1",
                symbol="AAPL",
                qty="10",
                filled_qty="0",
                side="buy",
                type="limit",
                status="new",
                limit_price="100",
                stop_price=None,
                filled_avg_price=None,
                time_in_force="day",
                legs=[],
            )
        ]

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
        return [
            SimpleNamespace(
                symbol="AAPL",
                qty="1",
                side="long",
                avg_entry_price="100",
                current_price="110",
                market_value="110",
                unrealized_pl="10",
            )
        ]

    def get_orders(self, _filter: Any = None) -> list[SimpleNamespace]:
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
        self.submitted_orders.append(order_data)
        return SimpleNamespace(
            id="submitted-1",
            symbol=order_data.symbol,
            qty=str(int(order_data.qty)),
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

    def get_portfolio_history(self, _filter: Any = None) -> SimpleNamespace:
        ts = int(datetime(2026, 4, 1, tzinfo=timezone.utc).timestamp())
        return SimpleNamespace(timestamp=[ts], equity=[1000.0])


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
        assert len(portfolio.open_orders) == 1

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
