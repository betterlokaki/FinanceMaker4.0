"""Abstract scanner class for stock scanning operations."""
from abc import ABC, abstractmethod
from typing import List

from common.models.scanner_params import ScannerParams


class Scanner(ABC):
    """Abstract base class for stock scanners.
    
    Provides an interface for implementing different scanning strategies
    that identify and return lists of stock tickers meeting specific criteria.
    """

    @abstractmethod
    async def scan(self, params: ScannerParams) -> List[str]:
        """Scan for stocks matching the given parameters.
        
        Args:
            params: ScannerParams object containing scan configuration and filters.
            
        Returns:
            List of stock ticker symbols (strings) that match the scan criteria.
            
        Raises:
            NotImplementedError: This is an abstract method and must be implemented
                by subclasses.
        """
        pass
