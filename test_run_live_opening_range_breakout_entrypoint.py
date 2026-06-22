"""Tests for the ORB Alpaca live runner entrypoint."""
from __future__ import annotations

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

import common.di_container as di_container
import run_live_opening_range_breakout_strategy as runner
from run_live_opening_range_breakout_strategy import (
    create_orb_alpaca_config_from_env,
    create_orb_strategy_input_from_env,
    seconds_until_israel_stop,
    seconds_until_ny_regular_open,
    validate_orb_alpaca_config,
)

ISRAEL_TZ = ZoneInfo("Asia/Jerusalem")
NY_TZ = ZoneInfo("America/New_York")


def test_create_orb_alpaca_config_reads_dedicated_env_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ALPACA_API_KEY", "shared-key")
    monkeypatch.setenv("ALPACA_SECRET_KEY", "shared-secret")
    monkeypatch.setenv("ORB_ALPACA_API_KEY", "orb-key")
    monkeypatch.setenv("ORB_ALPACA_SECRET_KEY", "orb-secret")
    monkeypatch.setenv("ORB_ALPACA_PAPER", "false")

    config = create_orb_alpaca_config_from_env()

    assert config.api_key == "orb-key"
    assert config.secret_key == "orb-secret"
    assert config.paper is False


def test_validate_orb_alpaca_config_requires_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ORB_ALPACA_API_KEY", raising=False)
    monkeypatch.delenv("ORB_ALPACA_SECRET_KEY", raising=False)
    config = create_orb_alpaca_config_from_env()

    with pytest.raises(RuntimeError, match="ORB_ALPACA_API_KEY"):
        validate_orb_alpaca_config(config)


def test_create_orb_strategy_input_maps_env_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ORB_PORTFOLIO_PCT_PER_TRADE", "0.2")
    monkeypatch.setenv("ORB_STOP_LOSS_PCT", "0.03")
    monkeypatch.setenv("ORB_REWARD_TO_RISK", "3.0")

    strategy_input = create_orb_strategy_input_from_env()

    assert strategy_input.portfolio_pct_per_trade == 0.2
    assert strategy_input.risk_pct == 0.03
    assert strategy_input.reward_pct == 0.09


def test_create_orb_strategy_input_defaults_to_half_portfolio_split(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ORB_PORTFOLIO_PCT_PER_TRADE", raising=False)

    strategy_input = create_orb_strategy_input_from_env()

    assert strategy_input.portfolio_pct_per_trade == pytest.approx(0.5 / 7)


def test_seconds_until_stop_and_open_match_existing_runtime_window() -> None:
    now_israel = datetime(2026, 5, 6, 14, 0, tzinfo=ISRAEL_TZ)
    before_open = datetime(2026, 5, 6, 9, 0, tzinfo=NY_TZ)
    after_open = datetime(2026, 5, 6, 9, 31, tzinfo=NY_TZ)

    assert seconds_until_israel_stop(now_israel) == 9 * 60 * 60
    assert seconds_until_ny_regular_open(before_open) == 30 * 60
    assert seconds_until_ny_regular_open(after_open) == 0


def test_main_wires_runner_dependencies_and_cleans_up(
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

        def __init__(self, **kwargs: object) -> None:
            self.kwargs = kwargs
            self.is_initialized = False
            self.shutdown_called = False
            self.tickers = ["PLTR"]
            FakeStrategy.instances.append(self)

        async def initialize(self) -> None:
            self.is_initialized = True

        async def shutdown(self) -> None:
            self.shutdown_called = True

    fake_container = FakeContainer()
    waited = False

    async def fake_wait_until_stop(_shutdown_event: asyncio.Event) -> None:
        nonlocal waited
        waited = True

    monkeypatch.setenv("ORB_ALPACA_API_KEY", "orb-key")
    monkeypatch.setenv("ORB_ALPACA_SECRET_KEY", "orb-secret")
    monkeypatch.setenv("ORB_OPENING_RANGE_MINUTES", "10")
    monkeypatch.setattr(runner, "MarketCalendar", FakeMarketCalendar)
    monkeypatch.setattr(runner, "AlpacaBroker", FakeBroker)
    monkeypatch.setattr(runner, "OpeningRangeBreakoutLiveStrategy", FakeStrategy)
    monkeypatch.setattr(runner, "seconds_until_israel_stop", lambda _now=None: 60.0)
    monkeypatch.setattr(runner, "seconds_until_ny_regular_open", lambda _now=None: 0.0)
    monkeypatch.setattr(runner, "install_shutdown_signal_handlers", lambda _event: None)
    monkeypatch.setattr(runner, "wait_until_stop_or_shutdown", fake_wait_until_stop)
    monkeypatch.setattr(di_container, "container", fake_container)

    asyncio.run(runner.main())

    assert waited is True
    assert FakeStrategy.instances[0].is_initialized is True
    assert FakeStrategy.instances[0].shutdown_called is True
    assert FakeStrategy.instances[0].kwargs["opening_range_minutes"] == 10
    assert fake_container.realtime_provider.disconnected is True
    assert fake_container.http_client_instance.closed is True
