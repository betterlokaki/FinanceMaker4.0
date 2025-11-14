"""
QUICK START GUIDE - FINANCEMAKER 4.0
====================================

## 🚀 Getting Started (5 minutes)

### 1. Setup Environment Variables
```bash
# Copy the template
cp .env.example .env

# Edit and add your API keys
nano .env
```

Fill in:
```
GROK_API_KEY=your_grok_key_from_console.x.ai
GEMINI_API_KEY=your_gemini_key_from_ai.google.dev
```

### 2. Run Application
```bash
python main.py
```

That's it! Everything is configured and ready.

---

## 📖 Common Tasks

### Use Grok AI
```python
from common.di_container import container

async def ask_grok():
    grok = container.grok_client()
    response = await grok.generate_text("Explain quantum computing")
    print(response)

# Run: asyncio.run(ask_grok())
```

### Use Gemini AI
```python
from common.di_container import container

async def ask_gemini():
    gemini = container.gemini_client()
    response = await gemini.generate_text("Write a poem about AI")
    print(response)

# Run: asyncio.run(ask_gemini())
```

### Scan Stocks
```python
from common.di_container import container
from common.models.scanner_params import ScannerParams

async def scan_stocks():
    scanner = container.finviz_scanner()
    params = ScannerParams(name="earners", filters={})
    tickers = await scanner.scan(params)
    print(f"Found {len(tickers)} tickers: {tickers[:5]}")

# Run: asyncio.run(scan_stocks())
```

### Access Configuration
```python
from common.settings import settings

# Any setting can be accessed
print(settings.grok.model)          # "grok-beta"
print(settings.gemini.model)        # "gemini-2.0-flash"
print(settings.finviz.base_url)     # "https://finviz.com/..."
print(settings.http.timeout)        # 30.0
print(settings.debug)               # false
```

---

## ⚙️ Configure Settings

Edit `config.yaml` to customize:

```yaml
# Change timeouts
http:
  timeout: 60.0  # Increase from 30

# Change models
grok:
  model: "grok-beta"
  max_tokens: 2000

gemini:
  model: "gemini-2.0-flash"
  max_tokens: 2000

# Change scanner settings
finviz:
  max_pages: 10  # Reduce from 30
  timeout: 45.0
```

### Override via Environment
```bash
# Override any setting via environment
export HTTP__TIMEOUT=60.0
export GROK__MAX_TOKENS=2000
export FINVIZ__MAX_PAGES=10

# Then run your code
python main.py
```

---

## 📁 Project Structure

```
common/
  ├── settings.py          ← Configuration system
  ├── di_container.py      ← Services registration
  └── user_agent.py        ← User-agent rotation

gpt/
  ├── abstracts/gpt_base.py    ← Base class
  ├── grok/grok_base.py        ← Grok client
  └── gemini/gemini_base.py    ← Gemini client

pullers/
  └── scanners/
      ├── abstracts/scanner.py  ← Base class
      └── finviz/finviz_base.py ← Finviz scanner

config.yaml         ← Settings (committed)
.env.example        ← Template (committed)
.env                ← Secrets (NOT committed)
```

---

## 🔑 API Keys Setup

### Get Grok API Key
1. Visit https://console.x.ai/
2. Sign up or login
3. Create new API key
4. Copy key to .env: `GROK_API_KEY=xxx`

### Get Gemini API Key
1. Visit https://ai.google.dev/
2. Sign up or login
3. Create new API key
4. Copy key to .env: `GEMINI_API_KEY=yyy`

---

## 🧪 Testing Code

### Create a test script
```python
import asyncio
from common.di_container import container
from common.models.scanner_params import ScannerParams

async def test_all():
    print("Testing Grok...")
    grok = container.grok_client()
    result = await grok.generate_text("Hello")
    print(f"Grok: {result[:50]}...")
    
    print("\nTesting Gemini...")
    gemini = container.gemini_client()
    result = await gemini.generate_text("Hello")
    print(f"Gemini: {result[:50]}...")
    
    print("\nTesting Scanner...")
    scanner = container.finviz_scanner()
    params = ScannerParams(name="test")
    tickers = await scanner.scan(params)
    print(f"Scanner: Found {len(tickers)} tickers")

asyncio.run(test_all())
```

Run:
```bash
python test_script.py
```

---

## 📚 Documentation

- **IMPLEMENTATION_SUMMARY.md** - What was built
- **CONFIG_MANAGEMENT.md** - How configuration works
- **AI_CLIENTS.md** - Grok & Gemini details
- **PROJECT_STRUCTURE.md** - File organization
- **DI_BEST_PRACTICES.md** - Dependency injection
- **FINVIZ_SCANNER_DOCS.md** - Scanner details

---

## 🆘 Troubleshooting

### "API key not configured" error
→ Check .env file exists and has your API keys

### "Import error: No module named 'pydantic_settings'"
→ Run: `pip install pydantic-settings pyyaml python-dotenv`

### "Connection timeout"
→ Increase timeout in config.yaml:
```yaml
http:
  timeout: 60.0
```

### "API returned unexpected format"
→ Check if API endpoint URL is correct in config.yaml
→ Verify API key has required permissions

---

## 💡 Tips

1. **Services are Singletons**
   - Each service created once and reused
   - No need to manage lifecycles

2. **Configuration is Flexible**
   - Change via config.yaml or .env
   - Or override with environment variables

3. **Everything is Async**
   - Use `asyncio.run()` to run async code
   - Or `await` in async functions

4. **Type Safe**
   - Full type hints everywhere
   - IDE autocomplete support
   - Catch errors early

5. **Well Documented**
   - Comprehensive docstrings
   - Configuration examples
   - Usage patterns

---

## 🚀 Next Steps

1. Get your API keys (see above)
2. Fill in .env file
3. Run: `python main.py`
4. Explore services from container
5. Read documentation for details
6. Customize config.yaml as needed

Happy coding! 🎉
"""
