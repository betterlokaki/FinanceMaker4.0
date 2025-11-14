# AI Consensus Scanner Documentation

## Overview

The **EarningTomorrowAI** scanner is a sophisticated multi-stage intelligence system that combines stock market data with AI analysis to provide high-confidence trading recommendations. It uses a consensus approach where only tickers suggested by **both** Grok and Gemini AI providers are returned.

## Architecture

### Workflow

```
┌─────────────────────────────────────────────────────────────────┐
│ 1. Get Earnings Tickers                                         │
│    └─ EarningTomorrow scanner retrieves stocks earning tomorrow │
└──────────────────────┬──────────────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────────────┐
│ 2. Send to AI Providers (Parallel)                              │
│    ├─ Grok: "From following tickers: {TICKERS} ..."           │
│    └─ Gemini: "From following tickers: {TICKERS} ..."         │
└──────────────────────┬──────────────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────────────┐
│ 3. Extract Tickers from AI Responses                           │
│    ├─ Grok suggestions: {AAPL, MSFT, TSLA, ...}              │
│    └─ Gemini suggestions: {AAPL, MSFT, NVDA, ...}            │
└──────────────────────┬──────────────────────────────────────────┘
                       │
┌──────────────────────▼──────────────────────────────────────────┐
│ 4. Find Consensus (Intersection)                               │
│    └─ Return only: {AAPL, MSFT}  (Both AIs suggested)         │
└──────────────────────┬──────────────────────────────────────────┘
                       │
                       ▼
              Final High-Confidence List
```

## Components

### EarningTomorrowAI Scanner

**Location**: `pullers/scanners/ai_scanners/earning_tommrow_ai.py`

**Main Methods**:

#### `async scan() -> List[str]`
Main entry point orchestrating the entire workflow:
1. Retrieves earnings tickers from EarningTomorrow scanner
2. Queries both Grok and Gemini AI providers
3. Extracts ticker suggestions from both responses
4. Returns only tickers suggested by both AIs (consensus)

#### `async _get_earnings_tickers() -> List[str]`
Gets the list of stocks that have earnings announcements tomorrow.

#### `async _get_ai_suggestions(ticker_list: List[str], ai_name: str) -> str`
Sends tickers to specified AI provider with configurable prompt template. Handles API communication and error handling.

#### `_extract_tickers_from_response(response: str, valid_tickers: List[str]) -> Set[str]`
Extracts ticker symbols from AI response using regex pattern matching. Only includes tickers from the original valid list.

**Regex Pattern**: `r'\b([A-Z]{1,5}(?:\.[A-Z]{1,2})?)(?:\s|,|\.|\n|$)'`
- Matches 1-5 uppercase letters (ticker symbol)
- Optional period followed by 1-2 letters (exchange suffix like .L for London)
- Word boundary protection

#### `_find_consensus(grok_suggestions: Set[str], gemini_suggestions: Set[str]) -> List[str]`
Finds intersection of both AI suggestions using set intersection operation.

## Configuration

### Settings Structure

```python
class AIScannerConfig(BaseSettings):
    prompt_template: str = "From following tickers: {TICKERS}\n\n..."
    extraction_method: str = "regex"  # For future expansion
```

### Configuration File (config.yaml)

```yaml
ai_scanner:
  prompt_template: |
    From following tickers: {TICKERS}
    
    Which ones do you suggest for trading today? 
    Please provide only the ticker symbols, one per line.
  extraction_method: regex
```

### How to Customize

1. Edit `config.yaml` under the `ai_scanner` section
2. Modify `prompt_template` to change AI instructions
3. Both Grok and Gemini receive the same prompt
4. Tickers are injected into `{TICKERS}` placeholder

## Dependency Injection

The scanner is registered in the DI container as a singleton:

```python
container = Container()
container.earning_tomorrow_ai_scanner = singleton(
    EarningTomorrowAI,
    http_client=container.http_client,
    earnings_scanner=container.earning_tomorrow_scanner,
)
```

### Dependencies Injected

- `http_client`: Async HTTP client for API calls
- `earnings_scanner`: EarningTomorrow scanner instance
- `grok_client`: Resolved at runtime from container (lazy)
- `gemini_client`: Resolved at runtime from container (lazy)

## Usage

### Basic Usage

```python
from common.di_container import container

# Get scanner from DI container
ai_scanner = container.earning_tomorrow_ai_scanner()

# Run consensus analysis
consensus_tickers = await ai_scanner.scan()
print(f"Consensus tickers: {consensus_tickers}")
# Output: Consensus tickers: ['AAPL', 'MSFT']
```

### With Custom Configuration

```python
from common.settings import settings

# View current prompt template
print(settings.ai_scanner.prompt_template)

# Change via environment variable
import os
os.environ['AI_SCANNER_PROMPT_TEMPLATE'] = "Your custom prompt..."
```

