"""Unit tests for Yahoo data adapter normalization."""
import asyncio
from datetime import datetime, timezone
import unittest

import pandas as pd

from backtests.backtesting_py.data_adapter import fetch_ohlcv_from_yahoo_provider
from common.models.period import Period


class _FakeProvider:
    async def get_prices(self, **_: object) -> pd.DataFrame:
        idx = pd.to_datetime(
            [
                datetime(2025, 1, 3, tzinfo=timezone.utc),
                datetime(2025, 1, 1, tzinfo=timezone.utc),
                datetime(2025, 1, 2, tzinfo=timezone.utc),
            ]
        )
        return pd.DataFrame(
            {
                "open": [10, 11, 12],
                "high": [11, 12, 13],
                "low": [9, 10, 11],
                "close": [10.5, 11.5, 12.5],
                "volume": [1000, 2000, 3000],
                "period": [Period.DAILY, Period.DAILY, Period.DAILY],
            },
            index=idx,
        )


class DataAdapterTests(unittest.TestCase):
    def test_adapter_normalizes_ohlcv_schema(self) -> None:
        provider = _FakeProvider()
        df = asyncio.run(
            fetch_ohlcv_from_yahoo_provider(
                provider=provider,
                ticker="AAPL",
                start_time=datetime(2025, 1, 1, tzinfo=timezone.utc),
                end_time=datetime(2025, 1, 4, tzinfo=timezone.utc),
                period=Period.DAILY,
            )
        )

        self.assertEqual(list(df.columns), ["Open", "High", "Low", "Close", "Volume"])
        self.assertEqual(len(df), 3)
        self.assertTrue(df.index.is_monotonic_increasing)
        self.assertIsNone(df.index.tz)


if __name__ == "__main__":
    unittest.main()
