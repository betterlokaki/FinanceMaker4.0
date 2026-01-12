"""Breakout swing backtest engine for large cap stocks."""
from backtesting.engines.vectorbt_engine import VectorBTEngine
from backtesting.models.backtest_params import BacktestParams
from backtesting.models.trade_record import TradeRecord


class BreakoutSwingEngine(VectorBTEngine):
    """Backtest engine specialized for breakout swing strategy.
    
    Extends VectorBTEngine to handle daily candles for swing trading
    on large cap stocks.
    """
    
    def __init__(self, strategy) -> None:
        """Initialize engine with breakout swing strategy.
        
        Args:
            strategy: BreakoutSwingStrategy instance.
        """
        super().__init__(strategy)
    
    def run_single(
        self,
        ticker: str,
        params: BacktestParams,
        position_value: float | None = None,
    ) -> list[TradeRecord]:
        """Run backtest on a single ticker.
        
        Args:
            ticker: Stock ticker symbol.
            params: Backtest parameters.
            position_value: Fixed position value per trade.
            
        Returns:
            List of TradeRecord objects.
        """
        if position_value is None:
            position_value = params.calculate_position_value(params.initial_capital)
        
        # Fetch data (zone_df includes extended history for resistance detection)
        zone_df, df = self._fetch_data(ticker, params)
        
        if df.empty:
            return []
        
        # Generate signals using extended data for zone detection
        signals_df = self._strategy.generate_signals(
            df.copy(),
            params,
            zone_df=zone_df,
        )
        
        # Simulate trades
        trades = self._simulate_trades(
            ticker,
            signals_df,
            params,
            position_value,
        )
        
        return trades
