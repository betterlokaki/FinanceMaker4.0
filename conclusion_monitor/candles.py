"""Market candlestick collection for conclusion reports."""

from __future__ import annotations

from datetime import date
from typing import Any

from common.models.period import Period
from pullers.market.abstracts import IMarketProvider

from conclusion_monitor.serialization import dataframe_to_candles
from conclusion_monitor.time_windows import (
    first_rth_five_minutes_utc,
    local_date_range_bounds_utc,
    session_bounds_utc,
)


class CandleCollector:
    """Collect compact strategy-relevant candlestick payloads."""

    def __init__(self, market_provider: IMarketProvider) -> None:
        self._market_provider = market_provider

    async def collect(
        self,
        tickers: set[str],
        trading_day: date,
    ) -> dict[str, Any]:
        """Collect hourly session candles and first-RTH minute candles."""
        candles_by_ticker: dict[str, Any] = {}
        for ticker in sorted(self._normalize(tickers)):
            candles_by_ticker[ticker] = await self._collect_ticker(ticker, trading_day)
        return {
            "description": (
                "Actual report-date candlestick data. hourly_session covers 4:00-20:00 "
                "New York time; first_rth_minutes covers 9:30-9:35 New York time."
            ),
            "candles_by_ticker": candles_by_ticker,
        }

    async def collect_range(
        self,
        tickers: set[str],
        start_date: date,
        end_date: date,
    ) -> dict[str, Any]:
        """Collect hourly candles for an inclusive date range."""
        candles_by_ticker: dict[str, Any] = {}
        for ticker in sorted(self._normalize(tickers)):
            candles_by_ticker[ticker] = await self._collect_ticker_range(
                ticker=ticker,
                start_date=start_date,
                end_date=end_date,
            )
        return {
            "description": (
                "Actual date-range candlestick data for broker-touched tickers only. "
                "hourly_range covers the inclusive New York date range with extended "
                "hours included by the market provider when available."
            ),
            "candles_by_ticker": candles_by_ticker,
        }

    async def _collect_ticker(self, ticker: str, trading_day: date) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        try:
            start, end = session_bounds_utc(trading_day)
            hourly = await self._market_provider.get_prices(
                ticker=ticker,
                start_time=start,
                end_time=end,
                period=Period.HOUR,
            )
            payload["hourly_session"] = dataframe_to_candles(hourly)
        except Exception as exc:
            payload["hourly_session_error"] = str(exc)
            payload["hourly_session"] = []

        try:
            start, end = first_rth_five_minutes_utc(trading_day)
            first_rth = await self._market_provider.get_prices(
                ticker=ticker,
                start_time=start,
                end_time=end,
                period=Period.MINUTE,
            )
            payload["first_rth_minutes"] = dataframe_to_candles(first_rth)
        except Exception as exc:
            payload["first_rth_minutes_error"] = str(exc)
            payload["first_rth_minutes"] = []

        return payload

    async def _collect_ticker_range(
        self,
        ticker: str,
        start_date: date,
        end_date: date,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        try:
            start, end = local_date_range_bounds_utc(start_date, end_date)
            hourly = await self._market_provider.get_prices(
                ticker=ticker,
                start_time=start,
                end_time=end,
                period=Period.HOUR,
            )
            payload["hourly_range"] = dataframe_to_candles(hourly)
        except Exception as exc:
            payload["hourly_range_error"] = str(exc)
            payload["hourly_range"] = []
        return payload

    @staticmethod
    def _normalize(tickers: set[str]) -> set[str]:
        return {ticker.strip().upper() for ticker in tickers if ticker and ticker.strip()}
