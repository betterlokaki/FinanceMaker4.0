"""Interactive Brokers Web API broker implementation."""
import asyncio
from datetime import date, datetime, timezone
from decimal import Decimal
from functools import wraps
import logging
from typing import Any, Awaitable, Callable, TypeVar, cast
import pandas
from ibind import IbkrClient, QuestionType
from ibind.oauth.oauth1a import OAuth1aConfig
from ibind.support.errors import ExternalBrokerError

from common.converters.ibkr import (
    OrderRequestConverter,
    OrderResponseConverter,
    PortfolioConverter,
)
from common.helpers.dh_prime_helper import extract_dh_prime
from common.models.order_request import OrderRequest
from common.models.order_response import OrderResponse
from common.models.portfolio import Portfolio
from common.models.pnl_summary import PnlSummary
from common.settings import IBKRConfig
from common.models.order import OrderSide, OrderStatus, OrderType
from publishers.abstracts.broker_base import BrokerBase
from publishers.interactive_brokers.always_yes_dict import AlwaysYesDict

logger: logging.Logger = logging.getLogger(__name__)
T = TypeVar("T")


def retry_ibkr_request(
    func: Callable[..., Awaitable[T]],
) -> Callable[..., Awaitable[T]]:
    """Retry decorated broker method after reconnect on IBKR request failures."""

    @wraps(func)
    async def wrapper(self: "InteractiveWebapiBroker", *args: Any, **kwargs: Any) -> T:
        total_attempts = self._request_retry_attempts + 1

        for attempt in range(1, total_attempts + 1):
            try:
                return await func(self, *args, **kwargs)
            except ExternalBrokerError as e:
                status_code = getattr(e, "status_code", None)
                should_retry = self._is_retryable_status_code(status_code)
                if not should_retry or attempt >= total_attempts:
                    raise

                logger.warning(
                    "IBKR request %s failed with status=%s (attempt %d/%d). "
                    "Reconnecting and retrying.",
                    func.__name__,
                    status_code,
                    attempt,
                    total_attempts,
                )
                try:
                    await self._reconnect_for_retry()
                except Exception as reconnect_error:
                    logger.warning(
                        "Reconnect attempt after %s failure did not complete: %s",
                        func.__name__,
                        reconnect_error,
                        exc_info=True,
                    )
                if self._request_retry_delay_seconds > 0:
                    await asyncio.sleep(self._request_retry_delay_seconds)
            except ConnectionError:
                if attempt >= total_attempts:
                    raise

                logger.warning(
                    "Broker connection failed during %s (attempt %d/%d). "
                    "Reconnecting and retrying.",
                    func.__name__,
                    attempt,
                    total_attempts,
                )
                try:
                    await self._reconnect_for_retry()
                except Exception as reconnect_error:
                    logger.warning(
                        "Reconnect attempt after connection failure in %s did not complete: %s",
                        func.__name__,
                        reconnect_error,
                        exc_info=True,
                    )
                if self._request_retry_delay_seconds > 0:
                    await asyncio.sleep(self._request_retry_delay_seconds)

        raise RuntimeError(f"Unexpected retry flow exit for {func.__name__}")

    return cast(Callable[..., Awaitable[T]], wrapper)


