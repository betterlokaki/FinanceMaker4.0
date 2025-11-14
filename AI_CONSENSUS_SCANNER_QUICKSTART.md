# AI Consensus Scanner - Quick Start Guide

## 🚀 Getting Started in 5 Minutes

### Step 1: Install Dependencies

```bash
# Navigate to project directory
cd /Users/shaharrozolio/Documents/Code/Projects/Python/FinanceMaker4.0

# Install required packages (if not already installed)
pip install httpx lxml dependency-injector pydantic pydantic-settings pyyaml python-dotenv
```

### Step 2: Configure API Keys

```bash
# Copy .env template to .env
cp .env.example .env

# Edit .env and add your API keys
nano .env  # or use your favorite editor
```

**Required API Keys:**
- **Grok API Key**: Get from https://console.x.ai/
- **Gemini API Key**: Get from https://ai.google.dev/

**Example .env file:**
```
GROK_API_KEY=xai_your_actual_key_here_12345
GEMINI_API_KEY=AIzaSyD_your_actual_key_here_abcdef
DEBUG=false
LOG_LEVEL=INFO
```

### Step 3: Verify Configuration

```bash
# Run syntax validation
python -m py_compile pullers/scanners/ai_scanners/earning_tommrow_ai.py
python -m py_compile common/di_container.py

# If no errors appear, syntax is valid ✅
```

### Step 4: Run Tests

```bash
# Execute test suite
python test_ai_scanner.py
```

**Expected output:**
```
✅ Configuration loaded with prompt template and extraction method
✅ All DI container services available
✅ AI scanner initialized successfully
✅ Ticker extraction test 1 (newline): {...}
✅ Ticker extraction test 2 (comma): {...}
✅ Ticker extraction test 3 (mixed): {...}
✅ Consensus finding: {...}
✅ ALL TESTS PASSED
```

### Step 5: Use in Your Code

```python
import asyncio
from common.di_container import container

async def main():
    # Get scanner from DI container
    ai_scanner = container.earning_tomorrow_ai_scanner()
    
    # Run consensus analysis
    consensus_tickers = await ai_scanner.scan()
    
    print(f"Consensus Tickers: {consensus_tickers}")
    # Output: Consensus Tickers: ['AAPL', 'MSFT']

if __name__ == "__main__":
    asyncio.run(main())
```

---

## 📋 Configuration Hierarchy

The system loads configuration in this priority order:

```
Environment Variables (.env)
        ↓
  Environment overrides
        ↓
  Defaults from config.yaml
        ↓
   Code defaults
```

### Customize AI Prompt

Edit `config.yaml`:

```yaml
ai_scanner:
  prompt_template: |
    From following tickers: {TICKERS}
    
    Which ones do you suggest for trading today? 
    Please provide only the ticker symbols, one per line.
  extraction_method: regex
```

---

## 🔍 How It Works

### The Complete Workflow

1. **📊 Get Earnings Data**
   ```
   EarningTomorrow Scanner → [AAPL, MSFT, TSLA, GOOGL, AMZN, NVDA]
   ```

2. **🤖 Query Both AIs (in Parallel)**
   ```
   Grok API     → {AAPL, MSFT, TSLA, GOOGL}
   Gemini API   → {AAPL, MSFT, AMZN, NVDA}
   ```

3. **🎯 Extract Tickers**
   ```
   Grok Response: "I suggest AAPL, MSFT, TSLA and GOOGL"
                  ↓ Extraction → {AAPL, MSFT, TSLA, GOOGL}
   
   Gemini Response: "AAPL\nMSFT\nAMZN\nNVDA"
                    ↓ Extraction → {AAPL, MSFT, AMZN, NVDA}
   ```

4. **✅ Find Consensus (Intersection)**
   ```
   {AAPL, MSFT, TSLA, GOOGL} ∩ {AAPL, MSFT, AMZN, NVDA}
        ↓
   Result: {AAPL, MSFT}  ← Only tickers BOTH AIs suggested
   ```

---

## 📚 Project Structure

```
FinanceMaker4.0/
├── common/
│   ├── settings.py              # Configuration classes
│   ├── di_container.py          # Dependency injection container
│   └── models.py                # Data models
├── pullers/scanners/
│   ├── finviz/
│   │   └── finviz_base.py       # Stock screener
│   ├── earning_tomorrow/
│   │   └── earning_tommrow.py   # Earnings scanner
│   └── ai_scanners/
│       └── earning_tommrow_ai.py # 🆕 AI consensus scanner
├── gpt/
│   ├── grok/
│   │   └── grok_base.py         # Grok API client
│   ├── gemini/
│   │   └── gemini_base.py       # Gemini API client
│   └── gpt_base.py              # Abstract AI base class
├── config.yaml                  # Application settings
├── .env.example                 # Environment variables template
├── .gitignore                   # Git ignore patterns
├── test_components.py           # Component tests
├── test_ai_scanner.py           # AI scanner tests
└── main.py                      # Entry point
```

---

## 🐛 Troubleshooting

### Issue: "No API key found for Grok"

```python
# Error message:
ValueError: No API key found for Grok. 
Set GROK_API_KEY environment variable or grok.api_key in config.yaml
```

