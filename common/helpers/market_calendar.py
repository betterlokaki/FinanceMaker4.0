"""Market calendar helper for NYSE trading hours."""
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd

logger: logging.Logger = logging.getLogger(__name__)


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
        # Cache valid date range to avoid repeated lookups
        self._first_session = self._calendar.first_session
        self._last_session = self._calendar.last_session
        logger.info("Calendar initialized: first_session=%s, last_session=%s", 
                   self._first_session.date(), self._last_session.date())

    @property
    def timezone(self) -> ZoneInfo:
        """Get market timezone."""
        return self._timezone

    def now(self) -> datetime:
        """Get current time in market timezone."""
        return datetime.now(self._timezone)

    def _clamp_timestamp_to_valid_range(self, ts: pd.Timestamp) -> pd.Timestamp:
        """Clamp timestamp to calendar's valid range.
        
        Args:
            ts: Timestamp to clamp.
            
        Returns:
            Timestamp clamped to valid range.
        """
        if ts < self._first_session:
            logger.warning("Date %s is before calendar start (%s), clamping to first session",
                          ts.date(), self._first_session.date())
            return self._first_session
        elif ts > self._last_session:
            logger.warning("Date %s is after calendar end (%s), clamping to last session",
                          ts.date(), self._last_session.date())
            return self._last_session
        return ts

    def get_next_trading_day(self, after: datetime) -> datetime:
        """Get next trading day's market open.
        
        Args:
            after: Find trading day after this datetime.
            
        Returns:
            Market open datetime for next trading day.
        """
        # Log input for debugging
        logger.debug("get_next_trading_day called with: after=%s (tz=%s)", after, after.tzinfo)
        
        # Convert to date-only timestamp (no time component) to avoid timezone issues
        date_only = after.date()
        ts = pd.Timestamp(date_only)
        
        # Ensure timezone-naive (exchange_calendars requires this)
        if ts.tz is not None:
            ts = ts.tz_localize(None)
        
        # Clamp to valid range BEFORE using with calendar
        ts = self._clamp_timestamp_to_valid_range(ts)
        
        logger.debug("Converted to timestamp: ts=%s (tz=%s), type=%s", ts, ts.tz, type(ts))
        
        try:
            if self._calendar.is_session(ts):
                market_close = self._calendar.session_close(ts).tz_convert(self._timezone)
                if after < market_close.to_pydatetime():
                    return self._calendar.session_open(ts).tz_convert(self._timezone).to_pydatetime()
            
            next_session = self._calendar.next_open(ts)
            return next_session.tz_convert(self._timezone).to_pydatetime()
        except Exception as e:
            logger.error(
                "Error in get_next_trading_day: after=%s, ts=%s, ts_type=%s, error=%s",
                after, ts, type(ts), str(e), exc_info=True
            )
            raise

    def get_pre_market_open(self, trading_day: datetime) -> datetime:
        """Get pre-market open time (4:00 AM EST)."""
        return trading_day.replace(hour=self.PRE_MARKET_OPEN_HOUR, minute=0, second=0, microsecond=0)

    def get_after_hours_close(self, trading_day: datetime) -> datetime:
        """Get after-hours close time (8:00 PM EST)."""
        return trading_day.replace(hour=self.AFTER_HOURS_CLOSE_HOUR, minute=0, second=0, microsecond=0)

    def is_trading_day(self, date: datetime) -> bool:
        """Check if a given date is a trading day.
        
        Args:
            date: Date to check.
            
        Returns:
            True if the date is a trading day, False otherwise.
        """
        date_only = date.date()
        ts = pd.Timestamp(date_only)
        
        # Ensure timezone-naive (exchange_calendars requires this)
        if ts.tz is not None:
            ts = ts.tz_localize(None)
        
        # Clamp to valid range BEFORE using with calendar
        ts = self._clamp_timestamp_to_valid_range(ts)
        
        try:
            return self._calendar.is_session(ts)
        except Exception as e:
            logger.error(
                "Error in is_trading_day: date=%s, ts=%s, ts_type=%s, error=%s",
                date, ts, type(ts), str(e), exc_info=True
            )
            raise

    def is_market_hours_open(self) -> bool:
        """Check if currently within market hours (4 AM - 8 PM EST).
        
        Returns:
            True if current time is between pre-market open and after-hours close
            on a trading day, False otherwise.
        """
        now: datetime = self.now()
        if not self.is_trading_day(now):
            return False
        pre_market: datetime = self.get_pre_market_open(now)
        after_hours: datetime = self.get_after_hours_close(now)
        return pre_market <= now <= after_hours
