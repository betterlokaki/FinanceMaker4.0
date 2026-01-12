"""Backtest result model for aggregated performance metrics."""
from dataclasses import dataclass, field

import pandas as pd

from backtesting.models.trade_record import TradeRecord


@dataclass(frozen=True)
class BacktestResult:
    """Immutable aggregated backtest performance results.
    
    Attributes:
        total_return_pct: Total return as percentage.
        total_return_dollars: Total return in dollars.
        win_rate: Percentage of winning trades (0.0-1.0).
        total_trades: Total number of completed trades.
        winning_trades: Number of profitable trades.
        losing_trades: Number of unprofitable trades.
        avg_win_pct: Average winning trade percentage.
        avg_loss_pct: Average losing trade percentage.
        max_drawdown_pct: Maximum drawdown as percentage.
        sharpe_ratio: Risk-adjusted return (annualized).
        profit_factor: Gross profit / Gross loss ratio.
        initial_capital: Starting capital.
        final_capital: Ending capital.
        trades: List of individual trade records.
        tickers_traded: Number of unique tickers traded.
        skipped_tickers: Number of tickers skipped (no valid entries).
        capital_depleted: True if strategy ran out of capital.
        unrealized_trades: Number of positions still open at end of data.
        unrealized_pnl: P&L from unrealized positions.
        price_data: Optional dict of ticker -> OHLCV DataFrame for visualization.
    """
    
    total_return_pct: float
    total_return_dollars: float
    win_rate: float
    total_trades: int
    winning_trades: int
    losing_trades: int
    avg_win_pct: float
    avg_loss_pct: float
    max_drawdown_pct: float
    sharpe_ratio: float
    profit_factor: float
    initial_capital: float
    final_capital: float
    trades: tuple[TradeRecord, ...] = field(default_factory=tuple)
    tickers_traded: int = 0
    skipped_tickers: int = 0
    capital_depleted: bool = False
    unrealized_trades: int = 0
    unrealized_pnl: float = 0.0
    price_data: dict[str, pd.DataFrame] = field(default_factory=dict)
    
    def summary(self) -> str:
        """Generate a human-readable summary of results."""
        summary_str = (
            f"\n{'='*60}\n"
            f"BACKTEST RESULTS SUMMARY\n"
            f"{'='*60}\n"
        )
        
        if self.capital_depleted:
            summary_str += (
                f"⚠️  CAPITAL DEPLETED - Strategy terminated early!\n"
                f"{'='*60}\n"
            )
        
        summary_str += (
            f"Initial Capital:     ${self.initial_capital:,.2f}\n"
            f"Final Capital:       ${self.final_capital:,.2f}\n"
            f"Total Return:        {self.total_return_pct:+.2f}% "
            f"(${self.total_return_dollars:+,.2f})\n"
            f"{'='*60}\n"
            f"Total Trades:        {self.total_trades}\n"
            f"Winning Trades:      {self.winning_trades}\n"
            f"Losing Trades:       {self.losing_trades}\n"
            f"Win Rate:            {self.win_rate*100:.1f}%\n"
        )
        
        if self.unrealized_trades > 0:
            summary_str += (
                f"Unrealized Trades:   {self.unrealized_trades} "
                f"(${self.unrealized_pnl:+,.2f})\n"
            )
        
        summary_str += (
            f"{'='*60}\n"
            f"Avg Win:             {self.avg_win_pct:+.2f}%\n"
            f"Avg Loss:            {self.avg_loss_pct:+.2f}%\n"
            f"Profit Factor:       {self.profit_factor:.2f}\n"
            f"Max Drawdown:        {self.max_drawdown_pct:.2f}%\n"
            f"Sharpe Ratio:        {self.sharpe_ratio:.2f}\n"
            f"{'='*60}\n"
            f"Tickers Traded:      {self.tickers_traded}\n"
            f"Tickers Skipped:     {self.skipped_tickers}\n"
            f"{'='*60}\n"
        )
        
        return summary_str