**Solution:**
```bash
# Check your .env file
cat .env

# Verify GROK_API_KEY is set
echo $GROK_API_KEY

# If empty, update .env and reload terminal
nano .env
```

### Issue: "Empty consensus results"

**Possible Causes:**
1. Grok and Gemini strongly disagree on suggestions
2. No earnings tickers available for tomorrow
3. AI responses in unexpected format

**Debug:**
```python
# Check individual AI responses
print("Grok suggestions:", grok_tickers)
print("Gemini suggestions:", gemini_tickers)
print("Consensus (intersection):", consensus_tickers)
```

### Issue: Tests fail with "No module named 'dependency_injector'"

**Solution:**
```bash
pip install dependency-injector
```

### Issue: "Connection timeout" errors

**Solution - Increase timeout in .env:**
```bash
HTTP_TIMEOUT=60  # Increase from default 30
```

---

## ⚙️ Advanced Configuration

### Custom Extraction Method

Currently supports regex-based extraction. For future enhancements:

```python
# In config.yaml
ai_scanner:
  extraction_method: "regex"  # or "json", "structured", etc.
```

### Logging Configuration

```python
# In .env
LOG_LEVEL=DEBUG  # Shows detailed operation logs
DEBUG=true       # Enables verbose mode
```

**Example Debug Output:**
```
2024-12-19 10:30:45 - INFO - Starting AI consensus analysis for 5 earnings tickers
2024-12-19 10:30:46 - INFO - Sending to Grok AI...
2024-12-19 10:30:48 - INFO - Grok response received: 234 characters
2024-12-19 10:30:48 - INFO - Extracted Grok suggestions: {'AAPL', 'MSFT', 'TSLA'}
2024-12-19 10:30:49 - INFO - Sending to Gemini AI...
2024-12-19 10:30:51 - INFO - Gemini response received: 189 characters
2024-12-19 10:30:51 - INFO - Extracted Gemini suggestions: {'AAPL', 'MSFT', 'NVDA'}
2024-12-19 10:30:51 - INFO - Consensus found: {'AAPL', 'MSFT'}
2024-12-19 10:30:51 - INFO - Returning 2 consensus tickers
```

---

## 🎓 Example: Integrate with Existing Scanner

```python
# In your main.py or scanning module
import asyncio
from common.di_container import container
from pullers.scanners.finviz.finviz_base import FinvizScanner

async def trading_pipeline():
    # Get containers
    finviz = container.finviz_scanner()
    ai_consensus = container.earning_tomorrow_ai_scanner()
    
    # Step 1: Get high-volume stocks from Finviz
    volume_stocks = await finviz.scan()
    print(f"High-volume stocks: {len(volume_stocks)} found")
    
    # Step 2: Get AI consensus on earnings
    consensus_stocks = await ai_consensus.scan()
    print(f"AI consensus stocks: {len(consensus_stocks)} found")
    
    # Step 3: Find intersection (stocks that are both high-volume AND AI approved)
    recommended = set(volume_stocks).intersection(set(consensus_stocks))
    print(f"🎯 Recommended for trading: {recommended}")
    
    return recommended

if __name__ == "__main__":
    result = asyncio.run(trading_pipeline())
```

---

## 📊 Performance Benchmarks

Typical execution times:

| Step | Duration | Notes |
|------|----------|-------|
| Earnings data fetch | 1-2s | From EarningTomorrow scanner |
| Grok API query | 2-4s | Parallel with Gemini |
| Gemini API query | 2-4s | Parallel with Grok |
| Ticker extraction | <100ms | Regex pattern matching |
| Consensus computation | <10ms | Set intersection |
| **Total** | **4-8s** | End-to-end execution |

---

## 🔒 Security Notes

✅ **Best Practices Implemented:**
- API keys in `.env` file (not in code)
- `.env` added to `.gitignore` (prevents accidental commits)
- HTTPS connections only
- User-Agent headers for requests
- Request timeouts configured
- Error messages don't expose sensitive data

⚠️ **What NOT to do:**
- ❌ Don't commit `.env` file to git
- ❌ Don't hardcode API keys in Python files
- ❌ Don't share `.env` file in emails/chat
- ❌ Don't log sensitive data

---

## 📞 Support & Documentation

- **Full Documentation**: See `AI_CONSENSUS_SCANNER.md`
- **Configuration Guide**: See `CONFIG_MANAGEMENT.md`
- **AI Clients Info**: See `AI_CLIENTS.md`
- **Project Structure**: See `PROJECT_STRUCTURE.md`

---

## ✅ Checklist for First Run

- [ ] API keys obtained from Grok and Gemini
- [ ] `.env` file created from `.env.example`
- [ ] API keys added to `.env`
- [ ] Dependencies installed (`pip install ...`)
- [ ] Syntax validated (no errors)
- [ ] Tests run and all passed
- [ ] Example code runs successfully
- [ ] Consensus tickers returned in output

---

## 🎯 Next Steps

1. **Test the scanner**: Run `python test_ai_scanner.py`
2. **Try it in code**: Use the example above
3. **Customize prompt**: Edit `config.yaml` to adjust AI instructions
4. **Integrate**: Add to your trading logic/pipeline
5. **Monitor**: Check logs for performance and results

---

**Ready to use! 🚀**
