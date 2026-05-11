"""Quick script: fetch IBKR P/L summary for today via DI container."""

import asyncio
from datetime import date

from common.di_container import container
from publishers.interactive_brokers.interactive_webapi_broker import InteractiveWebapiBroker


async def main() -> None:
    broker: InteractiveWebapiBroker = container.ibkr_broker()
    summary = await broker._get_relized_money(start_date=date.fromisoformat("2024-07-31"), end_date=date.fromisoformat("2026-05-10"))
    print(summary)


if __name__ == "__main__":
    asyncio.run(main())
