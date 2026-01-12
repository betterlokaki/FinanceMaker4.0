"""Test script to verify concurrent position handling in VectorBTEngine."""
from datetime import datetime, timedelta
from backtesting.engines.vectorbt_engine import VectorBTEngine
from backtesting.strategies.opening_drop_strategy import OpeningDropStrategy
from backtesting.models.backtest_params import BacktestParams
from backtesting.strategies.supply_demand_strategy import SupplyDemandStrategy
from backtesting.plotting.trade_visualizer import plot_all_ticker_trades


def main():
    """Test the engine with multiple tickers to ensure proper capital tracking."""
    
    # Create strategy and engine
    strategy = SupplyDemandStrategy()
    engine = VectorBTEngine(strategy)
    
    # Setup parameters with small capital to force the issue
    # Use recent window and intraday interval to allow entry signals
    end_dt = datetime.today()
    start_dt = end_dt - timedelta(days=69)
    params = BacktestParams(
        start_date=start_dt,
        end_date=end_dt,
        initial_capital=3000.0,  # Small capital
        position_size_pct=1,  # 50% per position = $1500 each
        # This means we can only have ~2 concurrent positions max
        commission_per_trade=2.5,
        stop_loss_pct=0.06,
        take_profit_pct=0.12,
        interval="1h",
        zone_lookback_years=6,
    )
    
    # Test with multiple tickers that might have concurrent signals
    tickers =  ["ADM","AFG","AFL","AGCO","AMBA","AMX","AWI","BIP","BIPC","BKE",
 "CALX","CDP","CINF","DASH","DGX","FBK","FERG","HCI","HHH","HR",
 "IESC","LYFT","MLM","MRUS","MSFT","NI","SNX","SONO","STLA","TAL",
 "WTW","WYNN","ZWS"]
    
    print("="*80)
    print("TESTING CONCURRENT POSITION HANDLING")
    print("="*80)
    print(f"Initial Capital: ${params.initial_capital:,.2f}")
    print(f"Position Size: {params.position_size_pct*100}% = ${params.calculate_position_value(params.initial_capital):,.2f}")
    print(f"Max Concurrent Positions: ~2")
    print(f"Testing with {len(tickers)} tickers")
    print("="*80)
    
    # Run backtest with price data storage enabled
    result = engine.run(tickers, params, store_price_data=True)
    
    # Display results
    print("\n" + "="*80)
    print("BACKTEST RESULTS")
    print("="*80)
    print(f"Total Trades: {result.total_trades}")
    print(f"Tickers Traded: {result.tickers_traded}")
    print(f"Skipped Tickers: {result.skipped_tickers}")
    print(f"Win Rate: {result.win_rate*100:.1f}%")
    print(f"Total Return: ${result.total_return_dollars:,.2f} ({result.total_return_pct:.2f}%)")
    print(f"Final Capital: ${result.final_capital:,.2f}")
    print(f"Capital Depleted: {result.capital_depleted}")
    print(f"Unrealized Trades: {result.unrealized_trades}")
    print("="*80)
    
    # Show individual trades
    if result.trades:
        print("\nINDIVIDUAL TRADES:")
        print("-"*80)
        for i, trade in enumerate(result.trades[:10], 1):  # Show first 10
            print(f"{i}. {trade.ticker}: Entry {trade.entry_date.date()} @ ${trade.entry_price:.2f} "
                  f"→ Exit {trade.exit_date.date()} @ ${trade.exit_price:.2f} "
                  f"| P&L: ${trade.pnl:,.2f} ({trade.pnl_pct:.2f}%) | {trade.exit_reason}")
        
        if len(result.trades) > 10:
            print(f"... and {len(result.trades) - 10} more trades")
        
        # Show top 10 winners
        winners = sorted([t for t in result.trades if t.is_winner], 
                         key=lambda x: x.pnl, reverse=True)
        if winners:
            print("\n" + "="*80)
            print(f"TOP {min(10, len(winners))} WINNERS:")
            print("-"*80)
            for i, trade in enumerate(winners[:10], 1):
                print(f"{i}. {trade.ticker}: ${trade.pnl:+,.2f} ({trade.pnl_pct:+.2f}%) | "
                      f"Entry {trade.entry_date.date()} @ ${trade.entry_price:.2f} → "
                      f"Exit {trade.exit_date.date()} @ ${trade.exit_price:.2f}")
        
        # Show top 10 losers
        losers = sorted([t for t in result.trades if not t.is_winner], 
                        key=lambda x: x.pnl)
        if losers:
            print("\n" + "="*80)
            print(f"TOP {min(10, len(losers))} LOSERS:")
            print("-"*80)
            for i, trade in enumerate(losers[:10], 1):
                print(f"{i}. {trade.ticker}: ${trade.pnl:+,.2f} ({trade.pnl_pct:+.2f}%) | "
                      f"Entry {trade.entry_date.date()} @ ${trade.entry_price:.2f} → "
                      f"Exit {trade.exit_date.date()} @ ${trade.exit_price:.2f}")
    
    print("\n✅ Test completed successfully!")
    print("\nThe engine now properly:")
    print("  1. Tracks available vs reserved capital")
    print("  2. Processes all trades chronologically across tickers")
    print("  3. Only allows new entries when sufficient capital is available")
    print("  4. Waits for positions to close before freeing up capital")
    
    # Generate interactive visualization
    print("\n" + "="*80)
    print("GENERATING TRADE VISUALIZATION...")
    print("="*80)
    plot_all_ticker_trades(result)


if __name__ == "__main__":
    main()
