"""Tests for the pullback Alpaca live runner entrypoint."""
from __future__ import annotations

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import common.di_container as di_container
import run_live_pullback_trading_strategy as runner
from run_live_pullback_trading_strategy import (
    create_pullback_alpaca_config_from_env,
    create_pullback_strategy_input_from_env,
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


def test_create_pullback_strategy_input_maps_existing_env_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PULLBACK_CASH_ALLOCATION_PCT", "0.2")
    monkeypatch.setenv("PULLBACK_STOP_LOSS_PCT", "0.03")
    monkeypatch.setenv("PULLBACK_TAKE_PROFIT_PCT", "0.07")

    strategy_input = create_pullback_strategy_input_from_env()

    assert strategy_input.portfolio_pct_per_trade == 0.2
    assert strategy_input.risk_pct == 0.03
    assert strategy_input.reward_pct == 0.07


def test_seconds_until_israel_stop_matches_existing_runtime_window() -> None:
    now = datetime(2026, 5, 6, 14, 0, tzinfo=ISRAEL_TZ)

    assert seconds_until_israel_stop(now) == 9 * 60 * 60


def test_seconds_until_ny_regular_open_waits_until_0930_and_is_zero_afterward() -> None:
    before_open = datetime(2026, 5, 6, 9, 0, tzinfo=NY_TZ)
    after_open = datetime(2026, 5, 6, 9, 31, tzinfo=NY_TZ)

    assert seconds_until_ny_regular_open(before_open) == 30 * 60
    assert seconds_until_ny_regular_open(after_open) == 0


def test_main_stays_alive_until_stop_when_pullback_scan_has_no_signals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeMarketCalendar:
        def now(self) -> datetime:
            return datetime(2026, 5, 6, 9, 31, tzinfo=NY_TZ)

        def is_trading_day(self, _now: datetime) -> bool:
            return True

    class FakeBroker:
        def __init__(self, config: object) -> None:
            self.config = config
            self.connected = False
            self.disconnected = False

        async def connect(self) -> None:
            self.connected = True

        async def disconnect(self) -> None:
            self.disconnected = True

    class FakeRealtimeProvider:
        def __init__(self) -> None:
            self.disconnected = False

        async def disconnect(self) -> None:
            self.disconnected = True

    class FakeHttpClient:
        def __init__(self) -> None:
            self.closed = False

        async def aclose(self) -> None:
            self.closed = True

    class FakeContainer:
        def __init__(self) -> None:
            self.realtime_provider = FakeRealtimeProvider()
            self.market_provider = object()
            self.http_client_instance = FakeHttpClient()

        def yahoo_realtime_provider(self) -> FakeRealtimeProvider:
            return self.realtime_provider

        def yahoo_market_provider(self) -> object:
            return self.market_provider

        def http_client(self) -> FakeHttpClient:
            return self.http_client_instance

    class FakeStrategy:
        instances: list["FakeStrategy"] = []

        def __init__(self, **_kwargs: object) -> None:
            self.active_signals: dict[str, object] = {}
            self.initialized = False
            self.shutdown_called = False
            FakeStrategy.instances.append(self)

        async def initialize(self) -> None:
            self.initialized = True

        async def shutdown(self) -> None:
            self.shutdown_called = True

    fake_container = FakeContainer()
    waited = False

    async def fake_wait_until_stop(_shutdown_event: asyncio.Event) -> None:
        nonlocal waited
        waited = True

    monkeypatch.setenv("PULLBACK_ALPACA_API_KEY", "pullback-key")
    monkeypatch.setenv("PULLBACK_ALPACA_SECRET_KEY", "pullback-secret")
    monkeypatch.setattr(runner, "MarketCalendar", FakeMarketCalendar)
    monkeypatch.setattr(runner, "AlpacaBroker", FakeBroker)
    monkeypatch.setattr(runner, "PullbackTradingLiveStrategy", FakeStrategy)
    monkeypatch.setattr(runner, "seconds_until_israel_stop", lambda _now=None: 60.0)
    monkeypatch.setattr(runner, "seconds_until_ny_regular_open", lambda _now=None: 0.0)
    monkeypatch.setattr(runner, "install_shutdown_signal_handlers", lambda _event: None)
    monkeypatch.setattr(runner, "wait_until_stop_or_shutdown", fake_wait_until_stop)
    monkeypatch.setattr(di_container, "container", fake_container)

    asyncio.run(runner.main())

    assert waited is True
    assert FakeStrategy.instances[0].initialized is True
    assert FakeStrategy.instances[0].shutdown_called is True
    assert fake_container.realtime_provider.disconnected is True
    assert fake_container.http_client_instance.closed is True
