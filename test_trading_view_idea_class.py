"""Real-time tester for TradingView idea puller class.

This is NOT a unit test - it's a real-time end-to-end test that actually
fetches data from TradingView's website and displays the results.
"""
import asyncio
import logging

from common.di_container import container
from common.models.idea_params import IdeaParams

# Setup logging
logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)


async def end_to_end() -> None:
    """Run end-to-end test of TradingView idea puller with AAPL stock."""
    logger.info("=" * 80)
    logger.info("Starting TradingView Idea Puller End-to-End Test")
    logger.info("=" * 80)
    
    # Get the idea puller from DI container
    idea_puller = container.tradingview_idea_puller()
    
    # Test with Apple stock
    test_ticker: str = "AAPL"
    
    logger.info(f"\n📊 Fetching TradingView ideas for {test_ticker}...")
    logger.info(f"URL: https://www.tradingview.com/symbols/NASDAQ-{test_ticker}/ideas/\n")
    
    try:
        # Create params
        params = IdeaParams(ticker=test_ticker)
        
        # Pull ideas
        ideas = await idea_puller.pull_ideas(params)
        
        logger.info("=" * 80)
        if ideas:
            logger.info(f"✅ SUCCESS! Found {len(ideas)} trade ideas for {test_ticker}:\n")
            
            for i, idea in enumerate(ideas, 1):
                logger.info(f"  Idea #{i}:")
                logger.info(f"    Ticker:       {idea.ticker}")
                logger.info(f"    Entry Price:  ${idea.entry_price:.2f}")
                logger.info(f"    Take Profit:  ${idea.take_profit:.2f}")
                logger.info(f"    Stop Loss:    ${idea.stop_loss:.2f}")
                
                # Calculate risk/reward ratio
                risk = idea.entry_price - idea.stop_loss
                reward = idea.take_profit - idea.entry_price
                rr_ratio = reward / risk if risk > 0 else 0
                
                logger.info(f"    Risk:         ${risk:.2f}")
                logger.info(f"    Reward:       ${reward:.2f}")
                logger.info(f"    R/R Ratio:    {rr_ratio:.2f}x")
                logger.info("")
        else:
            logger.warning(f"⚠️  No trade ideas found for {test_ticker}")
            logger.info("\nPossible reasons:")
            logger.info("  1. No ideas are currently posted on TradingView for this ticker")
            logger.info("  2. The XPath selector needs to be updated (site structure changed)")
            logger.info("  3. Price patterns in idea text don't match regex patterns")
            logger.info("  4. TradingView is blocking the request (try different user-agent)")
            
        logger.info("=" * 80)
            
    except Exception as e:
        logger.error("=" * 80)
        logger.error(f"❌ ERROR: Failed to fetch ideas for {test_ticker}")
        logger.error(f"Exception: {type(e).__name__}: {e}")
        logger.error("=" * 80)
        raise
    finally:
        # Close HTTP client
        logger.info("🔒 Closing HTTP client...")
        await container.http_client().aclose()
        logger.info("✅ Test completed!\n")


if __name__ == "__main__":
    asyncio.run(end_to_end())
