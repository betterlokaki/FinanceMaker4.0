#!/usr/bin/env python3
"""Find stocks close to demand zones using zone-filtered Finviz scanner.

Scans Finviz for high-volume stocks, then filters by 5-year demand zone presence.
Prints all tickers where the latest close price is within an active/tested demand zone.
"""
import asyncio
import httpx
import logging
import warnings

# Suppress multiprocessing resource_tracker warnings (known macOS issue with yfinance-cache)
warnings.filterwarnings("ignore", message="resource_tracker:")

from common.models.scanner_params import ScannerParams
from pullers.scanners.finviz.zone_filtered_scanner import ZoneFilteredScanner

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)
"""
",

"stop Loss": "$225.00",

"take profit": "$265.00",

"entry zones": "$235.00 - $240.00"

},

{

"Ticker": "NBIX",

"Score": "88",

"why": "Rare technical pivot bottom and healthcare conference presentation create perfect conditions for a violent mean reversion.",

"stop Loss": "$128.00",

"take profit": "$150.00",

"entry zones": "$134.00 - $136.00"

},

{

"Ticker": "BEP",

"Score": "85",

"why": "Strong buy rating and multi-billion dollar energy pact position this clean-tech play for significant recovery month.",

"stop Loss": "$24.12",

"take profit": "$33.00",

"entry zones": "$25.50 - $27.00"

},

{

"Ticker": "SNOW",

"Score": "82",

"why": "Oversold RSI conditions and successful transition to AI platform narrative support a sharp bounce from levels.",

"stop Loss": "$195.00",

"take profit": "$250.00",

"entry zones": "$210.00 - $220.00"

},

{

"Ticker": "PRVA",

"Score": "80",

"why": "Healthcare services name currently testing major volume support suggests institutional accumulation and high probability of explosion.",

"stop Loss": "$21.80",

"take profit": "$26.00",

"entry zones": "$22.80 - $23.20"

},

{

"Ticker": "LYFT",

"Score": "78",

"why": "Outperforming industry peers while consolidating near 200-day support makes this transport stock a prime bullish breakout.",

"stop Loss": "$18.50",

"take profit": "$22.00",

"entry zones": "$19.20 - $19.80"

}
]
"""

async def main() -> None:
    """Run zone-filtered scanner and print results."""
    # High-volume stocks filter URL
    finviz_url = "https://finviz.com/screener.ashx?v=111&f=cap_midover%2Cfa_epsqoq_pos%2Cta_perf_13wdown%2Cta_sma200_pa&ft=4"
    
    print("=" * 70)
    print("CLOSE TO DEMAND ZONE TICKER SCANNER")
    print("=" * 70)
    print(f"\nFilter: High volume stocks (avg vol > 2M)")
    print(f"URL: {finviz_url}")
    print("\nScanning for stocks with latest close in 5-year demand zones...")
    print("-" * 70)
    
    async with httpx.AsyncClient() as client:
        scanner = ZoneFilteredScanner(http_client=client, url=finviz_url)
        
        try:
            # Run the scanner
            tickers = await scanner.scan(ScannerParams("close_to_demand_zone"))
            
            # Print results
            print()
            print("=" * 70)
            print(f"RESULTS: {len(tickers)} tickers found with close in demand zone")
            print("=" * 70)
            
            if tickers:
                # Print in columns for readability
                cols = 5
                for i, ticker in enumerate(tickers):
                    print(f"{ticker:>8}", end="")
                    if (i + 1) % cols == 0:
                        print()
                if len(tickers) % cols != 0:
                    print()
                
                print("\n" + "-" * 70)
                print(f"Total: {len(tickers)} tickers")
                print("\nTickers (comma-separated):")
                print(",".join(tickers))
            else:
                print("\nNo tickers found matching the criteria.")
                
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