## Error Handling

The scanner gracefully handles several error scenarios:

### Missing API Keys
- If Grok API key missing: logs warning, continues with Gemini-only analysis
- If Gemini API key missing: logs warning, continues with Grok-only analysis
- If both missing: raises informative error with instructions

### Network Errors
- Timeouts handled with configured HTTP timeout
- Connection errors logged with full context
- Graceful failure with meaningful error messages

### Invalid AI Responses
- Empty responses handled gracefully
- Malformed JSON handled safely
- No tickers extracted → empty list returned

### Logging Example
```
INFO: Starting AI consensus analysis for 5 earnings tickers
INFO: Sending to Grok AI...
INFO: Grok response received: 234 characters
INFO: Extracted Grok suggestions: {'AAPL', 'MSFT', 'TSLA'}
INFO: Sending to Gemini AI...
INFO: Gemini response received: 189 characters
INFO: Extracted Gemini suggestions: {'AAPL', 'MSFT', 'NVDA'}
INFO: Consensus found: {'AAPL', 'MSFT'}
INFO: Returning 2 consensus tickers
```

## Ticker Extraction Examples

### Example 1: Newline-separated Response
```
Grok Response:
AAPL
MSFT
TSLA
GOOGL

Extracted: {'AAPL', 'MSFT', 'TSLA', 'GOOGL'}
```

### Example 2: Comma-separated Response
```
Gemini Response:
I suggest AAPL, MSFT, NVDA

Extracted: {'AAPL', 'MSFT', 'NVDA'}
```

### Example 3: Mixed Format with Explanations
```
Response:
AAPL - Strong earnings expected
MSFT showing good momentum
NVDA is positioned well

Extracted: {'AAPL', 'MSFT', 'NVDA'}
```

### Example 4: Ticker with Exchange Suffix
```
Response:
SHELL.L (Royal Dutch Shell - London listing)
ASML

Extracted: {'SHELL.L', 'ASML'}
```

## Consensus Logic

### Set Intersection Example

```python
grok_suggestions = {'AAPL', 'MSFT', 'TSLA', 'GOOGL'}
gemini_suggestions = {'AAPL', 'MSFT', 'AMZN', 'NVDA'}

consensus = grok_suggestions.intersection(gemini_suggestions)
# Result: {'AAPL', 'MSFT'}
```

**Why Consensus?**
- ✅ Higher confidence: Both independent AIs agree
- ✅ Reduced false positives: Filters out isolated suggestions
- ✅ Better signal-to-noise: Only strong signals pass through
- ✅ Diversified analysis: Combines different AI models' perspectives

## Performance Characteristics

### Timing
- **Data Fetch**: ~1-2 seconds (EarningTomorrow scanning)
- **AI Query (Grok)**: ~2-5 seconds (API call + response)
- **AI Query (Gemini)**: ~2-5 seconds (API call + response)
- **Parallel Processing**: ~4-8 seconds total (both AIs queried in parallel)
- **Total Execution**: ~5-10 seconds end-to-end

### Resource Usage
- **Memory**: ~5-10 MB (reasonable for application)
- **Network Requests**: 3 outbound (EarningTomorrow + Grok + Gemini)
- **Connection Pooling**: HTTP client reuses connections efficiently

## Testing

### Run Test Suite

```bash
cd /Users/shaharrozolio/Documents/Code/Projects/Python/FinanceMaker4.0
python test_ai_scanner.py
```

### Test Coverage

1. **Configuration Tests**
   - Verifies AI scanner config loads correctly
   - Checks prompt template exists
   - Validates extraction method setting

2. **DI Container Tests**
   - Confirms all services available in container
   - Gracefully handles missing API keys
   - Validates service registration

3. **Initialization Tests**
   - Verifies scanner instantiates properly
   - Checks dependency injection works
   - Validates logger setup

4. **Ticker Extraction Tests** (3 scenarios)
   - **Test 1**: Newline-separated tickers
   - **Test 2**: Comma-separated tickers
   - **Test 3**: Mixed format with various separators

5. **Consensus Finding Tests**
   - Validates set intersection logic
   - Tests consensus with realistic data
   - Verifies only shared tickers returned

### Expected Test Output
```
✅ Configuration loaded with prompt template and extraction method
✅ All DI container services available
✅ AI scanner initialized successfully
✅ Ticker extraction test 1 (newline): {'GOOGL', 'NVDA', 'TSLA', 'MSFT', 'AAPL'}
✅ Ticker extraction test 2 (comma): {'GOOGL', 'MSFT', 'AAPL'}
✅ Ticker extraction test 3 (mixed): {'MSFT', 'NVDA', 'AAPL'}
✅ Consensus finding: {'AAPL', 'MSFT'} correctly found
✅ ALL TESTS PASSED
```

