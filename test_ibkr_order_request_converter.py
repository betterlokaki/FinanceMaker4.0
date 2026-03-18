"""Unit tests for IBKR OrderRequest converter stop/bracket behavior."""
import time
import unittest

from common.converters.ibkr.order_request_converter import OrderRequestConverter
from common.models.order import OrderSide, OrderType, TimeInForce
from common.models.order_request import OrderRequest


class OrderRequestConverterTests(unittest.TestCase):
    def test_stop_price_plus_take_profit_routes_to_bracket_with_stp(self) -> None:
        request = OrderRequest(
            ticker="AAPL",
            quantity=10,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            limit_price=100.0,
            stop_price=97.0,
            take_profit_price=106.0,
            time_in_force=TimeInForce.DAY,
        )

        orders = OrderRequestConverter.to_ibkr(
            order_request=request,
            conid=265598,
            account_id="DU123",
            outside_rth=False,
        )
        self.assertIsInstance(orders, list)
        assert isinstance(orders, list)

        self.assertEqual(len(orders), 3)
        parent, stop_loss, take_profit = orders
        self.assertEqual(parent.order_type, "LMT")
        self.assertEqual(stop_loss.order_type, "STP")
        self.assertEqual(stop_loss.aux_price, 97.0)
        self.assertEqual(stop_loss.side, "SELL")
        self.assertEqual(take_profit.order_type, "LMT")
        self.assertEqual(take_profit.price, 106.0)

    def test_stop_loss_price_is_honored_for_fixed_bracket_stops(self) -> None:
        request = OrderRequest(
            ticker="AAPL",
            quantity=10,
            side=OrderSide.SELL,
            order_type=OrderType.LIMIT,
            limit_price=100.0,
            stop_loss_price=103.0,
            take_profit_price=94.0,
            time_in_force=TimeInForce.DAY,
        )

        orders = OrderRequestConverter.to_ibkr(
            order_request=request,
            conid=265598,
            account_id="DU123",
            outside_rth=False,
        )
        self.assertIsInstance(orders, list)
        assert isinstance(orders, list)

        _, stop_loss, _ = orders
        self.assertEqual(stop_loss.order_type, "STP")
        self.assertEqual(stop_loss.side, "BUY")
        self.assertEqual(stop_loss.aux_price, 103.0)

    def test_simple_orders_get_unique_generated_coid(self) -> None:
        request = OrderRequest(
            ticker="AAPL",
            quantity=1,
            side=OrderSide.BUY,
            order_type=OrderType.LIMIT,
            limit_price=100.0,
            time_in_force=TimeInForce.DAY,
        )

        first = OrderRequestConverter.to_ibkr(
            order_request=request,
            conid=265598,
            account_id="DU123",
            outside_rth=False,
        )
        time.sleep(0.002)
        second = OrderRequestConverter.to_ibkr(
            order_request=request,
            conid=265598,
            account_id="DU123",
            outside_rth=False,
        )
        self.assertIsInstance(first, list)
        self.assertIsInstance(second, list)
        assert isinstance(first, list) and isinstance(second, list)
        self.assertNotEqual(first[0].coid, second[0].coid)


if __name__ == "__main__":
    unittest.main()
