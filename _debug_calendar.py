"""End-to-end test of the earning demand zone backtest."""
import logging
import sys
import warnings
from datetime import date

warnings.filterwarnings("ignore")


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", handlers=[logging.StreamHandler(sys.stdout)])
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("yfinance").setLevel(logging.WARNING)
    logging.getLogger("peewee").setLevel(logging.WARNING)

    from backtesting.engines.earning_demand_zone_engine import EarningDemandZoneEngine
    from backtesting.models.backtest_params import BacktestParams
    from backtesting.strategies.earning_demand_zone_strategy import EarningDemandZoneStrategy
    from common.helpers.yahoo_earnings_calendar import YahooEarningsCalendarScraper

    strategy = EarningDemandZoneStrategy()
    calendar = YahooEarningsCalendarScraper(delay=2.0)
    engine = EarningDemandZoneEngine(strategy=strategy, calendar=calendar)

    params = BacktestParams(
        initial_capital=3000.0,
        commission_per_trade=2.5,
        position_size_pct=0.5,
        take_profit_pct=0.08,
        stop_loss_pct=0.04,
        start_date=date(2025, 1, 13),
        end_date=date(2025, 1, 17),
        interval="1d",
    )

    print("Starting end-to-end test...")
    result = engine.run(params=params)
    print(result.summary())

    if result.trades:
        print("TRADES:")
        for t in result.trades:
            print(f"  {t.ticker:8} | Entry: ${t.entry_price:.2f} | Exit: ${t.exit_price:.2f} | P&L: ${t.pnl:+.2f} | {t.exit_reason}")

    calendar.close()
    print("Test complete!")


if __name__ == "__main__":
    main()
