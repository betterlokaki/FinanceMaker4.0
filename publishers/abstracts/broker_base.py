"""Abstract broker base class for trading operations."""
from abc import ABC, abstractmethod
from datetime import date

from common.models.order_request import OrderRequest
from common.models.order_response import OrderResponse
from common.models.portfolio import Portfolio
from common.models.pnl_summary import PnlSummary
from publishers.abstracts.i_broker import IBroker


class BrokerBase(IBroker, ABC):
    """Abstract base class for broker implementations.
    
    Provides a common interface for different broker integrations
    (e.g., Interactive Brokers, Alpaca, TD Ameritrade).
    
    All broker implementations should inherit from this class and implement
    the abstract methods. This class implements the IBroker protocol.
    """
    
    def __init__(self) -> None:
        """Initialize the broker base."""
        self._connected: bool = False

    @abstractmethod
    async def connect(self) -> None:
        """Establish connection to the broker.
        
        Raises:
            ConnectionError: If connection fails.
        """
        pass

    @abstractmethod
    async def disconnect(self) -> None:
        """Disconnect from the broker."""
        pass

    @abstractmethod
    async def place_order(self, order_request: OrderRequest) -> OrderResponse:
        """Place an order with the broker.
        
        Args:
            order_request: The order request containing order details.
            
        Returns:
            OrderResponse with order status and details.
            
        Raises:
            ValueError: If order request is invalid.
            ConnectionError: If not connected to broker.
        """
        pass

    @abstractmethod
    async def cancel_order(self, order_id: str) -> OrderResponse:
        """Cancel an existing order.
        
        Args:
            order_id: The unique identifier of the order to cancel.
            
        Returns:
            OrderResponse with updated status.
            
        Raises:
            ValueError: If order_id is invalid or order cannot be cancelled.
        """
        pass

    @abstractmethod
    async def get_order(self, order_id: str) -> OrderResponse:
        """Get the current status of an order.
        
        Args:
            order_id: The unique identifier of the order.
            
        Returns:
            OrderResponse with current order details and status.
            
        Raises:
            ValueError: If order_id is not found.
        """
        pass

    @abstractmethod
    async def get_portfolio(self) -> Portfolio:
        """Get the current portfolio with all positions.
        
        Returns:
            Portfolio containing positions and account summary.
        """
        pass
    
    @abstractmethod
    async def get_open_orders(self) -> list[OrderResponse]:
        """Get all open/pending orders.
        
        Returns:
            List of OrderResponse objects for all active orders (pending, submitted, partially filled).
        """
        pass

    async def get_buying_power(self) -> float:
        """Get the current buying power available for trading.
        
        Returns:
            Available buying power in account currency.
        """
        portfolio: Portfolio = await self.get_portfolio()
        return portfolio.buying_power

    @abstractmethod
    async def get_pnl_summary(self, since_date: date) -> PnlSummary:
        """Get P/L summary for reporting.

        Args:
            since_date: Baseline date used for cumulative P/L.

        Returns:
            Summary containing daily P/L and P/L since `since_date`.
        """
        pass

    @property
    def is_connected(self) -> bool:
        """Check if broker is currently connected.
        
        Returns:
            True if connected, False otherwise.
        """
        return self._connected

    async def _ensure_connected(self) -> None:
        """Ensure broker is connected, auto-connect if not connected.
        
        Raises:
            ConnectionError: If connection fails.
        """
        if not self._connected:
            await self.connect()
