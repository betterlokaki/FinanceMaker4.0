"""Unit tests for the local daily conclusion monitor services."""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
import json

from common.models.order import OrderSide, OrderStatus, OrderType, TimeInForce
from common.models.order_response import OrderResponse
from common.models.position import Position
from conclusion_monitor.json_writer import ConclusionJsonWriter
from conclusion_monitor.order_history import BrokerOrderHistoryProvider
from conclusion_monitor.prompt_builder import ConclusionPromptBuilder
from conclusion_monitor.strategy_activity import StrategyActivityClassifier
from conclusion_monitor.trade_outcomes import TradeOutcomeClassifier


def _filled_order(
    order_id: str,
    ticker: str,
    side: OrderSide,
    quantity: int,
    price: float,
    filled_at: datetime,
) -> OrderResponse:
    return OrderResponse(
        order_id=order_id,
        ticker=ticker,
        quantity=quantity,
        filled_quantity=quantity,
        side=side,
        order_type=OrderType.LIMIT,
        status=OrderStatus.FILLED,
        limit_price=price,
        average_fill_price=price,
        time_in_force=TimeInForce.DAY,
        created_at=filled_at,
        updated_at=filled_at,
        filled_at=filled_at,
    )


class _HistoryBroker:
    def __init__(self) -> None:
        self.after: datetime | None = None
        self.until: datetime | None = None

    async def get_orders_between(
        self,
        after: datetime,
        until: datetime,
    ) -> list[OrderResponse]:
        self.after = after
        self.until = until
        return []


def test_order_history_provider_uses_new_york_day_bounds() -> None:
    async def _run() -> None:
        broker = _HistoryBroker()
        provider = BrokerOrderHistoryProvider(broker)  # type: ignore[arg-type]

        await provider.get_orders_for_day(date(2026, 4, 23))

        assert broker.after == datetime(2026, 4, 23, 4, 0, tzinfo=timezone.utc)
        assert broker.until is not None
        assert broker.until.date() == date(2026, 4, 24)

    asyncio.run(_run())


def test_order_history_provider_uses_inclusive_new_york_range_bounds() -> None:
    async def _run() -> None:
        broker = _HistoryBroker()
        provider = BrokerOrderHistoryProvider(broker)  # type: ignore[arg-type]

        await provider.get_orders_for_range(date(2026, 5, 8), date(2026, 6, 3))

        assert broker.after == datetime(2026, 5, 8, 4, 0, tzinfo=timezone.utc)
        assert broker.until is not None
        assert broker.until.date() == date(2026, 6, 4)

    asyncio.run(_run())


def test_trade_outcome_classifier_pairs_long_and_short_fills() -> None:
    classifier = TradeOutcomeClassifier()
    orders = [
        _filled_order(
            "long-entry",
            "AAPL",
            OrderSide.BUY,
            10,
            100.0,
            datetime(2026, 4, 23, 14, 0, tzinfo=timezone.utc),
        ),
        _filled_order(
            "long-exit",
            "AAPL",
            OrderSide.SELL,
            10,
            105.0,
            datetime(2026, 4, 23, 15, 0, tzinfo=timezone.utc),
        ),
        _filled_order(
            "short-entry",
            "TSLA",
            OrderSide.SELL,
            5,
            200.0,
            datetime(2026, 4, 23, 16, 0, tzinfo=timezone.utc),
        ),
        _filled_order(
            "short-exit",
            "TSLA",
            OrderSide.BUY,
            5,
            190.0,
            datetime(2026, 4, 23, 17, 0, tzinfo=timezone.utc),
        ),
    ]

    result = classifier.classify(orders, positions=[])

    assert result["summary"]["closed_trade_count"] == 2
    assert result["summary"]["realized_trade_pnl"] == 100.0
    assert len(result["successful_trades"]) == 2
    assert result["unsuccessful_trades"] == []


def test_trade_outcome_classifier_reports_open_position_outcomes() -> None:
    classifier = TradeOutcomeClassifier()
    result = classifier.classify(
        filled_orders=[],
        positions=[
            Position(
                ticker="NVDA",
                quantity=3,
                average_cost=100.0,
                current_price=110.0,
                unrealized_pnl=30.0,
            )
        ],
    )

    assert result["open_position_outcomes"][0]["ticker"] == "NVDA"
    assert result["open_position_outcomes"][0]["result"] == "successful"


def test_strategy_activity_classifier_splits_mag7_and_earnings() -> None:
    classifier = StrategyActivityClassifier()

    result = classifier.classify(
        broker_activity_tickers={"AAPL", "XYZ"},
    )

    assert result["mag7"]["observed_tickers"] == ["AAPL"]
    assert result["earnings"]["observed_tickers"] == ["XYZ"]
    assert result["running_today_inference"] == ["mag7", "earnings"]


def test_prompt_builder_includes_strategy_logic_candles_and_required_actions() -> None:
    prompt = ConclusionPromptBuilder().build(
        {
            "trading_day": "2026-04-23",
            "account_context": {
                "started_at": "2026-05-08",
                "initial_capital_usd": 100000.0,
            },
            "pnl": {"daily_pnl": 10},
            "market_data": {
                "candles_by_ticker": {
                    "AAPL": [{"time": "2026-04-23T14:30:00+00:00", "close": 100.0}]
                }
            },
        }
    )

    assert "make as much money as possible" in prompt
    assert "May 8, 2026" in prompt
    assert "$100,000" in prompt
    assert "market_data.candles_by_ticker" in prompt
    assert "mag7_ticker_changes" in prompt
    assert "def on_tick" in prompt


def test_json_writer_creates_dated_conclusion_file(tmp_path) -> None:
    writer = ConclusionJsonWriter(tmp_path)
    path = writer.write(date(2026, 4, 23), {"trading_day": "2026-04-23"})

    assert path == tmp_path / "2026-04-23.json"
    assert json.loads(path.read_text(encoding="utf-8"))["trading_day"] == "2026-04-23"


def test_json_writer_creates_dated_range_file(tmp_path) -> None:
    writer = ConclusionJsonWriter(tmp_path)
    path = writer.write_range(
        date(2026, 5, 8),
        date(2026, 6, 3),
        {"date_range": {"start_date": "2026-05-08", "end_date": "2026-06-03"}},
    )

    assert path == tmp_path / "2026-05-08_to_2026-06-03.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["date_range"]["start_date"] == "2026-05-08"
