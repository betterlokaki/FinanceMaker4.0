"""Unit tests for shared-capital portfolio orchestrator."""
from datetime import datetime
import unittest

import pandas as pd

from backtests.backtesting_py.config import PortfolioConfig
from backtests.backtesting_py.portfolio_orchestrator import run_shared_capital_portfolio


class PortfolioOrchestratorTests(unittest.TestCase):
    def test_shared_capacity_rejects_second_simultaneous_trade(self) -> None:
        t_entry = datetime(2025, 1, 2)
        t_exit = datetime(2025, 1, 10)

        trades_by_ticker = {
            "AAPL": pd.DataFrame(
                [
                    {
                        "EntryTime": t_entry,
                        "ExitTime": t_exit,
                        "EntryPrice": 100.0,
                        "ExitPrice": 110.0,
                        "Size": 300,
                    }
                ]
            ),
            "MSFT": pd.DataFrame(
                [
                    {
                        "EntryTime": t_entry,
                        "ExitTime": t_exit,
                        "EntryPrice": 100.0,
                        "ExitPrice": 90.0,
                        "Size": 300,
                    }
                ]
            ),
        }

        result = run_shared_capital_portfolio(
            trades_by_ticker=trades_by_ticker,
            portfolio_config=PortfolioConfig(
                initial_capital=10_000.0,
                max_leverage=3.0,
                commission_rate=0.0005,
                slippage_ticks=2.0,
                default_tick_size=0.01,
            ),
            tick_size_by_ticker={"AAPL": 0.01, "MSFT": 0.01},
        )

        self.assertEqual(result.total_trades, 1)
        self.assertEqual(len(result.skipped_trades), 1)
        self.assertAlmostEqual(result.final_equity, 12_956.5, places=6)

    def test_fixed_commission_per_side_is_applied(self) -> None:
        t_entry = datetime(2025, 1, 2)
        t_exit = datetime(2025, 1, 3)

        trades_by_ticker = {
            "AAPL": pd.DataFrame(
                [
                    {
                        "EntryTime": t_entry,
                        "ExitTime": t_exit,
                        "EntryPrice": 100.0,
                        "ExitPrice": 100.0,
                        "Size": 100,
                    }
                ]
            )
        }

        result = run_shared_capital_portfolio(
            trades_by_ticker=trades_by_ticker,
            portfolio_config=PortfolioConfig(
                initial_capital=10_000.0,
                max_leverage=5.0,
                commission_rate=0.0,
                slippage_ticks=0.0,
                fixed_commission_per_side=0.5,
                default_tick_size=0.01,
            ),
            tick_size_by_ticker={"AAPL": 0.01},
        )

        self.assertEqual(result.total_trades, 1)
        self.assertAlmostEqual(result.final_equity, 9_999.0, places=6)

    def test_short_borrow_fee_is_applied(self) -> None:
        t_entry = datetime(2025, 1, 2)
        t_exit = datetime(2025, 2, 1)

        trades_by_ticker = {
            "TSLA": pd.DataFrame(
                [
                    {
                        "EntryTime": t_entry,
                        "ExitTime": t_exit,
                        "EntryPrice": 100.0,
                        "ExitPrice": 90.0,
                        "Size": -100,
                    }
                ]
            )
        }

        result = run_shared_capital_portfolio(
            trades_by_ticker=trades_by_ticker,
            portfolio_config=PortfolioConfig(
                initial_capital=10_000.0,
                max_leverage=2.0,
                commission_rate=0.0,
                slippage_ticks=0.0,
                fixed_commission_per_side=0.0,
                short_borrow_fee_apr=0.10,
                default_tick_size=0.01,
            ),
            tick_size_by_ticker={"TSLA": 0.01},
        )

        self.assertEqual(result.total_trades, 1)
        self.assertAlmostEqual(result.final_equity, 10_917.808219178083, places=6)
        self.assertAlmostEqual(result.executed_trades[0].short_borrow_fee, 82.19178082191782, places=8)

    def test_dynamic_position_sizing_uses_current_cash(self) -> None:
        t_entry = datetime(2025, 1, 2)
        t_exit = datetime(2025, 1, 3)

        trades_by_ticker = {
            "AAPL": pd.DataFrame(
                [
                    {
                        "EntryTime": t_entry,
                        "ExitTime": t_exit,
                        "EntryPrice": 100.0,
                        "ExitPrice": 110.0,
                        "Size": 1,
                    }
                ]
            )
        }

        result = run_shared_capital_portfolio(
            trades_by_ticker=trades_by_ticker,
            portfolio_config=PortfolioConfig(
                initial_capital=10_000.0,
                max_leverage=1.0,
                commission_rate=0.0,
                slippage_ticks=0.0,
                fixed_commission_per_side=0.0,
                dynamic_position_sizing=True,
                position_size_cash_fraction=1.0,
                default_tick_size=0.01,
            ),
            tick_size_by_ticker={"AAPL": 0.01},
        )

        self.assertEqual(result.total_trades, 1)
        self.assertAlmostEqual(result.final_equity, 11_000.0, places=6)


if __name__ == "__main__":
    unittest.main()
