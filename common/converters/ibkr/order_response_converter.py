"""IBKR order response converter."""
from datetime import datetime
from typing import Any

from common.models.order import OrderSide, OrderStatus, OrderType, TimeInForce
from common.models.order_request import OrderRequest
from common.models.order_response import OrderResponse


class OrderResponseConverter:
    """Converts IBKR order response to our OrderResponse model."""
    
    # Mapping from IBKR side strings to our OrderSide
    # live_orders uses "BUY"/"SELL", order_status uses "B"/"S"
    SIDE_MAP: dict[str, OrderSide] = {
        "BUY": OrderSide.BUY,
        "SELL": OrderSide.SELL,
        "B": OrderSide.BUY,
        "S": OrderSide.SELL,
        "buy": OrderSide.BUY,
        "sell": OrderSide.SELL,
    }
    
    # Mapping from IBKR order_type strings to our OrderType
    # live_orders uses "Limit"/"Market", order_status uses "LIMIT"/"MKT"
    ORDER_TYPE_MAP: dict[str, OrderType] = {
        "MKT": OrderType.MARKET,
        "MARKET": OrderType.MARKET,
        "Market": OrderType.MARKET,
        "LMT": OrderType.LIMIT,
        "LIMIT": OrderType.LIMIT,
        "Limit": OrderType.LIMIT,
        "STP": OrderType.STOP,
        "STOP": OrderType.STOP,
        "Stop": OrderType.STOP,
        "STP_LIMIT": OrderType.STOP_LIMIT,
        "STPLMT": OrderType.STOP_LIMIT,
        "STP LMT": OrderType.STOP_LIMIT,
    }
    
    # Mapping from IBKR status strings to our OrderStatus
    STATUS_MAP: dict[str, OrderStatus] = {
        "Pending": OrderStatus.PENDING,
        "PendingSubmit": OrderStatus.PENDING,
        "PreSubmitted": OrderStatus.PENDING,
        "Submitted": OrderStatus.SUBMITTED,
        "Filled": OrderStatus.FILLED,
        "PartiallyFilled": OrderStatus.PARTIALLY_FILLED,
        "Cancelled": OrderStatus.CANCELLED,
        "Inactive": OrderStatus.CANCELLED,
        "ApiCancelled": OrderStatus.CANCELLED,
        "Rejected": OrderStatus.REJECTED,
        "Expired": OrderStatus.EXPIRED,
    }
    
    # Mapping from IBKR tif strings to our TimeInForce
    TIF_MAP: dict[str, TimeInForce] = {
        "DAY": TimeInForce.DAY,
        "GTC": TimeInForce.GTC,
        "IOC": TimeInForce.IOC,
        "FOK": TimeInForce.FOK,
        "GTD": TimeInForce.GTD,
    }

    @classmethod
    def from_ibkr(cls, ibkr_response: dict[str, Any]) -> OrderResponse:
        """Convert IBKR order response to our OrderResponse.
        
        Args:
            ibkr_response: Dictionary from IBKR API response.
            
        Returns:
            Our OrderResponse model.
        """
        # Extract order ID (could be 'order_id', 'orderId', or 'id')
        # order_status uses snake_case, live_orders uses camelCase
        order_id = str(
            ibkr_response.get("order_id") 
            or ibkr_response.get("orderId") 
            or ibkr_response.get("id", "")
        )
        
        # Extract ticker symbol
        # live_orders: ticker, order_status: symbol, contract_description_1
        ticker = (
            ibkr_response.get("ticker") 
            or ibkr_response.get("symbol") 
            or ibkr_response.get("contract_description_1")
            or ibkr_response.get("conidex", "").split("@")[0]
            or ""
        )
        
        # Extract quantities
        # live_orders: totalSize, order_status: total_size/size
        quantity = int(float(
            ibkr_response.get("totalSize") 
            or ibkr_response.get("total_size")
            or ibkr_response.get("size")
            or ibkr_response.get("quantity", 0)
        ))
        filled_quantity = int(float(
            ibkr_response.get("filledQuantity") 
            or ibkr_response.get("cum_fill")
            or 0
        ))
        
        # Extract side
        side_str = ibkr_response.get("side", "BUY").upper()
        side = cls.SIDE_MAP.get(side_str, OrderSide.BUY)
        
        # Extract order type
        # live_orders: orderType ("Limit"), order_status: order_type ("LIMIT")
        order_type_str = (
            ibkr_response.get("orderType")
            or ibkr_response.get("order_type")
            or ibkr_response.get("origOrderType")
            or "MKT"
        )
        order_type = cls.ORDER_TYPE_MAP.get(order_type_str, OrderType.MARKET)
        
        # Extract status
        # live_orders: status, order_status: order_status
        status_str = (
            ibkr_response.get("status") 
            or ibkr_response.get("order_status")
            or "Pending"
        )
        status = cls.STATUS_MAP.get(status_str, OrderStatus.PENDING)
        
        # Extract prices
        # live_orders: price, order_status: limit_price
        limit_price_raw = (
            ibkr_response.get("price") 
            or ibkr_response.get("limitPrice")
            or ibkr_response.get("limit_price")
        )
        limit_price = float(limit_price_raw) if limit_price_raw else None
        
        stop_price_raw = (
            ibkr_response.get("auxPrice") 
            or ibkr_response.get("stopPrice")
            or ibkr_response.get("stop_price")
        )
        stop_price = float(stop_price_raw) if stop_price_raw else None
        
        avg_fill_price_raw = (
            ibkr_response.get("avgPrice") 
            or ibkr_response.get("averagePrice")
            or ibkr_response.get("average_price")
        )
        avg_fill_price = float(avg_fill_price_raw) if avg_fill_price_raw else None
        
        # Extract time in force
        # live_orders: timeInForce, order_status: tif
        tif_str = (
            ibkr_response.get("timeInForce") 
            or ibkr_response.get("tif") 
            or "DAY"
        ).upper()
        time_in_force = cls.TIF_MAP.get(tif_str, TimeInForce.DAY)
        
        return OrderResponse(
            order_id=order_id,
            ticker=ticker,
            quantity=quantity,
            filled_quantity=filled_quantity,
            side=side,
            order_type=order_type,
            status=status,
            limit_price=limit_price,
            stop_price=stop_price,
            average_fill_price=avg_fill_price,
            time_in_force=time_in_force,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )

    @classmethod
    def from_place_order_response(
        cls,
        response_data: list[dict[str, Any]] | dict[str, Any],
        original_request: OrderRequest,
    ) -> OrderResponse:
        """Convert IBKR place_order response to our OrderResponse.
        
        The place_order response may be a list or single dict.
        
        Args:
            response_data: Response from IBKR place_order API.
            original_request: The original order request for fallback values.
            
        Returns:
            Our OrderResponse model.
        """
        # Handle list response (batch orders)
        if isinstance(response_data, list) and len(response_data) > 0:
            data = response_data[0]
        else:
            data = response_data if isinstance(response_data, dict) else {}
        
        order_id = str(data.get("order_id") or data.get("orderId") or data.get("id", ""))
        
        # If we have local_order_id from request
        if not order_id and data.get("local_order_id"):
            order_id = str(data["local_order_id"])
        
        return OrderResponse(
            order_id=order_id,
            ticker=original_request.ticker,
            quantity=original_request.quantity,
            filled_quantity=0,
            side=original_request.side,
            order_type=original_request.order_type,
            status=OrderStatus.SUBMITTED,
            limit_price=original_request.limit_price,
            stop_price=original_request.stop_price,
            time_in_force=original_request.time_in_force,
            created_at=datetime.now(),
            updated_at=datetime.now(),
        )
