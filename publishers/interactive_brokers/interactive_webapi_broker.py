"""Interactive Brokers Web API broker implementation."""
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
from publishers.abstracts.broker_base import BrokerBase


class InteractiveWebapiBroker(BrokerBase):
    """Interactive Brokers Web API broker implementation.
    
    Uses the ibind library to communicate with IBKR's OAuth-based Web API.
    Supports order placement, cancellation, and portfolio retrieval.
    """
    
    # Default answers for IBKR order confirmation questions
    DEFAULT_QUESTION_ANSWERS: dict[QuestionType | str, bool] = {
        QuestionType.PRICE_PERCENTAGE_CONSTRAINT: True,
        QuestionType.ORDER_VALUE_LIMIT: True,
        QuestionType.MISSING_MARKET_DATA: True,
        QuestionType.MANDATORY_CAP_PRICE: True,
        "Stop Variant Order Confirmation": True,
    }
    
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
        try:
            # Extract DH prime from param file
            dh_prime = extract_dh_prime(self._config.dh_param_path)
            
            # Create OAuth config
            oauth_config = OAuth1aConfig(
                access_token=self._config.access_token,
                access_token_secret=self._config.access_token_secret,
                consumer_key=self._config.consumer_key,
                dh_prime=dh_prime,
                encryption_key_fp=self._config.encryption_key_path,
                signature_key_fp=self._config.signature_key_path,
            )
            
            # Create IBKR client
            self._client = IbkrClient(use_oauth=True, oauth_config=oauth_config)
            
            # Get account ID
            accounts_response = self._client.portfolio_accounts()
            if not accounts_response.data:
                raise ConnectionError("No accounts found")
            
            self._account_id = accounts_response.data[0]["id"]
            self._connected = True
            
        except Exception as e:
            self._connected = False
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
        self._ensure_connected()
        assert self._client is not None and self._account_id is not None
        
        # Get contract ID for ticker
        conid = await self._get_conid(order_request.ticker)
        
        # Convert to IBKR order request
        ibkr_order = OrderRequestConverter.to_ibkr(
            order_request=order_request,
            conid=conid,
            account_id=self._account_id,
            listing_exchange=self._config.listing_exchange,
            outside_rth=self._config.outside_rth,
        )
        
        # Place order - returns Result with data attribute
        result = self._client.place_order(
            ibkr_order,
            self.DEFAULT_QUESTION_ANSWERS,
            self._account_id,
        )
        
        # Result.data contains the response - check for errors in data
        if result.data is None:
            raise ValueError("Order placement failed: no response data")
        
        # Check if response contains error
        if isinstance(result.data, dict) and "error" in result.data:
            raise ValueError(f"Order placement failed: {result.data['error']}")
        
        return OrderResponseConverter.from_place_order_response(
            result.data,
            order_request,
        )
    
    async def cancel_order(self, order_id: str) -> OrderResponse:
        """Cancel an existing order.
        
        Args:
            order_id: The unique identifier of the order to cancel.
            
        Returns:
            OrderResponse with updated status.
            
        Raises:
            ValueError: If order_id is invalid or order cannot be cancelled.
        """
        self._ensure_connected()
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
        """Get the current portfolio with all positions.
        
        Returns:
            Portfolio containing positions and account summary.
        """
        self._ensure_connected()
        assert self._client is not None and self._account_id is not None
        
        # Get positions using positions() method
        positions_result = self._client.positions(self._account_id)
        positions_data: list[dict[str, Any]] = (
            positions_result.data 
            if isinstance(positions_result.data, list) 
            else []
        )
        
        # Get ledger data for cash balances
        ledger_result = self._client.get_ledger(self._account_id)
        ledger_data: dict[str, Any] | None = (
            ledger_result.data 
            if isinstance(ledger_result.data, dict) 
            else None
        )
        
        return PortfolioConverter.from_ibkr_positions(positions_data, ledger_data)
    
    async def get_buying_power(self) -> float:
        """Get the current buying power available for trading.
        
        Optimized implementation that only fetches ledger data,
        avoiding the positions endpoint.
        
        Returns:
            Available buying power in account currency.
        """
        self._ensure_connected()
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
