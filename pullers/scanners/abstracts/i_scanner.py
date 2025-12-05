"""Scanner interface protocol."""
from typing import Protocol

from common.models.scanner_params import ScannerParams


class IScanner(Protocol):
    """Scanner interface - defines contract for all scanners.
    
    This protocol defines the interface that all scanner implementations
    must follow. Use this for type hints instead of concrete classes.
    """

    async def scan(self, params: ScannerParams) -> list[str]:
        """Scan for stocks matching the given parameters.
        
        Args:
            params: ScannerParams object containing scan configuration and filters.
            
        Returns:
            List of stock ticker symbols that match the scan criteria.
        """
        ...
