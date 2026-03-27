"""Unit tests for Big7 last-year EMA+slope runner CLI and validation."""
import unittest

from backtests.run_big7_last_year_ema_slope_backtest import (
    _parse_args,
    _validate_backtest_inputs,
)


class Big7LastYearEmaSlopeBacktestTests(unittest.TestCase):
    def test_defaults_match_last_year_no_leverage_profile(self) -> None:
        args = _parse_args([])
        self.assertEqual(args.lookback_days, 475)
        self.assertAlmostEqual(float(args.leverage), 1.0, places=8)
        self.assertAlmostEqual(float(args.initial_capital), 10_000.0, places=8)
        self.assertAlmostEqual(float(args.notional_per_trade), 10_000.0, places=8)
        self.assertEqual(str(args.run_mode), "shared")

    def test_invalid_sizing_fails_fast(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            _validate_backtest_inputs(
                lookback_days=365,
                initial_capital=10_000.0,
                leverage=1.0,
                notional_per_trade=30_000.0,
                target_return_pct=250.0,
            )
        self.assertIn("Invalid sizing", str(ctx.exception))

    def test_valid_sizing_passes(self) -> None:
        _validate_backtest_inputs(
            lookback_days=365,
            initial_capital=10_000.0,
            leverage=1.0,
            notional_per_trade=10_000.0,
            target_return_pct=250.0,
        )

    def test_isolated_mode_allows_oversized_notional_argument(self) -> None:
        _validate_backtest_inputs(
            lookback_days=365,
            initial_capital=10_000.0,
            leverage=1.0,
            notional_per_trade=30_000.0,
            target_return_pct=250.0,
            run_mode="isolated",
        )


if __name__ == "__main__":
    unittest.main()
