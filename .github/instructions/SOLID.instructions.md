---
applyTo: '**/*.py'
---

# SOLID Principles & Clean Code Guidelines

Follow these strict rules when generating, modifying, or reviewing Python code.

---

## 1. Single Responsibility Principle (SRP)

- **One class = One responsibility.** Each class must do exactly ONE thing.
- **One file = One class/interface/model.** Never put multiple classes in the same file.
- **Max 200 lines per file.** If a file exceeds this, split it into smaller focused modules.
- **Method length: ≤ 20 lines.** If longer, extract helper methods.

```python
# ✅ Correct - each class in its own file
# file: user_repository.py
class UserRepository:
    async def get_user(self, user_id: str) -> User: ...

# file: user_service.py  
class UserService:
    def __init__(self, repo: UserRepository) -> None: ...
```

```python
# ❌ Wrong - multiple classes in one file
class UserRepository: ...
class UserService: ...
class UserValidator: ...
```

---

## 2. Open/Closed Principle (OCP)

- **Open for extension, closed for modification.**
- Use **abstract base classes** and **protocols** for extensibility.
- New functionality = New class implementing existing interface, NOT modifying existing classes.

```python
# ✅ Correct - extend via new implementation
class EmailNotifier(Notifier):
    async def notify(self, message: str) -> None: ...

class SlackNotifier(Notifier):  # New feature = new class
    async def notify(self, message: str) -> None: ...
```

---

## 3. Liskov Substitution Principle (LSP)

- **Subtypes must be substitutable for their base types.**
- Child classes must honor the contract of their parent.
- Never override methods to do nothing or raise `NotImplementedError` for inherited behavior.

```python
# ✅ Correct - subclass honors contract
class PremiumScanner(ScannerBase):
    async def scan(self, params: ScannerParams) -> list[str]:
        # Must return list[str] as parent contract specifies
        return await self._advanced_scan(params)
```

---

## 4. Interface Segregation Principle (ISP)

- **Only BASE classes need an interface (Protocol or ABC).**
- Child classes that inherit from a base class do NOT need their own interface.
- Interfaces should be small and focused - clients shouldn't depend on methods they don't use.
- Prefer multiple small interfaces over one large interface.

### Interface Hierarchy Pattern

```
IScanner (Protocol)          ← Interface for type hints
    ↑
ScannerBase (ABC, IScanner)  ← Abstract base class EXPLICITLY implements interface
    ↑
FinvizScanner               ← Concrete implementation (NO separate interface needed)
    ↑
EarningTommrow              ← Child class (inherits from FinvizScanner, NO interface needed)
```

```python
# ✅ Correct - only base class has interface, and EXPLICITLY inherits it
# abstracts/i_scanner.py
class IScanner(Protocol):
    async def scan(self, params: ScannerParams) -> list[str]: ...

# abstracts/scanner_base.py
class ScannerBase(ABC, IScanner):  # MUST explicitly inherit the interface!
    @abstractmethod
    async def scan(self, params: ScannerParams) -> list[str]: ...

# implementations/finviz_scanner.py
class FinvizScanner(ScannerBase):  # NO separate IFinvizScanner needed
    async def scan(self, params: ScannerParams) -> list[str]: ...

# implementations/earning_tomorrow.py
class EarningTommrow(FinvizScanner):  # Inherits, NO interface needed
    pass

# ❌ Wrong - base class does NOT inherit the interface (implicit is NOT allowed)
class ScannerBase(ABC):  # MISSING IScanner inheritance!
    @abstractmethod
    async def scan(self, params: ScannerParams) -> list[str]: ...

# ❌ Wrong - creating interface for every class
class IFinvizScanner(Protocol): ...  # UNNECESSARY
class IEarningTommrow(Protocol): ...  # UNNECESSARY
```

```python
# ✅ Correct - separate focused interfaces
class Readable(Protocol):
    def read(self) -> str: ...

class Writable(Protocol):
    def write(self, data: str) -> None: ...

# ❌ Wrong - fat interface
class FileHandler(Protocol):
    def read(self) -> str: ...
    def write(self, data: str) -> None: ...
    def delete(self) -> None: ...
    def compress(self) -> bytes: ...
    def encrypt(self) -> bytes: ...
```

---

## 5. Dependency Inversion Principle (DIP)

- **Depend on abstractions, not concretions.**
- All dependencies must be injected via constructor.
- Type hints must reference interfaces/protocols, not concrete classes.
- Use the DI container - never instantiate dependencies directly.

