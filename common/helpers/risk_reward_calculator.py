"""Risk/reward calculator for trading order parameters."""
from dataclasses import dataclass


@dataclass
class OrderParams:
    """Order parameters calculated from current price and trade value.
    
    Attributes:
        entry_price: Entry price (current price - 2%).
        stop_loss_price: Stop loss price (entry - 4.5%).
        take_profit_price: Take profit price (entry + 10%).
        quantity: Number of shares to trade.
    """
    entry_price: float
    stop_loss_price: float
    take_profit_price: float
    quantity: int


class RiskRewardCalculator:
    """Calculator for risk/reward order parameters."""

    @staticmethod
    def calculate_order_params(
        current_price: float, trade_value: float = 3000.0
    ) -> OrderParams:
        """Calculate order parameters from current price.
        
        Args:
            current_price: Current market price of the stock.
            trade_value: Total dollar value for the trade (default: $3000).
            
        Returns:
            OrderParams with entry, stop loss, take profit, and quantity.
        """
        entry_price: float = round(current_price * 0.98, 2)
        stop_loss_price: float = round(entry_price * 0.955, 2)
        take_profit_price: float = round(entry_price * 1.10, 2)
        quantity: int = int(trade_value / entry_price)
        
        return OrderParams(
            entry_price=entry_price,
            stop_loss_price=stop_loss_price,
            take_profit_price=take_profit_price,
            quantity=quantity,
        )
