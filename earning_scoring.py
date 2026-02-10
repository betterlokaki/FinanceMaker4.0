#!/usr/bin/env python3
"""Combines earning tomorrow scanner with ticker scoring logic.

Fetches tickers from the earning tomorrow scanner (Finviz),
then scores each ticker using technical/fundamental analysis,
and prints the results sorted by score.
"""
import asyncio
import logging
import sys

from common.di_container import container
from common.models.scanner_params import ScannerParams
from ticker_logic_scoring import analyze_ticker_strategy

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

# Set third-party loggers to WARNING to reduce noise
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("yfinance").setLevel(logging.WARNING)
logging.getLogger("peewee").setLevel(logging.WARNING)

logger: logging.Logger = logging.getLogger(__name__)


async def main() -> None:
    """Fetch earning tickers and score them."""
    http_client = container.http_client()
    finviz_scanner = container.finviz_scanner()

    try:
        logger.info("Fetching tickers from earning tomorrow scanner...")

        scan_params: ScannerParams = ScannerParams(
            name="earning_tomorrow_scoring",
            filters={},
            config={},
        )

        # Get tickers from earning tomorrow scanner
        tickers: list[str] = await finviz_scanner.scan(scan_params)

        if not tickers:
            logger.warning("No tickers found from earning scanner")
            print("\nNo tickers found from earning scanner.")
            return

        logger.info(f"Found {len(tickers)} tickers: {tickers}")
        print(f"\nFound {len(tickers)} tickers from earning scanner. Scoring...\n")

        # Score each ticker
        results: list[dict] = []
        for ticker in tickers:
            try:
                data = analyze_ticker_strategy(ticker)
                if data and "error" not in data:
                    results.append(data)
                else:
                    error_msg = data.get("error", "Unknown error") if data else "No data"
                    logger.warning(f"Skipping {ticker}: {error_msg}")
            except Exception as e:
                logger.warning(f"Error scoring {ticker}: {e}")

        # Sort by score descending
        results.sort(key=lambda x: x["Score"], reverse=True)

        # Print results
        print("=" * 70)
        print(f"  EARNING TOMORROW TICKERS - SCORED ({len(results)}/{len(tickers)} scored)")
        print("=" * 70)
        print(f"  {'Ticker':<10} {'Score':>5}   {'Reason'}")
        print("-" * 70)

        for r in results:
            print(f"  {r['Ticker']:<10} {r['Score']:>5}   {r['Reason']}")

        print("-" * 70)

        if results:
            top = results[0]
            print(f"\n  Top pick: {top['Ticker']} (Score: {top['Score']})")

        print("=" * 70 + "\n")

    except Exception as e:
        logger.error(f"Error during earning scoring: {e}", exc_info=True)
        print(f"\nError: {e}\n")
    finally:
        await http_client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
