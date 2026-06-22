"""Tests for shared strategy input and trading helper contracts."""
from __future__ import annotations

import pytest

from common.models.order import OrderSide, OrderType, TimeInForce
from common.models.portfolio import Portfolio
from common.models.strategy_input import StrategyInputModel
from common.trading.order_request_factory import OrderRequestFactory
from common.trading.position_sizing import PositionSizer


def test_strategy_input_model_validates_percentages() -> None:
    model = StrategyInputModel(
        portfolio_pct_per_trade=0.25,
        risk_pct=0.03,
        reward_pct=0.05,
        max_notional_per_trade=1_000,
    )

    assert model.portfolio_pct_per_trade == 0.25

    with pytest.raises(ValueError, match="portfolio_pct_per_trade"):
        StrategyInputModel(portfolio_pct_per_trade=0, risk_pct=0.03, reward_pct=0.05)
    with pytest.raises(ValueError, match="risk_pct"):
        StrategyInputModel(portfolio_pct_per_trade=0.25, risk_pct=-0.01, reward_pct=0.05)
    with pytest.raises(ValueError, match="reward_pct"):
        StrategyInputModel(portfolio_pct_per_trade=0.25, risk_pct=0.03, reward_pct=-0.01)
    with pytest.raises(ValueError, match="max_notional_per_trade"):
        StrategyInputModel(
            portfolio_pct_per_trade=0.25,
            risk_pct=0.03,
            reward_pct=0.05,
            max_notional_per_trade=0,
        )


def test_position_sizer_uses_equity_then_cash_then_buying_power_and_cap() -> None:
    strategy_input = StrategyInputModel(
        portfolio_pct_per_trade=0.25,
        risk_pct=0.03,
        reward_pct=0.05,
        max_notional_per_trade=1_000,
    )

    assert PositionSizer.quantity_for_entry(
        Portfolio(total_equity=10_000, cash_balance=9_000, buying_power=20_000),
        entry_price=100,
        strategy_input=strategy_input,
    ) == 10
    assert PositionSizer.quantity_for_entry(
        Portfolio(cash_balance=10_000, buying_power=20_000),
        entry_price=100,
        strategy_input=strategy_input,
    ) == 10
    assert PositionSizer.quantity_for_entry(
        Portfolio(buying_power=10_000),
        entry_price=100,
        strategy_input=strategy_input,
    ) == 10


def test_position_sizer_respects_reserved_notional() -> None:
    strategy_input = StrategyInputModel(
        portfolio_pct_per_trade=0.25,
        risk_pct=0.03,
        reward_pct=0.05,
    )

    quantity = PositionSizer.quantity_for_entry(
        Portfolio(cash_balance=10_000, buying_power=10_000),
        entry_price=100,
        strategy_input=strategy_input,
        reserved_notional=9_000,
    )

    assert quantity == 10


def test_order_request_factory_builds_long_and_short_brackets() -> None:
    strategy_input = StrategyInputModel(
        portfolio_pct_per_trade=0.25,
        risk_pct=0.03,
        reward_pct=0.05,
    )

    long_order = OrderRequestFactory.bracket_entry(
        ticker="aapl",
        quantity=10,
        entry_price=100,
        side=OrderSide.BUY,
        strategy_input=strategy_input,
    )
    short_order = OrderRequestFactory.bracket_entry(
        ticker="aapl",
        quantity=10,
        entry_price=100,
        side=OrderSide.SELL,
        strategy_input=strategy_input,
    )

    assert long_order.ticker == "AAPL"
    assert long_order.stop_loss_price == 97
    assert long_order.take_profit_price == 105
    assert short_order.stop_loss_price == 103
    assert short_order.take_profit_price == 95


def test_order_request_factory_builds_plain_extended_hours_entry() -> None:
    order = OrderRequestFactory.plain_extended_hours_entry(
        ticker="aapl",
        quantity=10,
        entry_price=100,
    )

    assert order.ticker == "AAPL"
    assert order.side == OrderSide.BUY
    assert order.order_type == OrderType.LIMIT
    assert order.time_in_force == TimeInForce.DAY
    assert order.extended_hours is True
    assert order.stop_price is None
    assert order.stop_loss_price is None
    assert order.take_profit_price is None
