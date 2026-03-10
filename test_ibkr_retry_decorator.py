"""Unit tests for Interactive Brokers request retry decorator."""

from __future__ import annotations

import asyncio

import pytest
from ibind.support.errors import ExternalBrokerError

from publishers.interactive_brokers.interactive_webapi_broker import retry_ibkr_request


class DummyBroker:
    def __init__(self, retries: int) -> None:
        self._request_retry_attempts = retries
        self._request_retry_delay_seconds = 0.0
        self.calls = 0
        self.reconnects = 0

    @staticmethod
    def _is_retryable_status_code(status_code: int | None) -> bool:
        if status_code is None:
            return True
        return not (200 <= status_code <= 299)

    async def _reconnect_for_retry(self) -> None:
        self.reconnects += 1


def test_retry_reconnects_on_401_and_succeeds() -> None:
    class _Broker(DummyBroker):
        @retry_ibkr_request
        async def request(self) -> str:
            self.calls += 1
            if self.calls == 1:
                raise ExternalBrokerError("unauthorized", status_code=401)
            return "ok"

    broker = _Broker(retries=2)
    result = asyncio.run(broker.request())
    assert result == "ok"
    assert broker.calls == 2
    assert broker.reconnects == 1


def test_retry_reconnects_on_404_until_exhausted() -> None:
    class _Broker(DummyBroker):
        @retry_ibkr_request
        async def request(self) -> None:
            self.calls += 1
            raise ExternalBrokerError("not found", status_code=404)

    broker = _Broker(retries=2)

    with pytest.raises(ExternalBrokerError):
        asyncio.run(broker.request())

    # initial attempt + 2 retries
    assert broker.calls == 3
    assert broker.reconnects == 2


def test_non_ibkr_exception_is_not_retried() -> None:
    class _Broker(DummyBroker):
        @retry_ibkr_request
        async def request(self) -> None:
            self.calls += 1
            raise ValueError("bad input")

    broker = _Broker(retries=3)

    with pytest.raises(ValueError):
        asyncio.run(broker.request())

    assert broker.calls == 1
    assert broker.reconnects == 0
