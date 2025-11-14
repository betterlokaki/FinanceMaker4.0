"""
PROFESSIONAL DEPENDENCY INJECTION PATTERN
==========================================

## Overview
Implemented a professional-grade dependency injection container using the 
`dependency-injector` library - the Python equivalent to C# DI containers.

This follows the same principles as C#:
- ✅ Never use `new` for singletons
- ✅ Container manages all lifecycle
- ✅ Request instances from container
- ✅ Centralized configuration

## Architecture

### Container Definition (common/di_container.py)

Uses `dependency-injector` with DeclarativeContainer pattern:

```python
class Container(containers.DeclarativeContainer):
    # All services defined as providers
    http_client = providers.Singleton(...)
    finviz_scanner = providers.Singleton(...)
    user_agent_manager = providers.Singleton(...)
```

### Provider Types

1. **Singleton**: Created once, reused everywhere
   ```python
   http_client = providers.Singleton(httpx.AsyncClient, ...)
   ```
   - Used for: HTTP clients, database connections, configuration, services

2. **Factory**: Creates new instance each time
   ```python
   scanner = providers.Factory(...)
   ```
   - Used for: Stateless operations, temporary objects

### Service Registration

Each service is registered declaratively:

```python
finviz_scanner = providers.Singleton(
    FinvizScanner,
    http_client=http_client,  # Dependency injection - container injects
)
```

## Usage

### In Application Code (main.py)

```python
from common.di_container import container

# ✅ CORRECT: Request from container
finviz_scanner = container.finviz_scanner()
http_client = container.http_client()

# ❌ WRONG: Never instantiate directly
# scanner = FinvizScanner(http_client)  # DO NOT DO THIS
```

### Benefits

1. **Centralized Configuration**: All services defined in one place
2. **Single Responsibility**: Change implementation without changing code
3. **Testing**: Easy to mock/replace dependencies
4. **Lifecycle Management**: Container handles creation and cleanup
5. **Type Safety**: Static container definition

## Comparison: Manual vs DI Container

### Manual (Bad Practice)
```python
# Anti-pattern: Creating singletons manually
class App:
    _http_client = None
    
    @classmethod
    def get_http_client(cls):
        if cls._http_client is None:
            cls._http_client = httpx.AsyncClient(...)
        return cls._http_client

# Problem: Scattered across codebase, hard to manage
```

### DI Container (Best Practice)
```python
# Professional: Container manages everything
class Container(containers.DeclarativeContainer):
    http_client = providers.Singleton(httpx.AsyncClient, ...)

# Usage: Simple and clear
client = container.http_client()
```

## Adding New Services

### Step 1: Define in container (common/di_container.py)
```python
class Container(containers.DeclarativeContainer):
    # Add new service
    my_service = providers.Singleton(
        MyService,
        dependency1=http_client,
        dependency2=config,
    )
```

### Step 2: Use in application
```python
from common.di_container import container

my_service = container.my_service()
result = await my_service.do_something()
```

## Dependency Resolution

The container automatically resolves dependencies:

```python
# This works automatically:
finviz_scanner = providers.Singleton(
    FinvizScanner,
    http_client=http_client,  # Container injects this
)
```

When you call `container.finviz_scanner()`, the container:
1. Checks if http_client singleton exists (creates if not)
2. Passes http_client to FinvizScanner constructor
3. Returns the singleton instance

## Configuration (Future)

Add configuration easily:

```python
class Container(containers.DeclarativeContainer):
    config = providers.Configuration()
    
    http_client = providers.Singleton(
        httpx.AsyncClient,
        timeout=config.http.timeout,  # Load from config
    )

# Usage
container.config.from_yaml('config.yaml')
client = container.http_client()
```

## Testing with DI

```python
from unittest.mock import AsyncMock
from common.di_container import container

async def test_scanner():
    # Create mock
    mock_client = AsyncMock()
    
    # Override dependency
    container.http_client.override(mock_client)
    
    try:
        scanner = container.finviz_scanner()
        # Test with mock
        await scanner.scan(params)
    finally:
        # Reset to original
        container.http_client.reset_override()
```

## Summary

✅ Implements professional Python DI patterns equivalent to C# containers
✅ Never use `new` for managed services
✅ Container handles all lifecycle management
✅ Centralized service configuration
✅ Easy to test and maintain
✅ Scales with project complexity
"""
