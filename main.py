"""FinanceMaker application entry point with professional dependency injection."""
import asyncio
import logging

from common.di_container import container
from common.models.scanner_params import ScannerParams

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


async def main() -> None:
    """Main application entry point.
    
    Demonstrates proper dependency injection usage:
    - Never use 'new' for singletons
    - Let the container manage all instances
    - Simply request what you need from the container
    """
    # Get instances from the container (created once, reused)
    # The container manages the lifecycle automatically
    http_client = container.http_client()
    earnings_scanner = container.earning_tomorrow_scanner()
    grok_client = container.grok_client()
    gemini_client = container.gemini_client()
    
    # Initialize AI scanner with all dependencies
    earning_tomorrow_ai_scanner = container.earning_tomorrow_ai_scanner()
    
    try:
        # Create scan parameters
        scan_params = ScannerParams(
            name="earning_tomorrow_ai",
            filters={},
            config={}
        )
        
        # Run scan
        logger.info("Starting AI Consensus scan for earnings stocks...")
        logger.info("Dependencies loaded: HTTP Client, Earnings Scanner, Grok, Gemini")
        tickers = await earning_tomorrow_ai_scanner.scan(scan_params)
        
        logger.info(f"Scan completed. Found {len(tickers)} tickers.")
        logger.info(f"Sample tickers: {tickers[:10]}")  # Log first 10 tickers
        
    except Exception as e:
        logger.error(f"Application error: {str(e)}", exc_info=True)
        raise
    finally:
        # Container manages cleanup of all resources
        await http_client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
