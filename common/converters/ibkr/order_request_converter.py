"""IBKR order request converter."""
from __future__ import annotations

from typing import Union
import time

from ibind import OrderRequest as IbkrOrderRequest

from common.models.order import OrderSide, OrderType, TimeInForce
from common.models.order_request import OrderRequest


class OrderRequestConverter:
    """Converts our OrderRequest model to IBKR OrderRequest.

    Notes:
    - For bracket orders we create three requests: parent (entry) + stop loss +
      take profit. The parent order will receive a `coid` (client id) which is
      referenced by child `parent_id` fields. This mirrors the `ibind` examples
      and IBKR WebAPI usage.
    - Take profit and parent (limit) are allowed OTH (outside regular trading
      hours). Stop loss child is explicitly set to NOT allow OTH.
    """

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
    ) -> Union[IbkrOrderRequest, list[IbkrOrderRequest]]:
        """Convert our OrderRequest to IBKR OrderRequest or a bracket set.

        Returns a single `IbkrOrderRequest` for simple orders, or a list of
        requests [parent, stop_loss, take_profit] for bracket orders.
        """
        # If bracket fields are present, build bracket orders
        if order_request.stop_loss_price is not None and order_request.take_profit_price is not None:
            return cls.to_bracket_ibkr(
                order_request=order_request,
                conid=conid,
                account_id=account_id,
                listing_exchange=listing_exchange,
                outside_rth=outside_rth,
                order_id=order_id,
                parent_id=parent_id,
            )
        coid = f"{order_request.ticker}_{conid}"
        ibkr_order = IbkrOrderRequest(
            conid=conid,
            side=cls.SIDE_MAP[order_request.side],
            quantity=order_request.quantity,
            coid=coid,
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
        # Add optional order IDs
        stopp_loss_order = None
        take_profit_order = None
        if order_request.stop_price is not None:
            stopp_loss_order = IbkrOrderRequest(
                conid=conid,
                side="SELL" if order_request.side == OrderSide.BUY else "BUY",
                quantity=order_request.quantity,
                order_type="STP",
                acct_id=account_id,
                ticker=order_request.ticker,
                listing_exchange=listing_exchange,
                outside_rth=False,
                tif="GTC",
                price=order_request.stop_price,
                aux_price=order_request.stop_price,
                parent_id=coid
            )
        if order_request.take_profit_price is not None:
            take_profit_order = IbkrOrderRequest(
                conid=conid,
                side="SELL" if order_request.side == OrderSide.BUY else "BUY",
                quantity=order_request.quantity,
                order_type="LMT",
                acct_id=account_id,
                ticker=order_request.ticker,
                listing_exchange=listing_exchange,
                outside_rth=True,
                tif="GTC",
                price=order_request.take_profit_price,
                parent_id=coid
            )
        
        return [order for order in [ibkr_order, stopp_loss_order, take_profit_order] if order is not None]

    @classmethod
    def to_bracket_ibkr(
        cls,
        order_request: OrderRequest,
        conid: int,
        account_id: str,
        listing_exchange: str = "SMART",
        outside_rth: bool = True,
        order_id: str | None = None,
        parent_id: str | None = None,
    ) -> list[IbkrOrderRequest]:
        """Build a bracket order set: parent + stop loss + take profit.

        - Parent (entry) and take-profit allow `outside_rth` (OTH) per request.
        - Stop-loss child is created with `outside_rth=False`.
        - Parent receives a `coid` client order id which children reference via
          `parent_id`.
        """
        # Ensure we have a client order id for the parent
        parent_coid = order_id or f"{order_request.ticker}-{int(time.time())}"

        # Parent order (entry). For bracket usage parent is typically a limit
        # entry; still mirror fields from the request.
        parent = IbkrOrderRequest(
            conid=conid,
            side=cls.SIDE_MAP[order_request.side],
            quantity=order_request.quantity,
            order_type=cls.ORDER_TYPE_MAP[order_request.order_type],
            acct_id=account_id,
            ticker=order_request.ticker,
            listing_exchange=listing_exchange,
            outside_rth=outside_rth,
            tif=cls.TIF_MAP[order_request.time_in_force],
            price=order_request.limit_price,
        )
        parent.coid = parent_coid

        # Stop loss child: opposite side, STP order, no OTH
        stop_loss = IbkrOrderRequest(
            conid=conid,
            side="SELL" if order_request.side == OrderSide.BUY else "BUY",
            quantity=order_request.quantity,
            order_type="STP",
            acct_id=account_id,
            ticker=order_request.ticker,
            listing_exchange=listing_exchange,
            outside_rth=False,
            tif="GTC",
            price=order_request.stop_loss_price,
            parent_id=parent_coid,
        )

        # Take profit child: opposite side, LMT order, allow OTH
        take_profit = IbkrOrderRequest(
            conid=conid,
            side="SELL" if order_request.side == OrderSide.BUY else "BUY",
            quantity=order_request.quantity,
            order_type="LMT",
            acct_id=account_id,
            ticker=order_request.ticker,
            listing_exchange=listing_exchange,
            outside_rth=True,
            tif="GTC",
            price=order_request.take_profit_price,
            parent_id=parent_coid,
        )

        return [parent, stop_loss, take_profit]
    