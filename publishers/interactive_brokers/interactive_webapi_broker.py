"""Interactive Brokers Web API broker implementation."""
import logging
from typing import Any

from ibind import IbkrClient, QuestionType
from ibind.oauth.oauth1a import OAuth1aConfig

from common.converters.ibkr import (
    OrderRequestConverter,
    OrderResponseConverter,
    PortfolioConverter,
)
from common.helpers.dh_prime_helper import extract_dh_prime
from common.models.order_request import OrderRequest
from common.models.order_response import OrderResponse
from common.models.portfolio import Portfolio
from common.settings import IBKRConfig
from common.models.order import OrderSide, OrderStatus, OrderType
from publishers.abstracts.broker_base import BrokerBase
from publishers.interactive_brokers.always_yes_dict import AlwaysYesDict

logger: logging.Logger = logging.getLogger(__name__)


class InteractiveWebapiBroker(BrokerBase):
    """Interactive Brokers Web API broker implementation.
    
    Uses the ibind library to communicate with IBKR's OAuth-based Web API.
    Supports order placement, cancellation, and portfolio retrieval.
    """
    
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
                portfolio = await self.get_portfolio()
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
            
        except Exception as e:
            self._connected = False
            logger.error("❌ Failed to connect to IBKR: %s", e, exc_info=True)
            raise ConnectionError(f"Failed to connect to IBKR: {e}") from e
    
    async def disconnect(self) -> None:
        """Disconnect from Interactive Brokers."""
        if self._client is not None:
            self._client.close()
        self._client = None
        self._account_id = None
        self._conid_cache.clear()
        self._connected = False
    
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
        return order_responses
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
    
    async def get_order(self, order_id: str) -> OrderResponse:
        """Get the current status of an order.
        
        Args:
            order_id: The unique identifier of the order.
            
        Returns:
            OrderResponse with current order details and status.
            
        Raises:
            ValueError: If order_id is not found.
        """
        self._ensure_connected()
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
    
    async def get_portfolio(self) -> Portfolio:
        """Get the current portfolio with all positions and open orders.
        
        Returns:
            Portfolio containing positions, open orders, and account summary.
        """
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
        open_orders = await self.get_open_orders()
        logger.info("📋 Retrieved %d open order(s) from IBKR", len(open_orders))
        
        portfolio = PortfolioConverter.from_ibkr_positions(positions_data, ledger_data, open_orders)
        logger.debug("✅ Portfolio converted successfully")
        
        return portfolio
    
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
    
    async def get_open_orders(self) -> list[OrderResponse]:
        """Get all open/pending orders.
        
        Returns:
            List of OrderResponse objects for all active orders (pending, submitted, partially filled).
        """
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
    
    @property
    def account_id(self) -> str | None:
        """Get the current account ID.
        
        Returns:
            Account ID if connected, None otherwise.
        """
        return self._account_id
