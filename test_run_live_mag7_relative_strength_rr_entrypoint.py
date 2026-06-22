"""Tests for the Mag7 relative-strength Alpaca live runner entrypoint."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import run_live_mag7_relative_strength_rr_strategy as runner
from run_live_mag7_relative_strength_rr_strategy import (
    create_mag7_rs_alpaca_config_from_env,
    create_mag7_rs_strategy_input_from_env,
    seconds_until_israel_stop,
    seconds_until_ny_regular_open,
    validate_mag7_rs_alpaca_config,
)

ISRAEL_TZ = ZoneInfo("Asia/Jerusalem")
NY_TZ = ZoneInfo("America/New_York")


def test_create_mag7_rs_alpaca_config_reads_dedicated_env_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALPACA_API_KEY", "shared-key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "shared-secret")
    monkeypatch.setenv("MAG7_RS_ALPACA_API_KEY", "rs-key")
    monkeypatch.setenv("MAG7_RS_ALPACA_SECRET_KEY", "rs-secret")
    monkeypatch.setenv("MAG7_RS_ALPACA_PAPER", "false")

    config = create_mag7_rs_alpaca_config_from_env()

    assert config.api_key == "rs-key"
    assert config.secret_key == "rs-secret"
    assert config.paper is False


def test_validate_mag7_rs_alpaca_config_requires_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MAG7_RS_ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("MAG7_RS_ALPACA_SECRET_KEY", raising=False)
    config = create_mag7_rs_alpaca_config_from_env()

    with pytest.raises(RuntimeError, match="MAG7_RS_ALPACA_API_KEY"):
        validate_mag7_rs_alpaca_config(config)


def test_create_mag7_rs_strategy_input_maps_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAG7_RS_PORTFOLIO_PCT_PER_TRADE", "0.75")
    monkeypatch.setenv("MAG7_RS_MAX_NOTIONAL_PER_TRADE", "12345")

    strategy_input = create_mag7_rs_strategy_input_from_env()

    assert strategy_input.portfolio_pct_per_trade == 0.75
    assert strategy_input.risk_pct == 0.04
    assert strategy_input.reward_pct == 0.08
    assert strategy_input.max_notional_per_trade == 12345


def test_seconds_until_israel_stop_matches_runtime_window() -> None:
    now = datetime(2026, 5, 6, 14, 0, tzinfo=ISRAEL_TZ)

    assert seconds_until_israel_stop(now) == 9 * 60 * 60


def test_seconds_until_ny_regular_open_waits_until_0930_and_is_zero_afterward() -> None:
    before_open = datetime(2026, 5, 6, 9, 0, tzinfo=NY_TZ)
    after_open = datetime(2026, 5, 6, 9, 31, tzinfo=NY_TZ)

    assert seconds_until_ny_regular_open(before_open) == 30 * 60
    assert seconds_until_ny_regular_open(after_open) == 0


def test_env_float_validation_rejects_bad_percentage(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAG7_RS_PORTFOLIO_PCT_PER_TRADE", "1.5")

    with pytest.raises(ValueError, match="portfolio_pct_per_trade"):
        runner.create_mag7_rs_strategy_input_from_env()
