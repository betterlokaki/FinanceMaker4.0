"""Tests for the pullback Alpaca live runner entrypoint."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from run_live_pullback_trading_strategy import (
    create_pullback_alpaca_config_from_env,
    seconds_until_israel_stop,
    seconds_until_ny_regular_open,
    validate_pullback_alpaca_config,
)

ISRAEL_TZ = ZoneInfo("Asia/Jerusalem")
NY_TZ = ZoneInfo("America/New_York")


def test_create_pullback_alpaca_config_reads_dedicated_env_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALPACA_API_KEY", "shared-key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "shared-secret")
    monkeypatch.setenv("MOMENTUM_ALPACA_API_KEY", "momentum-key")
    monkeypatch.setenv("MOMENTUM_ALPACA_SECRET_KEY", "momentum-secret")
    monkeypatch.setenv("PULLBACK_ALPACA_API_KEY", "pullback-key")
    monkeypatch.setenv("PULLBACK_ALPACA_SECRET_KEY", "pullback-secret")
    monkeypatch.setenv("PULLBACK_ALPACA_PAPER", "false")

    config = create_pullback_alpaca_config_from_env()

    assert config.api_key == "pullback-key"
    assert config.secret_key == "pullback-secret"
    assert config.paper is False


def test_validate_pullback_alpaca_config_requires_pullback_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PULLBACK_ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("PULLBACK_ALPACA_SECRET_KEY", raising=False)
    config = create_pullback_alpaca_config_from_env()

    with pytest.raises(RuntimeError, match="PULLBACK_ALPACA_API_KEY"):
        validate_pullback_alpaca_config(config)


def test_seconds_until_israel_stop_matches_existing_runtime_window() -> None:
    now = datetime(2026, 5, 6, 14, 0, tzinfo=ISRAEL_TZ)

    assert seconds_until_israel_stop(now) == 9 * 60 * 60


def test_seconds_until_ny_regular_open_waits_until_0930_and_is_zero_afterward() -> None:
    before_open = datetime(2026, 5, 6, 9, 0, tzinfo=NY_TZ)
    after_open = datetime(2026, 5, 6, 9, 31, tzinfo=NY_TZ)

    assert seconds_until_ny_regular_open(before_open) == 30 * 60
    assert seconds_until_ny_regular_open(after_open) == 0
