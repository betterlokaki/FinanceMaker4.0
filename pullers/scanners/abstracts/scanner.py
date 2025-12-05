"""Abstract scanner base class for stock scanning operations."""
from abc import ABC, abstractmethod

from common.models.scanner_params import ScannerParams


class ScannerBase(ABC):
    """Abstract base class for stock scanners.
    
    Provides an interface for implementing different scanning strategies
    that identify and return lists of stock tickers meeting specific criteria.
    
    All scanner implementations should inherit from this class and implement
    the scan method. This class implements the IScanner protocol.
    """

    @abstractmethod
    async def scan(self, params: ScannerParams) -> list[str]:
        """Scan for stocks matching the given parameters.
        
        Args:
            params: ScannerParams object containing scan configuration and filters.
            
        Returns:
            List of stock ticker symbols (strings) that match the scan criteria.
        """
        pass

