"""Unit tests for RSI extreme hourly runner CLI and validation."""
from __future__ import annotations

from datetime import datetime, timezone
import unittest

from backtests.run_rsi_extreme_rr_backtest import (
    _parse_args,
    _resolve_date_range,
    _validate_backtest_inputs,
)


class RsiExtremeRRBacktestRunnerTests(unittest.TestCase):
    def test_cli_requires_start_and_end_dates(self) -> None:
        with self.assertRaises(SystemExit):
            _parse_args([])

    def test_resolve_date_range_is_inclusive_of_end_date(self) -> None:
        args = _parse_args(
            [
                "--start-date",
                "2025-01-01",
                "--end-date",
                "2025-01-31",
            ]
        )
        start, end_exclusive = _resolve_date_range(args)

        self.assertEqual(start, datetime(2025, 1, 1, tzinfo=timezone.utc))
        self.assertEqual(end_exclusive, datetime(2025, 2, 1, tzinfo=timezone.utc))

    def test_validate_inputs_rejects_invalid_thresholds(self) -> None:
        with self.assertRaises(SystemExit) as ctx:
            _validate_backtest_inputs(
                start_time=datetime(2025, 1, 1, tzinfo=timezone.utc),
                end_time_exclusive=datetime(2025, 2, 1, tzinfo=timezone.utc),
                initial_capital=10_000.0,
                leverage=1.0,
                round_trip_commission=1.0,
                rsi_period=14,
                rsi_oversold=90.0,
                rsi_overbought=10.0,
                stop_loss_pct=0.02,
                risk_reward_ratio=3.0,
            )
        self.assertIn("RSI thresholds", str(ctx.exception))

    def test_validate_inputs_rejects_non_positive_risk_params(self) -> None:
        with self.assertRaises(SystemExit):
            _validate_backtest_inputs(
                start_time=datetime(2025, 1, 1, tzinfo=timezone.utc),
                end_time_exclusive=datetime(2025, 2, 1, tzinfo=timezone.utc),
                initial_capital=10_000.0,
                leverage=1.0,
                round_trip_commission=1.0,
                rsi_period=14,
                rsi_oversold=10.0,
                rsi_overbought=90.0,
                stop_loss_pct=0.0,
                risk_reward_ratio=3.0,
            )

        with self.assertRaises(SystemExit):
            _validate_backtest_inputs(
                start_time=datetime(2025, 1, 1, tzinfo=timezone.utc),
                end_time_exclusive=datetime(2025, 2, 1, tzinfo=timezone.utc),
                initial_capital=10_000.0,
                leverage=1.0,
                round_trip_commission=1.0,
                rsi_period=14,
                rsi_oversold=10.0,
                rsi_overbought=90.0,
                stop_loss_pct=0.02,
                risk_reward_ratio=0.0,
            )

    def test_validate_inputs_rejects_negative_warmup_days(self) -> None:
        with self.assertRaises(SystemExit):
            _validate_backtest_inputs(
                start_time=datetime(2025, 1, 1, tzinfo=timezone.utc),
                end_time_exclusive=datetime(2025, 2, 1, tzinfo=timezone.utc),
                initial_capital=10_000.0,
                leverage=1.0,
                round_trip_commission=1.0,
                rsi_period=14,
                rsi_oversold=10.0,
                rsi_overbought=90.0,
                stop_loss_pct=0.02,
                risk_reward_ratio=3.0,
                warmup_days=-1,
            )


if __name__ == "__main__":
    unittest.main()