```python
# ✅ Correct - depend on abstraction
class StockAnalyzer:
    def __init__(self, scanner: Scanner, ai_client: GPTBase) -> None:
        self._scanner = scanner
        self._ai_client = ai_client

# ❌ Wrong - depend on concrete class
class StockAnalyzer:
    def __init__(self) -> None:
        self._scanner = FinvizScanner()  # Hardcoded dependency
```

---

## 6. Type Hinting (MANDATORY)

- **EVERY variable, parameter, and return type MUST have type hints.**
- **No `Any` type allowed** unless absolutely unavoidable (document why).
- Use `list[T]`, `dict[K, V]`, `set[T]` (Python 3.9+ lowercase generics).
- Use `Optional[T]` or `T | None` for nullable types.
- Use `TypeVar` for generic classes.

```python
# ✅ Correct - full type hints
def calculate_average(prices: list[float]) -> float:
    total: float = sum(prices)
    count: int = len(prices)
    return total / count

async def fetch_tickers(
    scanner: Scanner,
    params: ScannerParams,
    timeout: float = 30.0
) -> list[str]:
    ...

# ❌ Wrong - missing type hints
def calculate_average(prices):
    total = sum(prices)
    return total / len(prices)
```

---

## 7. Directory Structure (MANDATORY)

### Main Feature Directory Pattern

Every feature/module MUST follow this exact structure:

```
feature_name/
├── __init__.py              # Export public interface only
├── abstracts/
│   ├── __init__.py
│   ├── i_feature.py         # Interface/Protocol (prefix with 'i_' or 'I')
│   └── feature_base.py      # Abstract base class (suffix with '_base' or 'Base')
├── implementations/
│   ├── __init__.py
│   └── concrete_feature.py  # Concrete implementation
├── models/
│   ├── __init__.py
│   └── feature_model.py     # Pydantic models / dataclasses
└── exceptions/
    ├── __init__.py
    └── feature_error.py     # Custom exceptions
```

### Example: Scanner Feature

```
scanners/
├── __init__.py
├── abstracts/
│   ├── __init__.py
│   ├── i_scanner.py              # Protocol definition
│   └── scanner_base.py           # Abstract base class
├── implementations/
│   ├── __init__.py
│   ├── finviz_scanner.py         # Finviz implementation
│   └── yahoo_scanner.py          # Yahoo implementation
├── models/
│   ├── __init__.py
│   └── scanner_params.py         # ScannerParams model
└── exceptions/
    ├── __init__.py
    └── scanner_error.py          # ScannerError, ScannerTimeoutError
```

### Common Library Rules

**ALL helper methods, utilities, and shared code MUST be in `common/` directory.**

```
common/
├── __init__.py
├── abstracts/
│   ├── __init__.py
│   └── i_http_client.py          # Shared interfaces
├── helpers/
│   ├── __init__.py
│   ├── string_helpers.py         # String utilities
│   ├── date_helpers.py           # Date utilities
│   └── validation_helpers.py     # Validation utilities
├── models/
│   ├── __init__.py
│   └── base_model.py             # Base Pydantic model
├── exceptions/
│   ├── __init__.py
│   └── base_error.py             # Base exception class
├── decorators/
│   ├── __init__.py
│   └── retry_decorator.py        # Retry logic
├── di_container.py               # Dependency injection container
└── settings.py                   # Application settings
```

### Rules:

| Rule | Description |
|------|-------------|
| **Helpers in `common/helpers/`** | ALL utility functions (string parsing, date formatting, validation) |
| **Shared models in `common/models/`** | Base classes, shared DTOs, common types |
| **Shared exceptions in `common/exceptions/`** | Base exceptions used across features |
| **No utils scattered in features** | Move ANY reusable code to `common/` |
| **Feature-specific only in feature** | Only domain-specific code stays in feature directory |

```python
# ✅ Correct - helper in common
# common/helpers/ticker_helpers.py
def extract_tickers_from_text(text: str) -> list[str]: ...

# ✅ Correct - use from common
from common.helpers.ticker_helpers import extract_tickers_from_text

# ❌ Wrong - helper inside feature
# scanners/implementations/utils.py  <- WRONG LOCATION
def extract_tickers_from_text(text: str) -> list[str]: ...
```

---

## 8. Dependency Injection with `dependency-injector` (MANDATORY)

Use the `dependency-injector` library for C#-style dependency injection in Python.

### Installation

```bash
pip install dependency-injector
```

### Container Configuration Pattern

