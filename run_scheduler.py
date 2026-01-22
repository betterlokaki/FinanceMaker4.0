"""Main entry point for Trading Scheduler with Multiple Strategies."""
import asyncio
import logging
import sys

from common.di_container import container

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

# Set third-party loggers to WARNING to reduce noise
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("websockets").setLevel(logging.WARNING)

logger: logging.Logger = logging.getLogger(__name__)


async def main() -> None:
    """Main entry point for Trading Scheduler.
    
    Runs multiple trading strategies (earning_strategy and demand_zone_strategy)
    during market hours. The scheduler:
    1. Waits for pre-market open (4:00 AM EST)
    2. Starts all strategies
    3. Runs until after-hours close (8:00 PM EST)
    4. Stops all strategies
    5. Repeats next market day
    """
    logger.info("🚀 Starting Trading Scheduler with Multiple Strategies...")
    
    try:
        # Get the trading scheduler from DI container
        scheduler = container.trading_scheduler()
        
        logger.info("📅 Scheduler initialized. Will start strategies at pre-market open (4:00 AM EST).")
        logger.info("Press Ctrl+C to stop the scheduler.")
        
        # Start the scheduler (runs continuously)
        await scheduler.start()
        
    except KeyboardInterrupt:
        logger.info("🛑 Received interrupt signal, shutting down...")
        scheduler = container.trading_scheduler()
        await scheduler.stop()
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}", exc_info=True)
        raise
    finally:
        # Cleanup
        http_client = container.http_client()
        await http_client.aclose()
        logger.info("✅ Scheduler shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
