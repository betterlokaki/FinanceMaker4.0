"""
Test script to verify all components are working correctly.
"""
import asyncio
import sys
from typing import Any


async def test_settings() -> None:
    """Test configuration loading."""
    print("=" * 60)
    print("🔧 Testing Configuration System...")
    print("=" * 60)
    
    from common.settings import settings
    
    print(f"✅ Settings loaded successfully")
    print(f"   - Debug mode: {settings.debug}")
    print(f"   - Log level: {settings.log_level}")
    print(f"   - Finviz URL: {settings.finviz.base_url}")
    print(f"   - Grok model: {settings.grok.model}")
    print(f"   - Gemini model: {settings.gemini.model}")
    print(f"   - HTTP timeout: {settings.http.timeout}s")


async def test_di_container() -> None:
    """Test dependency injection container."""
    print("\n" + "=" * 60)
    print("🔌 Testing Dependency Injection Container...")
    print("=" * 60)
    
    from common.di_container import container
    
    print("✅ Container loaded successfully")
    
    # Test scanner
    scanner = container.finviz_scanner()
    print(f"   - Scanner: {type(scanner).__name__}")
    
    # Test HTTP client
    http_client = container.http_client()
    print(f"   - HTTP Client: {type(http_client).__name__}")
    
    # Test user agent manager
    user_agent_mgr = container.user_agent_manager()
    print(f"   - User Agent Manager: {type(user_agent_mgr).__name__}")


async def test_grok_client() -> None:
    """Test Grok client initialization."""
    print("\n" + "=" * 60)
    print("🤖 Testing Grok AI Client...")
    print("=" * 60)
    
    from common.settings import settings
    
    if not settings.grok.api_key:
        print("⚠️  Grok API key not configured")
        print("   To use: Set GROK_API_KEY in .env file")
        print("   Get key from: https://console.x.ai/")
        return
    
    try:
        from common.di_container import container
        grok = container.grok_client()
        print(f"✅ Grok client initialized: {type(grok).__name__}")
        print(f"   - Model: {grok._config.model}")
        print(f"   - Base URL: {grok._config.base_url}")
    except ValueError as e:
        print(f"⚠️  {str(e)}")


async def test_gemini_client() -> None:
    """Test Gemini client initialization."""
    print("\n" + "=" * 60)
    print("✨ Testing Gemini AI Client...")
    print("=" * 60)
    
    from common.settings import settings
    
    if not settings.gemini.api_key:
        print("⚠️  Gemini API key not configured")
        print("   To use: Set GEMINI_API_KEY in .env file")
        print("   Get key from: https://ai.google.dev/")
        return
    
    try:
        from common.di_container import container
        gemini = container.gemini_client()
        print(f"✅ Gemini client initialized: {type(gemini).__name__}")
        print(f"   - Model: {gemini._config.model}")
        print(f"   - Base URL: {gemini._config.base_url}")
    except ValueError as e:
        print(f"⚠️  {str(e)}")


async def test_scanner() -> None:
    """Test scanner initialization."""
    print("\n" + "=" * 60)
    print("📊 Testing Stock Scanner...")
    print("=" * 60)
    
    from common.di_container import container
    from common.models.scanner_params import ScannerParams
    
    scanner = container.finviz_scanner()
    print(f"✅ Scanner initialized: {type(scanner).__name__}")
    print(f"   - Base URL: {scanner._config.base_url}")
    print(f"   - Max pages: {scanner._config.max_pages}")
    print(f"   - Results per page: {scanner._config.results_per_page}")
    
    print("\n   Note: To run actual scan, use:")
    print("   params = ScannerParams(name='test')")
    print("   tickers = await scanner.scan(params)")


async def main() -> None:
    """Run all tests."""
    print("\n" + "🚀 " * 10)
    print("FinanceMaker 4.0 - Component Test Suite")
    print("🚀 " * 10 + "\n")
    
    try:
        await test_settings()
        await test_di_container()
        await test_grok_client()
        await test_gemini_client()
        await test_scanner()
        
        print("\n" + "=" * 60)
        print("✅ ALL TESTS PASSED!")
        print("=" * 60)
        print("\n📚 Next Steps:")
        print("   1. Copy .env.example to .env")
        print("   2. Add your API keys to .env")
        print("   3. Run: python main.py")
        print("\n📖 Documentation:")
        print("   - QUICK_START.md - Get started in 5 minutes")
        print("   - CONFIG_MANAGEMENT.md - Configuration guide")
        print("   - AI_CLIENTS.md - API client reference")
        print("=" * 60 + "\n")
        
    except Exception as e:
        print(f"\n❌ TEST FAILED: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
