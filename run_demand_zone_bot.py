"""Main entry point for Demand Zone Trading Bot."""
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
    """Main entry point for Demand Zone Trading Bot.
    
    Runs the demand zone scheduler which executes the strategy
    immediately on startup, then every 2 hours during market hours (4 AM - 8 PM EST).
    Exits when market hours close.
    """
    logger.info("🚀 Starting Demand Zone Trading Bot...")
    
    try:
        # Get the demand zone scheduler from DI container
        scheduler = container.demand_zone_scheduler()
        
        logger.info("📅 Scheduler initialized. Will scan immediately, then every 2 hours during market hours.")
        logger.info("Press Ctrl+C to stop the bot.")
        
        # Start the scheduler (runs until market hours close)
        await scheduler.start()
        
    except KeyboardInterrupt:
        logger.info("🛑 Received interrupt signal, shutting down...")
        scheduler = container.demand_zone_scheduler()
        await scheduler.stop()
    except Exception as e:
        logger.error(f"❌ Fatal error: {e}", exc_info=True)
        raise
    finally:
        # Cleanup
        http_client = container.http_client()
        await http_client.aclose()
        logger.info("✅ Bot shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
