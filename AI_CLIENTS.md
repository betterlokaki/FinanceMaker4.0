"""
AI API CLIENTS - GROK & GEMINI IMPLEMENTATION
==============================================

## Overview

Implemented two professional AI client implementations:
1. **GrokClient** - X.ai Grok API wrapper
2. **GeminiClient** - Google Gemini API wrapper

Both follow:
- Same abstract base class (GPTBase)
- Consistent async/await patterns
- OpenAI-compatible Chat Completions API
- Configuration injection from settings
- Proper error handling and logging

## Architecture

### Abstract Base Class (gpt_base.py)

```python
class GPTBase(ABC):
    def __init__(self, http_client: Optional[httpx.AsyncClient] = None):
        self._http_client = http_client
        
    @abstractmethod
    async def generate_text(self, prompt: str) -> str:
        """Generate text based on the given prompt."""
        pass
```

### Concrete Implementations

Both GrokClient and GeminiClient:
1. Inherit from GPTBase
2. Implement generate_text() method
3. Load config from settings
4. Manage HTTP client lifecycle
5. Handle API responses
6. Provide comprehensive logging

## GrokClient Implementation

### Configuration (from config.yaml):

```yaml
grok:
  base_url: "https://api.x.ai/v1"
  model: "grok-beta"
  timeout: 30.0
  max_tokens: 1000
  # api_key: loaded from .env (GROK_API_KEY)
```

### API Endpoint:

```
POST https://api.x.ai/v1/chat/completions
Authorization: Bearer {GROK_API_KEY}
Content-Type: application/json
```

### Request Format:

```json
{
  "model": "grok-beta",
  "messages": [
    {
      "role": "user",
      "content": "Your prompt here"
    }
  ],
  "max_tokens": 1000,
  "temperature": 0.7
}
```

### Response Format:

```json
{
  "choices": [
    {
      "message": {
        "content": "Generated text response"
      }
    }
  ]
}
```

### Usage Example:

```python
from common.di_container import container

async def use_grok():
    grok = container.grok_client()
    response = await grok.generate_text("Explain quantum computing")
    print(response)
```

### Features:

✅ Validates API key is configured
✅ Automatic HTTP client management
✅ Proper error handling and logging
✅ Configurable model and max_tokens
✅ Supports custom HTTP client injection

## GeminiClient Implementation

### Configuration (from config.yaml):

```yaml
gemini:
  base_url: "https://generativelanguage.googleapis.com/v1beta/openai/"
  model: "gemini-2.0-flash"
  timeout: 30.0
  max_tokens: 1000
  # api_key: loaded from .env (GEMINI_API_KEY)
```

### API Endpoint:

```
POST https://generativelanguage.googleapis.com/v1beta/openai/chat/completions
Authorization: Bearer {GEMINI_API_KEY}
Content-Type: application/json
```

### Request Format:

Same as Grok (OpenAI-compatible):

```json
{
  "model": "gemini-2.0-flash",
  "messages": [
    {
      "role": "user",
      "content": "Your prompt here"
    }
  ],
  "max_tokens": 1000,
  "temperature": 0.7
}
```

### Response Format:

Same as Grok (OpenAI-compatible):

```json
{
  "choices": [
    {
      "message": {
        "content": "Generated text response"
      }
    }
  ]
}
```

### Usage Example:

```python
from common.di_container import container

async def use_gemini():
    gemini = container.gemini_client()
    response = await gemini.generate_text("Write a poem about technology")
    print(response)
```

### Features:

✅ Validates API key is configured
✅ Uses OpenAI-compatible endpoint
✅ Automatic HTTP client management
✅ Proper error handling and logging
✅ Configurable model and max_tokens
✅ Supports custom HTTP client injection

## Dependency Injection Integration

Both clients are registered in the DI container as singletons:

```python
class Container(containers.DeclarativeContainer):
    grok_client = providers.Singleton(
        GrokClient,
        http_client=http_client,
    )
    
    gemini_client = providers.Singleton(
        GeminiClient,
        http_client=http_client,
    )
```

### Usage from Container:

```python
from common.di_container import container

# Get singleton instances
grok = container.grok_client()
gemini = container.gemini_client()

# Both share the same HTTP client
# Both are singletons - same instance on each call
```

## Error Handling

### Configuration Errors:

```python
# If API key not configured:
try:
    client = container.grok_client()
except ValueError as e:
    print(e)
    # ValueError: Grok API key not configured...
```

### API Errors:

```python
# If API request fails:
try:
    response = await grok.generate_text("prompt")
except httpx.HTTPStatusError as e:
    print(f"API Error: {e.response.status_code}")
except ValueError as e:
    print(f"Response Format Error: {e}")
```

## Logging

Both clients provide detailed logging:

```
INFO: Calling Grok API with model: grok-beta
DEBUG: Generated text length: 456
ERROR: Error generating text with Grok: connection timeout
```

## Features Comparison

| Feature | Grok | Gemini |
|---------|------|--------|
| API Type | OpenAI-compatible | OpenAI-compatible |
| Base URL | api.x.ai | generativelanguage.googleapis.com |
| Default Model | grok-beta | gemini-2.0-flash |
| Auth | Bearer token | Bearer token |
| Max Tokens | Configurable | Configurable |
| Temperature | 0.7 | 0.7 |
| Timeout | From config | From config |
| HTTP Client | Shared | Shared |
| Validation | Full | Full |
| Logging | Full | Full |

## Implementation Notes

### Design Decisions:

1. **Abstract Base Class**: Allows easy addition of new AI providers
2. **Shared HTTP Client**: Efficiency through DI container
3. **Configuration Injection**: All settings from config, not hardcoded
4. **OpenAI Format**: Consistency with widely-used API format
5. **Singleton Pattern**: One instance per service (no redundant connections)

### Code Quality:

✅ Type hints throughout
✅ Comprehensive docstrings
✅ Proper async/await usage
✅ Error handling with logging
✅ Resource cleanup (HTTP client)
✅ Configuration validation
✅ Security (API keys in .env)

## Future Enhancements

1. **Streaming Responses**: Support streaming text generation
2. **Token Counting**: Count tokens before sending
3. **Retry Logic**: Exponential backoff for API errors
4. **Rate Limiting**: Respect API rate limits
5. **Caching**: Cache responses for identical prompts
6. **Batch Processing**: Process multiple prompts efficiently
7. **Cost Tracking**: Track API usage and costs
8. **Additional Providers**: OpenAI, Claude, etc.

## Testing

Example test with mocked HTTP client:

```python
from unittest.mock import AsyncMock
from common.di_container import container

async def test_grok_client():
    # Create mock response
    mock_client = AsyncMock()
    mock_response = {
        "choices": [{"message": {"content": "test response"}}]
    }
    mock_client.post.return_value.json.return_value = mock_response
    
    # Override HTTP client
    container.http_client.override(mock_client)
    
    try:
        grok = container.grok_client()
        result = await grok.generate_text("test")
        assert result == "test response"
    finally:
        container.http_client.reset_override()
```

## Security Considerations

✅ API keys in .env (not in code or config.yaml)
✅ .env in .gitignore (not committed)
✅ Configuration validation (prevents invalid values)
✅ HTTPS only (httpx enforces by default)
✅ Bearer token authentication (secure)
✅ Error messages don't leak API keys
✅ Timeout prevents hanging requests
"""
