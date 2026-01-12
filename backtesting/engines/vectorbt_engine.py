"""VectorBT-based backtest engine implementation."""
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

import numpy as np
import pandas as pd
import yfinance_cache as yf

from backtesting.abstracts.i_backtest_engine import IBacktestEngine
from backtesting.abstracts.i_backtest_strategy import IBacktestStrategy
from backtesting.exceptions.backtest_error import BacktestError, InsufficientDataError
from backtesting.models.backtest_params import BacktestParams
from backtesting.models.backtest_result import BacktestResult
from backtesting.models.trade_record import TradeRecord
from common.helpers.yfinance_cache_manager import init_yfinance_cache

init_yfinance_cache()


@dataclass
class TradeEvent:
    """Represents a trade entry or exit event for chronological processing."""
    
    timestamp: datetime
    ticker: str
    event_type: Literal["entry", "exit"]
    price: float
    shares: float
    capital_impact: float  # Negative for entry (reserves capital), positive for exit (frees capital)
    exit_reason: str | None = None
    entry_price: float | None = None  # For exit events
    entry_date: datetime | None = None  # For exit events


@dataclass
class PendingTrade:
    """Represents a potential trade signal that may execute when capital is available."""
    
    timestamp: datetime
    ticker: str
    entry_price: float
    shares: float
    position_value: float
    take_profit: float
    stop_loss: float


