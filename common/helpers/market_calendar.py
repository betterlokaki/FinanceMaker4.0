"""Market calendar helper for NYSE trading hours."""
from datetime import datetime
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd


class MarketCalendar:
    """Helper for NYSE market hours and trading day detection.
    
    Provides pre-market, regular, and after-hours times.
    Uses exchange_calendars for holiday awareness.
    """

    PRE_MARKET_OPEN_HOUR: int = 4
    AFTER_HOURS_CLOSE_HOUR: int = 20

    def __init__(self, exchange: str = "XNYS", timezone: str = "America/New_York") -> None:
        """Initialize market calendar.
        
        Args:
            exchange: Exchange calendar code (XNYS = NYSE).
            timezone: Market timezone.
        """
        self._calendar = xcals.get_calendar(exchange)
        self._timezone: ZoneInfo = ZoneInfo(timezone)

    @property
    def timezone(self) -> ZoneInfo:
        """Get market timezone."""
        return self._timezone

    def now(self) -> datetime:
        """Get current time in market timezone."""
        return datetime.now(self._timezone)

    def get_next_trading_day(self, after: datetime) -> datetime:
        """Get next trading day's market open.
        
        Args:
            after: Find trading day after this datetime.
            
        Returns:
            Market open datetime for next trading day.
        """
        ts = pd.Timestamp(after.date())
        
        if self._calendar.is_session(ts):
            market_close = self._calendar.session_close(ts).tz_convert(self._timezone)
            if after < market_close.to_pydatetime():
                return self._calendar.session_open(ts).tz_convert(self._timezone).to_pydatetime()
        
        next_session = self._calendar.next_open(ts)
        return next_session.tz_convert(self._timezone).to_pydatetime()

    def get_pre_market_open(self, trading_day: datetime) -> datetime:
        """Get pre-market open time (4:00 AM EST)."""
        return trading_day.replace(hour=self.PRE_MARKET_OPEN_HOUR, minute=0, second=0, microsecond=0)

    def get_after_hours_close(self, trading_day: datetime) -> datetime:
        """Get after-hours close time (8:00 PM EST)."""
        return trading_day.replace(hour=self.AFTER_HOURS_CLOSE_HOUR, minute=0, second=0, microsecond=0)
