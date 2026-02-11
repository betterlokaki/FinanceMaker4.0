#!/usr/bin/env python3
"""Combines earning tomorrow scanner with demand zone filtering and ticker scoring.

Pipeline:
1. Fetches tickers from the earning tomorrow scanner (Finviz)
2. Filters to only tickers whose latest close is inside an active/tested demand zone
3. Scores each qualifying ticker using technical/fundamental analysis
4. Prints results sorted by score with reasoning
"""
import asyncio
import logging
import sys
import warnings
from datetime import datetime, timedelta

import pandas as pd
import yfinance as yf

from common.di_container import container
from common.helpers.zone_detection import (
    find_demand_zones_at_price,
    get_supply_demand_zones,
)
from common.models.scanner_params import ScannerParams
from ticker_logic_scoring import analyze_ticker_strategy

# Suppress noisy warnings
warnings.filterwarnings("ignore", message="resource_tracker:")
warnings.filterwarnings("ignore", category=DeprecationWarning)

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

REQUIRED_COLS: list[str] = ["Open", "High", "Low", "Close", "Volume"]
MIN_DATA_ROWS: int = 200


async def filter_tickers_by_demand_zone(tickers: list[str]) -> list[str]:
    """Filter tickers to only those whose latest close is inside a demand zone.

    Downloads 5-year daily data per ticker, computes supply/demand zones,
    and keeps tickers where the latest close falls in an active/tested demand zone.

    Args:
        tickers: Ticker symbols to check.

    Returns:
        Subset of tickers that are currently in a demand zone.
    """
    end_date = datetime.now()
    start_date = end_date - timedelta(days=365 * 5)

    filtered_tickers: list[str] = []

    for idx, ticker in enumerate(tickers, 1):
        if ticker == "N":
            continue

        try:
            logger.info(f"[{idx}/{len(tickers)}] Checking {ticker} for demand zone...")

            ticker_obj = yf.Ticker(ticker)
            df = ticker_obj.history(period="5y", interval="1d")

            if df is None or df.empty or len(df) < MIN_DATA_ROWS:
                row_count = len(df) if df is not None and not df.empty else 0
                logger.warning(
                    f"Skipping {ticker}: insufficient data "
                    f"(got {row_count} rows, need {MIN_DATA_ROWS}+)"
                )
                continue

            if not all(col in df.columns for col in REQUIRED_COLS):
                logger.warning(
                    f"Skipping {ticker}: missing required columns. "
                    f"Has: {list(df.columns)}"
                )
                continue

            for col in REQUIRED_COLS:
                df[col] = pd.to_numeric(df[col], errors="coerce")
            df = df.dropna(subset=REQUIRED_COLS)

            if df.empty:
                logger.warning(f"Skipping {ticker}: all rows are NaN")
                continue

            latest_close = float(df["Close"].iloc[-1])

            zones = get_supply_demand_zones(df)
            demand_zones = find_demand_zones_at_price(zones, latest_close)

            if demand_zones:
                filtered_tickers.append(ticker)
                logger.info(
                    f"{ticker}: IN DEMAND ZONE - close ${latest_close:.2f} in "
                    f"{len(demand_zones)} demand zone(s)"
                )
            else:
                logger.debug(f"{ticker}: no demand zones containing close")

        except Exception as e:
            logger.warning(f"Error processing {ticker}: {e}")
            continue

    logger.info(
        f"Demand zone filter complete: {len(filtered_tickers)} of "
        f"{len(tickers)} tickers passed"
    )
    return filtered_tickers


async def main() -> None:
    """Fetch earning tickers, filter by demand zone, score and print."""
    http_client = container.http_client()
    finviz_scanner = container.finviz_scanner()

    try:
        # --- Step 1: Fetch earning tickers ---
        logger.info("Fetching tickers from earning tomorrow scanner...")

        scan_params: ScannerParams = ScannerParams(
            name="earning_demand_zone_scoring",
            filters={},
            config={},
        )

        tickers: list[str] = await finviz_scanner.scan(scan_params)

        if not tickers:
            logger.warning("No tickers found from earning scanner")
            print("\nNo tickers found from earning scanner.")
            return

        logger.info(f"Found {len(tickers)} earning tickers: {tickers}")
        print(f"\nFound {len(tickers)} earning tickers. Filtering by demand zone...\n")

        # --- Step 2: Filter by demand zone ---
        # zone_tickers: list[str] = await filter_tickers_by_demand_zone(tickers)
        zone_tickers = tickers

        if not zone_tickers:
            logger.warning("No earning tickers are currently in a demand zone")
            print("No earning tickers are currently in a demand zone.")
            return

        logger.info(
            f"{len(zone_tickers)} of {len(tickers)} earning tickers "
            f"are in a demand zone: {zone_tickers}"
        )
        print(
            f"{len(zone_tickers)} of {len(tickers)} earning tickers "
            f"are in a demand zone. Scoring...\n"
        )

        # --- Step 3: Score filtered tickers ---
        results: list[dict] = []
        for ticker in zone_tickers:
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

        # --- Step 4: Print results ---
        print("=" * 80)
        print(
            f"  EARNING TICKERS IN DEMAND ZONE - SCORED "
            f"({len(zone_tickers)}/{len(tickers)} in zone, "
            f"{len(results)} scored)"
        )
        print("=" * 80)
        print(f"  {'Ticker':<10} {'Score':>5}   {'Reason'}")
        print("-" * 80)

        for r in results:
            print(f"  {r['Ticker']:<10} {r['Score']:>5}   {r['Reason']}")

        print("-" * 80)

        if results:
            top = results[0]
            print(f"\n  Top pick: {top['Ticker']} (Score: {top['Score']})")
            print(f"  Why: {top['Reason']}")

        print("=" * 80 + "\n")

    except Exception as e:
        logger.error(f"Error during earning demand zone scoring: {e}", exc_info=True)
        print(f"\nError: {e}\n")
    finally:
        await http_client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
