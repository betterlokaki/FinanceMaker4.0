"""Tests for Yahoo realtime provider wire subscription behavior."""
from __future__ import annotations

import asyncio
import json

from common.models.pricing_data import PricingData
from pullers.realtime.yahoo.yahoo_realtime_provider import YahooRealtimeProvider


class _FakeWebSocket:
    def __init__(self) -> None:
        self.sent_messages: list[dict[str, list[str]]] = []
        self.close_count = 0

    async def send(self, message: str) -> None:
        self.sent_messages.append(json.loads(message))

    async def close(self) -> None:
        self.close_count += 1

    def __aiter__(self) -> "_FakeWebSocket":
        return self

    async def __anext__(self) -> str:
        raise StopAsyncIteration


class _TestYahooRealtimeProvider(YahooRealtimeProvider):
    def __init__(self) -> None:
        super().__init__(reconnect_delay=0)
        self.websockets: list[_FakeWebSocket] = []
        self.connect_count = 0

    @property
    def fake_websocket(self) -> _FakeWebSocket:
        return self.websockets[-1]

    async def _connect(self) -> None:
        await asyncio.sleep(0)
        self.connect_count += 1
        websocket = _FakeWebSocket()
        self.websockets.append(websocket)
        self._websocket = websocket  # type: ignore[assignment]
        self._is_connected = True

    def _start_listener(self) -> None:
        return None


async def _callback(_data: PricingData) -> None:
    return None


async def _other_callback(_data: PricingData) -> None:
    return None


def _subscribe_payloads(provider: _TestYahooRealtimeProvider) -> list[list[str]]:
    return [
        sorted(message["subscribe"])
        for message in provider.fake_websocket.sent_messages
        if "subscribe" in message
    ]


def test_yahoo_subscribe_resends_full_active_ticker_set() -> None:
    async def _run() -> None:
        provider = _TestYahooRealtimeProvider()

        await provider.subscribe(["AAPL"], _callback)
        await provider.subscribe(["AAPL", "MSFT", "NVDA"], _other_callback)

        assert _subscribe_payloads(provider) == [
            ["AAPL"],
            ["AAPL", "MSFT", "NVDA"],
        ]

    asyncio.run(_run())


def test_yahoo_concurrent_subscribe_uses_one_connection_and_keeps_union() -> None:
    async def _run() -> None:
        provider = _TestYahooRealtimeProvider()

        await asyncio.gather(
            provider.subscribe(["AAPL"], _callback),
            provider.subscribe(["MSFT"], _other_callback),
        )

        assert provider.connect_count == 1
        assert _subscribe_payloads(provider)[-1] == ["AAPL", "MSFT"]

    asyncio.run(_run())


def test_yahoo_receive_loop_reconnects_and_resubscribes_when_socket_ends() -> None:
    async def _run() -> None:
        provider = _TestYahooRealtimeProvider()

        await provider.subscribe(["AAPL"], _callback)
        first_websocket = provider.fake_websocket

        await provider._receive_messages()

        assert provider.connect_count == 2
        assert first_websocket.close_count == 1
        assert provider.fake_websocket is not first_websocket
        assert _subscribe_payloads(provider)[-1] == ["AAPL"]

    asyncio.run(_run())
