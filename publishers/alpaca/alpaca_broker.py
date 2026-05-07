"""Alpaca broker implementation."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime, time as dt_time, timezone
import logging
from typing import Any, TypeVar

from alpaca.common.exceptions import APIError
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import QueryOrderStatus
from alpaca.trading.requests import (
    GetOrderByIdRequest,
    GetOrdersRequest,
    GetPortfolioHistoryRequest,
)
import requests

from common.converters.alpaca import (
    AlpacaOrderRequestConverter,
    AlpacaOrderResponseConverter,
    AlpacaPortfolioConverter,
)
from common.models.order import OrderStatus
from common.models.order_request import OrderRequest
from common.models.order_response import OrderResponse
from common.models.pnl_summary import PnlSummary
from common.models.portfolio import Portfolio
from common.settings import AlpacaConfig
from publishers.abstracts.broker_base import BrokerBase

logger = logging.getLogger(__name__)
T = TypeVar("T")


class AlpacaBroker(BrokerBase):
    """Alpaca Trading API broker implementation."""

    def __init__(
        self,
        config: AlpacaConfig,
        client_factory: Callable[..., Any] | None = None,
    ) -> None:
        """Initialize Alpaca broker."""
        super().__init__()
        self._config = config
        self._client_factory = client_factory or TradingClient
        self._client: Any | None = None
        self._request_retry_attempts = max(0, config.request_retry_attempts)
        self._request_retry_delay_seconds = max(0.0, config.request_retry_delay_seconds)
        self._portfolio_refresh_interval_seconds = max(
            0,
            config.portfolio_refresh_interval_seconds,
        )
        self._portfolio_refresh_task: asyncio.Task[None] | None = None
        self.portfolio = Portfolio()

    async def connect(self) -> None:
        """Create and validate the Alpaca trading client."""
        if not self._config.api_key or not self._config.secret_key:
            raise ConnectionError(
                "Missing Alpaca credentials. Set ALPACA_API_KEY and ALPACA_SECRET_KEY."
            )

        logger.info(
            "Starting Alpaca connection (%s mode)...",
            "paper" if self._config.paper else "live",
        )
        try:
            self._client = self._client_factory(
                api_key=self._config.api_key,
                secret_key=self._config.secret_key,
                paper=self._config.paper,
                url_override=self._config.url_override or None,
            )
            account = await asyncio.to_thread(self._client.get_account)
            self._connected = True
            logger.info("Connected to Alpaca account %s", self._get(account, "account_number", ""))

            try:
                self.portfolio = await self.get_portfolio()
                logger.info(
                    "Alpaca portfolio: equity=$%.2f buying_power=$%.2f positions=%d open_orders=%d",
                    self.portfolio.total_equity,
                    self.portfolio.buying_power,
                    self.portfolio.position_count,
                    len(self.portfolio.open_orders),
                )
            except Exception as exc:
                logger.warning("Connected to Alpaca but portfolio refresh failed: %s", exc)

            if self._portfolio_refresh_interval_seconds > 0:
                existing_task = self._portfolio_refresh_task
                if existing_task is None or existing_task.done():
                    self._portfolio_refresh_task = asyncio.create_task(
                        self._refresh_portfolio_loop()
                    )
        except Exception as exc:
            self._client = None
            self._connected = False
            raise ConnectionError(f"Failed to connect to Alpaca: {exc}") from exc

    async def disconnect(self) -> None:
        """Disconnect from Alpaca."""
        refresh_task = self._portfolio_refresh_task
        if refresh_task is not None:
            current_task = asyncio.current_task()
            if refresh_task is current_task:
                logger.debug("Disconnect called from portfolio refresh task; skipping self-cancel")
            elif refresh_task.done():
                self._portfolio_refresh_task = None
            else:
                refresh_task.cancel()
                try:
                    await refresh_task
                except asyncio.CancelledError:
                    pass
                self._portfolio_refresh_task = None

        self._client = None
        self._connected = False

    async def place_order(self, order_request: OrderRequest) -> OrderResponse:
        """Place an order with Alpaca."""
        alpaca_request = AlpacaOrderRequestConverter.to_alpaca(order_request)
        alpaca_order = await self._run_client(
            "submit_order",
            lambda client: client.submit_order(alpaca_request),
        )
        response = AlpacaOrderResponseConverter.from_alpaca(alpaca_order)
        try:
            self.portfolio = await self.get_portfolio()
        except Exception as exc:
            logger.warning("Order submitted but Alpaca portfolio refresh failed: %s", exc)
        return response

    async def cancel_order(self, order_id: str) -> OrderResponse:
        """Cancel an existing Alpaca order."""
        if not order_id:
            raise ValueError("order_id is required")

        existing = await self.get_order(order_id)
        await self._run_client(
            "cancel_order_by_id",
            lambda client: client.cancel_order_by_id(order_id),
        )
        try:
            return await self.get_order(order_id)
        except Exception:
            return replace(
                existing,
                status=OrderStatus.CANCELLED,
                updated_at=datetime.now(timezone.utc),
            )

    async def get_order(self, order_id: str) -> OrderResponse:
        """Get an Alpaca order by ID."""
        if not order_id:
            raise ValueError("order_id is required")

        alpaca_order = await self._run_client(
            "get_order_by_id",
            lambda client: client.get_order_by_id(
                order_id,
                GetOrderByIdRequest(nested=True),
            ),
        )
        return AlpacaOrderResponseConverter.from_alpaca(alpaca_order)

    async def get_portfolio(self) -> Portfolio:
        """Get portfolio positions, open orders, and account summary."""
        account = await self._run_client("get_account", lambda client: client.get_account())
        positions = await self._run_client(
            "get_all_positions",
            lambda client: client.get_all_positions(),
        )
        open_orders = await self.get_open_orders()
        self.portfolio = AlpacaPortfolioConverter.from_alpaca(account, positions, open_orders)
        return self.portfolio

    async def get_open_orders(self) -> list[OrderResponse]:
        """Get all active Alpaca orders, including bracket legs."""
        orders = await self._run_client(
            "get_orders",
            lambda client: client.get_orders(
                GetOrdersRequest(status=QueryOrderStatus.OPEN, nested=True),
            ),
        )
        if isinstance(orders, dict):
            raw_orders = orders.get("orders", [])
        else:
            raw_orders = orders or []

        flattened = AlpacaOrderResponseConverter.flatten_orders(list(raw_orders))
        responses = [
            AlpacaOrderResponseConverter.from_alpaca(order)
            for order in flattened
        ]
        return [order for order in responses if order.is_active]

    async def get_buying_power(self) -> float:
        """Get Alpaca buying power without fetching positions."""
        account = await self._run_client("get_account", lambda client: client.get_account())
        return AlpacaPortfolioConverter._safe_float(self._get(account, "buying_power", None)) or 0.0

    async def get_pnl_summary(self, since_date: date) -> PnlSummary:
        """Get daily P/L and approximate since-date P/L from Alpaca account history."""
        account = await self._run_client("get_account", lambda client: client.get_account())
        current_equity = AlpacaPortfolioConverter._safe_float(self._get(account, "equity", None))
        last_equity = AlpacaPortfolioConverter._safe_float(self._get(account, "last_equity", None))
        daily_pnl = (
            current_equity - last_equity
            if current_equity is not None and last_equity is not None
            else None
        )

        pnl_since_date: float | None = None
        baseline_date: date | None = None
        baseline_nav: float | None = None
        try:
            history = await self._run_client(
                "get_portfolio_history",
                lambda client: client.get_portfolio_history(
                    GetPortfolioHistoryRequest(
                        start=datetime.combine(since_date, dt_time.min, tzinfo=timezone.utc),
                        timeframe="1D",
                        extended_hours=False,
                    )
                ),
            )
            baseline_date, baseline_nav = self._extract_history_baseline(history, since_date)
            if current_equity is not None and baseline_nav is not None:
                pnl_since_date = current_equity - baseline_nav
        except Exception as exc:
            logger.warning("Failed to fetch Alpaca portfolio history: %s", exc)

        return PnlSummary(
            as_of_date=datetime.now(timezone.utc).date(),
            since_date=since_date,
            currency="USD",
            daily_pnl=daily_pnl,
            pnl_since_date=pnl_since_date,
            baseline_date=baseline_date,
            baseline_nav=baseline_nav,
            current_nav=current_equity,
        )

    async def _run_client(
        self,
        operation_name: str,
        operation: Callable[[Any], T],
    ) -> T:
        await self._ensure_connected()
        total_attempts = self._request_retry_attempts + 1

        for attempt in range(1, total_attempts + 1):
            client = self._require_client()
            try:
                return await asyncio.to_thread(operation, client)
            except (APIError, requests.RequestException, ConnectionError) as exc:
                if not self._should_retry(exc, attempt, total_attempts):
                    raise

                logger.warning(
                    "Alpaca request %s failed (attempt %d/%d). Reconnecting and retrying: %s",
                    operation_name,
                    attempt,
                    total_attempts,
                    exc,
                )
                await self._reconnect_for_retry()
                if self._request_retry_delay_seconds > 0:
                    await asyncio.sleep(self._request_retry_delay_seconds)

        raise RuntimeError(f"Unexpected Alpaca retry flow exit for {operation_name}")

    def _require_client(self) -> Any:
        if self._client is None:
            raise ConnectionError("Alpaca client is not connected")
        return self._client

    async def _reconnect_for_retry(self) -> None:
        try:
            await self.disconnect()
        except Exception as exc:
            logger.warning("Alpaca disconnect during retry failed: %s", exc)
        await self.connect()

    def _should_retry(self, exc: Exception, attempt: int, total_attempts: int) -> bool:
        if attempt >= total_attempts:
            return False
        status_code = getattr(exc, "status_code", None)
        if status_code is None:
            return True
        return status_code == 429 or status_code >= 500

    async def _refresh_portfolio_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(self._portfolio_refresh_interval_seconds)
                self.portfolio = await self.get_portfolio()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Failed to refresh Alpaca portfolio: %s", exc, exc_info=True)

    @classmethod
    def _extract_history_baseline(
        cls,
        history: Any,
        since_date: date,
    ) -> tuple[date | None, float | None]:
        timestamps = cls._get(history, "timestamp", []) or []
        equities = cls._get(history, "equity", []) or []
        for raw_ts, raw_equity in zip(timestamps, equities):
            try:
                point_date = datetime.fromtimestamp(int(raw_ts), tz=timezone.utc).date()
                equity = float(raw_equity)
            except (TypeError, ValueError, OSError):
                continue
            if point_date >= since_date:
                return point_date, equity
        return None, None

    @staticmethod
    def _get(item: Any, key: str, default: Any = None) -> Any:
        if isinstance(item, dict):
            return item.get(key, default)
        return getattr(item, key, default)
