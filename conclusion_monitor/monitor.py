"""Daily conclusion monitor orchestration."""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from common.models.order import OrderStatus
from common.models.order_response import OrderResponse
from gpt.abstracts import IGPTClient
from publishers.abstracts import IBroker
from pullers.market.abstracts import IMarketProvider

from conclusion_monitor.ai_conclusion import AIConclusionRunner
from conclusion_monitor.broker_snapshot import BrokerSnapshotCollector
from conclusion_monitor.candles import CandleCollector
from conclusion_monitor.json_writer import ConclusionJsonWriter
from conclusion_monitor.order_history import BrokerOrderHistoryProvider
from conclusion_monitor.prompt_builder import ConclusionPromptBuilder
from conclusion_monitor.serialization import serialize_order
from conclusion_monitor.strategy_activity import StrategyActivityClassifier
from conclusion_monitor.trade_outcomes import TradeOutcomeClassifier

DEFAULT_ACCOUNT_START_DATE = date(2026, 5, 8)
DEFAULT_INITIAL_CAPITAL = 100_000.0


class ConclusionMonitor:
    """Generate one local daily trading conclusion report."""

    def __init__(
        self,
        broker: IBroker,
        order_history_provider: BrokerOrderHistoryProvider,
        market_provider: IMarketProvider,
        ai_clients: dict[str, IGPTClient],
        writer: ConclusionJsonWriter | None = None,
    ) -> None:
        self._broker = broker
        self._order_history_provider = order_history_provider
        self._snapshot_collector = BrokerSnapshotCollector(broker)
        self._trade_classifier = TradeOutcomeClassifier()
        self._strategy_classifier = StrategyActivityClassifier()
        self._candle_collector = CandleCollector(market_provider)
        self._prompt_builder = ConclusionPromptBuilder()
        self._ai_runner = AIConclusionRunner(ai_clients)
        self._writer = writer or ConclusionJsonWriter()

    async def generate(self, trading_day: date) -> Path:
        """Connect to broker, generate the report, and write it to disk."""
        await self._broker.connect()
        try:
            report = await self._build_report(
                start_date=trading_day,
                end_date=trading_day,
                account_start_date=DEFAULT_ACCOUNT_START_DATE,
                initial_capital=DEFAULT_INITIAL_CAPITAL,
            )
            output_path = self._writer.write(trading_day, report)
            return output_path
        finally:
            await self._broker.disconnect()

    async def generate_range(
        self,
        start_date: date,
        end_date: date,
        account_start_date: date = DEFAULT_ACCOUNT_START_DATE,
        initial_capital: float = DEFAULT_INITIAL_CAPITAL,
    ) -> Path:
        """Connect to broker, generate a range report, and write it to disk."""
        if end_date < start_date:
            raise ValueError("end_date must be greater than or equal to start_date")

        await self._broker.connect()
        try:
            report = await self._build_report(
                start_date=start_date,
                end_date=end_date,
                account_start_date=account_start_date,
                initial_capital=initial_capital,
            )
            return self._writer.write_range(start_date, end_date, report)
        finally:
            await self._broker.disconnect()

    async def _build_report(
        self,
        start_date: date,
        end_date: date,
        account_start_date: date,
        initial_capital: float,
    ) -> dict[str, Any]:
        snapshot = await self._snapshot_collector.collect(since_date=start_date)
        portfolio = snapshot.pop("raw_portfolio")
        all_orders = await self._order_history_provider.get_orders_for_range(
            start_date=start_date,
            end_date=end_date,
        )
        open_orders = portfolio.open_orders
        filled_orders = self._filled_orders(all_orders)
        broker_activity_tickers = self._broker_activity_tickers(
            all_orders=all_orders,
            open_orders=open_orders,
            positions=portfolio.positions,
        )
        relevant_tickers = broker_activity_tickers
        strategy_activity = self._strategy_classifier.classify(
            broker_activity_tickers=broker_activity_tickers,
        )
        trade_outcomes = self._trade_classifier.classify(filled_orders, portfolio.positions)
        market_data = await self._collect_market_data(
            relevant_tickers=relevant_tickers,
            start_date=start_date,
            end_date=end_date,
        )
        total_return = self._total_return_pct(
            current_nav=snapshot["pnl"].get("current_nav"),
            initial_capital=initial_capital,
        )

        report_context: dict[str, Any] = {
            "trading_day": start_date.isoformat() if start_date == end_date else None,
            "date_range": {
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "inclusive": True,
            },
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "account_context": {
                "started_at": account_start_date.isoformat(),
                "initial_capital_usd": initial_capital,
                "current_nav": snapshot["pnl"].get("current_nav"),
                "total_return_pct_from_initial_capital": total_return,
            },
            "broker_metadata": {
                "provider": snapshot["provider"],
                "is_connected": snapshot["is_connected"],
            },
            "pnl": {
                **snapshot["pnl"],
                "realized_trade_pnl_from_paired_fills": trade_outcomes["summary"][
                    "realized_trade_pnl"
                ],
                "open_position_unrealized_pnl": trade_outcomes["summary"][
                    "open_position_unrealized_pnl"
                ],
            },
            "portfolio": snapshot["portfolio"],
            "open_orders": [serialize_order(order) for order in open_orders],
            "filled_orders": [serialize_order(order) for order in filled_orders],
            "all_orders": [serialize_order(order) for order in all_orders],
            "successful_trades": trade_outcomes["successful_trades"],
            "unsuccessful_trades": trade_outcomes["unsuccessful_trades"],
            "open_position_outcomes": trade_outcomes["open_position_outcomes"],
            "unpaired_filled_orders": trade_outcomes["unpaired_filled_orders"],
            "trade_outcome_summary": trade_outcomes["summary"],
            "strategies_observed": strategy_activity,
            "relevant_tickers": sorted(relevant_tickers),
            "market_data": market_data,
        }

        prompt = self._prompt_builder.build(report_context)
        report_context["ai_conclusion"] = await self._ai_runner.run(prompt)
        return report_context

    async def _collect_market_data(
        self,
        relevant_tickers: set[str],
        start_date: date,
        end_date: date,
    ) -> dict[str, Any]:
        if start_date == end_date:
            return await self._candle_collector.collect(relevant_tickers, start_date)
        return await self._candle_collector.collect_range(
            tickers=relevant_tickers,
            start_date=start_date,
            end_date=end_date,
        )

    @staticmethod
    def _filled_orders(orders: list[OrderResponse]) -> list[OrderResponse]:
        return [
            order
            for order in orders
            if order.status == OrderStatus.FILLED or order.filled_quantity > 0
        ]

    @staticmethod
    def _broker_activity_tickers(
        all_orders: list[OrderResponse],
        open_orders: list[OrderResponse],
        positions: list[Any],
    ) -> set[str]:
        tickers = {order.ticker.upper() for order in [*all_orders, *open_orders] if order.ticker}
        tickers.update(
            position.ticker.upper()
            for position in positions
            if getattr(position, "ticker", "")
        )
        return tickers

    @staticmethod
    def _total_return_pct(
        current_nav: float | None,
        initial_capital: float,
    ) -> float | None:
        if current_nav is None or initial_capital <= 0:
            return None
        return round(((current_nav - initial_capital) / initial_capital) * 100.0, 2)
