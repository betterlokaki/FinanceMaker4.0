"""Test script to run demand zone scanner + AI analyzer and print results."""
import asyncio
import logging
import sys

from common.di_container import container
from common.models.scanner_params import ScannerParams

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

# Set third-party loggers to WARNING to reduce noise
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger: logging.Logger = logging.getLogger(__name__)


async def main() -> None:
    """Run demand zone scanner + AI analyzer and print results."""
    logger.info("🔍 Starting Demand Zone Scanner + AI Analyzer...")
    
    try:
        # Step 1: Get the demand zone scanner
        scanner = container.demand_zone_scanner()
        params = ScannerParams("demand_zone_test")
        
        logger.info("📊 Step 1: Scanning Finviz for stocks close to demand zones...")
        logger.info("This may take a few minutes...")
        
        # Run the scanner
        demand_tickers = await scanner.scan(params)
        
        print("\n" + "=" * 70)
        print(f"STEP 1 - SCANNER RESULTS: {len(demand_tickers)} tickers found")
        print("=" * 70)
        if demand_tickers:
            print(f"Tickers: {', '.join(demand_tickers)}")
        print()
        
        if not demand_tickers:
            logger.warning("⚠️  No tickers found from scanner, cannot run AI analysis")
            return
        
        # Step 2: Run AI analyzer
        logger.info("🤖 Step 2: Running AI consensus analysis...")
        logger.info("Sending to Grok and Gemini for analysis...")
        
        ai_analyzer = container.ai_ticker_analyzer()
        grok_client = container.grok_client()
        gemini_client = container.gemini_client()
        
        # Get prompt template from settings
        prompt_template = container.config().demand_zone_strategy.prompt_template
        
        # Run AI analysis
        ai_tickers = await ai_analyzer.analyze_tickers(
            demand_tickers,
            prompt_template,
            grok_client,
            gemini_client,
        )
        
        # Print AI results
        print("=" * 70)
        print(f"STEP 2 - AI CONSENSUS RESULTS: {len(ai_tickers)} tickers selected")
        print("=" * 70)
        
        if ai_tickers:
            print("\n✅ Tickers selected by BOTH Grok and Gemini (consensus):")
            print(f"   {', '.join(ai_tickers)}")
            print(f"\n   Total: {len(ai_tickers)} tickers")
        else:
            print("\n⚠️  No consensus reached - Grok and Gemini did not agree on any tickers")
            print("   This means either:")
            print("   - The AIs had different opinions")
            print("   - No tickers met the criteria")
            print("   - There was an issue extracting tickers from responses")
        
        print("\n" + "=" * 70)
        logger.info(f"✅ Complete: {len(demand_tickers)} scanner tickers → {len(ai_tickers)} AI consensus tickers")
        
    except Exception as e:
        logger.error(f"❌ Error: {e}", exc_info=True)
        raise
    finally:
        # Cleanup
        http_client = container.http_client()
        await http_client.aclose()


if __name__ == "__main__":
    asyncio.run(main())
