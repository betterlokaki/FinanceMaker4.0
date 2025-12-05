"""IBKR portfolio converter."""
from typing import Any

from common.models.portfolio import Portfolio
from common.models.position import Position


class PortfolioConverter:
    """Converts IBKR portfolio data to our Portfolio model."""

    @classmethod
    def from_ibkr_positions(
        cls,
        positions_data: list[dict[str, Any]],
        ledger_data: dict[str, Any] | None = None,
    ) -> Portfolio:
        """Convert IBKR positions and ledger data to our Portfolio.
        
        Args:
            positions_data: List of position dictionaries from IBKR positions().
            ledger_data: Optional ledger data from get_ledger() - dict keyed by currency.
            
        Returns:
            Our Portfolio model.
        """
        positions = [
            cls._convert_position(pos_data) 
            for pos_data in positions_data
        ]
        
        # Extract ledger values - ledger is keyed by currency, use BASE or USD
        ledger = ledger_data or {}
        base_ledger = ledger.get("BASE", ledger.get("USD", {}))
        
        # Field names from get_ledger() are lowercase
        return Portfolio(
            positions=positions,
            cash_balance=float(base_ledger.get("cashbalance", 0) or 0),
            total_market_value=float(base_ledger.get("netliquidationvalue", 0) or 0),
            total_equity=float(base_ledger.get("netliquidationvalue", 0) or 0),
            buying_power=float(base_ledger.get("settledcash", 0) or 0),
            unrealized_pnl=float(base_ledger.get("unrealizedpnl", 0) or sum(p.unrealized_pnl or 0 for p in positions)),
            realized_pnl=float(base_ledger.get("realizedpnl", 0) or sum(p.realized_pnl or 0 for p in positions)),
        )

    @classmethod
    def _convert_position(cls, pos_data: dict[str, Any]) -> Position:
        """Convert a single IBKR position to our Position model.
        
        Args:
            pos_data: Position dictionary from IBKR positions() endpoint.
            
        Returns:
            Our Position model.
        """
        # Extract ticker symbol
        # positions() returns: ticker, contractDesc
        ticker = (
            pos_data.get("ticker") 
            or pos_data.get("symbol") 
            or pos_data.get("contractDesc", "").split(" ")[0]
            or ""
        )
        
        # Extract position quantity
        # positions() uses "position" field
        quantity = int(float(pos_data.get("position", 0) or pos_data.get("size", 0) or 0))
        
        # Extract prices
        # positions() uses avgCost, avgPrice, mktPrice, mktValue
        avg_cost = float(pos_data.get("avgCost", 0) or pos_data.get("avgPrice", 0) or 0)
        current_price = float(pos_data.get("mktPrice", 0) or pos_data.get("lastPrice", 0) or 0)
        market_value = float(pos_data.get("mktValue", 0) or pos_data.get("marketValue", 0) or 0)
        
        # Extract P&L values
        # positions() uses unrealizedPnl, realizedPnl (camelCase)
        unrealized_pnl = float(pos_data.get("unrealizedPnl", 0) or pos_data.get("unrealPnL", 0) or 0)
        realized_pnl = float(pos_data.get("realizedPnl", 0) or pos_data.get("realPnL", 0) or 0)
        
        return Position(
            ticker=ticker,
            quantity=quantity,
            average_cost=avg_cost,
            current_price=current_price if current_price > 0 else None,
            market_value=market_value if market_value != 0 else None,
            unrealized_pnl=unrealized_pnl if unrealized_pnl != 0 else None,
            realized_pnl=realized_pnl if realized_pnl != 0 else None,
        )
