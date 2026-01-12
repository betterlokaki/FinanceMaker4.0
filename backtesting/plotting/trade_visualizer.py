"""Trade visualization tools for backtest results."""
from datetime import datetime

import matplotlib.pyplot as plt
import pandas as pd

from backtesting.models.backtest_result import BacktestResult
from backtesting.models.trade_record import TradeRecord

try:
    import mplcursors
    HAS_MPLCURSORS = True
except ImportError:
    HAS_MPLCURSORS = False
    print("Warning: mplcursors not installed. Install with 'pip install mplcursors' for interactive tooltips.")


def plot_all_ticker_trades(result: BacktestResult) -> None:
    """Plot all ticker price charts with trade entry/exit markers in a grid layout.
    
    Creates a 6x4 grid of subplots showing:
    - Price line charts for each ticker
    - Green up-arrows (^) at entry points
    - Red down-arrows (v) at exit points
    - Interactive hover tooltips with trade details (if mplcursors installed)
    
    Args:
        result: BacktestResult containing price_data and trades.
    """
    if not result.price_data:
        print("Error: No price data available. Run backtest with store_price_data=True")
        return
    
    # Get all tickers from price data
    tickers = sorted(result.price_data.keys())
    
    if not tickers:
        print("Error: No tickers found in price_data")
        return
    
    # Create ticker->trades mapping for easy lookup
    trades_by_ticker: dict[str, list[TradeRecord]] = {}
    for trade in result.trades:
        if trade.ticker not in trades_by_ticker:
            trades_by_ticker[trade.ticker] = []
        trades_by_ticker[trade.ticker].append(trade)
    
    # Calculate grid dimensions (6 columns x 4 rows = 24 tickers)
    n_cols = 6
    n_rows = 4
    n_plots = n_cols * n_rows
    
    # Create figure with larger size to accommodate all content
    fig, axes = plt.subplots(n_rows, n_cols, figsize=(32, 20))
    fig.suptitle('Backtest Results - All Tickers with Trade Markers', 
                 fontsize=20, fontweight='bold', y=0.995)
    
    # Flatten axes array for easier iteration
    axes_flat = axes.flatten()
    
    # Plot each ticker
    for idx, ticker in enumerate(tickers[:n_plots]):
        ax = axes_flat[idx]
        price_df = result.price_data[ticker]
        ticker_trades = trades_by_ticker.get(ticker, [])
        
        # Plot price line chart (using Close price)
        if 'Close' in price_df.columns:
            price_line = ax.plot(price_df.index, price_df['Close'], 
                                 linewidth=1.5, color='#1f77b4', label='Close Price')
        else:
            ax.text(0.5, 0.5, 'No price data', ha='center', va='center', 
                   transform=ax.transAxes, fontsize=11, color='red')
            ax.set_title(ticker, fontsize=12, fontweight='bold')
            ax.axis('off')
            continue
        
        # Add trade markers
        entry_markers = []
        exit_markers = []
        
        if ticker_trades:
            for trade in ticker_trades:
                # Entry marker (green up-arrow)
                entry_marker = ax.scatter(trade.entry_date, trade.entry_price, 
                                         marker='^', s=150, c='green', 
                                         edgecolors='darkgreen', linewidths=1.5, 
                                         zorder=5, label='Entry' if not entry_markers else '')
                entry_markers.append((entry_marker, trade, 'entry'))
                
                # Exit marker (red down-arrow)
                exit_marker = ax.scatter(trade.exit_date, trade.exit_price, 
                                        marker='v', s=150, c='red', 
                                        edgecolors='darkred', linewidths=1.5, 
                                        zorder=5, label='Exit' if not exit_markers else '')
                exit_markers.append((exit_marker, trade, 'exit'))
            
            # Add interactive tooltips if mplcursors is available
            if HAS_MPLCURSORS:
                _add_interactive_tooltips(ax, entry_markers, exit_markers)
        
        # Customize subplot
        ax.set_title(f'{ticker} ({len(ticker_trades)} trades)', 
                    fontsize=12, fontweight='bold')
        ax.set_xlabel('Date', fontsize=10)
        ax.set_ylabel('Price ($)', fontsize=10)
        ax.tick_params(labelsize=9)
        ax.grid(True, alpha=0.3, linestyle='--', linewidth=0.5)
        
        # Rotate x-axis labels for better readability
        for label in ax.get_xticklabels():
            label.set_rotation(45)
            label.set_ha('right')
        
        # Show legend only if there are trades
        if ticker_trades:
            ax.legend(fontsize=9, loc='best', framealpha=0.9)
    
    # Hide unused subplots
    for idx in range(len(tickers), n_plots):
        axes_flat[idx].axis('off')
    
    # Add overall statistics text box at the top
    stats_text = _generate_stats_text(result)
    fig.text(0.5, 0.98, stats_text, fontsize=12, 
             bbox=dict(boxstyle='round', facecolor='lightyellow', alpha=0.9, pad=0.8),
             horizontalalignment='center', verticalalignment='top', 
             family='monospace', fontweight='bold')
    
    # Adjust layout to prevent overlap - leave room for top stats
    plt.tight_layout(rect=(0, 0, 1, 0.96))
    
    # Show the plot with full screen maximization
    mng = plt.get_current_fig_manager()
    try:
        # Try to maximize the window (works on most systems)
        mng.window.showMaximized()
    except AttributeError:
        # Fallback for systems where maximize doesn't work
        pass
    
    # Show the plot
    plt.show()