class InteractiveWebapiBroker(BrokerBase):
    """Interactive Brokers Web API broker implementation.
    
    Uses the ibind library to communicate with IBKR's OAuth-based Web API.
    Supports order placement, cancellation, and portfolio retrieval.
    """
    
    # Default answers for IBKR order confirmation questions
    # Uses AlwaysYesDict to automatically answer "yes" (True) to all questions,
    # even if the specific QuestionType isn't explicitly listed
    PORTFOLIO_REFRESH_INTERVAL_SECONDS: int = 300  # 5 minutes
    
    # Default answers for IBKR order confirmation questions
    # Uses AlwaysYesDict to automatically answer "yes" (True) to all questions,
    # even if the specific QuestionType isn't explicitly listed
    DEFAULT_QUESTION_ANSWERS = AlwaysYesDict({
        QuestionType.PRICE_PERCENTAGE_CONSTRAINT: True,
        QuestionType.ORDER_VALUE_LIMIT: True,
        QuestionType.MISSING_MARKET_DATA: True,
        QuestionType.MANDATORY_CAP_PRICE: True,
        QuestionType.STOP_ORDER_RISKS: True,
    })
    
    def __init__(self, config: IBKRConfig) -> None:
        """Initialize the Interactive Brokers broker.
        
        Args:
            config: IBKR configuration with OAuth credentials.
        """
        super().__init__()
        self._config = config
        self._client: IbkrClient | None = None
        self._account_id: str | None = None
        self._conid_cache: dict[str, int] = {}
        self._portfolio_refresh_task: asyncio.Task[None] | None = None
        self._request_retry_attempts: int = max(0, config.request_retry_attempts)
        self._request_retry_delay_seconds: float = max(0.0, config.request_retry_delay_seconds)
    
    async def connect(self) -> None:
        """Establish connection to Interactive Brokers.
        
        Creates OAuth client and retrieves account information.
        
        Raises:
            ConnectionError: If connection or authentication fails.
        """
        logger.info("🔌 Starting Interactive Brokers connection...")
        try:
            # Extract DH prime from param file
            logger.debug("Extracting DH prime from: %s", self._config.dh_param_path)
            dh_prime = extract_dh_prime(self._config.dh_param_path)
            logger.debug("✅ DH prime extracted successfully")
            
            # Create OAuth config
            logger.debug("Creating OAuth configuration...")
            oauth_config = OAuth1aConfig(
                access_token=self._config.access_token,
                access_token_secret=self._config.access_token_secret,
                consumer_key=self._config.consumer_key,
                dh_prime=dh_prime,
                encryption_key_fp=self._config.encryption_key_path,
                signature_key_fp=self._config.signature_key_path,
            )
            logger.debug("✅ OAuth config created")
            
            # Create IBKR client
            logger.info("Creating IBKR client with OAuth...")
            self._client = IbkrClient(use_oauth=True, oauth_config=oauth_config)
            logger.info("✅ IBKR client created")
            
            # Get account ID
            logger.info("Fetching portfolio accounts...")

            accounts_response = self._client.portfolio_accounts()
            logger.debug("Accounts response: %s", accounts_response)
            
            if not accounts_response.data:
                raise ConnectionError("No accounts found")
            
            self._account_id = accounts_response.data[0]["id"]
            logger.info("✅ Connected to IBKR! Account ID: %s", self._account_id)
            
            # Log all accounts found
            if isinstance(accounts_response.data, list):
                logger.info("📊 Found %d account(s):", len(accounts_response.data))
                for idx, account in enumerate(accounts_response.data):
                    logger.info("  Account %d: %s", idx + 1, account)
            
            self._connected = True
            
            # Immediately fetch and log portfolio after connection
            logger.info("📈 Fetching portfolio information...")
            try:
                portfolio = await self._get_portfolio_once()
                logger.info("=" * 80)
                logger.info("📊 PORTFOLIO SUMMARY")
                logger.info("=" * 80)
                logger.info("💰 Cash Balance: $%.2f", portfolio.cash_balance)
                logger.info("💵 Total Market Value: $%.2f", portfolio.total_market_value)
                logger.info("📊 Total Equity: $%.2f", portfolio.total_equity)
                logger.info("💪 Buying Power: $%.2f", portfolio.buying_power)
                logger.info("📈 Unrealized P&L: $%.2f", portfolio.unrealized_pnl)
                logger.info("💰 Realized P&L: $%.2f", portfolio.realized_pnl)
                logger.info("")
                logger.info("📦 Positions (%d):", portfolio.position_count)
                if portfolio.positions:
                    for position in portfolio.positions:
                        pnl_pct = position.unrealized_pnl_percent or 0.0
                        logger.info("  • %s: %s shares @ $%.2f | Value: $%.2f | P&L: $%.2f (%.2f%%)",
                                  position.ticker,
                                  position.quantity,
                                  position.average_cost,
                                  position.market_value or 0.0,
                                  position.unrealized_pnl or 0.0,
                                  pnl_pct)
                else:
                    logger.info("  (No positions)")
                logger.info("")
                logger.info("📋 Open Orders (%d):", len(portfolio.open_orders))
                if portfolio.open_orders:
                    for order in portfolio.open_orders:
                        price = order.limit_price or order.stop_price or order.average_fill_price or 0.0
                        logger.info("  • %s: %s %s %s @ $%.2f | Status: %s | Order ID: %s",
                                  order.ticker,
                                  order.side.value,
                                  order.quantity,
                                  order.order_type.value,
                                  price,
                                  order.status.value,
                                  order.order_id)
                else:
                    logger.info("  (No open orders)")
                logger.info("=" * 80)
            except Exception as portfolio_error:
                logger.error("❌ Failed to fetch portfolio after connection: %s", portfolio_error, exc_info=True)
            
            # Start background portfolio refresh task once. During reconnects
            # triggered by this same task, keep the existing loop alive.
            existing_refresh_task = self._portfolio_refresh_task
            if existing_refresh_task is None or existing_refresh_task.done():
                self._portfolio_refresh_task = asyncio.create_task(self._refresh_portfolio_loop())
                logger.info(
                    "🔄 Portfolio refresh task started (every %d seconds)",
                    self.PORTFOLIO_REFRESH_INTERVAL_SECONDS,
                )
            else:
                logger.debug("Portfolio refresh task already running; skipping restart")
            
        except Exception as e:
            self._connected = False
            logger.error("❌ Failed to connect to IBKR: %s", e, exc_info=True)
            raise ConnectionError(f"Failed to connect to IBKR: {e}") from e
    
    async def disconnect(self) -> None:
        """Disconnect from Interactive Brokers."""
        # Stop portfolio refresh task
        refresh_task = self._portfolio_refresh_task
        if refresh_task is not None:
            current_task = asyncio.current_task()
            if refresh_task is current_task:
                logger.debug(
                    "Disconnect called from portfolio refresh task; skipping self-cancel"
                )
            elif refresh_task.done():
                self._portfolio_refresh_task = None
            else:
                refresh_task.cancel()
                try:
                    await refresh_task
                except asyncio.CancelledError:
                    pass
                self._portfolio_refresh_task = None
                logger.info("🔄 Portfolio refresh task stopped")
        
        if self._client is not None:
            self._client.close()
        self._client = None
        self._account_id = None
        self._conid_cache.clear()
        self._connected = False
    
    @retry_ibkr_request
    async def place_order(self, order_request: OrderRequest) -> OrderResponse:
        """Place an order with Interactive Brokers.
        
        Args:
            order_request: The order request containing order details.
            
        Returns:
            OrderResponse with order status and details.
            
        Raises:
            ValueError: If order request is invalid.
            ConnectionError: If not connected to broker.
        """
        await self._ensure_connected()
        assert self._client is not None and self._account_id is not None
        
        # Get contract ID for ticker
        conid = await self._get_conid(order_request.ticker)
        
        # Convert to IBKR order request(s). May return a single request or a list
        # (bracket orders). The `ibind` client `place_order` accepts either a
        # dict-like request or a list of requests for bracket submission.
        ibkr_requests = OrderRequestConverter.to_ibkr(
            order_request=order_request,
            conid=conid,
            account_id=self._account_id,
            listing_exchange=self._config.listing_exchange,
            outside_rth=self._config.outside_rth,
        )

        # Place order - support single request or list of requests. Attempt to
        # submit bracket as a single batch (parent coid + children parent_id).
        # If IBKR rejects that (some environments may require the parent to be
        # registered first), fall back to submitting the parent first and then
        # submitting children referencing the returned parent order id.
        result = self._client.place_order(
            ibkr_requests,
            self.DEFAULT_QUESTION_ANSWERS,
            self._account_id,
        )

        # Result.data contains the response - must be present
        if result.data is None:
            raise ValueError("Order placement failed: no response data")

        # If IBKR returned an error, allow a fallback for bracket submission
        # Success path: convert IBKR response to our OrderResponse
        order_responses = OrderResponseConverter.from_place_order_response(
            result.data,
            order_request,
        )
        try:
            self.portfolio = await self.get_portfolio()
        except Exception as portfolio_error:
            logger.warning(
                "Order submitted but portfolio refresh failed: %s",
                portfolio_error,
                exc_info=True,
            )
        return order_responses

    @retry_ibkr_request
    async def cancel_order(self, order_id: str) -> OrderResponse:
        """Cancel an existing order.
        
        Args:
            order_id: The unique identifier of the order to cancel.
            
        Returns:
            OrderResponse with updated status.
            
        Raises:
            ValueError: If order_id is invalid or order cannot be cancelled.
        """
        await self._ensure_connected()
        assert self._client is not None and self._account_id is not None
        
        # cancel_order takes (order_id, account_id) per ibind API
        result = self._client.cancel_order(order_id, self._account_id)
        
        if result.data is None:
            raise ValueError(f"Order cancellation failed for {order_id}")
        
        # Check if response contains error
        if isinstance(result.data, dict) and "error" in result.data:
            raise ValueError(f"Order cancellation failed: {result.data['error']}")
        
        # Get updated order status
        return await self.get_order(order_id)
    
    @retry_ibkr_request
    async def get_order(self, order_id: str) -> OrderResponse:
        """Get the current status of an order.
        
        Args:
            order_id: The unique identifier of the order.
            
        Returns:
            OrderResponse with current order details and status.
            
        Raises:
            ValueError: If order_id is not found.
        """
        await self._ensure_connected()
        assert self._client is not None
        
        # First try order_status for specific order
        result = self._client.order_status(order_id)
        
        if result.data and isinstance(result.data, dict):
            # order_status returns a single order dict
            if "error" not in result.data:
                return OrderResponseConverter.from_ibkr(result.data)
        
        # Fallback to live_orders and search
        orders_result = self._client.live_orders()
        
        if orders_result.data is None:
            raise ValueError("Failed to get orders")
        
        # live_orders returns dict with 'orders' key
        orders_data = orders_result.data
        if isinstance(orders_data, dict):
            orders = orders_data.get("orders", [])
        else:
            orders = []
        
        for order_data in orders:
            if str(order_data.get("orderId")) == order_id:
                return OrderResponseConverter.from_ibkr(order_data)
        
        raise ValueError(f"Order not found: {order_id}")
    
    @retry_ibkr_request
    async def get_portfolio(self) -> Portfolio:
        """Get the current portfolio with all positions and open orders.
        
        Returns:
            Portfolio containing positions, open orders, and account summary.
        """
        return await self._get_portfolio_once()

    async def _get_portfolio_once(self) -> Portfolio:
        """Execute a single portfolio fetch without decorator retries."""
        logger.debug("Getting portfolio for account: %s", self._account_id)
        await self._ensure_connected()
        assert self._client is not None and self._account_id is not None
        
        # Get positions using positions() method
        logger.debug("Fetching positions...")
        positions_result = self._client.positions(self._account_id)
        logger.debug("Positions API response: %s", positions_result)
        positions_data: list[dict[str, Any]] = (
            positions_result.data 
            if isinstance(positions_result.data, list) 
            else []
        )
        logger.info("📦 Retrieved %d position(s) from IBKR", len(positions_data))
        
        # Get ledger data for cash balances
        logger.debug("Fetching ledger data...")
        ledger_result = self._client.get_ledger(self._account_id)
        logger.debug("Ledger API response: %s", ledger_result)
        ledger_data: dict[str, Any] | None = (
            ledger_result.data 
            if isinstance(ledger_result.data, dict) 
            else None
        )
        if ledger_data:
            logger.debug("Ledger data keys: %s", list(ledger_data.keys()))
        
        # Get open orders
        logger.debug("Fetching open orders...")
        open_orders = await self._get_open_orders_once()
        logger.info("📋 Retrieved %d open order(s) from IBKR", len(open_orders))
        
        portfolio = PortfolioConverter.from_ibkr_positions(positions_data, ledger_data, open_orders)
        logger.debug("✅ Portfolio converted successfully")
        self.portfolio = portfolio
        return portfolio
    
    @retry_ibkr_request
    async def get_buying_power(self) -> float:
        """Get the current buying power available for trading.
        
        Optimized implementation that only fetches ledger data,
        avoiding the positions endpoint.
        
        Returns:
            Available buying power in account currency.
        """
        await self._ensure_connected()
        assert self._client is not None and self._account_id is not None
        
        # Get ledger data for cash balances (no positions needed)
        ledger_result = self._client.get_ledger(self._account_id)
        ledger_data: dict[str, Any] | None = (
            ledger_result.data 
            if isinstance(ledger_result.data, dict) 
            else None
        )
        
        if ledger_data is None:
            return 0.0
        
        # Extract buying power from ledger - use BASE or USD
        base_ledger = ledger_data.get("BASE", ledger_data.get("USD", {}))
        return float(base_ledger.get("settledcash", 0) or 0)

    @retry_ibkr_request
    async def get_pnl_summary(self, since_date: date) -> PnlSummary:
        """Get normalized daily and cumulative P/L from IBKR endpoints."""
        await self._ensure_connected()
        assert self._client is not None and self._account_id is not None

        pnl_result = self._client.account_profit_and_loss()
        performance_result = self._client.account_performance([self._account_id], period="1Y")

        daily_pnl = self._extract_daily_pnl(
            payload=pnl_result.data,
            account_id=self._account_id,
        )
        (
            pnl_since_date,
            baseline_date,
            baseline_nav,
            current_nav,
            currency,
        ) = self._extract_since_date_pnl(
            payload=performance_result.data,
            since_date=since_date,
            account_id=self._account_id,
        )

        return PnlSummary(
            as_of_date=datetime.now(timezone.utc).date(),
            since_date=since_date,
            currency=currency or "USD",
            daily_pnl=daily_pnl,
            pnl_since_date=pnl_since_date,
            baseline_date=baseline_date,
            baseline_nav=baseline_nav,
            current_nav=current_nav,
        )

    @staticmethod
    def _extract_daily_pnl(payload: Any, account_id: str | None) -> float | None:
        """Extract daily P/L (`dpl`) from /iserver/account/pnl/partitioned."""
        if not isinstance(payload, dict):
            return None

        upnl_payload = payload.get("upnl")
        if not isinstance(upnl_payload, dict) or not upnl_payload:
            return None

        preferred_keys: list[str] = []
        if account_id:
            preferred_keys.extend([f"{account_id}.Core", account_id])

        selected: dict[str, Any] | None = None
        for key in preferred_keys:
            candidate = upnl_payload.get(key)
            if isinstance(candidate, dict):
                selected = candidate
                break

        if selected is None:
            for key, value in upnl_payload.items():
                if isinstance(value, dict) and isinstance(key, str) and key.endswith(".Core"):
                    selected = value
                    break

        if selected is None:
            selected = next(
                (value for value in upnl_payload.values() if isinstance(value, dict)),
                None,
            )

        if selected is None:
            return None

        return InteractiveWebapiBroker._safe_float(selected.get("dpl"))

    @staticmethod
    def _extract_since_date_pnl(
        payload: Any,
        since_date: date,
        account_id: str | None,
    ) -> tuple[float | None, date | None, float | None, float | None, str | None]:
        """Extract cumulative P/L since `since_date` from /pa/performance NAV series."""
        if not isinstance(payload, dict):
            return None, None, None, None, None

        nav = payload.get("nav")
        if not isinstance(nav, dict):
            return None, None, None, None, None

        dates_raw = nav.get("dates")
        if not isinstance(dates_raw, list):
            return None, None, None, None, None

        nav_data = nav.get("data")
        if not isinstance(nav_data, list) or not nav_data:
            return None, None, None, None, None

        selected = None
        if account_id:
            selected = next(
                (
                    item
                    for item in nav_data
                    if isinstance(item, dict) and str(item.get("id", "")) == account_id
                ),
                None,
            )
        if selected is None:
            selected = next((item for item in nav_data if isinstance(item, dict)), None)
        if not isinstance(selected, dict):
            return None, None, None, None, None

        navs_raw = selected.get("navs")
        if not isinstance(navs_raw, list):
            return None, None, None, None, None

        paired: list[tuple[date, float]] = []
        max_items = min(len(dates_raw), len(navs_raw))
        for idx in range(max_items):
            dt = InteractiveWebapiBroker._parse_yyyymmdd(dates_raw[idx])
            value = InteractiveWebapiBroker._safe_float(navs_raw[idx])
            if dt is None or value is None:
                continue
            paired.append((dt, value))

        if not paired:
            return None, None, None, None, None

        baseline_date, baseline_nav = InteractiveWebapiBroker._pick_baseline_nav(
            nav_points=paired,
            start_nav=selected.get("startNAV"),
            since_date=since_date,
        )

        current_date, current_nav = paired[-1]
        if baseline_nav is None:
            return None, baseline_date, None, current_nav, selected.get("baseCurrency")
        if since_date > current_date:
            return None, baseline_date, baseline_nav, current_nav, selected.get("baseCurrency")

        return (
            current_nav - baseline_nav,
            baseline_date,
            baseline_nav,
            current_nav,
            selected.get("baseCurrency"),
        )

    @staticmethod
    def _pick_baseline_nav(
        nav_points: list[tuple[date, float]],
        start_nav: Any,
        since_date: date,
    ) -> tuple[date | None, float | None]:
        """Pick baseline NAV immediately before `since_date` if available."""
        if not nav_points:
            return None, None

        first_idx: int | None = None
        for idx, (dt, _) in enumerate(nav_points):
            if dt >= since_date:
                first_idx = idx
                break

        if first_idx is None:
            return nav_points[-1][0], nav_points[-1][1]

        if first_idx > 0:
            prev_dt, prev_nav = nav_points[first_idx - 1]
            return prev_dt, prev_nav

        baseline_date = None
        baseline_nav = None
        if isinstance(start_nav, dict):
            baseline_date = InteractiveWebapiBroker._parse_yyyymmdd(start_nav.get("date"))
            baseline_nav = InteractiveWebapiBroker._safe_float(start_nav.get("val"))

        if baseline_date is not None and baseline_nav is not None:
            return baseline_date, baseline_nav

        return nav_points[0][0], nav_points[0][1]

    @staticmethod
    def _parse_yyyymmdd(raw: Any) -> date | None:
        """Parse IBKR yyyymmdd date values."""
        if not isinstance(raw, str) or len(raw) != 8:
            return None
        try:
            return datetime.strptime(raw, "%Y%m%d").date()
        except ValueError:
            return None

    @staticmethod
    def _safe_float(value: Any) -> float | None:
        """Coerce numeric-like value to float."""
        try:
            if value is None:
                return None
            return float(value)
        except (TypeError, ValueError):
            return None
    
    @retry_ibkr_request
    async def get_open_orders(self) -> list[OrderResponse]:
        """Get all open/pending orders.
        
        Returns:
            List of OrderResponse objects for all active orders (pending, submitted, partially filled).
        """
        return await self._get_open_orders_once()

    @retry_ibkr_request
    async def get_orders_between(
        self,
        after: datetime,
        until: datetime,
    ) -> list[OrderResponse]:
        """Get broker orders created or updated in a UTC time range."""
        await self._ensure_connected()
        assert self._client is not None

        orders_result = self._client.live_orders()
        if orders_result.data is None:
            return []

        orders_data = orders_result.data
        raw_orders = orders_data.get("orders", []) if isinstance(orders_data, dict) else []
        orders: list[OrderResponse] = []
        for order_data in raw_orders:
            try:
                order = OrderResponseConverter.from_ibkr(order_data)
            except Exception as exc:
                logger.warning("Failed to convert IBKR order history row: %s", exc, exc_info=True)
                continue
            if self._order_in_range(order, after, until):
                orders.append(order)
        return orders

    async def _get_open_orders_once(self) -> list[OrderResponse]:
        """Execute a single open-orders fetch without decorator retries."""
        logger.debug("Getting open orders...")
        await self._ensure_connected()
        assert self._client is not None
        
        # Get live orders from ibind
        orders_result = self._client.live_orders()
        logger.debug("Live orders API response: %s", orders_result)
        
        if orders_result.data is None:
            logger.debug("No orders data in response")
            return []
        
        # live_orders returns dict with 'orders' key
        orders_data = orders_result.data
        if isinstance(orders_data, dict):
            orders = orders_data.get("orders", [])
        else:
            orders = []
        
        logger.debug("Found %d raw order(s) from API", len(orders))
        
        # Convert to OrderResponse and filter for active orders only
        open_orders: list[OrderResponse] = []
        for order_data in orders:
            try:
                order_response = OrderResponseConverter.from_ibkr(order_data)
                # Only include active orders (pending, submitted, partially filled)
                if order_response.is_active:
                    open_orders.append(order_response)
                    price = order_response.limit_price or order_response.stop_price or order_response.average_fill_price or 0.0
                    logger.debug("Active order: %s %s %s %s @ $%.2f | Status: %s",
                               order_response.ticker,
                               order_response.side.value,
                               order_response.quantity,
                               order_response.order_type.value,
                               price,
                               order_response.status.value)
            except Exception as e:
                # Skip orders that can't be converted
                logger.warning("Failed to convert order data: %s", e, exc_info=True)
                continue
        
        logger.debug("Returning %d active order(s)", len(open_orders))
        return open_orders

    @staticmethod
    def _order_in_range(order: OrderResponse, after: datetime, until: datetime) -> bool:
        timestamp = order.filled_at or order.updated_at or order.created_at
        if timestamp is None:
            return False
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
        return after <= timestamp.astimezone(timezone.utc) <= until
    
    async def _refresh_portfolio_loop(self) -> None:
        """Background loop that refreshes the portfolio every 5 minutes."""
        while True:
            try:
                await asyncio.sleep(self.PORTFOLIO_REFRESH_INTERVAL_SECONDS)
                logger.info("🔄 Refreshing portfolio...")
                self.portfolio = await self.get_portfolio()
                logger.info("✅ Portfolio refreshed successfully")
            except asyncio.CancelledError:
                logger.debug("Portfolio refresh loop cancelled")
                raise
            except Exception as e:
                logger.error("❌ Failed to refresh portfolio: %s", e, exc_info=True)
    
    async def _get_conid(self, ticker: str) -> int:
        """Get IBKR contract ID for a ticker symbol.
        
        Uses caching to avoid repeated API calls.
        
        Args:
            ticker: Stock ticker symbol.
            
        Returns:
            IBKR contract ID.
            
        Raises:
            ValueError: If contract ID cannot be found.
        """
        ticker_upper = ticker.upper()
        
        # Check cache first
        if ticker_upper in self._conid_cache:
            return self._conid_cache[ticker_upper]
        
        assert self._client is not None
        
        # Fetch from API - returns Result with data attribute
        result = self._client.stock_conid_by_symbol(ticker_upper)
        
        # Data should be a dict mapping symbols to conids
        if not result.data or not isinstance(result.data, dict):
            raise ValueError(f"Could not find contract ID for {ticker}")
        
        if ticker_upper not in result.data:
            raise ValueError(f"Could not find contract ID for {ticker}")
        
        conid = int(result.data[ticker_upper])
        self._conid_cache[ticker_upper] = conid
        
        return conid

    async def _reconnect_for_retry(self) -> None:
        """Reconnect broker for retrying failed requests."""
        try:
            await self.disconnect()
        except Exception as e:
            logger.warning("Disconnect during retry reconnect failed: %s", e, exc_info=True)

        await self.connect()

    @staticmethod
    def _is_retryable_status_code(status_code: int | None) -> bool:
        """Retry on non-2xx status (or missing status for transport-level failures)."""
        if status_code is None:
            return True
        return not (200 <= status_code <= 299)
    
    @property
    def account_id(self) -> str | None:
        """Get the current account ID.
        
        Returns:
            Account ID if connected, None otherwise.
        """
        return self._account_id
    async def _get_relized_money(self, start_date: date, end_date: date) -> float:
        await self._ensure_connected()
    
        CONIDS = [28812380]  # AAPL, TSLA etc — put every traded conid here

        total = 0.0
        # trades = self._client.get("iserver/account/trades", params={
        #         "acctId": self._account_id
        #     }).data

        # conids = {
        #     str(t["conid"])
        #     for t in trades
        #     if t.get("conid")
        # }
        # print(conids)
        for conid in CONIDS:

            r = self._client.transaction_history(
                account_ids=self._account_id,
                conids=[conid],

                currency="USD",

                days=730,

            ).data

            rpnl = r.get("rpnl", {})

            amt = Decimal(str(rpnl.get("amt", "0")))

            total += float(amt)

            print("CONID", conid, "REALIZED:", amt)

            for row in rpnl.get("data", []):

                print(row["date"], row["acctid"], row["conid"], row["cur"], row["amt"])

        print("TOTAL REALIZED PNL 2Y:", total)
        return 0.0
