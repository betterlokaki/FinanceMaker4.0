"""Order request model for placing orders with brokers."""
from dataclasses import dataclass
from typing import Optional

from common.models.order import OrderSide, OrderType, TimeInForce


@dataclass
class OrderRequest:
    """Request to place an order with a broker.
    
    Attributes:
        ticker: Stock ticker symbol (e.g., "AAPL").
        quantity: Number of shares to trade.
        side: Buy or sell.
        order_type: Market, limit, stop, etc.
        limit_price: Price for limit orders.
        stop_price: Trigger price for stop orders.
        stop_loss_price: Stop loss price for bracket orders.
        take_profit_price: Take profit price for bracket orders.
        time_in_force: How long the order remains active.
    """
    ticker: str
    quantity: int
    side: OrderSide
    order_type: OrderType = OrderType.MARKET
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    stop_loss_price: Optional[float] = None
    take_profit_price: Optional[float] = None
    time_in_force: TimeInForce = TimeInForce.DAY
    
    def __post_init__(self) -> None:
        """Validate order request parameters."""
        if self.quantity <= 0:
            raise ValueError("Quantity must be positive")
        
        if self.order_type == OrderType.LIMIT and self.limit_price is None:
            raise ValueError("Limit price required for limit orders")
        
        if self.order_type == OrderType.STOP and self.stop_price is None:
            raise ValueError("Stop price required for stop orders")
        
        if self.order_type == OrderType.STOP_LIMIT:
            if self.limit_price is None or self.stop_price is None:
                raise ValueError(
                    "Both limit and stop prices required for stop-limit orders"
                )