```python
# common/di_container.py
from dependency_injector import containers, providers
from dependency_injector.wiring import Provide, inject

class Container(containers.DeclarativeContainer):
    """Application DI container - similar to C# IServiceCollection."""
    
    # Wiring configuration - modules that use @inject
    wiring_config = containers.WiringConfiguration(
        modules=["main", "app.services"]
    )
    
    # Configuration provider
    config = providers.Configuration()
    
    # Singleton services (like C# AddSingleton)
    http_client = providers.Singleton(
        HttpClient,
        timeout=config.http.timeout,
    )
    
    # Transient services (like C# AddTransient) 
    request_handler = providers.Factory(
        RequestHandler,
        client=http_client,
    )
    
    # Scoped-like behavior with Resource
    database_session = providers.Resource(
        create_db_session,
        connection_string=config.database.connection_string,
    )
```

### Service Registration Types

| Python (`dependency-injector`) | C# Equivalent | Use Case |
|--------------------------------|---------------|----------|
| `providers.Singleton` | `AddSingleton` | One instance for app lifetime (HTTP clients, configs) |
| `providers.Factory` | `AddTransient` | New instance every time (handlers, processors) |
| `providers.Resource` | `AddScoped` | Managed lifecycle with cleanup (DB sessions) |
| `providers.ThreadLocalSingleton` | Thread-scoped | Thread-safe singletons |

### Dependency Injection Patterns

#### Pattern 1: Constructor Injection via Container (PREFERRED)

```python
# abstracts/i_scanner.py
from abc import ABC, abstractmethod

class IScanner(ABC):
    @abstractmethod
    async def scan(self, params: ScannerParams) -> list[str]: ...

# implementations/finviz_scanner.py
class FinvizScanner(IScanner):
    def __init__(self, http_client: IHttpClient) -> None:
        self._http_client = http_client
    
    async def scan(self, params: ScannerParams) -> list[str]: ...

# di_container.py
class Container(containers.DeclarativeContainer):
    http_client = providers.Singleton(HttpClient)
    
    scanner = providers.Singleton(
        FinvizScanner,
        http_client=http_client,  # Injected automatically
    )
```

#### Pattern 2: @inject Decorator (for functions/methods)

```python
from dependency_injector.wiring import inject, Provide

@inject
async def process_stocks(
    scanner: IScanner = Provide[Container.scanner],
    ai_client: IGPTClient = Provide[Container.ai_client],
) -> list[str]:
    tickers = await scanner.scan(params)
    return await ai_client.analyze(tickers)
```

#### Pattern 3: Override for Testing (like C# mock injection)

```python
# test_scanner.py
from unittest.mock import AsyncMock

def test_scanner():
    mock_http = AsyncMock(spec=IHttpClient)
    
    with container.http_client.override(mock_http):
        scanner = container.scanner()
        # scanner now uses mock_http
```

### Container Best Practices

```python
# ✅ Correct - interfaces in type hints
class Container(containers.DeclarativeContainer):
    scanner: providers.Provider[IScanner] = providers.Singleton(
        FinvizScanner,
        http_client=http_client,
    )

# ✅ Correct - get from container
scanner = container.scanner()

# ❌ Wrong - direct instantiation
scanner = FinvizScanner(http_client)

# ❌ Wrong - importing concrete class in business logic
from scanners.implementations.finviz_scanner import FinvizScanner
```

### Full Container Example

```python
"""common/di_container.py - Application DI Container"""
from dependency_injector import containers, providers

from common.settings import Settings
from common.abstracts.i_http_client import IHttpClient
from common.implementations.http_client import HttpClient
from scanners.abstracts.i_scanner import IScanner
from scanners.implementations.finviz_scanner import FinvizScanner
from gpt.abstracts.i_gpt_client import IGPTClient
from gpt.implementations.grok_client import GrokClient
from gpt.implementations.gemini_client import GeminiClient


class Container(containers.DeclarativeContainer):
    """
    Application Dependency Injection Container.
    
    Follows C# IServiceCollection/IServiceProvider pattern.
    All services registered here, retrieved via container.service_name().
    """
    
    # Configuration
    config = providers.Singleton(Settings)
    
    # Infrastructure Layer
    http_client: providers.Provider[IHttpClient] = providers.Singleton(
        HttpClient,
        timeout=30.0,
    )
    
    # Scanner Layer (depends on Infrastructure)
    scanner: providers.Provider[IScanner] = providers.Singleton(
        FinvizScanner,
        http_client=http_client,
    )
    
    # AI Layer (depends on Infrastructure)
    grok_client: providers.Provider[IGPTClient] = providers.Singleton(
        GrokClient,
        http_client=http_client,
    )
    
    gemini_client: providers.Provider[IGPTClient] = providers.Singleton(
        GeminiClient,
        http_client=http_client,
    )


# Global container instance
container = Container()
```

