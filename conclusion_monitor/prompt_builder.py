"""Prompt construction for AI trading conclusions."""

from __future__ import annotations

import inspect
import json
from typing import Any

from strategy.earning_strategy import EarningStrategy
from strategy.mag7_ema_slope_regime_strategy import Mag7EmaSlopeRegimeLiveStrategy

from conclusion_monitor.serialization import json_default


class ConclusionPromptBuilder:
    """Build provider-agnostic prompts for quant review agents."""

    def build(self, report_context: dict[str, Any]) -> str:
        """Build the full conclusion prompt."""
        payload = json.dumps(report_context, indent=2, default=json_default)
        snippets = json.dumps(self._strategy_snippets(), indent=2)

        return (
            "You are a senior quant trader and trading-system reviewer. Your goal is to "
            "make as much money as possible while protecting capital and improving "
            "risk-adjusted returns.\n\n"
            "Review the full automated trading performance period in date_range. The "
            "account_context.started_at and account_context.initial_capital_usd fields "
            "are important baseline facts: this project started on May 8, 2026 with "
            "$100,000. Use that context when judging returns, drawdown, opportunity "
            "cost, and whether the strategy is learning or repeating mistakes.\n\n"
            "Use the broker P&L, filled "
            "orders, open positions, open orders, strategy evidence, and the actual "
            "candlestick data in market_data.candles_by_ticker. Do not ignore the "
            "candlestick payload; cite concrete candles, times, prices, or patterns "
            "when they support your conclusion.\n\n"
            "You may recommend adding or removing MAG7 tickers, changing strategy "
            "parameters, pausing weak setups, or creating an entirely new strategy if "
            "the market data/news backdrop makes that attractive.\n\n"
            "Return strict JSON only, with this shape:\n"
            "{\n"
            '  "daily_assessment": "short conclusion",\n'
            '  "what_went_well": ["..."],\n'
            '  "what_went_wrong": ["..."],\n'
            '  "successful_trade_lessons": ["..."],\n'
            '  "unsuccessful_trade_lessons": ["..."],\n'
            '  "recommended_actions": ["..."],\n'
            '  "mag7_ticker_changes": {"add": [], "remove": [], "rationale": "..."},\n'
            '  "new_strategy_ideas": ["..."],\n'
            '  "risk_controls": ["..."],\n'
            '  "confidence": 0\n'
            "}\n\n"
            "Strategy logic snippets:\n"
            f"{snippets}\n\n"
            "Report payload:\n"
            f"{payload}"
        )

    def _strategy_snippets(self) -> dict[str, dict[str, str]]:
        return {
            "mag7": {
                "on_tick": self._source(Mag7EmaSlopeRegimeLiveStrategy.on_tick),
                "signal_logic": self._source(
                    Mag7EmaSlopeRegimeLiveStrategy._evaluate_signal_with_price
                ),
            },
            "earnings": {
                "scanner_logic": self._source(EarningStrategy._run_ai_scanner),
                "entry_logic": self._source(EarningStrategy._process_entry_candle_tick),
                "order_logic": self._source(EarningStrategy.on_candle),
                "exit_logic": self._source(EarningStrategy._sync_position_exits),
            },
        }

    @staticmethod
    def _source(value: Any) -> str:
        try:
            return inspect.getsource(value).strip()
        except (OSError, TypeError):
            return ""
