"""Earning demand zone backtesting strategy.

Combines earnings calendar data with demand zone proximity filtering
and technical/fundamental scoring to identify high-probability entries
the day before earnings announcements.
"""
import logging

import pandas as pd

from backtesting.abstracts.backtest_strategy_base import BacktestStrategyBase
from backtesting.models.backtest_params import BacktestParams
from backtesting.models.zone import Zone
from common.helpers.zone_detection import (
    find_demand_zones_at_price,
    get_supply_demand_zones,
)
from ticker_logic_scoring import analyze_ticker_strategy

logger: logging.Logger = logging.getLogger(__name__)

_MINIMUM_SCORE: int = 70
_TAKE_PROFIT_PCT: float = 0.08
_STOP_LOSS_PCT: float = 0.04


class EarningDemandZoneStrategy(BacktestStrategyBase):
    """Strategy that buys tickers near demand zones before earnings.
    
    Entry Rules:
        - Ticker must have earnings the next trading day.
        - Close on the day before earnings must be inside an active
          or tested demand zone (5-year zone detection).
        - Technical/fundamental score must be >= 70 out of 100.
        - Entry price = close of the day before earnings.
        
    Exit Rules:
        - Take profit: 8% above entry price.
        - Stop loss: 4% below entry price.
    """
    
    def __init__(self) -> None:
        """Initialize strategy with earnings tracking state."""
        self._earnings_date: pd.Timestamp | None = None
        self._ticker_symbol: str | None = None
    
    @property
    def name(self) -> str:
        """Return the strategy name."""
        return "Earning Demand Zone Strategy"
    
    def set_earnings_date(self, earnings_date: pd.Timestamp | None) -> None:
        """Set the earnings date for the current ticker.
        
        Args:
            earnings_date: The earnings announcement date for the ticker.
        """
        self._earnings_date = earnings_date
    
    def set_ticker(self, ticker_symbol: str | None) -> None:
        """Set the ticker symbol for fundamentals lookup during scoring.
        
        Args:
            ticker_symbol: Stock ticker symbol, or None to clear.
        """
        self._ticker_symbol = ticker_symbol
    
    def generate_signals(
        self,
        df: pd.DataFrame,
        params: BacktestParams,
        zone_df: pd.DataFrame | None = None,
    ) -> pd.DataFrame:
        """Generate trading signals based on demand zone + scoring filter.
        
        For the configured earnings date:
        1. Find the trading day before earnings in the DataFrame.
        2. Compute supply/demand zones from extended historical data.
        3. Check if the close on that day sits inside a demand zone.
        4. Score the ticker using technicals + fundamentals.
        5. If score >= 70, generate a buy signal.
        
        Args:
            df: DataFrame with OHLCV columns (backtest period).
            params: Backtest parameters.
            zone_df: Optional extended DataFrame with historical data
                before the backtest start date for zone detection.
            
        Returns:
            DataFrame with signal columns added.
        """
        self._validate_dataframe(df)
        df = self._initialize_signal_columns(df)
        
        if not isinstance(df.index, pd.DatetimeIndex):
            return df
        
        if self._earnings_date is None:
            return df
        
        if self._ticker_symbol is None:
            return df
        
        # --- Step 1: Find the day before earnings ---
        day_before = self._find_day_before_earnings(df)
        if day_before is None:
            return df
        
        day_before_mask = self._get_date_mask(df, day_before)
        if not day_before_mask.any():
            return df
        
        day_before_data = df[day_before_mask]
        if day_before_data.empty:
            return df
        
        close_price: float = float(day_before_data["Close"].iloc[-1])
        
        # --- Step 2: Compute zones from historical data up to this day ---
        zone_detection_df = self._build_zone_detection_data(df, zone_df, day_before)
        if zone_detection_df.empty or len(zone_detection_df) < 200:
            logger.debug(
                f"{self._ticker_symbol}: Insufficient data for zone detection "
                f"({len(zone_detection_df)} rows)"
            )
            return df
        
        zones: list[Zone] = get_supply_demand_zones(zone_detection_df)
        if not zones:
            return df
        
        # --- Step 3: Check if close is in a demand zone ---
        demand_zones = find_demand_zones_at_price(zones, close_price)
        if not demand_zones:
            return df
        
        logger.debug(
            f"{self._ticker_symbol}: Close ${close_price:.2f} is in "
            f"{len(demand_zones)} demand zone(s)"
        )
        
        # --- Step 4: Score the ticker using data up to day before earnings ---
        score_hist = self._build_scoring_data(df, zone_df, day_before)
        if score_hist is None or score_hist.empty:
            return df
        
        score_result = analyze_ticker_strategy(self._ticker_symbol, hist=score_hist)
        
        if score_result is None or "error" in score_result:
            error_msg = score_result.get("error", "Unknown") if score_result else "No data"
            logger.debug(f"{self._ticker_symbol}: Scoring failed - {error_msg}")
            return df
        
        score: int = score_result["Score"]
        
        if score < _MINIMUM_SCORE:
            logger.debug(
                f"{self._ticker_symbol}: Score {score} < {_MINIMUM_SCORE}, skipping"
            )
            return df
        
        # --- Step 5: Generate buy signal ---
        entry_price: float = close_price
        take_profit: float = entry_price * (1 + _TAKE_PROFIT_PCT)
        stop_loss: float = entry_price * (1 - _STOP_LOSS_PCT)
        
        last_bar_idx = day_before_data.index[-1]
        bar_position: int = df.index.get_loc(last_bar_idx)
        
        df.iloc[bar_position, df.columns.get_loc("entry_signal")] = True
        df.iloc[bar_position, df.columns.get_loc("entry_price")] = entry_price
        df.iloc[bar_position, df.columns.get_loc("take_profit")] = take_profit
        df.iloc[bar_position, df.columns.get_loc("stop_loss")] = stop_loss
        
        logger.info(
            f"{self._ticker_symbol}: BUY signal | Score={score} | "
            f"Entry=${entry_price:.2f} | TP=${take_profit:.2f} | SL=${stop_loss:.2f} | "
            f"Reason: {score_result.get('Reason', '')}"
        )
        
        return df
    
    def _find_day_before_earnings(self, df: pd.DataFrame) -> pd.Timestamp | None:
        """Find the trading day before the earnings date.
        
        Normalizes the earnings date, subtracts one day, and skips
        weekends to find the last trading day before the announcement.
        
        Args:
            df: DataFrame with DatetimeIndex to validate against.
            
        Returns:
            Normalized timestamp of the day before earnings, or None.
        """
        earnings_date = pd.Timestamp(self._earnings_date)
        if earnings_date.tzinfo is not None:
            earnings_date = earnings_date.tz_localize(None)
        
        day_before = earnings_date.normalize() - pd.Timedelta(days=1)
        
        # Skip weekends
        while day_before.weekday() >= 5:
            day_before -= pd.Timedelta(days=1)
        
        return day_before
    
    def _get_date_mask(
        self, df: pd.DataFrame, target_date: pd.Timestamp
    ) -> pd.Series:
        """Create a boolean mask for rows matching a normalized date.
        
        Args:
            df: DataFrame with DatetimeIndex.
            target_date: Normalized date to match against.
            
        Returns:
            Boolean Series where True indicates matching rows.
        """
        df_dates = df.index.normalize()
        if hasattr(df_dates, "tz") and df_dates.tz is not None:
            df_dates = df_dates.tz_localize(None)
        
        target_normalized = target_date.normalize()
        if target_normalized.tzinfo is not None:
            target_normalized = target_normalized.tz_localize(None)
        
        return df_dates == target_normalized
    
    def _build_zone_detection_data(
        self,
        df: pd.DataFrame,
        zone_df: pd.DataFrame | None,
        cutoff_date: pd.Timestamp,
    ) -> pd.DataFrame:
        """Build the combined DataFrame for zone detection.
        
        Combines zone_df (historical data before backtest period) with
        df data up to the cutoff date to provide full history for
        zone detection.
        
        Args:
            df: Backtest period DataFrame.
            zone_df: Extended historical data before backtest start.
            cutoff_date: Only include data up to this date.
            
        Returns:
            Combined DataFrame for zone detection.
        """
        cutoff = cutoff_date.normalize()
        if cutoff.tzinfo is not None:
            cutoff = cutoff.tz_localize(None)
        
        # Slice df up to the cutoff date
        df_dates = df.index.normalize()
        if hasattr(df_dates, "tz") and df_dates.tz is not None:
            df_dates = df_dates.tz_localize(None)
        
        df_before = df[df_dates <= cutoff]
        
        if zone_df is not None and not zone_df.empty:
            # Combine zone_df and df_before
            combined = pd.concat([zone_df, df_before])
            combined = combined[~combined.index.duplicated(keep="first")]
            combined = combined.sort_index()
            return combined
        
        return df_before
    
    def _build_scoring_data(
        self,
        df: pd.DataFrame,
        zone_df: pd.DataFrame | None,
        cutoff_date: pd.Timestamp,
    ) -> pd.DataFrame | None:
        """Build the DataFrame for ticker scoring.
        
        The scoring function needs ~200 days of data for SMA200.
        Combines zone_df and df data up to cutoff, then takes the
        last 365 rows to approximate 1 year of daily data.
        
        Args:
            df: Backtest period DataFrame.
            zone_df: Extended historical data before backtest start.
            cutoff_date: Only include data up to this date.
            
        Returns:
            DataFrame suitable for scoring, or None if insufficient data.
        """
        combined = self._build_zone_detection_data(df, zone_df, cutoff_date)
        
        if combined.empty or len(combined) < 200:
            return None
        
        # Take the last year of data (up to 365 rows)
        return combined.tail(365)
    
    def _check_price_triggers_entry(
        self,
        price: float,
        zones: list[Zone],
        params: BacktestParams,
    ) -> tuple[float, float, float] | None:
        """Check if price triggers entry (not used by this strategy).
        
        This method is required by the base class interface but is not
        used by this strategy. Signal generation is handled entirely
        within generate_signals.
        """
        return None
