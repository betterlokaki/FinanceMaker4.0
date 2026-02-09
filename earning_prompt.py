"""FinanceMaker interactive menu system."""
import asyncio
import logging
import sys
from datetime import date

from common.di_container import container
from common.helpers.prompt_helpers import build_ticker_analysis_prompt
from common.models.scanner_params import ScannerParams
from common.settings import settings

# Configure logging to show in terminal during debugging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

# Set third-party loggers to WARNING to reduce noise
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger: logging.Logger = logging.getLogger(__name__)


async def get_prompt() -> None:
    """Get tickers from earning scanner and print the prompt without sending to AI."""
    http_client = container.http_client()
    finviz_scanner = container.finviz_scanner()
    
    try:
        logger.info("Fetching tickers from earning scanner...")
        
        scan_params: ScannerParams = ScannerParams(
            name="earning_tomorrow_ai",
            filters={},
            config={}
        )
        
        # Get tickers from base earning scanner (always fresh, no cache)
        tickers: list[str] = await finviz_scanner.scan(scan_params)
        
        if not tickers:
            logger.warning("No tickers found from earning scanner")
            print("\n⚠️  No tickers found from earning scanner.")
            return
        
        logger.info(f"Found {len(tickers)} tickers: {tickers}")
        
        # Build prompt using the configured template
        prompt: str = build_ticker_analysis_prompt(
            tickers, settings.ai_scanner.prompt_template
        )
        
        # Print the prompt
        print("\n" + "=" * 70)
        print("PROMPT")
        print("=" * 70)
        print(f"\nTickers found: {len(tickers)}")
        print(f"Tickers: {', '.join(tickers)}")
        print("\n" + "-" * 70)
        print("PROMPT (as it would be sent to AI):")
        print("-" * 70)
        print(prompt)
        print("-" * 70)
        print("=" * 70 + "\n")
        
    except Exception as e:
        logger.error(f"Error during prompt preview: {str(e)}", exc_info=True)
        print(f"\n❌ Error: {str(e)}\n")
    finally:
        await http_client.aclose()


async def run_full_ai_scanner() -> None:
    """Run the full AI scanner (existing main.py functionality)."""
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
        print(f"\n✅ Scan completed. Found {len(tickers)} tickers.")
        print(f"Consensus tickers: {tickers}\n")
        
    except Exception as e:
        logger.error(f"Application error: {str(e)}", exc_info=True)
        print(f"\n❌ Error: {str(e)}\n")
    finally:
        await http_client.aclose()


def print_menu() -> None:
    """Print the main menu options."""
    print("\n" + "=" * 70)
    print("FINANCEMAKER MENU")
    print("=" * 70)
    print("1. Get Prompt")
    print("2. Run Full AI Scanner")
    print("3. Exit")
    print("=" * 70)


async def main() -> None:
    """Main menu entry point."""
    while True:
        print_menu()
        
        try:
            choice = input("\nEnter your choice (1-3): ").strip()
            
            if choice == "1":
                await get_prompt()
            elif choice == "2":
                await run_full_ai_scanner()
            elif choice == "3":
                print("\n👋 Goodbye!\n")
                break
            else:
                print("\n❌ Invalid choice. Please enter 1, 2, or 3.\n")
                
        except KeyboardInterrupt:
            print("\n\n👋 Goodbye!\n")
            break
        except Exception as e:
            logger.error(f"Unexpected error: {str(e)}", exc_info=True)
            print(f"\n❌ Unexpected error: {str(e)}\n")


if __name__ == "__main__":
    asyncio.run(main())
