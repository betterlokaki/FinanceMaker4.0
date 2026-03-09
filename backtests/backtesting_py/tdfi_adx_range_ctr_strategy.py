"""Pine-strategy port for backtesting.py.

Strategy:
- TDFI + ADX > ADX EMA
- Range Filter [DW]
- Commodity Trend Reactor confluence
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from backtesting import Strategy


def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=max(period, 1), adjust=False, min_periods=1).mean()


def _rma(series: pd.Series, period: int) -> pd.Series:
    length = max(period, 1)
    return series.ewm(alpha=1.0 / length, adjust=False, min_periods=1).mean()


def _true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["Close"].shift(1)
    tr1 = df["High"] - df["Low"]
    tr2 = (df["High"] - prev_close).abs()
    tr3 = (df["Low"] - prev_close).abs()
    return pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)


def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    return _rma(_true_range(df), period)


def _cci(source: pd.Series, period: int) -> pd.Series:
    p = max(period, 1)
    sma = source.rolling(window=p, min_periods=1).mean()
    mad = (source - sma).abs().rolling(window=p, min_periods=1).mean()
    cci = (source - sma) / (0.015 * mad.replace(0, np.nan))
    return cci.replace([np.inf, -np.inf], np.nan)


def _cross_over(a: pd.Series, b: pd.Series) -> pd.Series:
    return (a.shift(1) <= b.shift(1)) & (a > b)


def _cross_under(a: pd.Series, b: pd.Series) -> pd.Series:
    return (a.shift(1) >= b.shift(1)) & (a < b)


def _range_filter_type1(
    high_src: pd.Series,
    low_src: pd.Series,
    rng: pd.Series,
) -> tuple[pd.Series, pd.Series]:
    filt = pd.Series(index=high_src.index, dtype=float)
    fdir = pd.Series(index=high_src.index, dtype=float)

    for i in range(len(high_src)):
        h = float(high_src.iloc[i])
        l = float(low_src.iloc[i])
        r = float(rng.iloc[i]) if np.isfinite(rng.iloc[i]) else 0.0
        prev = (h + l) / 2.0 if i == 0 else float(filt.iloc[i - 1])
        current = prev

        if (h - r) > prev:
            current = h - r
        if (l + r) < prev:
            current = l + r
        filt.iloc[i] = current

        if i == 0:
            fdir.iloc[i] = 0.0
        else:
            prev_filt = float(filt.iloc[i - 1])
            if current > prev_filt:
                fdir.iloc[i] = 1.0
            elif current < prev_filt:
                fdir.iloc[i] = -1.0
            else:
                fdir.iloc[i] = float(fdir.iloc[i - 1])

    return filt, fdir


class TDFIAdxRangeCtrConfluenceStrategy(Strategy):
    """Port of the provided Pine strategy."""

    trade_direction: str = "Both"
    notional_per_trade: float = 30_000.0

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

    def init(self) -> None:
        """Precompute indicator arrays used in next()."""
        index = pd.Index(self.data.index)
        df = pd.DataFrame(
            {
                "Open": np.asarray(self.data.Open, dtype=float),
                "High": np.asarray(self.data.High, dtype=float),
                "Low": np.asarray(self.data.Low, dtype=float),
                "Close": np.asarray(self.data.Close, dtype=float),
                "Volume": np.asarray(self.data.Volume, dtype=float),
            },
            index=index,
        )

        self._atr_values = _atr(df, self.atr_period).to_numpy()

        # ADX
        up = df["High"].diff()
        down = -df["Low"].diff()
        plus_dm = pd.Series(
            np.where((up > down) & (up > 0), up, 0.0),
            index=df.index,
        )
        minus_dm = pd.Series(
            np.where((down > up) & (down > 0), down, 0.0),
            index=df.index,
        )
        truerange = _rma(_true_range(df), self.adx_di_len)
        plus = 100.0 * _rma(plus_dm, self.adx_di_len) / truerange.replace(0, np.nan)
        minus = 100.0 * _rma(minus_dm, self.adx_di_len) / truerange.replace(0, np.nan)
        adx_sum = (plus + minus).replace(0, np.nan)
        adx_val = 100.0 * _rma((plus - minus).abs() / adx_sum, self.adx_len)
        adx_ema = _ema(adx_val, self.adx_ema_len)

        # TDFI
        source = df["Close"]
        mma = _ema(source * 1000.0, self.tdfi_lookback)
        smma = _ema(mma, self.tdfi_lookback)
        impet_mma = mma.diff()
        impet_smma = smma.diff()
        divma = (mma - smma).abs()
        number = (impet_mma + impet_smma) / 2.0
        tdf = divma * number.pow(3)
        highest_abs = tdf.abs().rolling(self.tdfi_lookback * 3, min_periods=1).max()
        ntdf = tdf / highest_abs.replace(0, np.nan)

        # Range filter [DW], fixed Type 1
        high_src = df["High"] if self.rf_movement_source == "Wicks" else df["Close"]
        low_src = df["Low"] if self.rf_movement_source == "Wicks" else df["Close"]
        rng_base = (high_src + low_src) / 2.0
        tr = _true_range(df)
        atr_rf = _ema(tr, self.rf_range_period)
        ac = _ema((rng_base - rng_base.shift(1)).abs(), self.rf_range_period)
        sd = rng_base.rolling(self.rf_range_period, min_periods=1).std(ddof=0)
        scale = self.rf_range_scale
        if scale == "Pips":
            rng = pd.Series(self.rf_range_size * 0.0001, index=df.index)
        elif scale == "Points":
            rng = pd.Series(self.rf_range_size, index=df.index)
        elif scale == "% of Price":
            rng = df["Close"] * self.rf_range_size / 100.0
        elif scale == "ATR":
            rng = self.rf_range_size * atr_rf
        elif scale == "Average Change":
            rng = self.rf_range_size * ac
        elif scale == "Standard Deviation":
            rng = self.rf_range_size * sd
        elif scale == "Ticks":
            rng = pd.Series(self.rf_range_size * 0.01, index=df.index)
        else:
            rng = pd.Series(self.rf_range_size, index=df.index)

        rng_smoothed = _ema(rng, self.rf_smooth_period)
        active_rng = rng_smoothed if self.rf_smooth_range else rng
        _, fdir = _range_filter_type1(high_src, low_src, active_rng.fillna(0.0))
        rf_allow_long = fdir == 1.0
        rf_allow_short = fdir == -1.0

        # Commodity Trend Reactor
        ctr_low = df["Low"].rolling(self.ctr_tlen, min_periods=1).min()
        ctr_high = df["High"].rolling(self.ctr_tlen, min_periods=1).max()
        ctr_cci = _cci(df["Close"], self.ctr_len)
        upper = pd.Series(self.ctr_upper, index=df.index)
        lower = pd.Series(self.ctr_lower, index=df.index)
        cross_up = _cross_over(ctr_cci, upper)
        cross_down = _cross_under(ctr_cci, lower)

        trend_state: list[bool | None] = []
        current_trend: bool | None = None
        for i in range(len(df)):
            if bool(cross_up.iloc[i]):
                current_trend = True
            if bool(cross_down.iloc[i]):
                current_trend = False
            trend_state.append(current_trend)
        ctr_trend = pd.Series(trend_state, index=df.index, dtype="object")
        ctr_trail_line = pd.Series(
            np.where(ctr_trend == True, ctr_low, ctr_high),  # noqa: E712
            index=df.index,
            dtype=float,
        )
        ctr_allow_long = (ctr_trend == True) & (ctr_trail_line < df["Close"])  # noqa: E712
        ctr_allow_short = (ctr_trend == False) & (ctr_trail_line > df["Close"])  # noqa: E712

        can_long = self.trade_direction in ("Both", "Long Only")
        can_short = self.trade_direction in ("Both", "Short Only")

        long_signal = (
            can_long
            & rf_allow_long
            & ctr_allow_long
            & (ntdf > self.tdfi_filter_high)
            & (adx_val > adx_ema)
        )
        short_signal = (
            can_short
            & rf_allow_short
            & ctr_allow_short
            & (ntdf < self.tdfi_filter_low)
            & (adx_val > adx_ema)
        )

        self._long_signal = long_signal.fillna(False).to_numpy(dtype=bool)
        self._short_signal = short_signal.fillna(False).to_numpy(dtype=bool)

    def next(self) -> None:
        """Execute strategy rules on each bar."""
        bar = len(self.data) - 1
        if bar < 1:
            return

        price = float(self.data.Close[-1])
        atr = float(self._atr_values[bar]) if bar < len(self._atr_values) else np.nan
        if not np.isfinite(price) or price <= 0 or not np.isfinite(atr) or atr <= 0:
            return

        long_signal = bool(self._long_signal[bar])
        short_signal = bool(self._short_signal[bar])

        if long_signal and short_signal:
            return

        if long_signal and self.position.is_short:
            self.position.close()
        if short_signal and self.position.is_long:
            self.position.close()

        shares = int(self.notional_per_trade / price)
        if shares < 1:
            return

        if long_signal and not self.position:
            long_sl = price - (self.atr_sl_multiplier * atr)
            long_tp = price + (self.atr_tp_multiplier * atr)
            self.buy(size=shares, sl=long_sl, tp=long_tp)

        if short_signal and not self.position:
            short_sl = price + (self.atr_sl_multiplier * atr)
            short_tp = price - (self.atr_tp_multiplier * atr)
            self.sell(size=shares, sl=short_sl, tp=short_tp)