def _add_interactive_tooltips(ax, entry_markers: list, exit_markers: list) -> None:
    """Add interactive hover tooltips to trade markers using mplcursors.
    
    Args:
        ax: Matplotlib axis object.
        entry_markers: List of (marker, trade, 'entry') tuples.
        exit_markers: List of (marker, trade, 'exit') tuples.
    """
    if not HAS_MPLCURSORS:
        return
    
    all_markers = entry_markers + exit_markers
    
    for marker, trade, event_type in all_markers:
        cursor = mplcursors.cursor(marker, hover=True)  # type: ignore[possibly-unbound]
        
        @cursor.connect("add")
        def on_add(sel, trade=trade, event_type=event_type):
            """Generate tooltip text for trade marker."""
            if event_type == 'entry':
                tooltip = (
                    f"{trade.ticker}\n"
                    f"ENTRY: {trade.entry_date.strftime('%Y-%m-%d %H:%M')}\n"
                    f"Price: ${trade.entry_price:.2f}\n"
                    f"Shares: {trade.shares:.0f}"
                )
            else:  # exit
                tooltip = (
                    f"{trade.ticker}\n"
                    f"EXIT: {trade.exit_date.strftime('%Y-%m-%d %H:%M')}\n"
                    f"Price: ${trade.exit_price:.2f}\n"
                    f"P&L: ${trade.pnl:,.2f} ({trade.pnl_pct:+.2f}%)\n"
                    f"Reason: {trade.exit_reason}\n"
                    f"Hold: {trade.hold_days} days"
                )
            
            sel.annotation.set_text(tooltip)
            sel.annotation.get_bbox_patch().set(fc="yellow", alpha=0.9)
            sel.annotation.set_fontsize(8)


def _generate_stats_text(result: BacktestResult) -> str:
    """Generate summary statistics text for display.
    
    Args:
        result: BacktestResult object.
        
    Returns:
        Formatted statistics string.
    """
    return (
        f"Initial: ${result.initial_capital:,.2f} | Final: ${result.final_capital:,.2f} | "
        f"Return: ${result.total_return_dollars:+,.2f} ({result.total_return_pct:+.2f}%) | "
        f"Trades: {result.total_trades} | Win Rate: {result.win_rate*100:.1f}% | "
        f"Max DD: {result.max_drawdown_pct:.2f}% | Sharpe: {result.sharpe_ratio:.2f}"
    )
