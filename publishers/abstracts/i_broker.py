"""Broker interface protocol."""
from typing import Protocol

from common.models.order_request import OrderRequest
from common.models.order_response import OrderResponse
from common.models.portfolio import Portfolio


class IBroker(Protocol):
    """Broker interface - defines contract for all broker implementations.
    
    This protocol defines the interface that all broker implementations
    must follow. Use this for type hints instead of concrete classes.
    """

    async def connect(self) -> None:
        """Establish connection to the broker.
        
        Raises:
            ConnectionError: If connection fails.
        """
        ...

    async def disconnect(self) -> None:
        """Disconnect from the broker."""
        ...

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
        ...

    async def cancel_order(self, order_id: str) -> OrderResponse:
        """Cancel an existing order.
        
        Args:
            order_id: The unique identifier of the order to cancel.
            
        Returns:
            OrderResponse with updated status.
            
        Raises:
            ValueError: If order_id is invalid or order cannot be cancelled.
        """
        ...

    async def get_order(self, order_id: str) -> OrderResponse:
        """Get the current status of an order.
        
        Args:
            order_id: The unique identifier of the order.
            
        Returns:
            OrderResponse with current order details and status.
            
        Raises:
            ValueError: If order_id is not found.
        """
        ...

    async def get_portfolio(self) -> Portfolio:
        """Get the current portfolio with all positions.
        
        Returns:
            Portfolio containing positions and account summary.
        """
        ...

    @property
    def is_connected(self) -> bool:
        """Check if broker is currently connected.
        
        Returns:
            True if connected, False otherwise.
        """
        ...
