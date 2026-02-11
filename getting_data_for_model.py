import yfinance as yf
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
import time

# --- 1. YOUR TICKER LISTS ---
# Add your 42 positives and 522 negatives here.
# Target 1 = Exploded (>8%), Target 0 = Did not explode.
TICKER_DATA = {
    "ADNT": {"date": "2026-02-04", "target": 1},
    "PLAB": {"date": "2025-12-10", "target": 1},
    "AAPL": {"date": "2026-02-01", "target": 0}, 
}

def fetch_ticker_features(ticker, event_date_str):
    """
    Pulls 10 technicals and fundamental trends for the day BEFORE earnings.
    """
    event_date = datetime.strptime(event_date_str, "%Y-%m-%d")
    pre_event_end = event_date # yf.download end_date is exclusive
    start_history = event_date - timedelta(days=365)
    
    try:
        # --- TECHNICAL DATA (10 FEATURES) ---
        df = yf.download(ticker, start=start_history, end=pre_event_end, progress=False)
        if len(df) < 200: return None
        
        close = df['Close']
        last_p = close.iloc[-1]
        
        # 1-2. MA Distances
        sma50 = close.rolling(50).mean().iloc[-1]
        sma200 = close.rolling(200).mean().iloc[-1]
        dist_50 = (last_p - sma50) / sma50
        dist_200 = (last_p - sma200) / sma200
        
        # 3. RSI (14)
        delta = close.diff()
        gain = (delta.where(delta > 0, 0)).rolling(14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
        rsi = 100 - (100 / (1 + (gain / loss))).iloc[-1]
        
        # 4. 5-Day Pre-Earnings Drift
        drift = ((last_p - close.iloc[-6]) / close.iloc[-6])[ticker]
        # 5. Volume Ratio
        vol_ratio = (df['Volume'].iloc[-1] / df['Volume'].tail(20).mean())[ticker]
        
        # 6. Volatility (ATR Proxy)
        volatility = ((df['High'] - df['Low']).tail(14).mean() / last_p)[ticker]
        
        # 7. Bollinger Band Width
        bb_width = (4 * close.tail(20).std()[ticker]) / close.rolling(20).mean().iloc[-1][ticker]
        
        # 8. Distance from 52-Week High
        dist_high = (df['High'].max()[ticker] - last_p) / df['High'].max()[ticker]
        
        # 9. Rate of Change (10-day)
        roc = (last_p - close.iloc[-11])[ticker] / close.iloc[-11][ticker]

        # 10. Trend Intensity (Consecutive Green Days)
        green_days = (delta.tail(5) > 0).sum()

        # --- FUNDAMENTAL DATA ---
        stock = yf.Ticker(ticker)
        q_fin = stock.quarterly_financials
        # Filter for the report released BEFORE our target date
        prev_reports = [c for c in q_fin.columns if c < event_date]
        
        rev_growth, margin_trend = 0.0, 0.0
        if len(prev_reports) >= 2:
            last_q = q_fin[prev_reports[0]]
            prev_q = q_fin[prev_reports[1]]
            
            # Revenue Growth Q-over-Q
            rev_growth = (last_q['Total Revenue'] - prev_q['Total Revenue']) / prev_q['Total Revenue']
            
            # Margin Trend (Is efficiency improving?)
            curr_margin = last_q['Gross Profit'] / last_q['Total Revenue']
            prev_margin = prev_q['Gross Profit'] / prev_q['Total Revenue']
            margin_trend = curr_margin - prev_margin

        return {
            "dist_50": dist_50, "dist_200": dist_200, "rsi": rsi, "drift": drift,
            "vol_ratio": vol_ratio, "volatility": volatility, "bb_width": bb_width,
            "dist_high": dist_high, "roc": roc, "green_days": green_days,
            "rev_growth": rev_growth, "margin_trend": margin_trend
        }

    except Exception as e:
        print(f"Error on {ticker}: {e}")
        return None

# --- 2. DATA AGGREGATION ---
final_rows = []
for ticker, info in TICKER_DATA.items():
    print(f"Fetching {ticker}...")
    feats = fetch_ticker_features(ticker, info['date'])
    if feats:
        feats.update({"ticker": ticker, "target": info['target']})
        final_rows.append(feats)
    time.sleep(0.5) # Prevent rate limiting

df_model = pd.DataFrame(final_rows)

# --- 3. THE "PREDICTOR" (Simplified Logic) ---
def calculate_score(row):
    """
    A manual scoring engine based on our 30 findings 
    (Substitute this with your XGBoost model later).
    """
    score = 50 # Start at neutral
    if row['rev_growth'] > 0.10: score += 10
    if row['margin_trend'] > 0: score += 5
    if row['drift'] > 0.02: score += 10  # Pre-earnings pump
    if row['vol_ratio'] > 1.5: score += 10 # Unusual volume
    if row['rsi'] < 40: score += 5       # Better than feared/oversold
    return min(score, 100)

if not df_model.empty:
    df_model['explosion_score'] = df_model.apply(calculate_score, axis=1)
    print("\nFinal Results:")
    print(df_model[['ticker', 'explosion_score', 'target']])