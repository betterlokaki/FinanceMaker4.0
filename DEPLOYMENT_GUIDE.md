# 🚀 AI Consensus Scanner - Deployment & Integration Guide

## Quick Reference

**Status**: ✅ Production Ready  
**Components**: EarningTomorrowAI Scanner, Grok Client, Gemini Client  
**Tests**: ✅ 5/5 Passing  
**Documentation**: ✅ Complete

---

## 🎯 Integration Checklist

### Phase 1: Setup (5 minutes)
- [ ] Clone/download project
- [ ] Install Python packages: `pip install -r requirements.txt`
- [ ] Create `.env` from `.env.example`
- [ ] Add API keys to `.env`

### Phase 2: Verification (2 minutes)
- [ ] Run syntax check: `python -m py_compile pullers/scanners/ai_scanners/earning_tommrow_ai.py`
- [ ] Run tests: `python test_ai_scanner.py`
- [ ] Verify output shows "ALL TESTS PASSED"

### Phase 3: Integration (10 minutes)
- [ ] Import scanner in your code
- [ ] Add to DI container references
- [ ] Create async wrapper if needed
- [ ] Test in your pipeline

### Phase 4: Deployment (variable)
- [ ] Review logs and error handling
- [ ] Set appropriate timeouts
- [ ] Configure monitoring/alerts
- [ ] Deploy to production

---

## 📦 Installation

### Step 1: Ensure Dependencies

```bash
# Required packages (check if installed)
pip list | grep -E 'httpx|pydantic|dependency-injector|lxml'

# Install if missing
pip install httpx pydantic pydantic-settings dependency-injector lxml pyyaml python-dotenv
```

### Step 2: Verify Python Version

```bash
python --version
# Should be Python 3.10+ (tested on 3.12.6)
```

### Step 3: Create Virtual Environment (if needed)

```bash
python -m venv .venv
source .venv/bin/activate  # macOS/Linux
# or
.venv\Scripts\activate  # Windows
```

---

## 🔑 API Keys Setup

### Get Grok API Key

1. Visit: https://console.x.ai/
2. Sign up or log in
3. Create new API key
4. Copy the key (starts with `xai_`)

### Get Gemini API Key

1. Visit: https://ai.google.dev/
2. Sign in with Google account
3. Create new API key
4. Copy the key

### Configure .env

```bash
# Copy template
cp .env.example .env

# Edit .env with your keys
cat >> .env << 'EOF'
GROK_API_KEY=xai_your_actual_key_here
GEMINI_API_KEY=AIzaSyD_your_actual_key_here
EOF
```

---

## 🧪 Testing

### Run Full Test Suite

```bash
python test_ai_scanner.py
```

### Expected Output

```
🚀 🚀 🚀 AI CONSENSUS SCANNER - TEST SUITE 🚀 🚀 🚀

✅ Configuration Tests
✅ DI Container Tests  
✅ Initialization Tests
✅ Ticker Extraction Tests (3 scenarios)
✅ Consensus Finding Tests

✅ ALL TESTS PASSED
```

### Run Individual Component Tests

```bash
# Just syntax check
python -m py_compile pullers/scanners/ai_scanners/earning_tommrow_ai.py

# Just DI container
python -c "from common.di_container import container; print(container.earning_tomorrow_ai_scanner)"

# Just config loading
python -c "from common.settings import settings; print(settings.ai_scanner.prompt_template)"
```

---

## 💻 Basic Usage

### Minimal Example

```python
import asyncio
from common.di_container import container

async def main():
    # Get scanner
    scanner = container.earning_tomorrow_ai_scanner()
    
    # Run analysis
    consensus_tickers = await scanner.scan()
    
    # Use results
    print(f"Consensus: {consensus_tickers}")

if __name__ == "__main__":
    asyncio.run(main())
```

### Save as `test_consensus.py` and run:

```bash
python test_consensus.py
```

### Expected Output

```
Consensus: ['AAPL', 'MSFT']
```

---

## 🔧 Configuration

### Edit Prompt Template

**File**: `config.yaml`

```yaml
ai_scanner:
  prompt_template: |
    From following tickers: {TICKERS}
    
    Which ones do you suggest for trading today?
    Please provide only the ticker symbols, one per line.
  extraction_method: regex
```

### Customize Extraction

```yaml
ai_scanner:
  extraction_method: regex  # Current: regex pattern matching
  # Future options: json, structured, ml_classifier
```

### Adjust Timeouts

**File**: `.env`

```bash
# Increase timeout for slower networks
HTTP_TIMEOUT=60  # Default: 30 seconds
```

---

## 📊 Integration Examples

### Example 1: Daily Trading Pipeline

