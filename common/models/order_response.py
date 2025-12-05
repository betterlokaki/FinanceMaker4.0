"""Order response model returned by broker after order submission."""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional

from common.models.order import OrderSide, OrderStatus, OrderType, TimeInForce


@dataclass
class OrderResponse:
    """Response from broker after order submission.
    
    Attributes:
        order_id: Unique identifier assigned by the broker.
        ticker: Stock ticker symbol.
        quantity: Number of shares ordered.
        filled_quantity: Number of shares filled.
        side: Buy or sell.
        order_type: Market, limit, stop, etc.
        status: Current order status.
        limit_price: Limit price if applicable.
        stop_price: Stop price if applicable.
        average_fill_price: Average price of filled shares.
        time_in_force: Order duration setting.
        created_at: When the order was created.
        updated_at: When the order was last updated.
    """
    order_id: str
    ticker: str
    quantity: int
    filled_quantity: int
    side: OrderSide
    order_type: OrderType
    status: OrderStatus
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    average_fill_price: Optional[float] = None
    time_in_force: TimeInForce = TimeInForce.DAY
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    
    @property
    def is_filled(self) -> bool:
        """Check if order is completely filled."""
        return self.status == OrderStatus.FILLED
    
    @property
    def is_active(self) -> bool:
        """Check if order is still active (pending or partially filled)."""
        return self.status in (OrderStatus.PENDING, OrderStatus.SUBMITTED, 
                               OrderStatus.PARTIALLY_FILLED)
    
    @property
    def remaining_quantity(self) -> int:
        """Get remaining quantity to be filled."""
        return self.quantity - self.filled_quantity
