"""Unit tests for backtesting.py cost model."""
import unittest

from backtests.backtesting_py.cost_model import calculate_side_cost


class CostModelTests(unittest.TestCase):
    def test_side_cost_matches_formula(self) -> None:
        cost = calculate_side_cost(
            order_size=100,
            price=50.0,
            commission_rate=0.0005,
            tick_size=0.01,
            slippage_ticks=2.0,
        )
        self.assertAlmostEqual(cost, 4.5, places=8)

    def test_side_cost_with_fixed_fee_only(self) -> None:
        cost = calculate_side_cost(
            order_size=100,
            price=50.0,
            commission_rate=0.0,
            tick_size=0.01,
            slippage_ticks=0.0,
            fixed_commission_per_side=0.5,
        )
        self.assertAlmostEqual(cost, 0.5, places=8)


if __name__ == "__main__":
    unittest.main()
