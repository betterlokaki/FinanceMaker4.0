"""Tests for shared realtime provider subscription fan-out."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from common.models.pricing_data import PricingData
from pullers.realtime.abstracts.realtime_provider_base import RealtimeProviderBase


class FakeRealtimeProvider(RealtimeProviderBase):
    def __init__(self) -> None:
        super().__init__()
        self.sent_subscribes: list[list[str]] = []
        self.sent_unsubscribes: list[list[str]] = []

    async def _connect(self) -> None:
        self._is_connected = True

    async def _send_subscribe_message(self, tickers: list[str]) -> None:
        self.sent_subscribes.append(tickers)

    async def _send_unsubscribe_message(self, tickers: list[str]) -> None:
        self.sent_unsubscribes.append(tickers)

    async def disconnect(self) -> None:
        self._is_connected = False


class TickRecorder:
    def __init__(self) -> None:
        self.ticks: list[str] = []

    async def on_tick(self, data: PricingData) -> None:
        self.ticks.append(data.id.upper())


def test_shared_provider_fans_out_ticks_and_unsubscribes_one_callback_at_a_time() -> None:
    async def _run() -> None:
        provider = FakeRealtimeProvider()
        mag7 = TickRecorder()
        earnings = TickRecorder()
        tick = PricingData(
            id="AAPL",
            price=100.0,
            time=datetime.now(timezone.utc),
        )

        await provider.subscribe(["AAPL"], mag7.on_tick)
        await provider.subscribe(["AAPL"], earnings.on_tick)

        assert provider.sent_subscribes == [["AAPL"]]
        assert len(provider._subscriptions["AAPL"]) == 2

        await provider._dispatch_tick(tick)

        assert mag7.ticks == ["AAPL"]
        assert earnings.ticks == ["AAPL"]

        await provider.unsubscribe(["AAPL"], earnings.on_tick)

        assert provider.sent_unsubscribes == []
        assert len(provider._subscriptions["AAPL"]) == 1

        await provider._dispatch_tick(tick)

        assert mag7.ticks == ["AAPL", "AAPL"]
        assert earnings.ticks == ["AAPL"]

        await provider.unsubscribe(["AAPL"], mag7.on_tick)

        assert provider.sent_unsubscribes == [["AAPL"]]
        assert "AAPL" not in provider._subscriptions

    asyncio.run(_run())
