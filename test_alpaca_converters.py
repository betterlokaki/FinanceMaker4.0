"""Unit tests for Alpaca response and portfolio conversion."""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

from common.converters.alpaca import (
    AlpacaOrderResponseConverter,
    AlpacaPortfolioConverter,
)
from common.models.order import OrderSide, OrderStatus, OrderType, TimeInForce


def test_order_response_converter_maps_status_and_fields() -> None:
    order = SimpleNamespace(
        id="order-1",
        symbol="AAPL",
        qty="10",
        filled_qty="4",
        side="buy",
        type="limit",
        status="partially_filled",
        limit_price="101.25",
        stop_price=None,
        filled_avg_price="101.0",
        time_in_force="day",
        created_at=None,
        updated_at=None,
        filled_at=datetime(2026, 4, 23, 15, 0, tzinfo=timezone.utc),
    )

    response = AlpacaOrderResponseConverter.from_alpaca(order)

    assert response.order_id == "order-1"
    assert response.ticker == "AAPL"
    assert response.quantity == 10
    assert response.filled_quantity == 4
    assert response.side == OrderSide.BUY
    assert response.order_type == OrderType.LIMIT
    assert response.status == OrderStatus.PARTIALLY_FILLED
    assert response.limit_price == 101.25
    assert response.average_fill_price == 101.0
    assert response.time_in_force == TimeInForce.DAY
    assert response.filled_at == datetime(2026, 4, 23, 15, 0, tzinfo=timezone.utc)


def test_flatten_orders_includes_nested_bracket_legs() -> None:
    stop = SimpleNamespace(id="stop", legs=None)
    take_profit = SimpleNamespace(id="take", legs=None)
    parent = SimpleNamespace(id="parent", legs=[stop, take_profit])

    flattened = AlpacaOrderResponseConverter.flatten_orders([parent])

    assert [item.id for item in flattened] == ["parent", "stop", "take"]


def test_portfolio_converter_maps_account_totals_and_short_positions() -> None:
    account = SimpleNamespace(
        cash="1000.50",
        long_market_value="2500",
        short_market_value="-300",
        equity="5200.25",
        portfolio_value="5200.25",
        buying_power="8000",
    )
    positions = [
        SimpleNamespace(
            symbol="AAPL",
            qty="10",
            side="long",
            avg_entry_price="100",
            current_price="110",
            market_value="1100",
            unrealized_pl="100",
        ),
        SimpleNamespace(
            symbol="TSLA",
            qty="2",
            side="short",
            avg_entry_price="200",
            current_price="190",
            market_value="-380",
            unrealized_pl="20",
        ),
    ]

    portfolio = AlpacaPortfolioConverter.from_alpaca(account, positions, open_orders=[])

    assert portfolio.cash_balance == 1000.50
    assert portfolio.total_market_value == 2800.0
    assert portfolio.total_equity == 5200.25
    assert portfolio.buying_power == 8000.0
    assert portfolio.unrealized_pnl == 120.0
    assert portfolio.get_position("AAPL").quantity == 10
    assert portfolio.get_position("TSLA").quantity == -2
