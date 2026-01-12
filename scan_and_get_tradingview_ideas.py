#!/usr/bin/env python3
"""Scan for tickers close to demand zones and fetch TradingView ideas with trade levels.

1. Runs zone-filtered Finviz scanner to find high-volume stocks near demand zones
2. For each ticker, fetches TradingView chart ideas
3. Extracts LineToolPriceRange objects to get entry, take profit, and stop loss levels
"""
import asyncio
import httpx
import logging
import warnings

# Suppress multiprocessing resource_tracker warnings (known macOS issue with yfinance-cache)
warnings.filterwarnings("ignore", message="resource_tracker:")

from common.models.scanner_params import ScannerParams
from common.models.idea_params import IdeaParams
from pullers.scanners.finviz.zone_filtered_scanner import ZoneFilteredScanner
from pullers.ideas.trading_view_idea_puller import TradingViewIdeaPuller

# Configure logging
logging.basicConfig(
    level=logging.WARNING,  # Only show warnings and errors to keep output clean
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    """Run scanner and fetch TradingView ideas for each ticker."""
    # High-volume stocks filter URL
    finviz_url = "https://finviz.com/screener.ashx?v=111&f=cap_midover%2Cfa_epsqoq_pos%2Cta_perf_13wdown%2Cta_sma200_pa&ft=4"
    
    print("=" * 80)
    print("DEMAND ZONE SCANNER + TRADINGVIEW IDEAS")
    print("=" * 80)
    print(f"\nStep 1: Scanning for stocks close to demand zones...")
    print("-" * 80)
    
    async with httpx.AsyncClient(timeout=30.0) as client:
        # Step 1: Run the demand zone scanner
        scanner = ZoneFilteredScanner(http_client=client, url=finviz_url)
        
        try:
            tickers = await scanner.scan(ScannerParams("close_to_demand_zone"))
            # tickers = ["A"]
            print(f"\n✅ Found {len(tickers)} tickers: {', '.join(tickers)}")
            
            if not tickers:
                print("\nNo tickers found. Exiting.")
                return
            
            # Step 2: Fetch TradingView ideas for each ticker
            print("\n" + "=" * 80)
            print(f"Step 2: Fetching TradingView ideas for {len(tickers)} tickers...")
            print("=" * 80)
            
            idea_puller = TradingViewIdeaPuller(http_client=client)
            all_ideas = []
            
            for ticker_idx, ticker in enumerate(tickers, 1):
                print(f"\n{'='*80}")
                print(f"📊 [{ticker_idx}/{len(tickers)}] {ticker}")
                print(f"{'='*80}")
                
                params = IdeaParams(ticker=ticker)
                
                try:
                    # This will print the ideas with trade levels
                    data = await idea_puller.pull_ideas(params)
                    all_ideas.extend(data)
                    # Pretty-print each SimpleIdea in the list
                    for idx, idea in enumerate(data, 1):
                        print(f"\n  💡 Idea {idx}:")
                        print(f"    Ticker: {idea.ticker}")
                        print(f"    Entry: {idea.entry_price}")
                        print(f"    Take Profit: {idea.take_profit}")
                        print(f"    Stop Loss: {idea.stop_loss}")
                except Exception as e:
                    print(f"❌ Error fetching ideas for {ticker}: {e}")
                    continue
            
            print("\n" + "=" * 80)
            print("✅ Scan complete!")
            print("=" * 80)
            
            # Reprint all collected ideas at the end
            print("\n" + "=" * 80)
            print("📋 SUMMARY: All Collected Ideas")
            print("=" * 80)
            
            if all_ideas:
                for idx, idea in enumerate(all_ideas, 1):
                    print(f"\n💡 Idea {idx}:")
                    print(f"   Ticker: {idea.ticker}")
                    print(f"   Entry: {idea.entry_price}")
                    print(f"   Take Profit: {idea.take_profit}")
                    print(f"   Stop Loss: {idea.stop_loss}")
                print(f"\n✅ Total ideas collected: {len(all_ideas)}")
            else:
                print("\nNo ideas collected.")
                
        except RuntimeError as e:
            logger.error(f"Scanner failed: {e}")
            print(f"\n✗ Error: {e}")
            raise
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            print(f"\n✗ Unexpected error: {e}")
            raise


if __name__ == "__main__":
    asyncio.run(main())