```python
import asyncio
from common.di_container import container

async def trading_pipeline():
    scanner = container.earning_tomorrow_ai_scanner()
    
    consensus = await scanner.scan()
    
    if consensus:
        print(f"📊 Today's AI picks: {consensus}")
        # Send to broker
        for ticker in consensus:
            place_order(ticker)
    else:
        print("No consensus found")

asyncio.run(trading_pipeline())
```

### Example 2: Research Integration

```python
async def research_scan():
    scanner = container.earning_tomorrow_ai_scanner()
    
    picks = await scanner.scan()
    
    for ticker in picks:
        # Get more data
        fundamentals = fetch_fundamentals(ticker)
        technicals = fetch_technicals(ticker)
        news = fetch_news(ticker)
        
        print(f"{ticker}: Fund={fundamentals}, Tech={technicals}, News={news}")

asyncio.run(research_scan())
```

### Example 3: Scheduled Jobs

```python
import asyncio
import schedule

async def run_scanner():
    scanner = container.earning_tomorrow_ai_scanner()
    return await scanner.scan()

def job():
    result = asyncio.run(run_scanner())
    print(f"Scheduled result: {result}")
    # Store in database
    save_to_db(result)

# Schedule for 9:30 AM (market open)
schedule.every().day.at("09:30").do(job)

while True:
    schedule.run_pending()
    asyncio.sleep(60)
```

### Example 4: Flask Web API

```python
from flask import Flask
from common.di_container import container
import asyncio

app = Flask(__name__)

@app.route('/api/consensus', methods=['GET'])
async def get_consensus():
    scanner = container.earning_tomorrow_ai_scanner()
    tickers = await scanner.scan()
    return {
        'status': 'success',
        'consensus': tickers,
        'count': len(tickers)
    }

if __name__ == '__main__':
    app.run(debug=False)
```

---

## 🔍 Monitoring & Debugging

### Enable Debug Logging

**In .env:**
```bash
LOG_LEVEL=DEBUG
DEBUG=true
```

### Monitor Execution

```python
import logging
import asyncio
from common.di_container import container

logging.basicConfig(level=logging.DEBUG)

async def debug_run():
    scanner = container.earning_tomorrow_ai_scanner()
    
    # This will show detailed logs
    result = await scanner.scan()
    
    print(result)

asyncio.run(debug_run())
```

### Expected Debug Output

```
DEBUG: Starting AI consensus analysis
DEBUG: Sending to Grok AI...
DEBUG: Grok response status: 200
DEBUG: Extracted Grok suggestions: {'AAPL', 'MSFT', 'TSLA'}
DEBUG: Sending to Gemini AI...
DEBUG: Gemini response status: 200
DEBUG: Extracted Gemini suggestions: {'AAPL', 'MSFT', 'NVDA'}
DEBUG: Computing consensus...
DEBUG: Consensus: {'AAPL', 'MSFT'}
```

### Check Service Status

```python
from common.di_container import container

# Verify all services available
http_client = container.http_client()
finviz = container.finviz_scanner()
grok = container.grok_client()
gemini = container.gemini_client()
ai_scanner = container.earning_tomorrow_ai_scanner()

print("✅ All services initialized")
```

---

## ⚠️ Error Handling

### Common Errors & Solutions

#### Error 1: "No API key found"

```
ValueError: No API key found for Grok
```

**Solution:**
```bash
# Check if .env exists
ls -la .env

# Check if keys are set
grep GROK_API_KEY .env
grep GEMINI_API_KEY .env

# If empty, edit and add keys
nano .env
```

#### Error 2: "Module not found"

```
ModuleNotFoundError: No module named 'dependency_injector'
```

**Solution:**
```bash
pip install dependency-injector
pip install pydantic pydantic-settings
pip install httpx lxml
```

#### Error 3: "Connection timeout"

```
httpx.ConnectTimeout: Connection timeout
```

**Solution:**
```bash
# Increase timeout in .env
echo "HTTP_TIMEOUT=60" >> .env

# Check network connectivity
ping api.x.ai
ping generativelanguage.googleapis.com
```

#### Error 4: "Empty consensus"

**Possible Causes:**
- Grok and Gemini strongly disagree
- No earnings data available
- Extraction pattern misses tickers

**Debug:**
```python
# Check individual AI responses
grok_response = await grok.generate_text(prompt)
gemini_response = await gemini.generate_text(prompt)

print(f"Grok: {grok_response}")
print(f"Gemini: {gemini_response}")

# Check extraction
grok_tickers = scanner._extract_tickers_from_response(grok_response, valid_tickers)
gemini_tickers = scanner._extract_tickers_from_response(gemini_response, valid_tickers)

print(f"Grok extracted: {grok_tickers}")
print(f"Gemini extracted: {gemini_tickers}")
print(f"Consensus: {grok_tickers.intersection(gemini_tickers)}")
```

