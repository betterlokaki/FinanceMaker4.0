"""Market calendar helper for NYSE trading hours."""
import logging
from datetime import datetime
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd
from common.helpers.yfinance_cache_manager import get_cached_market_session_hours

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
        
        import exchange_calendars.errors as xcals_errors
        
        try:
            # Use next_open directly - it handles date validation internally
            # Find a valid session date to use as input
            after_date = after.date()
            
            # Find the closest valid session that's <= after_date
            valid_sessions = [s for s in self._calendar.sessions if s.date() <= after_date]
            
            if valid_sessions:
                # Use the closest valid session
                input_session = valid_sessions[-1]
                logger.debug("Using closest valid session: %s", input_session.date())
                
                # Check if the input date is today and we're before market close
                if input_session.date() == after_date:
                    try:
                        # Check if we're before market close today
                        market_close = self._calendar.session_close(input_session).tz_convert(self._timezone)
                        if after < market_close.to_pydatetime():
                            # Return today's market open
                            session_open = self._calendar.session_open(input_session).tz_convert(self._timezone).to_pydatetime()
                            logger.debug("Returning today's market open: %s", session_open)
                            return session_open
                    except Exception:
                        # If session_close fails, fall through to next_open
                        pass
                
                # Get next session - next_open needs a timestamp with time, so use session_open
                try:
                    session_open_ts = self._calendar.session_open(input_session)
                    next_session = self._calendar.next_open(session_open_ts)
                    result = next_session.tz_convert(self._timezone).to_pydatetime()
                    logger.debug("Next trading day: %s", result)
                    return result
                except Exception as e:
                    logger.warning("next_open failed, trying with session timestamp: %s", e)
                    # Fallback: try with the session itself
                    next_session = self._calendar.next_open(input_session)
                    result = next_session.tz_convert(self._timezone).to_pydatetime()
                    return result
            else:
                # No valid sessions found - use first session from sessions list
                logger.warning("No valid sessions found <= %s, using first session market open", after_date)
                first_session = self._calendar.sessions[0]
                first_session_open = self._calendar.session_open(first_session)
                return first_session_open.tz_convert(self._timezone).to_pydatetime()
                
        except xcals_errors.DateOutOfBounds:
            logger.error("DateOutOfBounds in next_open")
            # Last resort: use the last valid session
            try:
                last_valid = self._calendar.sessions[-1]
                last_session_open = self._calendar.session_open(last_valid)
                next_session = self._calendar.next_open(last_session_open)
                return next_session.tz_convert(self._timezone).to_pydatetime()
            except Exception as e:
                logger.error("Complete failure in get_next_trading_day: %s", e, exc_info=True)
                # Return a safe default: today + 1 day at market open time
                tomorrow = after.replace(hour=9, minute=30, second=0, microsecond=0) + pd.Timedelta(days=1)
                return tomorrow
        except Exception as e:
            # Don't try to stringify DateOutOfBounds - it might fail
            error_msg = str(e) if not isinstance(e, xcals_errors.DateOutOfBounds) else "DateOutOfBounds"
            logger.error(
                "Error in get_next_trading_day: after=%s, error=%s",
                after, error_msg, exc_info=True
            )
            # Return a safe default
            tomorrow = after.replace(hour=9, minute=30, second=0, microsecond=0) + pd.Timedelta(days=1)
            return tomorrow

    def get_pre_market_open(self, trading_day: datetime) -> datetime:
        """Get pre-market open time (4:00 AM EST)."""
        return trading_day.replace(hour=self.PRE_MARKET_OPEN_HOUR, minute=0, second=0, microsecond=0)

    def get_regular_market_open(self, trading_day: datetime) -> datetime:
        """Get regular market open for the provided trading day."""
        session_open, _ = get_cached_market_session_hours(
            trading_day=trading_day.date(),
            exchange="XNYS",
            timezone="America/New_York",
        )
        return session_open

    def get_regular_market_close(self, trading_day: datetime) -> datetime:
        """Get regular market close for the provided trading day."""
        _, session_close = get_cached_market_session_hours(
            trading_day=trading_day.date(),
            exchange="XNYS",
            timezone="America/New_York",
        )
        return session_close

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
        
        import exchange_calendars.errors as xcals_errors
        
        try:
            # Use the calendar's sessions list directly instead of creating our own Timestamp
            # This avoids DateOutOfBounds issues
            # Check if this date is in the sessions list
            sessions_dates = [s.date() for s in self._calendar.sessions]
            return date_only in sessions_dates
        except Exception as e:
            # Don't try to stringify DateOutOfBounds - it might fail
            error_msg = str(e) if not isinstance(e, xcals_errors.DateOutOfBounds) else "DateOutOfBounds"
            logger.error(
                "Error in is_trading_day: date=%s, error=%s",
                date, error_msg, exc_info=True
            )
            return False

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
