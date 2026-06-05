"""Alpaca broker implementation."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import replace
from datetime import date, datetime, time as dt_time, timezone
import logging
from typing import Any, TypeVar

from alpaca.common.enums import Sort
from alpaca.common.exceptions import APIError
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import (
    OrderClass as AlpacaOrderClass,
    OrderSide as AlpacaOrderSide,
    OrderType as AlpacaOrderType,
    QueryOrderStatus,
    TimeInForce as AlpacaTimeInForce,
)
from alpaca.trading.requests import (
    GetOrderByIdRequest,
    GetOrdersRequest,
    GetPortfolioHistoryRequest,
    LimitOrderRequest,
    StopLossRequest,
    TakeProfitRequest,
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

ACTIVE_ALPACA_ORDER_STATUSES = {
    "new",
    "accepted",
    "pending_new",
    "accepted_for_bidding",
    "pending_review",
    "held",
    "pending_cancel",
    "pending_replace",
    "partially_filled",
}


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
        self._take_profit_pct = max(0.0, float(config.take_profit_pct))
        self._stop_loss_pct = max(0.0, float(config.stop_loss_pct))
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

            await self._protect_open_positions_on_startup()

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
        flattened = await self._get_raw_open_orders()
        responses = [
            AlpacaOrderResponseConverter.from_alpaca(order)
            for order in flattened
        ]
        return [order for order in responses if order.is_active]

    async def get_orders_between(
        self,
        after: datetime,
        until: datetime,
    ) -> list[OrderResponse]:
        """Get all Alpaca orders created in a UTC time range."""
        orders = await self._run_client(
            "get_orders",
            lambda client: client.get_orders(
                GetOrdersRequest(
                    status=QueryOrderStatus.ALL,
                    after=after,
                    until=until,
                    direction=Sort.ASC,
                    nested=True,
                ),
            ),
        )
        raw_orders = self._coerce_payload_list(orders, "orders")
        flattened = AlpacaOrderResponseConverter.flatten_orders(list(raw_orders))
        return [AlpacaOrderResponseConverter.from_alpaca(order) for order in flattened]

    async def _protect_open_positions_on_startup(self) -> None:
        """Ensure every existing Alpaca position has TP and SL exit orders."""
        positions_payload = await self._run_client(
            "get_all_positions",
            lambda client: client.get_all_positions(),
        )
        positions = self._coerce_payload_list(positions_payload, "positions")
        if not positions:
            return

        logger.info(
            "Checking Alpaca TP/SL protection for %d open positions",
            len(positions),
        )
        for position in positions:
            symbol = self._position_symbol(position)
            quantity = self._position_abs_quantity(position)
            if not symbol or quantity <= 0:
                continue

            try:
                open_orders = await self._get_raw_open_orders()
                if self._position_has_take_profit_and_stop_loss(position, open_orders):
                    logger.info("Alpaca position %s already has TP and SL protection", symbol)
                    continue

                logger.warning(
                    "Alpaca position %s is missing TP/SL protection; re-attaching OCO",
                    symbol,
                )
                await self._cancel_open_orders_for_symbol(symbol)
                await self._submit_protection_oco(position)

                open_orders = await self._get_raw_open_orders()
                if self._position_has_take_profit_and_stop_loss(position, open_orders):
                    logger.info("Attached TP/SL OCO protection for Alpaca position %s", symbol)
                    continue

                raise RuntimeError("submitted OCO protection but TP and SL were not visible")
            except Exception as exc:
                logger.error(
                    "Could not protect Alpaca position %s; closing only that position: %s",
                    symbol,
                    exc,
                    exc_info=True,
                )
                await self._close_unprotected_position(symbol)

    async def _get_raw_open_orders(self) -> list[Any]:
        orders = await self._run_client(
            "get_orders",
            lambda client: client.get_orders(
                GetOrdersRequest(status=QueryOrderStatus.OPEN, nested=True),
            ),
        )
        raw_orders = self._coerce_payload_list(orders, "orders")
        return AlpacaOrderResponseConverter.flatten_orders(list(raw_orders))

    async def _submit_protection_oco(self, position: Any) -> None:
        symbol = self._position_symbol(position)
        quantity = self._position_abs_quantity(position)
        average_price = self._safe_float(self._get(position, "avg_entry_price", None))
        is_long = self._position_is_long(position)
        if not symbol:
            raise ValueError("position symbol is required")
        if quantity <= 0:
            raise ValueError(f"{symbol} position quantity must be positive")
        if average_price is None or average_price <= 0:
            raise ValueError(f"{symbol} position average entry price must be positive")

        take_profit_price, stop_loss_price = self._protection_prices(
            average_price=average_price,
            is_long=is_long,
        )
        exit_side = AlpacaOrderSide.SELL if is_long else AlpacaOrderSide.BUY
        oco_request = LimitOrderRequest(
            symbol=symbol,
            qty=quantity,
            side=exit_side,
            time_in_force=AlpacaTimeInForce.GTC,
            order_class=AlpacaOrderClass.OCO,
            take_profit=TakeProfitRequest(limit_price=take_profit_price),
            stop_loss=StopLossRequest(stop_price=stop_loss_price),
            limit_price=take_profit_price,
            client_order_id=self._build_protection_client_order_id(symbol),
            extended_hours=False,
        )

        await self._run_client(
            "submit_order",
            lambda client: client.submit_order(oco_request),
        )

    async def _cancel_open_orders_for_symbol(self, symbol: str) -> None:
        open_orders = await self._get_raw_open_orders()
        seen_order_ids: set[str] = set()
        for order in open_orders:
            if not self._is_active_order(order):
                continue
            if self._order_symbol(order) != symbol.upper():
                continue
            order_id = str(self._get(order, "id", "") or "")
            if not order_id or order_id in seen_order_ids:
                continue
            seen_order_ids.add(order_id)
            try:
                await self._run_client(
                    "cancel_order_by_id",
                    lambda client, order_id=order_id: client.cancel_order_by_id(order_id),
                )
                logger.info("Cancelled Alpaca order %s for %s", order_id, symbol)
            except Exception as exc:
                logger.warning(
                    "Failed cancelling Alpaca order %s for %s before protection reset: %s",
                    order_id,
                    symbol,
                    exc,
                )

    async def _close_unprotected_position(self, symbol: str) -> None:
        await self._cancel_open_orders_for_symbol(symbol)
        await self._run_client(
            "close_position",
            lambda client: client.close_position(symbol),
        )
        logger.warning("Closed unprotected Alpaca position %s", symbol)

    def _position_has_take_profit_and_stop_loss(
        self,
        position: Any,
        open_orders: list[Any],
    ) -> bool:
        symbol = self._position_symbol(position)
        quantity = self._position_abs_quantity(position)
        exit_side = "sell" if self._position_is_long(position) else "buy"
        if not symbol or quantity <= 0:
            return False

        has_take_profit = False
        has_stop_loss = False
        for order in open_orders:
            if not self._is_active_order(order):
                continue
            if self._order_symbol(order) != symbol:
                continue
            if self._order_side(order) != exit_side:
                continue
            if self._order_remaining_quantity(order) + 1e-9 < quantity:
                continue

            order_type = self._order_type(order)
            if order_type == AlpacaOrderType.LIMIT.value:
                has_take_profit = (
                    has_take_profit
                    or self._safe_float(self._get(order, "limit_price", None)) is not None
                )
            elif order_type in (AlpacaOrderType.STOP.value, AlpacaOrderType.STOP_LIMIT.value):
                has_stop_loss = (
                    has_stop_loss
                    or self._safe_float(self._get(order, "stop_price", None)) is not None
                )

            if has_take_profit and has_stop_loss:
                return True

        return False

    def _protection_prices(self, average_price: float, is_long: bool) -> tuple[float, float]:
        if is_long:
            take_profit_price = average_price * (1.0 + self._take_profit_pct)
            stop_loss_price = average_price * (1.0 - self._stop_loss_pct)
        else:
            take_profit_price = average_price * (1.0 - self._take_profit_pct)
            stop_loss_price = average_price * (1.0 + self._stop_loss_pct)

        take_profit_price = self._round_price(take_profit_price)
        stop_loss_price = self._round_price(stop_loss_price)
        if take_profit_price <= 0 or stop_loss_price <= 0:
            raise ValueError("protection prices must be positive")
        if is_long and take_profit_price <= stop_loss_price:
            raise ValueError("long protection requires take-profit above stop-loss")
        if not is_long and take_profit_price >= stop_loss_price:
            raise ValueError("short protection requires take-profit below stop-loss")
        return take_profit_price, stop_loss_price

    def _position_symbol(self, position: Any) -> str:
        return str(self._get(position, "symbol", "") or "").upper()

    def _position_abs_quantity(self, position: Any) -> float:
        return abs(self._safe_float(self._get(position, "qty", 0)) or 0.0)

    def _position_is_long(self, position: Any) -> bool:
        side = self._enum_value(self._get(position, "side", "")).lower()
        if side == "short":
            return False
        if side == "long":
            return True
        quantity = self._safe_float(self._get(position, "qty", 0)) or 0.0
        return quantity >= 0

    def _order_symbol(self, order: Any) -> str:
        return str(self._get(order, "symbol", "") or "").upper()

    def _order_side(self, order: Any) -> str:
        return self._enum_value(self._get(order, "side", "")).lower()

    def _order_type(self, order: Any) -> str:
        return self._enum_value(
            self._get(order, "type", self._get(order, "order_type", ""))
        ).lower()

    def _order_remaining_quantity(self, order: Any) -> float:
        quantity = self._safe_float(self._get(order, "qty", 0)) or 0.0
        filled_quantity = self._safe_float(self._get(order, "filled_qty", 0)) or 0.0
        return max(0.0, quantity - filled_quantity)

    def _is_active_order(self, order: Any) -> bool:
        return (
            self._enum_value(self._get(order, "status", "")).lower()
            in ACTIVE_ALPACA_ORDER_STATUSES
        )

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
    def _coerce_payload_list(payload: Any, key: str) -> list[Any]:
        if isinstance(payload, dict):
            raw_items = payload.get(key, [])
        else:
            raw_items = payload or []
        if raw_items is None:
            return []
        if isinstance(raw_items, list):
            return raw_items
        return list(raw_items)

    @staticmethod
    def _enum_value(value: Any) -> str:
        raw_value = getattr(value, "value", value)
        return str(raw_value)

    @staticmethod
    def _safe_float(value: Any) -> float | None:
        try:
            if value is None:
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _round_price(price: float) -> float:
        return round(price, 2 if price >= 1.0 else 4)

    @staticmethod
    def _build_protection_client_order_id(symbol: str) -> str:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
        return f"{symbol.upper()[:16]}-PROTECT-{timestamp}"

    @staticmethod
    def _get(item: Any, key: str, default: Any = None) -> Any:
        if isinstance(item, dict):
            return item.get(key, default)
        return getattr(item, key, default)
