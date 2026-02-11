"""Interface for earnings calendar data providers."""
from datetime import date
from typing import Protocol


class IEarningsCalendar(Protocol):
    """Protocol defining the interface for earnings calendar providers.
    
    Implementations must provide methods to retrieve ticker symbols
    that have earnings announcements on specific dates.
    """
    
    def get_earnings_on_date(self, target_date: date) -> list[str]:
        """Get all ticker symbols with earnings on a specific date.
        
        Args:
            target_date: The date to query for earnings announcements.
            
        Returns:
            List of ticker symbols with earnings on the target date.
        """
        ...
    
    def get_earnings_between(
        self,
        start_date: date,
        end_date: date,
    ) -> dict[date, list[str]]:
        """Get all ticker symbols with earnings in a date range.
        
        Args:
            start_date: Start of the date range (inclusive).
            end_date: End of the date range (inclusive).
            
        Returns:
            Dictionary mapping each date to its list of earnings tickers.
            Only dates with at least one earnings announcement are included.
        """
        ...
