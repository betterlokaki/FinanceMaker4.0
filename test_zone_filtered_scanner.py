#!/usr/bin/env python3
"""Quick test of ZoneFilteredScanner implementation."""
import asyncio
import httpx

from common.models.scanner_params import ScannerParams
from pullers.scanners.finviz.zone_filtered_scanner import ZoneFilteredScanner


async def main() -> None:
    """Test zone-filtered scanner with a custom URL."""
    # Test URL: high volume stocks
    test_url = "https://finviz.com/screener.ashx?v=111&f=sh_avgvol_o2000&ft=4"
    
    print("=" * 60)
    print("Zone-Filtered Scanner Test")
    print("=" * 60)
    print(f"Using URL: {test_url}")
    print()
    
    async with httpx.AsyncClient() as client:
        scanner = ZoneFilteredScanner(http_client=client, url=test_url)
        print("Scanner initialized successfully")
        print()
        
        print("Starting scan (this may take a minute)...")
        try:
            tickers = await scanner.scan(ScannerParams("test"))
            print()
            print(f"✓ Scan completed successfully!")
            print(f"Found {len(tickers)} tickers with latest close in demand zone:")
            print(f"  {tickers[:10]}" + (" ..." if len(tickers) > 10 else ""))
        except Exception as e:
            print(f"✗ Scan failed: {e}")
            raise


if __name__ == "__main__":
    asyncio.run(main())
