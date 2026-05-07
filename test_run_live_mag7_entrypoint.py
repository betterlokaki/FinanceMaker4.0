"""Unit tests for the MAG7 Alpaca live runner entrypoint."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from run_live_mag7_ema_slope_regime_strategy import seconds_until_israel_stop

ISRAEL_TZ = ZoneInfo("Asia/Jerusalem")


def test_seconds_until_israel_stop_at_start_time() -> None:
    now = datetime(2026, 5, 6, 14, 0, tzinfo=ISRAEL_TZ)

    assert seconds_until_israel_stop(now) == 9 * 60 * 60


def test_seconds_until_israel_stop_before_stop_time() -> None:
    now = datetime(2026, 5, 6, 22, 59, tzinfo=ISRAEL_TZ)

    assert seconds_until_israel_stop(now) == 60


def test_seconds_until_israel_stop_at_stop_time() -> None:
    now = datetime(2026, 5, 6, 23, 0, tzinfo=ISRAEL_TZ)

    assert seconds_until_israel_stop(now) == 0
