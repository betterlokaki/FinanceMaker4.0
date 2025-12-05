"""IBKR order request converter."""
from ibind import OrderRequest as IbkrOrderRequest

from common.models.order import OrderSide, OrderType, TimeInForce
from common.models.order_request import OrderRequest


class OrderRequestConverter:
    """Converts our OrderRequest model to IBKR OrderRequest."""
    
    # Mapping from our OrderSide to IBKR side strings
    SIDE_MAP: dict[OrderSide, str] = {
        OrderSide.BUY: "BUY",
        OrderSide.SELL: "SELL",
    }
    
    # Mapping from our OrderType to IBKR order_type strings
    ORDER_TYPE_MAP: dict[OrderType, str] = {
        OrderType.MARKET: "MKT",
        OrderType.LIMIT: "LMT",
        OrderType.STOP: "STP",
        OrderType.STOP_LIMIT: "STP_LIMIT",
    }
    
    # Mapping from our TimeInForce to IBKR tif strings
    TIF_MAP: dict[TimeInForce, str] = {
        TimeInForce.DAY: "DAY",
        TimeInForce.GTC: "GTC",
        TimeInForce.IOC: "IOC",
        TimeInForce.FOK: "FOK",
        TimeInForce.GTD: "GTD",
    }

    @classmethod
    def to_ibkr(
        cls,
        order_request: OrderRequest,
        conid: int,
        account_id: str,
        listing_exchange: str = "SMART",
        outside_rth: bool = True,
        order_id: str | None = None,
        parent_id: str | None = None,
    ) -> IbkrOrderRequest:
        """Convert our OrderRequest to IBKR OrderRequest.
        
        Args:
            order_request: Our internal order request model.
            conid: IBKR contract ID for the ticker.
            account_id: IBKR account ID.
            listing_exchange: Exchange to use (default: SMART).
            outside_rth: Allow outside regular trading hours.
            order_id: Optional custom order ID.
            parent_id: Optional parent order ID for bracket orders.
            
        Returns:
            IBKR OrderRequest ready for submission.
        """
        ibkr_order = IbkrOrderRequest(
            conid=conid,
            side=cls.SIDE_MAP[order_request.side],
            quantity=order_request.quantity,
            order_type=cls.ORDER_TYPE_MAP[order_request.order_type],
            acct_id=account_id,
            ticker=order_request.ticker,
            listing_exchange=listing_exchange,
            outside_rth=outside_rth,
            tif=cls.TIF_MAP[order_request.time_in_force],
        )
        
        # Add limit price if applicable
        if order_request.limit_price is not None:
            ibkr_order.price = order_request.limit_price
        
        # Add stop price if applicable (aux_price in IBKR)
        if order_request.stop_price is not None:
            ibkr_order.aux_price = order_request.stop_price
        
        # Add optional order IDs
        if order_id is not None:
            ibkr_order.coid = order_id
        
        if parent_id is not None:
            ibkr_order.parent_id = parent_id
        
        return ibkr_order
