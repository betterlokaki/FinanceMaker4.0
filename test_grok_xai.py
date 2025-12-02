"""Quick test of the refactored GrokClient using xAI SDK."""
import asyncio
import os
from gpt.grok.grok_base import GrokClient


async def test_grok_client():
    """Test the Grok client with xAI SDK."""
    try:
        # Check if API key is set
        if not os.getenv("XAI_API_KEY"):
            print("⚠️  XAI_API_KEY not set in environment")
            print("Set it with: export XAI_API_KEY='your-key-here'")
            return
        
        print("✓ XAI_API_KEY is configured")
        
        # Initialize client
        client = GrokClient()
        print("✓ GrokClient initialized successfully with xAI SDK")
        
        # Test query
        prompt = "What are the top 3 stocks trending today? Provide tickers."
        print(f"\n📝 Test prompt: {prompt}\n")
        
        # Generate response
        response = await client.generate_text(prompt)
        print(f"\n✓ Response generated successfully")
        print(f"Response length: {len(response)} characters")
        
    except ValueError as e:
        print(f"❌ Configuration error: {e}")
    except Exception as e:
        print(f"❌ Error: {e}")


if __name__ == "__main__":
    asyncio.run(test_grok_client())