## Advanced Configuration

### Custom Prompt Engineering

Edit `config.yaml`:

```yaml
ai_scanner:
  prompt_template: |
    You are a stock trading advisor. Given the following earnings stocks,
    identify the 3 most promising for swing trading based on:
    - Momentum indicators
    - Volatility profile
    - Technical support levels
    
    Tickers: {TICKERS}
    
    Respond with only ticker symbols, one per line.
```

### Future Extensibility

The `extraction_method` setting allows for future expansion:

```python
if self._config.extraction_method == "regex":
    # Current implementation
    tickers = self._extract_tickers_from_response(response, valid_tickers)
elif self._config.extraction_method == "structured":
    # Future: Parse structured JSON from AI
    tickers = self._parse_structured_response(response)
elif self._config.extraction_method == "ml_classifier":
    # Future: ML-based extraction
    tickers = self._ml_extract_tickers(response)
```

## Integration with main.py

### Example Integration

```python
import asyncio
from common.di_container import container

async def main():
    # Get scanner from container
    ai_consensus_scanner = container.earning_tomorrow_ai_scanner()
    
    # Run analysis
    high_confidence_tickers = await ai_consensus_scanner.scan()
    
    if high_confidence_tickers:
        print(f"🎯 High-Confidence Consensus Tickers: {high_confidence_tickers}")
        # Use tickers for trading decisions, alerts, etc.
    else:
        print("⚠️  No consensus found between Grok and Gemini")

if __name__ == "__main__":
    asyncio.run(main())
```

## Troubleshooting

### Issue: Empty Results Despite Earnings Data

**Cause**: AIs may not extract tickers clearly
**Solution**: 
1. Review AI responses in logs
2. Adjust prompt template for clearer instructions
3. Add output format specification to prompt

### Issue: API Rate Limiting

**Cause**: Hitting AI provider rate limits
**Solution**:
1. Add backoff retry logic
2. Batch requests across time
3. Use provider's recommended limits

### Issue: Missing Consensus Results

**Cause**: Grok and Gemini strongly disagree
**Solution**:
1. Review individual AI suggestions in logs
2. Consider lowering consensus threshold to "majority"
3. Adjust prompt for more alignment

### Issue: Ticker Extraction Missing Valid Tickers

**Cause**: Regex pattern doesn't match format
**Solution**:
1. Add debug logging to see response format
2. Update regex pattern for edge cases
3. Consider ML-based extraction for robustness

## Performance Optimization Tips

1. **Cache earnings data** if querying multiple times per day
2. **Batch multiple prompts** if analyzing different criteria
3. **Use connection pooling** (already implemented in HTTP client)
4. **Set appropriate timeouts** in configuration (balance between reliability and speed)

## Security Considerations

- ✅ API keys stored in `.env` (not in code)
- ✅ HTTPS connections enforced for all API calls
- ✅ User agent headers prevent detection as bot
- ✅ Timeouts prevent indefinite hanging
- ✅ Sensitive data not logged
- ✅ Input validation for ticker extraction

## File Structure

```
pullers/scanners/ai_scanners/
├── __init__.py                 # Module exports
└── earning_tommrow_ai.py       # Main scanner implementation

Configuration:
├── config.yaml                 # Prompt template & settings
├── common/settings.py          # AIScannerConfig class

Tests:
└── test_ai_scanner.py          # Comprehensive test suite

Integration:
├── common/di_container.py      # DI registration
└── main.py                      # Main application entry
```

## API Dependencies

### External APIs Required

1. **Grok** (xAI)
   - Endpoint: OpenAI-compatible Chat Completions
   - Model: grok-2
   - API Key: `GROK_API_KEY` (from .env)

2. **Gemini** (Google)
   - Endpoint: OpenAI-compatible Chat Completions
   - Model: gemini-2.0-flash
   - API Key: `GEMINI_API_KEY` (from .env)

3. **EarningTomorrow** (Internal)
   - Scanner: Available in DI container
   - Returns: List of tickers with earnings tomorrow

## Version History

- **v1.0** (Current): Initial implementation with consensus approach
  - Dual AI provider analysis
  - Regex-based ticker extraction
  - Set intersection consensus finding
  - Full error handling and logging

## Related Documentation

- [DI Container Best Practices](DI_BEST_PRACTICES.md)
- [Configuration Management](CONFIG_MANAGEMENT.md)
- [AI Clients Documentation](AI_CLIENTS.md)
- [Project Structure](PROJECT_STRUCTURE.md)

## Support

For issues or enhancements:
1. Check logs for detailed error messages
2. Review test suite for expected behavior
3. Consult configuration validation
4. Verify API keys and network connectivity
