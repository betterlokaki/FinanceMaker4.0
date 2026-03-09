#!/usr/bin/env python3
"""Score earnings tickers for 10%+ breakout probability using technical data only."""
import argparse
import asyncio
import logging
import sys

from common.di_container import container
from common.helpers.earnings_breakout_technical_scorer import (
    score_tickers_for_earnings_breakout_details,
)
from common.models.scanner_params import ScannerParams

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger: logging.Logger = logging.getLogger(__name__)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Technical-only earnings breakout scorer",
    )
    parser.add_argument(
        "--tickers",
        type=str,
        default="",
        help="Comma-separated list of tickers (if omitted, uses earnings scanner)",
    )
    return parser.parse_args()


async def _get_tickers(arg_tickers: str) -> list[str]:
    if arg_tickers.strip():
        return [ticker.strip().upper() for ticker in arg_tickers.split(",") if ticker.strip()]

    scanner = container.finviz_scanner()
    params = ScannerParams(
        name="earning_breakout_technical_scoring",
        filters={},
        config={},
    )
    scanned = await scanner.scan(params)
    return [ticker.upper() for ticker in scanned]


async def main() -> None:
    args = _parse_args()
    http_client = container.http_client()
    provider = container.yahoo_market_provider()

    try:
        tickers = await _get_tickers(args.tickers)
        if not tickers:
            print("No tickers found.")
            return

        logger.info("Scoring %d tickers...", len(tickers))
        scored = await score_tickers_for_earnings_breakout_details(
            tickers=tickers,
            market_provider=provider,
        )

        print("\n" + "=" * 90)
        print("TECHNICAL EARNINGS BREAKOUT SCORES (1-100)")
        print("=" * 90)
        print(f"{'Ticker':<8} {'Score':>5} {'Daily':>5} {'Intra':>5}  Reasons")
        print("-" * 90)
        for row in scored:
            reason = ", ".join(row.reasons[:3])
            print(
                f"{row.ticker:<8} {row.score:>5} {row.daily_score:>5} "
                f"{row.intraday_score:>5}  {reason}"
            )
        print("-" * 90)

    finally:
        await http_client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