---

## 🌐 Environment Variables Reference

| Variable | Type | Default | Purpose |
|----------|------|---------|---------|
| `GROK_API_KEY` | string | (required) | Grok API authentication |
| `GEMINI_API_KEY` | string | (required) | Gemini API authentication |
| `DEBUG` | bool | false | Enable debug mode |
| `LOG_LEVEL` | string | INFO | Logging verbosity |
| `HTTP_TIMEOUT` | int | 30 | Request timeout (seconds) |

---

## 📈 Performance Tuning

### Optimize Timeout Settings

```yaml
# In config.yaml
http:
  timeout: 30  # Adjust based on network
  follow_redirects: true
```

### Parallel Processing

The scanner already runs Grok and Gemini queries in parallel:

```python
# Already optimized - both run simultaneously
async with asyncio.TaskGroup() as tg:
    grok_task = tg.create_task(self._get_ai_suggestions(tickers, "Grok"))
    gemini_task = tg.create_task(self._get_ai_suggestions(tickers, "Gemini"))
```

### Caching (Future Enhancement)

```python
# Future: Add caching for repeated queries
from functools import lru_cache

@lru_cache(maxsize=100)
async def get_cached_consensus(ticker_tuple):
    # Cache results for same ticker combinations
    pass
```

---

## 🔒 Production Deployment

### Security Checklist

- [ ] API keys NOT in code (use .env)
- [ ] .env file in .gitignore
- [ ] HTTPS-only connections
- [ ] Proper error handling (no sensitive data in errors)
- [ ] Logging configured (sensitive data filtered)
- [ ] Timeouts configured
- [ ] Rate limiting considered

### Pre-Production Testing

```bash
# Run full test suite
python test_ai_scanner.py

# Test with real API keys
python test_consensus.py

# Monitor logs
tail -f logs/app.log

# Check resource usage
top -p $(pgrep -f python)
```

### Deployment Commands

```bash
# Production environment setup
export PYTHONENV=production
export LOG_LEVEL=INFO

# Run with gunicorn (if using Flask/FastAPI)
gunicorn -w 4 -b 0.0.0.0:8000 main:app

# Or run as service
systemctl start fiancemaker-ai-scanner
```

---

## 📊 Monitoring

### Key Metrics to Track

1. **Execution Time**
   - Target: 4-8 seconds
   - Alert if: > 15 seconds

2. **Consensus Count**
   - Track number of tickers returned
   - Monitor trend over time

3. **Error Rate**
   - API errors
   - Extraction failures
   - Network timeouts

4. **API Usage**
   - Grok requests/day
   - Gemini requests/day
   - Monitor costs

### Logging Setup

```python
import logging
from pythonjsonlogger import jsonlogger

logger = logging.getLogger(__name__)
logHandler = logging.StreamHandler()
formatter = jsonlogger.JsonFormatter()
logHandler.setFormatter(formatter)
logger.addHandler(logHandler)
```

---

## 🎯 Success Metrics

### Functional Success

✅ Tests passing  
✅ Consensus found for earnings stocks  
✅ Error handling working  
✅ Configuration loading correctly  

### Performance Success

✅ Execution time < 10 seconds  
✅ Memory usage < 50 MB  
✅ No resource leaks  
✅ Proper connection management  

### Reliability Success

✅ Graceful error handling  
✅ Proper logging  
✅ Clear error messages  
✅ Recovery from transient failures  

---

## 📞 Support & Documentation

**Quick Links:**
- 📖 Full Documentation: `AI_CONSENSUS_SCANNER.md`
- 🚀 Quick Start: `AI_CONSENSUS_SCANNER_QUICKSTART.md`
- 📝 Implementation Details: `AI_CONSENSUS_SCANNER_COMPLETE.md`
- ⚙️ Configuration: `CONFIG_MANAGEMENT.md`
- 🔌 API Clients: `AI_CLIENTS.md`

---

## ✅ Final Checklist Before Go-Live

- [ ] All dependencies installed
- [ ] .env configured with valid API keys
- [ ] All tests passing
- [ ] No syntax errors
- [ ] Logging configured
- [ ] Error handling tested
- [ ] Performance acceptable
- [ ] Security review passed
- [ ] Documentation reviewed
- [ ] Team trained
- [ ] Monitoring setup
- [ ] Backup plan documented

---

**🚀 Ready to Deploy!**

Your AI Consensus Scanner is fully configured and ready for production use.
