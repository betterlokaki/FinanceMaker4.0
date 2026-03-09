#!/usr/bin/env python3
"""Run technical earnings breakout scoring on a ticker list."""
import argparse
import asyncio
import logging
import sys

from common.di_container import container
from common.models.scanner_params import ScannerParams

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger: logging.Logger = logging.getLogger(__name__)
http_client = container.http_client()
finviz_scanner = container.custom_finviz_scanner(url="https://finviz.com/screener.ashx?v=411&f=earningsdate_today&ft=4")

def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run earnings breakout technical scorer")
    parser.add_argument(
        "--tickers",
        type=str,
        default="",
        help="Comma-separated ticker list (overrides default list)",
    )
    return parser.parse_args()


async def _resolve_tickers(arg_tickers: str) -> list[str]:
    if arg_tickers.strip():
        return [ticker.strip().upper() for ticker in arg_tickers.split(",") if ticker.strip()]
    return await finviz_scanner.scan(ScannerParams(name="earnings_breakout_scorer"))


async def main() -> None:
    args = _parse_args()
    tickers = await _resolve_tickers(args.tickers)

    http_client = container.http_client()
    scorer = container.earnings_breakout_technical_scorer()

    try:
        logger.info("Scoring %d tickers: %s", len(tickers), tickers)
        scored = await scorer.score_tickers(tickers=tickers)

        scores_as_strings = [item.as_output_string() for item in scored]
        print("\nScores (list[str]):")
        print(scores_as_strings)

        print("\nDetails:")
        print("=" * 90)
        print(f"{'Ticker':<8} {'Score':>5} {'Daily':>5} {'Intra':>5}  Reasons")
        print("-" * 90)
        for row in scored:
            reason = ", ".join(row.reasons[:3])
            print(
                f"{row.ticker:<8} {row.score:>5} {row.daily_score:>5} "
                f"{row.intraday_score:>5}  {reason}"
            )
        print("=" * 90)

    finally:
        await http_client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
