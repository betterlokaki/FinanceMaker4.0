"""FinanceMaker application entry point with professional dependency injection."""
import asyncio
import logging
import sys
from datetime import date

from common.di_container import container
from common.models.scanner_params import ScannerParams

# Configure logging to show in terminal during debugging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

# Set third-party loggers to WARNING to reduce noise
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger: logging.Logger = logging.getLogger(__name__)


async def main() -> None:
    """Main application entry point.
    
    Demonstrates proper dependency injection usage:
    - Never use 'new' for singletons
    - Let the container manage all instances
    - Simply request what you need from the container
    
    Uses cached tickers if available for today, otherwise runs the scanner
    and caches the results.
    """
    http_client = container.http_client()
    earning_tomorrow_ai_scanner = container.earning_tomorrow_ai_scanner()
    ticker_cache = container.ticker_cache()
    today: date = date.today()
    
    try:
        # Check if we have cached tickers for today
        cached_tickers: list[str] | None = ticker_cache.load_tickers(today)
        
        if cached_tickers is not None:
            logger.info(f"✅ Using cached tickers from {today}: {cached_tickers}")
            tickers = cached_tickers
        else:
            scan_params: ScannerParams = ScannerParams(
                name="earning_tomorrow_ai",
                filters={},
                config={}
            )
            
            logger.info("Starting AI Consensus scan for earnings stocks...")
            logger.info("Dependencies loaded: HTTP Client, Earnings Scanner, Grok, Gemini")
            
            tickers = await earning_tomorrow_ai_scanner.scan(scan_params)
            
            # Cache the results for today
            ticker_cache.save_tickers(tickers, today)
            logger.info(f"💾 Cached {len(tickers)} tickers for {today}")
        
        logger.info(f"Scan completed. Found {len(tickers)} tickers.")
        logger.info(f"Consensus tickers: {tickers}")
        
    except Exception as e:
        logger.error(f"Application error: {str(e)}", exc_info=True)
        raise
    finally:
        await http_client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
