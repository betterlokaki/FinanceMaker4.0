"""Standalone scanner - runs AI consensus scanner and prints results."""
import asyncio
import logging
import sys

from common.di_container import container
from common.models.scanner_params import ScannerParams

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

# Reduce noise from third-party loggers
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

logger: logging.Logger = logging.getLogger(__name__)


async def main() -> None:
    """Run AI consensus scanner and print results."""
    scanner = container.earning_tomorrow_ai_scanner()
    
    params: ScannerParams = ScannerParams(
        name="only_scan",
        config={"source": "ai_consensus"},
    )
    
    logger.info("🔍 Starting AI Consensus scan...")
    logger.info("This will query Grok + Gemini for earnings stock recommendations")
    print("-" * 60)
    
    tickers: list[str] = await scanner.scan(params)
    
    print("-" * 60)
    print(f"\n✅ CONSENSUS TICKERS ({len(tickers)}):")
    print("-" * 40)
    
    if tickers:
        for ticker in sorted(tickers):
            print(f"  • {ticker}")
    else:
        print("  (No consensus - AIs did not agree on any tickers)")
    
    print("-" * 40)
    print(f"Total: {len(tickers)} tickers recommended by BOTH Grok and Gemini")


if __name__ == "__main__":
    asyncio.run(main())
