"""Test script for TradingView idea puller."""
import asyncio
import logging

from common.di_container import container
from common.models.idea_params import IdeaParams

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)


async def main() -> None:
    """Test the TradingView idea puller."""
    # Get the idea puller from DI container
    idea_puller = container.tradingview_idea_puller()
    
    # Test with a popular ticker
    test_ticker: str = "AAPL"
    
    logger.info(f"Fetching TradingView ideas for {test_ticker}...")
    
    try:
        # Create params
        params = IdeaParams(ticker=test_ticker)
        
        # Pull ideas
        ideas = await idea_puller.pull_ideas(params)
        
        if ideas:
            logger.info(f"Found {len(ideas)} ideas for {test_ticker}:")
            for i, idea in enumerate(ideas, 1):
                logger.info(f"  {i}. {idea.ticker}: Entry=${idea.entry_price:.2f}, "
                           f"TP=${idea.take_profit:.2f}, SL=${idea.stop_loss:.2f}")
        else:
            logger.info(f"No ideas found for {test_ticker}")
            
    except Exception as e:
        logger.error(f"Error fetching ideas: {e}", exc_info=True)
    finally:
        # Close HTTP client
        await container.http_client().aclose()


if __name__ == "__main__":
    asyncio.run(main())
