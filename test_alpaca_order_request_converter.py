"""Unit tests for Alpaca order request conversion."""
from __future__ import annotations

import logging

import pytest
from alpaca.trading.enums import OrderClass, OrderSide as AlpacaOrderSide, TimeInForce as AlpacaTimeInForce
from alpaca.trading.requests import LimitOrderRequest, MarketOrderRequest

from common.converters.alpaca import AlpacaOrderRequestConverter
from common.models.order import OrderSide, OrderType, TimeInForce
from common.models.order_request import OrderRequest


def test_mag7_fixed_bracket_maps_to_native_alpaca_bracket() -> None:
    request = OrderRequest(
        ticker="AAPL",
        quantity=10,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        limit_price=100.0,
        stop_loss_price=98.0,
        take_profit_price=106.0,
        time_in_force=TimeInForce.DAY,
        take_profit_rth=True,
        stop_loss_rth=False,
    )

    alpaca_request = AlpacaOrderRequestConverter.to_alpaca(request)

    assert isinstance(alpaca_request, LimitOrderRequest)
    assert alpaca_request.symbol == "AAPL"
    assert alpaca_request.qty == 10
    assert alpaca_request.side == AlpacaOrderSide.BUY
    assert alpaca_request.time_in_force == AlpacaTimeInForce.DAY
    assert alpaca_request.limit_price == 100.0
    assert alpaca_request.order_class == OrderClass.BRACKET
    assert alpaca_request.extended_hours is False
    assert alpaca_request.take_profit.limit_price == 106.0
    assert alpaca_request.stop_loss.stop_price == 98.0


def test_take_profit_extended_hours_is_rejected_for_bracket() -> None:
    request = OrderRequest(
        ticker="AAPL",
        quantity=10,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        limit_price=100.0,
        stop_loss_price=98.0,
        take_profit_price=106.0,
        take_profit_rth=False,
    )

    with pytest.raises(ValueError, match="extended-hours take-profit"):
        AlpacaOrderRequestConverter.to_alpaca(request)


def test_stop_loss_rth_is_ignored_and_logged(caplog: pytest.LogCaptureFixture) -> None:
    request = OrderRequest(
        ticker="AAPL",
        quantity=10,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        limit_price=100.0,
        stop_loss_price=98.0,
        take_profit_price=106.0,
        stop_loss_rth=False,
    )

    with caplog.at_level(logging.INFO):
        AlpacaOrderRequestConverter.to_alpaca(request)

    assert "Ignoring stop_loss_rth=False" in caplog.text


def test_simple_market_flatten_order_maps_to_market_order() -> None:
    request = OrderRequest(
        ticker="TSLA",
        quantity=3,
        side=OrderSide.SELL,
        order_type=OrderType.MARKET,
        time_in_force=TimeInForce.DAY,
    )

    alpaca_request = AlpacaOrderRequestConverter.to_alpaca(request)

    assert isinstance(alpaca_request, MarketOrderRequest)
    assert alpaca_request.symbol == "TSLA"
    assert alpaca_request.qty == 3
    assert alpaca_request.side == AlpacaOrderSide.SELL
    assert alpaca_request.extended_hours is False


def test_trailing_stop_bracket_is_not_supported() -> None:
    request = OrderRequest(
        ticker="AAPL",
        quantity=10,
        side=OrderSide.BUY,
        order_type=OrderType.LIMIT,
        limit_price=100.0,
        trailing_stop_amt=2.0,
        trailing_stop_type="%",
        take_profit_price=106.0,
    )

    with pytest.raises(ValueError, match="trailing stop legs"):
        AlpacaOrderRequestConverter.to_alpaca(request)
