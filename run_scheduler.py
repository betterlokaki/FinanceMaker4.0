import asyncio
from common.di_container import container

async def main():
    scheduler = container.trading_scheduler()
    await scheduler.start()

if __name__ == "__main__":
    asyncio.run(main())