---

## 9. Naming Conventions

| Type | Convention | Example |
|------|------------|---------|
| Interface (Protocol) | Prefix with `I` | `IScanner`, `IGPTClient`, `IHttpClient` |
| Abstract Base Class | Suffix with `Base` | `ScannerBase`, `GPTBase` |
| Implementation | Descriptive name | `FinvizScanner`, `GrokClient` |
| Model | Noun | `User`, `ScannerParams`, `StockData` |
| Exception | Suffix with `Error` | `ValidationError`, `ApiConnectionError` |
| Helper module | Suffix with `_helpers` | `string_helpers.py`, `date_helpers.py` |
| Private method | Prefix with `_` | `_parse_response()` |
| Private attribute | Prefix with `_` | `self._http_client` |

---

## 10. Additional Rules

### Immutability
- Prefer immutable data structures (`frozen=True` in dataclasses, `Frozen` Pydantic models).
- Use `Final` for constants.

```python
from typing import Final

MAX_RETRIES: Final[int] = 3
```

### Error Handling
- Create custom exceptions for domain-specific errors.
- Each exception class in its own file.
- Never catch bare `Exception` unless re-raising.

### Async Consistency
- If a class has async methods, ALL I/O methods should be async.
- Never mix sync and async I/O in the same class.

### Constructor Rules
- Constructors should only assign dependencies.
- No logic, no I/O, no initialization in `__init__`.
- Use factory methods or async `create()` for complex initialization.

```python
# ✅ Correct
class DataProcessor:
    def __init__(self, client: IHttpClient, config: Config) -> None:
        self._client = client
        self._config = config

    @classmethod
    async def create(cls, client: IHttpClient, config: Config) -> "DataProcessor":
        instance = cls(client, config)
        await instance._initialize()
        return instance
```

---

## 11. Code Smells to Avoid

| Smell | Rule |
|-------|------|
| God Class | Split into focused classes |
| Long Parameter List | Use parameter objects (dataclass/Pydantic model) |
| Feature Envy | Move method to the class it uses most |
| Primitive Obsession | Create value objects |
| Magic Numbers | Use named constants with `Final` |
| Commented-out Code | Delete it (use git history) |
| Dead Code | Delete unreachable code |
| Helper in feature folder | Move to `common/helpers/` |
| Direct instantiation | Use DI container |

---

## 12. Interface Definition Rules

Only **base classes** need interfaces. Child classes inherit from base and do NOT need separate interfaces.

### Using Protocol (Preferred for type hints)

```python
# abstracts/i_scanner.py
from typing import Protocol

class IScanner(Protocol):
    """Scanner interface - defines contract for all scanners."""
    
    async def scan(self, params: ScannerParams) -> list[str]:
        """Scan and return list of ticker symbols."""
        ...
```

### Using ABC (For shared implementation)

```python
# abstracts/scanner_base.py
from abc import ABC, abstractmethod

class ScannerBase(ABC):
    """Abstract base class with shared scanner logic.
    
    Implements IScanner protocol. All scanner implementations
    should inherit from this class.
    """
    
    @abstractmethod
    async def scan(self, params: ScannerParams) -> list[str]:
        """Scan and return list of ticker symbols."""
        ...
```

### Complete Interface Hierarchy

```python
# 1. Interface (Protocol) - for type hints in DI and function signatures
class IScanner(Protocol): ...

# 2. Base Class (ABC) - implements interface, provides shared logic
class ScannerBase(ABC): ...

# 3. Concrete Implementation - inherits from base
class FinvizScanner(ScannerBase): ...

# 4. Specialized Implementation - inherits from concrete (NO interface needed)
class EarningTommrow(FinvizScanner): ...
```

---

## Summary Checklist

Before submitting code, verify:

- [ ] Each class is in its own file
- [ ] Base classes have interfaces (Protocol); child classes do NOT need separate interfaces
- [ ] Each class has exactly ONE responsibility
- [ ] ALL variables, params, returns have type hints
- [ ] Dependencies are injected via DI container, not instantiated
- [ ] No method exceeds 20 lines
- [ ] No file exceeds 200 lines
- [ ] All I/O is async-consistent
- [ ] Helper methods are in `common/helpers/`
- [ ] Directory follows `abstracts/` + `implementations/` pattern
- [ ] Interfaces use `I` prefix (IScanner, IHttpClient)
- [ ] Container uses `providers.Singleton` or `providers.Factory`