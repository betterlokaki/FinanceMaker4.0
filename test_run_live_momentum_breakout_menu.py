"""Tests for the isolated momentum breakout runner menu."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from strategy.momentum_breakout_strategy.menu import (
    create_momentum_alpaca_config_from_env,
    seconds_until_israel_stop,
    seconds_until_ny_scan_time,
    validate_momentum_alpaca_config,
)

ISRAEL_TZ = ZoneInfo("Asia/Jerusalem")
NY_TZ = ZoneInfo("America/New_York")


def test_create_momentum_alpaca_config_reads_dedicated_env_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALPACA_API_KEY", "shared-key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "shared-secret")
    monkeypatch.setenv("MOMENTUM_ALPACA_API_KEY", "momentum-key")
    monkeypatch.setenv("MOMENTUM_ALPACA_SECRET_KEY", "momentum-secret")
    monkeypatch.setenv("MOMENTUM_ALPACA_PAPER", "false")

    config = create_momentum_alpaca_config_from_env()

    assert config.api_key == "momentum-key"
    assert config.secret_key == "momentum-secret"
    assert config.paper is False


def test_validate_momentum_alpaca_config_requires_momentum_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MOMENTUM_ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("MOMENTUM_ALPACA_SECRET_KEY", raising=False)
    config = create_momentum_alpaca_config_from_env()

    with pytest.raises(RuntimeError, match="MOMENTUM_ALPACA_API_KEY"):
        validate_momentum_alpaca_config(config)


def test_seconds_until_israel_stop_matches_existing_runtime_window() -> None:
    now = datetime(2026, 5, 6, 14, 0, tzinfo=ISRAEL_TZ)

    assert seconds_until_israel_stop(now) == 9 * 60 * 60


def test_seconds_until_ny_scan_time_waits_until_0925_and_is_zero_afterward() -> None:
    before_scan = datetime(2026, 5, 6, 9, 0, tzinfo=NY_TZ)
    after_scan = datetime(2026, 5, 6, 9, 26, tzinfo=NY_TZ)

    assert seconds_until_ny_scan_time(before_scan) == 25 * 60
    assert seconds_until_ny_scan_time(after_scan) == 0
