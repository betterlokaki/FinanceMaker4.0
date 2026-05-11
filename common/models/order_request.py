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
        stop_loss_price: Stop loss price for bracket orders (fixed STP).
            Also used as initial stop level reference for trailing stops.
        take_profit_price: Take profit price for bracket orders.
        trailing_stop_amt: Trailing amount for dynamic stop loss.
            When set, the bracket stop-loss child uses a TRAIL order instead of
            a fixed STP. The broker adjusts the stop automatically as price
            moves in your favour.
        trailing_stop_type: Trailing type — "%" for percentage or "amt" for
            dollar amount. Defaults to "%" when trailing_stop_amt is set.
        time_in_force: How long the order remains active.
        extended_hours: Whether a simple broker order may execute outside RTH.
        buy_limit_rth: Whether buy limit order executes only during RTH (None = use default).
        stop_loss_rth: Whether stop loss order executes only during RTH (None = use default).
        take_profit_rth: Whether take profit order executes only during RTH (None = use default).
    """
    ticker: str
    quantity: int
    side: OrderSide
    order_type: OrderType = OrderType.MARKET
    limit_price: Optional[float] = None
    stop_price: Optional[float] = None
    stop_loss_price: Optional[float] = None
    take_profit_price: Optional[float] = None
    trailing_stop_amt: Optional[float] = None
    trailing_stop_type: Optional[str] = None
    time_in_force: TimeInForce = TimeInForce.DAY
    extended_hours: Optional[bool] = None
    buy_limit_rth: Optional[bool] = None
    stop_loss_rth: Optional[bool] = None
    take_profit_rth: Optional[bool] = None
    
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
        
        if self.trailing_stop_amt is not None:
            if self.trailing_stop_amt <= 0:
                raise ValueError("Trailing stop amount must be positive")
            if self.trailing_stop_type is not None and self.trailing_stop_type not in ("%", "amt"):
                raise ValueError(
                    "Trailing stop type must be '%' (percentage) or 'amt' (dollar amount)"
                )
