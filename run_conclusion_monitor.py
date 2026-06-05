"""Local CLI for generating daily trading conclusion reports."""

from __future__ import annotations

import argparse
import asyncio
from datetime import date, datetime
import logging
import sys

from common.di_container import container
from conclusion_monitor import ConclusionMonitor
from conclusion_monitor.monitor import DEFAULT_ACCOUNT_START_DATE, DEFAULT_INITIAL_CAPITAL
from conclusion_monitor.order_history import BrokerOrderHistoryProvider
from conclusion_monitor.time_windows import NY_TZ

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

logger = logging.getLogger(__name__)


async def main() -> None:
    """Run the CLI or direct date command."""
    args = _parse_args()
    if args.date:
        await _generate_for_day(_parse_date(args.date))
        return
    if args.start_date:
        start_date = _parse_date(args.start_date)
        end_date = _parse_date(args.end_date) if args.end_date else datetime.now(NY_TZ).date()
        await _generate_for_range(
            start_date=start_date,
            end_date=end_date,
            account_start_date=_parse_date(args.account_start_date),
            initial_capital=args.initial_capital,
        )
        return

    while True:
        print("\n" + "=" * 70)
        print("LOCAL DAILY TRADING CONCLUSION MONITOR")
        print("=" * 70)
        print("1. Generate today's report")
        print("2. Generate report for a specific YYYY-MM-DD")
        print("3. Generate report for a date range")
        print("4. Exit")
        print("=" * 70)
        choice = input("Choose an option: ").strip()
        if choice == "1":
            await _generate_for_day(datetime.now(NY_TZ).date())
            return
        if choice == "2":
            raw_date = input("Trading day (YYYY-MM-DD or DD/MM/YYYY): ").strip()
            await _generate_for_day(_parse_date(raw_date))
            return
        if choice == "3":
            raw_start = input("Start date (YYYY-MM-DD or DD/MM/YYYY): ").strip()
            raw_end = input("End date (blank = today): ").strip()
            await _generate_for_range(
                start_date=_parse_date(raw_start),
                end_date=_parse_date(raw_end) if raw_end else datetime.now(NY_TZ).date(),
                account_start_date=DEFAULT_ACCOUNT_START_DATE,
                initial_capital=DEFAULT_INITIAL_CAPITAL,
            )
            return
        if choice == "4":
            return
        print("Unknown option.")


async def _generate_for_day(trading_day: date) -> None:
    broker = container.live_broker()
    monitor = ConclusionMonitor(
        broker=broker,
        order_history_provider=BrokerOrderHistoryProvider(broker),
        market_provider=container.yahoo_market_provider(),
        ai_clients={
            "grok": container.grok_client(),
            "gemini": container.gemini_client(),
        },
    )

    try:
        output_path = await monitor.generate(trading_day)
        logger.info("Conclusion report written to %s", output_path)
    finally:
        await container.http_client().aclose()


async def _generate_for_range(
    start_date: date,
    end_date: date,
    account_start_date: date,
    initial_capital: float,
) -> None:
    broker = container.live_broker()
    monitor = ConclusionMonitor(
        broker=broker,
        order_history_provider=BrokerOrderHistoryProvider(broker),
        market_provider=container.yahoo_market_provider(),
        ai_clients={
            "grok": container.grok_client(),
            "gemini": container.gemini_client(),
        },
    )

    try:
        output_path = await monitor.generate_range(
            start_date=start_date,
            end_date=end_date,
            account_start_date=account_start_date,
            initial_capital=initial_capital,
        )
        logger.info("Conclusion range report written to %s", output_path)
    finally:
        await container.http_client().aclose()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a local trading conclusion JSON.")
    parser.add_argument(
        "--date",
        help="Trading day to report, in YYYY-MM-DD format. If omitted, show the menu.",
    )
    parser.add_argument(
        "--start-date",
        help="Range start date in YYYY-MM-DD or DD/MM/YYYY format.",
    )
    parser.add_argument(
        "--end-date",
        help="Range end date in YYYY-MM-DD or DD/MM/YYYY format. Defaults to today if --start-date is set.",
    )
    parser.add_argument(
        "--account-start-date",
        default=DEFAULT_ACCOUNT_START_DATE.isoformat(),
        help="Baseline account start date. Default: 2026-05-08.",
    )
    parser.add_argument(
        "--initial-capital",
        type=float,
        default=DEFAULT_INITIAL_CAPITAL,
        help="Baseline starting capital in USD. Default: 100000.",
    )
    return parser.parse_args()


def _parse_date(raw_value: str) -> date:
    value = raw_value.strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"Invalid date: {raw_value}. Use YYYY-MM-DD or DD/MM/YYYY.")


if __name__ == "__main__":
    asyncio.run(main())
