"""Test script for AI consensus scanner."""
import asyncio
from typing import List, Set

from common.di_container import container
from common.models.scanner_params import ScannerParams


async def test_ai_scanner_initialization() -> None:
    """Test that AI scanner initializes correctly."""
    print("=" * 70)
    print("🤖 Testing AI Consensus Scanner Initialization...")
    print("=" * 70)
    
    try:
        ai_scanner = container.earning_tomorrow_ai_scanner()
        print(f"✅ AI Scanner initialized: {type(ai_scanner).__name__}")
        print(f"   - Configuration loaded: {ai_scanner._config}")
        print(f"   - Prompt template: {ai_scanner._config.prompt_template[:50]}...")
    except Exception as e:
        print(f"❌ Failed to initialize AI scanner: {str(e)}")
        raise


async def test_ticker_extraction() -> None:
    """Test ticker extraction from AI responses."""
    print("\n" + "=" * 70)
    print("🎯 Testing Ticker Extraction from AI Response...")
    print("=" * 70)
    
    ai_scanner = container.earning_tomorrow_ai_scanner()
    
    # Test case 1: Simple list with newlines
    response1 = """
    Based on the earnings and market conditions, I suggest:
    AAPL
    MSFT
    GOOGL
    TSLA
    NVDA
    """
    
    valid_tickers = ["AAPL", "MSFT", "GOOGL", "TSLA", "NVDA", "AMZN"]
    extracted1 = ai_scanner._extract_tickers_from_response(response1, valid_tickers)
    print(f"✅ Test 1 - Simple list: Extracted {extracted1}")
    assert "AAPL" in extracted1, "Should extract AAPL"
    assert "MSFT" in extracted1, "Should extract MSFT"
    
    # Test case 2: Comma-separated
    response2 = "I recommend: AAPL, MSFT, GOOGL for trading today"
    extracted2 = ai_scanner._extract_tickers_from_response(response2, valid_tickers)
    print(f"✅ Test 2 - Comma-separated: Extracted {extracted2}")
    assert "AAPL" in extracted2, "Should extract AAPL from comma-separated"
    
    # Test case 3: Mixed format with invalid tickers
    response3 = """
    Strong buy signals on: AAPL (excellent), MSFT (good), INVALID (skip), NVDA
    These are worth watching carefully
    """
    extracted3 = ai_scanner._extract_tickers_from_response(response3, valid_tickers)
    print(f"✅ Test 3 - Mixed format: Extracted {extracted3}")
    assert "INVALID" not in extracted3, "Should not include invalid tickers"
    assert "AAPL" in extracted3, "Should extract AAPL"
    
    print("✅ All extraction tests passed!")


async def test_consensus_finding() -> None:
    """Test consensus finding between AI providers."""
    print("\n" + "=" * 70)
    print("🤝 Testing AI Consensus Finding...")
    print("=" * 70)
    
    ai_scanner = container.earning_tomorrow_ai_scanner()
    
    # Simulate Grok suggestions
    grok_suggestions: Set[str] = {"AAPL", "MSFT", "GOOGL", "TSLA"}
    
    # Simulate Gemini suggestions
    gemini_suggestions: Set[str] = {"AAPL", "MSFT", "NVDA", "AMZN"}
    
    # Find consensus
    consensus = await ai_scanner._find_consensus(grok_suggestions, gemini_suggestions)
    
    print(f"Grok suggestions:    {grok_suggestions}")
    print(f"Gemini suggestions:  {gemini_suggestions}")
    print(f"Consensus (both):    {consensus}")
    
    # Check results
    assert "AAPL" in consensus, "AAPL should be in consensus"
    assert "MSFT" in consensus, "MSFT should be in consensus"
    assert len(consensus) == 2, "Should have exactly 2 items in consensus"
    assert "GOOGL" not in consensus, "GOOGL should not be in consensus (only Grok)"
    assert "NVDA" not in consensus, "NVDA should not be in consensus (only Gemini)"
    
    print("✅ Consensus finding works correctly!")


async def test_configuration() -> None:
    """Test AI scanner configuration."""
    print("\n" + "=" * 70)
    print("⚙️  Testing AI Scanner Configuration...")
    print("=" * 70)
    
    from common.settings import settings
    
    print(f"✅ AI Scanner Config Loaded:")
    print(f"   - Prompt template length: {len(settings.ai_scanner.prompt_template)} chars")
    print(f"   - Extraction method: {settings.ai_scanner.extraction_method}")
    print(f"   - Template: {settings.ai_scanner.prompt_template[:100]}...")
    
    assert "{TICKERS}" in settings.ai_scanner.prompt_template, "Should have {TICKERS} placeholder"
    assert settings.ai_scanner.extraction_method == "line_based", "Should use line_based extraction"


async def test_di_container() -> None:
    """Test DI container has all required services."""
    print("\n" + "=" * 70)
    print("🔌 Testing DI Container Services...")
    print("=" * 70)
    
    print("✅ Available services:")
    print(f"   - HTTP Client: {type(container.http_client()).__name__}")
    print(f"   - Finviz Scanner: {type(container.finviz_scanner()).__name__}")
    
    # Try to get AI clients, but handle missing API keys gracefully
    try:
        print(f"   - Grok Client: {type(container.grok_client()).__name__}")
    except ValueError:
        print(f"   - Grok Client: ⚠️  (API key not configured in .env)")
    
    try:
        print(f"   - Gemini Client: {type(container.gemini_client()).__name__}")
    except ValueError:
        print(f"   - Gemini Client: ⚠️  (API key not configured in .env)")
    
    try:
        print(f"   - AI Consensus Scanner: {type(container.earning_tomorrow_ai_scanner()).__name__}")
    except ValueError as e:
        print(f"   - AI Consensus Scanner: ⚠️  (Needs API keys)")
    
    print(f"   - User Agent Manager: {type(container.user_agent_manager()).__name__}")


async def main() -> None:
    """Run all tests."""
    print("\n" + "🚀 " * 15)
    print("AI CONSENSUS SCANNER - TEST SUITE")
    print("🚀 " * 15 + "\n")
    
    try:
        await test_configuration()
        await test_di_container()
        await test_ai_scanner_initialization()
        await test_ticker_extraction()
        await test_consensus_finding()
        
        print("\n" + "=" * 70)
        print("✅ ALL TESTS PASSED!")
        print("=" * 70)
        print("\n📝 Next Steps:")
        print("   1. Add your Grok API key to .env (GROK_API_KEY=...)")
        print("   2. Add your Gemini API key to .env (GEMINI_API_KEY=...)")
        print("   3. Run: python test_ai_scanner_full.py (for full integration test)")
        print("   4. Deploy the AI consensus scanner in production")
        print("=" * 70 + "\n")
        
    except AssertionError as e:
        print(f"\n❌ TEST FAILED - Assertion Error: {str(e)}")
        import traceback
        traceback.print_exc()
    except Exception as e:
        print(f"\n❌ TEST FAILED - {str(e)}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    asyncio.run(main())
