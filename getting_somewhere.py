import asyncio
import time
from datetime import date, datetime, timedelta

import pandas as pd
import yfinance as yf

from common.di_container import container
from common.models.period import Period
from common.models.scanner_params import ScannerParams
from pullers.market.abstracts.i_market_provider import IMarketProvider
from pullers.scanners.finviz.custom_finviz import CustomFinviz

_RATE_LIMIT_DELAY: float = 2.0  # seconds between Yahoo API calls

finviz_url = "https://finviz.com/screener.ashx?v=111&f=earningsdate_thismonth%2Csh_avgvol_o1000%2Csh_price_o5&ft=4"


def get_most_recent_past_earnings_date(ticker: str) -> date | None:
    """Get the most recent past earnings date for a ticker.

    Looks back 90 days from today to find the latest earnings event.
    Uses regular yfinance (not yfinance_cache) because the cache
    wrapper does not expose the earnings_dates attribute.

    Args:
        ticker: Stock ticker symbol.

    Returns:
        The most recent past earnings date, or None if not found.
    """
    try:
        stock = yf.Ticker(ticker)
        earnings_dates = getattr(stock, "earnings_dates", None)

        if earnings_dates is None or earnings_dates.empty:
            print(f"{ticker}: No earnings dates found")
            return None

        today_ts = pd.Timestamp(date.today()).tz_localize(None)
        lookback_ts = today_ts - pd.Timedelta(days=90)

        for dt in earnings_dates.index:
            dt_naive = pd.Timestamp(dt).tz_localize(None) if dt.tzinfo else pd.Timestamp(dt)
            if lookback_ts <= dt_naive <= today_ts:
                return dt_naive.date()

        print(f"{ticker}: No earnings in the last 90 days")
        return None

    except Exception as e:
        print(f"{ticker}: Error getting earnings date - {e}")
        return None


async def get_earnings_move(
    ticker: str,
    earnings_date: date,
    market_provider: IMarketProvider,
) -> dict | None:
    """Calculate price changes around an earnings date.

    Fetches daily prices in a window around the earnings date and computes:
    - pre_earning_close: close on the last trading day before earnings
    - earning_close: close on the earnings date
    - post_earning_close: close on the first trading day after earnings
    - pct_change_earning: % change from pre-earning to earning close
    - pct_change_post: % change from pre-earning to post-earning close

    Args:
        ticker: Stock ticker symbol.
        earnings_date: The date of the earnings announcement.
        market_provider: IMarketProvider instance for fetching prices.

    Returns:
        Dict with price data and % changes, or None if insufficient data.
    """
    start = datetime.combine(earnings_date - timedelta(days=0), datetime.min.time())
    end = datetime.combine(earnings_date + timedelta(days=3), datetime.min.time())

    df = await market_provider.get_prices(ticker, start, end, Period.DAILY)

    if df is None or df.empty:
        return None

    # Normalize index to date objects for reliable comparison
    idx = pd.to_datetime(df.index)
    df_dates = idx.date  # array of datetime.date

    # Split rows into before / on / after the earnings date
    pre_rows = df[[d < earnings_date for d in df_dates]]
    earning_rows = df[[d == earnings_date for d in df_dates]]
    post_rows = df[[d > earnings_date for d in df_dates]]

    if pre_rows.empty or (earning_rows.empty and post_rows.empty):
        return None

    pre_earning_close = float(pre_rows['close'].iloc[-1])

    earning_close = float(earning_rows['close'].iloc[0]) if not earning_rows.empty else None
    post_earning_close = float(post_rows['close'].iloc[0]) if not post_rows.empty else None

    pct_change_earning = (
        (earning_close - pre_earning_close) / pre_earning_close
        if earning_close is not None else None
    )
    pct_change_post = (
        (post_earning_close - pre_earning_close) / pre_earning_close
        if post_earning_close is not None else None
    )

    return {
        'ticker': ticker,
        'earnings_date': earnings_date,
        'pre_earning_close': pre_earning_close,
        'earning_close': earning_close,
        'post_earning_close': post_earning_close,
        'pct_change_earning': pct_change_earning,
        'pct_change_post': pct_change_post,
    }


async def main():
    finviz_scanner = CustomFinviz(http_client=container.http_client(), url=finviz_url)
    tickers = await finviz_scanner.scan(ScannerParams("earnings_demand_zone_scoring"))

    print(f"Found {len(tickers)} tickers")
    print(tickers)

    N = 0.08
    big_movers = []
    market_provider = container.yahoo_market_provider()

    for idx, ticker in enumerate(tickers):
        if idx > 0:
            time.sleep(_RATE_LIMIT_DELAY)

        # 1. Pull actual earnings date for this ticker
        earnings_date = get_most_recent_past_earnings_date(ticker)
        if earnings_date is None:
            print(f"Ticker: {ticker} - no recent earnings date found, skipping")
            continue

        # 2. Compute price move around earnings
        move = await get_earnings_move(ticker, earnings_date, market_provider)
        if move is None:
            print(f"Ticker: {ticker} - not enough price data around {earnings_date}, skipping")
            continue

        pct_earning = move['pct_change_earning']
        pct_post = move['pct_change_post']

        # Default to 0 when a value is unavailable (e.g. post-earning day hasn't happened yet)
        pct_e = pct_earning if pct_earning is not None else 0.0
        pct_p = pct_post if pct_post is not None else 0.0

        print(
            f"Ticker: {ticker} (earnings: {earnings_date}), "
            f"% change earning day: {pct_e:.2%}, "
            f"% change post-earning: {pct_p:.2%}"
        )

        # 3. Combined threshold: either individual move or sum exceeds N
        if pct_e > N or pct_p > N or pct_e + pct_p > N:
            big_movers.append(move)
            print(f"  -> Added {ticker} to big movers list")

    print(f"\n{'='*60}")
    print(f"Big movers (>{N:.0%} change): {len(big_movers)}/{len(tickers)}")
    for m in big_movers:
        pct_e = m['pct_change_earning'] or 0.0
        pct_p = m['pct_change_post'] or 0.0
        print(
            f"  {m['ticker']} (earnings: {m['earnings_date']}): "
            f"earning_day={pct_e:.2%}, post_earning={pct_p:.2%}"
        )


if __name__ == "__main__":
    asyncio.run(main())
