"""Configuration models for backtesting.py-based runs."""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum

from common.models.period import Period


class TradeDirection(str, Enum):
    """Trade-direction limiter mapped to Pine values."""

    BOTH = "Both"
    LONG_ONLY = "Long Only"
    SHORT_ONLY = "Short Only"


def _default_start_time() -> datetime:
    """Default backtest start date (3 years lookback)."""
    return datetime.now(timezone.utc) - timedelta(days=29)


def _default_end_time() -> datetime:
    """Default backtest end date (now)."""
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class BacktestRunConfig:
    """Single-symbol run configuration for the Pine-ported strategy."""

    initial_capital: float = 10_000.0
    notional_per_trade: float = 30_000.0
    leverage: float = 3.0
    commission_rate: float = 0.0005  # 0.05%
    slippage_ticks: float = 2.0
    fixed_commission_per_side: float = 0.0
    default_tick_size: float = 0.01

    start_time: datetime = field(default_factory=_default_start_time)
    end_time: datetime = field(default_factory=_default_end_time)
    period: Period = Period.MINUTE

    trade_direction: TradeDirection = TradeDirection.BOTH
    trade_on_close: bool = False
    finalize_trades: bool = True
    open_browser_plots: bool = True

    atr_sl_multiplier: float = 3.0
    atr_tp_multiplier: float = 3.0
    atr_period: int = 14

    adx_len: int = 14
    adx_di_len: int = 14
    adx_ema_len: int = 14

    tdfi_lookback: int = 13
    tdfi_filter_high: float = 0.05
    tdfi_filter_low: float = -0.05

    rf_movement_source: str = "Close"
    rf_range_size: float = 2.618
    rf_range_scale: str = "Average Change"
    rf_range_period: int = 14
    rf_smooth_range: bool = True
    rf_smooth_period: int = 27

    ctr_len: int = 25
    ctr_tlen: int = 20
    ctr_upper: int = 50
    ctr_lower: int = -50


@dataclass(frozen=True)
class PortfolioConfig:
    """Shared-capital portfolio orchestration settings."""

    initial_capital: float = 10_000.0
    max_leverage: float = 3.0
    commission_rate: float = 0.0005
    slippage_ticks: float = 2.0
    fixed_commission_per_side: float = 0.0
    default_tick_size: float = 0.01