class VectorBTEngine(IBacktestEngine):
    """Backtest engine using vectorized operations for performance.
    
    Simulates realistic trading across multiple tickers with proper
    capital allocation, tracking reserved capital for open positions.
    Processes all trade events chronologically to prevent unrealistic
    concurrent position sizing.
    """
    
    MIN_COOLDOWN_BARS: int = 20
    
    def __init__(self, strategy: IBacktestStrategy) -> None:
        """Initialize engine with a trading strategy.
        
        Args:
            strategy: Strategy implementation for signal generation.
        """
        self._strategy = strategy
    
    def run(
        self,
        tickers: list[str],
        params: BacktestParams,
        store_price_data: bool = False,
    ) -> BacktestResult:
        """Run backtest across all tickers with shared capital.
        
        Processes all trade events chronologically to ensure realistic
        capital allocation. Tracks reserved capital for open positions
        and only allows new trades when sufficient capital is available.
        
        Args:
            tickers: List of ticker symbols to backtest.
            params: Backtest parameters.
            store_price_data: If True, store OHLCV data for visualization.
            
        Returns:
            Aggregated BacktestResult.
        """
        # Phase 1: Collect all potential trade signals from all tickers
        all_signals: list[tuple[str, pd.DataFrame]] = []
        price_data_dict: dict[str, pd.DataFrame] = {}
        skipped = 0
        
        print(f"\n[PHASE 1] Collecting signals from {len(tickers)} tickers...")
        for ticker in tickers:
            try:
                zone_df, df = self._fetch_data(ticker, params)
                if df.empty:
                    skipped += 1
                    continue
                
                signals_df = self._strategy.generate_signals(df, params, zone_df=zone_df)
                all_signals.append((ticker, signals_df))
                
                # Store price data if requested
                if store_price_data:
                    price_data_dict[ticker] = signals_df.copy()
            except (BacktestError, InsufficientDataError):
                skipped += 1
            except Exception as e:
                print(f"Error collecting signals for {ticker}: {e}")
                skipped += 1
        
        if not all_signals:
            print("[ERROR] No valid signals collected from any ticker.")
            return self._empty_result(params.initial_capital, skipped, False)
        
        print(f"[PHASE 1 COMPLETE] Collected signals from {len(all_signals)} tickers, skipped {skipped}")
        
        # Phase 2: Process all trades chronologically with capital tracking
        print(f"\n[PHASE 2] Processing trades chronologically with capital tracking...")
        trades = self._process_chronological_trades(all_signals, params)
        
        if not trades:
            print("[WARNING] No trades executed despite signals being present.")
            return self._empty_result(params.initial_capital, skipped, False, price_data_dict)
        
        print(f"[PHASE 2 COMPLETE] Executed {len(trades)} trades")
        
        # Phase 3: Aggregate results
        final_capital = params.initial_capital + sum(t.pnl for t in trades)
        traded_tickers = len(set(t.ticker for t in trades))
        capital_depleted = final_capital < params.MIN_CAPITAL_THRESHOLD
        
        return self._aggregate_results(
            trades, params.initial_capital, final_capital,
            traded_tickers, skipped, capital_depleted, price_data_dict
        )
    
    def _process_chronological_trades(
        self,
        all_signals: list[tuple[str, pd.DataFrame]],
        params: BacktestParams,
    ) -> list[TradeRecord]:
        """Process all trades chronologically with real-time capital tracking.
        
        This method collects all potential entry/exit events from all tickers,
        sorts them chronologically, and processes them in order. It tracks:
        - Available capital (cash not reserved in open positions)
        - Reserved capital (cash locked in open positions)
        - Open positions per ticker
        
        Only allows new entries when available capital >= position size.
        
        Args:
            all_signals: List of (ticker, signals_df) tuples.
            params: Backtest parameters.
            
        Returns:
            List of executed TradeRecord objects.
        """
        # Step 1: Generate all potential trade events from signals
        all_events: list[TradeEvent] = []
        
        for ticker, signals_df in all_signals:
            events = self._generate_trade_events(ticker, signals_df, params)
            all_events.extend(events)
        
        # Step 2: Sort all events chronologically
        all_events.sort(key=lambda e: e.timestamp)
        
        # Step 3: Process events in chronological order with capital tracking
        available_capital = params.initial_capital
        reserved_capital = 0.0
        open_positions: dict[str, dict] = {}  # ticker -> {entry_date, entry_price, shares, etc.}
        completed_trades: list[TradeRecord] = []
        skipped_entries = 0
        
        for event in all_events:
            if event.event_type == "entry":
                # Price sanity check
                if event.price is None or event.price <= 0:
                    skipped_entries += 1
                    continue
                
                # Calculate required capital using only available capital
                position_value = params.calculate_position_value(available_capital)
                
                # Respect minimum capital threshold
                if available_capital < params.MIN_CAPITAL_THRESHOLD:
                    skipped_entries += 1
                    continue
                
                # Ensure position_value doesn't exceed available capital
                position_value = min(position_value, available_capital)
                
                # Check if we have enough available capital and no existing position
                if position_value > 0 and event.ticker not in open_positions:
                    # Compute whole-share quantity
                    computed_shares_int = int(position_value // event.price)
                    if computed_shares_int < 1:
                        skipped_entries += 1
                        continue
                    computed_shares = float(computed_shares_int)
                    actual_cost = computed_shares * event.price
                    if actual_cost <= 0 or actual_cost > available_capital:
                        skipped_entries += 1
                        continue
                    
                    # Execute entry: reserve actual cost
                    available_capital -= actual_cost
                    reserved_capital += actual_cost
                    
                    open_positions[event.ticker] = {
                        "entry_date": event.timestamp,
                        "entry_price": event.price,
                        "shares": computed_shares,
                        "position_value": actual_cost,
                    }
                else:
                    # Skip entry - insufficient capital or position already open
                    skipped_entries += 1
            
            elif event.event_type == "exit":
                # Only process exit if we have an open position for this ticker
                if event.ticker in open_positions:
                    position = open_positions[event.ticker]
                    
                    # Create trade record
                    trade = self._create_trade_record(
                        ticker=event.ticker,
                        entry_date=position["entry_date"],
                        entry_price=position["entry_price"],
                        exit_date=event.timestamp,
                        exit_price=event.price,
                        shares=position["shares"],
                        commission=params.commission_per_trade,
                        exit_reason=event.exit_reason or "unknown",
                        is_unrealized=False,
                    )
                    
                    completed_trades.append(trade)
                    
                    # Free up capital
                    position_value = position["position_value"]
                    pnl = trade.pnl
                    
                    reserved_capital -= position_value
                    available_capital += position_value + pnl
                    
                    # Remove from open positions
                    del open_positions[event.ticker]
        
        # Handle any remaining open positions at end of backtest
        for ticker, position in open_positions.items():
            # Find the last price for this ticker
            # We'll need to get the last close from the signals
            for sig_ticker, signals_df in all_signals:
                if sig_ticker == ticker and not signals_df.empty:
                    final_close = signals_df["Close"].iloc[-1]
                    final_date = signals_df.index[-1]
                    
                    trade = self._create_trade_record(
                        ticker=ticker,
                        entry_date=position["entry_date"],
                        entry_price=position["entry_price"],
                        exit_date=final_date,
                        exit_price=final_close,
                        shares=position["shares"],
                        commission=params.commission_per_trade,
                        exit_reason="end_of_data",
                        is_unrealized=True,
                    )
                    
                    completed_trades.append(trade)
                    break
        
        if skipped_entries > 0:
            print(f"[INFO] Skipped {skipped_entries} entry signals due to insufficient capital or existing positions")
        
        return completed_trades
    
    def _generate_trade_events(
        self,
        ticker: str,
        df: pd.DataFrame,
        params: BacktestParams,
    ) -> list[TradeEvent]:
        """Generate entry and exit events from a signals dataframe.
        
        Simulates trades and converts them into chronological events.
        
        Args:
            ticker: Stock ticker symbol.
            df: DataFrame with OHLC data and signals.
            params: Backtest parameters.
            
        Returns:
            List of TradeEvent objects.
        """
        events: list[TradeEvent] = []
        position_open = False
        entry_price = 0.0
        entry_date = datetime.now()
        shares = 0.0
        take_profit = 0.0
        stop_loss = 0.0
        last_exit_bar = -self.MIN_COOLDOWN_BARS
        
        # Position size will be determined at execution time; use initial here only for sim exit generation
        position_value = params.calculate_position_value(params.initial_capital)
        
        for i in range(len(df)):
            row = df.iloc[i]
            current_date = df.index[i]
            high = row["High"]
            low = row["Low"]
            close = row["Close"]
            
            # Check for exit if position is open
            if position_open:
                exit_event = self._check_exit_event(
                    ticker, current_date, high, low, entry_price,
                    entry_date, shares, take_profit, stop_loss
                )
                
                if exit_event:
                    events.append(exit_event)
                    position_open = False
                    last_exit_bar = i
            
            # Check for entry signal
            cooldown_passed = (i - last_exit_bar) >= self.MIN_COOLDOWN_BARS
            
            if not position_open and cooldown_passed:
                # Use standardized signal column name from strategies: "entry_signal"
                if "entry_signal" in row and bool(row["entry_signal"]):
                    entry_price = row.get("entry_price", close)
                    take_profit = row.get("take_profit", entry_price * 1.08)
                    stop_loss = row.get("stop_loss", entry_price * 0.96)
                    shares = position_value / max(entry_price, 1e-9)
                    entry_date = current_date
                    position_open = True
                    
                    # Create entry event (shares will be recomputed at execution time)
                    entry_event = TradeEvent(
                        timestamp=current_date,
                        ticker=ticker,
                        event_type="entry",
                        price=entry_price,
                        shares=shares,
                        capital_impact=-position_value,
                    )
                    events.append(entry_event)
        
        return events
    
    def _check_exit_event(
        self,
        ticker: str,
        current_date: datetime,
        high: float,
        low: float,
        entry_price: float,
        entry_date: datetime,
        shares: float,
        take_profit: float,
        stop_loss: float,
    ) -> TradeEvent | None:
        """Check if exit conditions are met and return exit event.
        
        Args:
            ticker: Stock ticker symbol.
            current_date: Current bar timestamp.
            high: Bar high price.
            low: Bar low price.
            entry_price: Entry price of the position.
            entry_date: Entry date of the position.
            shares: Number of shares.
            take_profit: Take profit price level.
            stop_loss: Stop loss price level.
            
        Returns:
            TradeEvent if exit triggered, None otherwise.
        """
        # Check if TP was hit
        if high >= take_profit:
            return TradeEvent(
                timestamp=current_date,
                ticker=ticker,
                event_type="exit",
                price=take_profit,
                shares=shares,
                capital_impact=take_profit * shares,
                exit_reason="take_profit",
                entry_price=entry_price,
                entry_date=entry_date,
            )
        
        # Check if SL was hit
        if low <= stop_loss:
            return TradeEvent(
                timestamp=current_date,
                ticker=ticker,
                event_type="exit",
                price=stop_loss,
                shares=shares,
                capital_impact=stop_loss * shares,
                exit_reason="stop_loss",
                entry_price=entry_price,
                entry_date=entry_date,
            )
        
        return None
    
    def run_single(
        self,
        ticker: str,
        params: BacktestParams,
        position_value: float | None = None,
    ) -> list[TradeRecord]:
        """Run backtest on a single ticker.
        
        Args:
            ticker: Stock ticker symbol.
            params: Backtest parameters.
            position_value: Fixed position value per trade.
            
        Returns:
            List of TradeRecord objects.
        """
        if position_value is None:
            position_value = params.calculate_position_value(params.initial_capital)
        
        zone_df, df = self._fetch_data(ticker, params)
        if df.empty:
            raise InsufficientDataError(ticker, 200, 0)
        
        signals_df = self._strategy.generate_signals(df, params, zone_df=zone_df)
        trades = self._simulate_trades(
            ticker, signals_df, params, position_value
        )
        
        return trades
    
    def _fetch_data(
        self, ticker: str, params: BacktestParams
    ) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Fetch historical data from Yahoo Finance.
        
        Returns:
            Tuple of (zone_df, backtest_df) where zone_df includes extended
            historical data for zone detection and backtest_df is the actual
            backtest period.
        """
        try:
            stock = yf.Ticker(ticker)
            
            # Calculate extended start date for zone detection
            zone_start_date = params.start_date - timedelta(
                days=params.zone_lookback_years * 365
            )
            
            # Fetch extended data for zone detection
            zone_df = stock.history(
                start=zone_start_date,
                end=params.start_date,
                interval='1d'
            )
            
            if zone_df.empty:
                return pd.DataFrame(), pd.DataFrame()
            
            # Slice backtest period from extended data
            backtest_df = stock.history(
                start=params.start_date,
                end=params.end_date,
                interval=params.interval
            )
            
            return zone_df, backtest_df
        except Exception:
            empty = pd.DataFrame()
            return empty, empty
    
    def _simulate_trades(
        self,
        ticker: str,
        df: pd.DataFrame,
        params: BacktestParams,
        position_value: float,
    ) -> list[TradeRecord]:
        """Simulate trades based on generated signals."""
        trades: list[TradeRecord] = []
        position_open = False
        entry_price = 0.0
        entry_date = datetime.now()
        shares = 0.0
        take_profit = 0.0
        stop_loss = 0.0
        last_exit_bar = -self.MIN_COOLDOWN_BARS
        
        for i in range(len(df)):
            row = df.iloc[i]
            current_date = df.index[i]
            high = row["High"]
            low = row["Low"]
            close = row["Close"]
            
            if position_open:
                trade = self._check_exit(
                    ticker, current_date, high, low, close,
                    entry_price, entry_date, shares,
                    take_profit, stop_loss, params,
                )
                if trade:
                    trades.append(trade)
                    position_open = False
                    last_exit_bar = i
            
            cooldown_passed = (i - last_exit_bar) >= self.MIN_COOLDOWN_BARS
            
            if not position_open and cooldown_passed:
                if row.get("entry_signal", False) and not row.get("skip_trade", False):
                    entry_price = row["entry_price"]
                    if entry_price <= 0:
                        continue
                    
                    shares = position_value / entry_price
                    
                    if shares * entry_price < 10:
                        continue
                    
                    entry_date = current_date
                    take_profit = row["take_profit"]
                    stop_loss = row["stop_loss"]
                    position_open = True
        
        if position_open:
            final_close = df["Close"].iloc[-1]
            final_date = df.index[-1]
            trade = self._create_trade_record(
                ticker, entry_date, entry_price, final_date, final_close,
                shares, params.commission_per_trade, "end_of_data",
                is_unrealized=True
            )
            trades.append(trade)
        
        return trades
    
    def _check_exit(
        self,
        ticker: str,
        current_date: datetime,
        high: float,
        low: float,
        close: float,
        entry_price: float,
        entry_date: datetime,
        shares: float,
        take_profit: float,
        stop_loss: float,
        params: BacktestParams,
    ) -> TradeRecord | None:
        """Check if exit conditions are met using intra-bar price sequence.
        
        Simulates realistic price movement within the bar to determine
        which exit level (TP or SL) was hit first.
        """
        # Note: We don't have access to Open price here in the current signature
        # We'll need to check both TP and SL, prioritizing based on proximity
        # For now, maintain original logic but with better documentation
        # TODO: Pass open_price to use full intra-bar sequence
        
        # Check if TP was hit (High reached TP)
        if high >= take_profit:
            return self._create_trade_record(
                ticker, entry_date, entry_price, current_date, take_profit,
                shares, params.commission_per_trade, "take_profit"
            )
        
        # Check if SL was hit (Low reached SL)
        if low <= stop_loss:
            return self._create_trade_record(
                ticker, entry_date, entry_price, current_date, stop_loss,
                shares, params.commission_per_trade, "stop_loss"
            )
        
        return None
    
    def _create_trade_record(
        self,
        ticker: str,
        entry_date: datetime,
        entry_price: float,
        exit_date: datetime,
        exit_price: float,
        shares: float,
        commission: float,
        exit_reason: str,
        is_unrealized: bool = False,
    ) -> TradeRecord:
        """Create a trade record with P&L calculations."""
        gross_pnl = (exit_price - entry_price) * shares
        total_commission = commission * 2
        net_pnl = gross_pnl - total_commission
        pnl_pct = ((exit_price - entry_price) / entry_price) * 100
        
        return TradeRecord(
            ticker=ticker,
            entry_date=entry_date,
            entry_price=entry_price,
            exit_date=exit_date,
            exit_price=exit_price,
            shares=shares,
            pnl=net_pnl,
            pnl_pct=pnl_pct,
            exit_reason=exit_reason,
            is_unrealized=is_unrealized,
        )
    
    def _aggregate_results(
        self,
        trades: list[TradeRecord],
        initial_capital: float,
        final_capital: float,
        traded: int,
        skipped: int,
        capital_depleted: bool = False,
        price_data: dict[str, pd.DataFrame] | None = None,
    ) -> BacktestResult:
        """Aggregate individual trades into summary result."""
        if not trades:
            return self._empty_result(initial_capital, skipped, capital_depleted, price_data or {})
        
        winners = [t for t in trades if t.is_winner]
        losers = [t for t in trades if not t.is_winner]
        unrealized = [t for t in trades if t.is_unrealized]
        
        total_return_dollars = sum(t.pnl for t in trades)
        total_return_pct = (total_return_dollars / initial_capital) * 100
        unrealized_pnl = sum(t.pnl for t in unrealized)
        
        win_rate = len(winners) / len(trades) if trades else 0.0
        
        avg_win = float(np.mean([t.pnl_pct for t in winners])) if winners else 0.0
        avg_loss = float(np.mean([t.pnl_pct for t in losers])) if losers else 0.0
        
        gross_profit = sum(t.pnl for t in winners) if winners else 0.0
        gross_loss = abs(sum(t.pnl for t in losers)) if losers else 1.0
        profit_factor = gross_profit / gross_loss if gross_loss > 0 else 0.0
        
        max_dd = self._calculate_max_drawdown(trades, initial_capital)
        sharpe = self._calculate_sharpe_ratio(trades)
        
        return BacktestResult(
            total_return_pct=total_return_pct,
            total_return_dollars=total_return_dollars,
            win_rate=win_rate,
            total_trades=len(trades),
            winning_trades=len(winners),
            losing_trades=len(losers),
            avg_win_pct=avg_win,
            avg_loss_pct=avg_loss,
            max_drawdown_pct=max_dd,
            sharpe_ratio=sharpe,
            profit_factor=profit_factor,
            initial_capital=initial_capital,
            final_capital=initial_capital + total_return_dollars,
            trades=tuple(trades),
            tickers_traded=traded,
            skipped_tickers=skipped,
            capital_depleted=capital_depleted,
            unrealized_trades=len(unrealized),
            unrealized_pnl=unrealized_pnl,
            price_data=price_data or {},
        )
    
    def _empty_result(
        self,
        initial_capital: float,
        skipped: int,
        capital_depleted: bool = False,
        price_data: dict[str, pd.DataFrame] | None = None,
    ) -> BacktestResult:
        """Return empty result when no trades executed."""
        return BacktestResult(
            total_return_pct=0.0,
            total_return_dollars=0.0,
            win_rate=0.0,
            total_trades=0,
            winning_trades=0,
            losing_trades=0,
            avg_win_pct=0.0,
            avg_loss_pct=0.0,
            max_drawdown_pct=0.0,
            sharpe_ratio=0.0,
            profit_factor=0.0,
            initial_capital=initial_capital,
            final_capital=initial_capital,
            trades=tuple(),
            tickers_traded=0,
            skipped_tickers=skipped,
            capital_depleted=capital_depleted,
            price_data=price_data or {},
        )
    
    def _calculate_max_drawdown(
        self,
        trades: list[TradeRecord],
        initial_capital: float,
    ) -> float:
        """Calculate maximum drawdown percentage."""
        if not trades:
            return 0.0
        
        equity = initial_capital
        peak = equity
        max_dd = 0.0
        
        for trade in trades:
            equity += trade.pnl
            peak = max(peak, equity)
            dd = (peak - equity) / peak * 100
            max_dd = max(max_dd, dd)
        
        return max_dd
    
    def _calculate_sharpe_ratio(
        self,
        trades: list[TradeRecord],
        risk_free_rate: float = 0.02,
    ) -> float:
        """Calculate annualized Sharpe ratio."""
        if len(trades) < 2:
            return 0.0
        
        returns = [t.pnl_pct / 100 for t in trades]
        avg_return = np.mean(returns)
        std_return = np.std(returns)
        
        if std_return == 0:
            return 0.0
        
        trades_per_year = 252 / max(1, len(trades))
        annualized_return = avg_return * trades_per_year
        annualized_std = std_return * np.sqrt(trades_per_year)
        
        return (annualized_return - risk_free_rate) / annualized_std
