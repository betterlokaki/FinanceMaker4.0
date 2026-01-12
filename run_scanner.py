#!/usr/bin/env python3
"""Launch script to run demand zone scanner and print results."""
import asyncio
import logging
import sys

from common.di_container import container
from common.models.scanner_params import ScannerParams

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

# Set third-party loggers to WARNING to reduce noise
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger: logging.Logger = logging.getLogger(__name__)


async def main() -> None:
    """Run demand zone scanner and print results."""
    logger.info("🔍 Starting Demand Zone Scanner...")
    
    try:
        # Get the demand zone scanner from DI container
        scanner = container.demand_zone_scanner()
        
        # Create scanner params
        params = ScannerParams("demand_zone_scanner")
        
        logger.info("📊 Scanning Finviz for stocks close to demand zones...")
        logger.info("This may take a few minutes as it processes tickers...")
        
        # Run the scanner
        tickers = await scanner.scan(params)
        
        # Print results
        print("\n" + "=" * 70)
        print(f"SCANNER RESULTS: {len(tickers)} tickers found")
        print("=" * 70)
        
        if tickers:
            print("\nTickers (comma-separated):")
            print(",".join(tickers))
            print("\nTickers (one per line):")
            for ticker in tickers:
                print(f"  - {ticker}")
        else:
            print("\n⚠️  No tickers found matching the criteria.")
        
        print("\n" + "=" * 70)
        logger.info(f"✅ Scan complete: {len(tickers)} tickers found")
        
    except Exception as e:
        logger.error(f"❌ Error running scanner: {e}", exc_info=True)
        raise
    finally:
        # Cleanup
        http_client = container.http_client()
        await http_client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
