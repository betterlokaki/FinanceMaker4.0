"""Unit tests for reusable candlestick marker conversion helpers."""
from __future__ import annotations

import unittest

import pandas as pd

from backtests.backtesting_py.plotting import (
    trade_markers_from_backtesting_trades,
    trade_markers_from_shared_executed_trades,
    trade_markers_from_stats_by_ticker,
)
from backtests.backtesting_py.portfolio_orchestrator import ExecutedPortfolioTrade


class BacktestingPlottingTests(unittest.TestCase):
    def test_trade_markers_from_backtesting_trades_maps_long_and_short(self) -> None:
        trades = pd.DataFrame(
            [
                {
                    "EntryTime": "2025-01-01 10:00:00",
                    "ExitTime": "2025-01-01 12:00:00",
                    "EntryPrice": 100.0,
                    "ExitPrice": 110.0,
                    "Size": 5,
                },
                {
                    "EntryTime": "2025-01-02 10:00:00",
                    "ExitTime": "2025-01-02 12:00:00",
                    "EntryPrice": 200.0,
                    "ExitPrice": 190.0,
                    "Size": -3,
                },
            ]
        )

        markers = trade_markers_from_backtesting_trades(ticker="aapl", trades=trades)

        self.assertEqual(len(markers), 2)
        self.assertEqual(markers[0].ticker, "AAPL")
        self.assertEqual(markers[0].direction, "Long")
        self.assertEqual(markers[1].ticker, "AAPL")
        self.assertEqual(markers[1].direction, "Short")

    def test_trade_markers_from_stats_by_ticker_extracts_from__trades(self) -> None:
        trades = pd.DataFrame(
            [
                {
                    "EntryTime": "2025-01-03 10:00:00",
                    "ExitTime": "2025-01-03 12:00:00",
                    "EntryPrice": 50.0,
                    "ExitPrice": 55.0,
                    "Size": 2,
                }
            ]
        )
        stats = pd.Series({"_trades": trades})

        markers = trade_markers_from_stats_by_ticker({"MSFT": stats})

        self.assertEqual(len(markers), 1)
        self.assertEqual(markers[0].ticker, "MSFT")
        self.assertEqual(markers[0].direction, "Long")

    def test_trade_markers_from_shared_executed_trades_ignores_invalid_rows(self) -> None:
        valid_trade = ExecutedPortfolioTrade(
            ticker="NVDA",
            direction="Short",
            size=10,
            entry_time=pd.Timestamp("2025-01-04 10:00:00"),
            exit_time=pd.Timestamp("2025-01-04 12:00:00"),
            entry_price=120.0,
            exit_price=110.0,
            gross_pnl=100.0,
            net_pnl=98.0,
            entry_cost=1.0,
            exit_cost=1.0,
            short_borrow_fee=0.0,
        )
        invalid_trade = ExecutedPortfolioTrade(
            ticker="",
            direction="Short",
            size=10,
            entry_time=pd.Timestamp("2025-01-04 10:00:00"),
            exit_time=pd.Timestamp("2025-01-04 12:00:00"),
            entry_price=120.0,
            exit_price=110.0,
            gross_pnl=100.0,
            net_pnl=98.0,
            entry_cost=1.0,
            exit_cost=1.0,
            short_borrow_fee=0.0,
        )

        markers = trade_markers_from_shared_executed_trades((valid_trade, invalid_trade))

        self.assertEqual(len(markers), 1)
        self.assertEqual(markers[0].ticker, "NVDA")
        self.assertEqual(markers[0].direction, "Short")


if __name__ == "__main__":
    unittest.main()
