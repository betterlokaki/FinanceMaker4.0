"""Scanner parameters model."""
from dataclasses import dataclass
from typing import Optional, Dict, Any


@dataclass
class ScannerParams:
    """Parameters for scanner operations.
    
    Attributes:
        name: The name/identifier of the scanner.
        filters: Optional dictionary of filters to apply during scanning.
        limit: Optional limit on number of results to return.
        config: Optional additional configuration parameters.
    """
    name: str
    filters: Optional[Dict[str, Any]] = None
    limit: Optional[int] = None
    config: Optional[Dict[str, Any]] = None
