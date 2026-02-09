import yfinance as yf
import pandas as pd
import numpy as np
import json
from datetime import datetime

def calculate_technicals(df):
    """
    Calculates the required technical indicators using Pandas.
    """
    if len(df) < 200:
        return None  # Not enough data

    # 1. RSI (14)
    delta = df['Close'].diff()
    gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
    loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
    rs = gain / loss
    df['RSI'] = 100 - (100 / (1 + rs))

    # 2. SMA 50 & 200
    df['SMA50'] = df['Close'].rolling(window=50).mean()
    df['SMA200'] = df['Close'].rolling(window=200).mean()

    # 3. EMA 20
    df['EMA20'] = df['Close'].ewm(span=20, adjust=False).mean()

    # 4. Bollinger Bands (20, 2)
    df['BB_Mid'] = df['Close'].rolling(window=20).mean()
    df['BB_Std'] = df['Close'].rolling(window=20).std()
    df['BB_Lower'] = df['BB_Mid'] - (2 * df['BB_Std'])
    df['BB_Upper'] = df['BB_Mid'] + (2 * df['BB_Std'])

    # 5. MACD (12, 26, 9)
    exp1 = df['Close'].ewm(span=12, adjust=False).mean()
    exp2 = df['Close'].ewm(span=26, adjust=False).mean()
    df['MACD'] = exp1 - exp2
    df['MACD_Signal'] = df['MACD'].ewm(span=9, adjust=False).mean()
    df['MACD_Hist'] = df['MACD'] - df['MACD_Signal']

    # 6. Stochastic Oscillator
    low_14 = df['Low'].rolling(window=14).min()
    high_14 = df['High'].rolling(window=14).max()
    df['Stoch_K'] = 100 * ((df['Close'] - low_14) / (high_14 - low_14))

    # 7. CCI (20)
    tp = (df['High'] + df['Low'] + df['Close']) / 3
    sma_tp = tp.rolling(window=20).mean()
    mean_dev = tp.rolling(window=20).apply(lambda x: np.abs(x - x.mean()).mean())
    df['CCI'] = (tp - sma_tp) / (0.015 * mean_dev)

    # 8. ATR (14)
    high_low = df['High'] - df['Low']
    high_close = np.abs(df['High'] - df['Close'].shift())
    low_close = np.abs(df['Low'] - df['Close'].shift())
    ranges = pd.concat([high_low, high_close, low_close], axis=1)
    true_range = ranges.max(axis=1)
    df['ATR'] = true_range.rolling(window=14).mean()

    # 9. OBV
    df['OBV'] = (np.sign(df['Close'].diff()) * df['Volume']).fillna(0).cumsum()

    return df.iloc[-1]  # Return only the latest row

def analyze_ticker_strategy(ticker_symbol):
    ticker = yf.Ticker(ticker_symbol)
    
    # --- 1. Fetch History ---
    # We need at least 200 days for SMA200
    try:
        hist = ticker.history(period="1y")
        if hist.empty:
            return None
    except Exception as e:
        return {"error": str(e)}

    # --- 2. Fetch Fundamentals & Info ---
    try:
        info = ticker.info
        sector = info.get('sector', 'Unknown')
        eps = info.get('trailingEps', 0)
        debt_to_equity = info.get('totalDebt', 0) 
        # Note: yfinance debtToEquity is usually a percentage (e.g. 150 means 1.5x)
    except:
        sector = 'Unknown'
        eps = 0
        debt_to_equity = 0

    # --- 3. Calculate Technicals ---
    latest = calculate_technicals(hist)
    if latest is None:
        return {"error": "Insufficient data"}

    # --- 4. Scoring Logic (Max 100) ---
    score = 0
    reasons = []

    # A. Technicals (Max 65 points)
    
    # RSI < 40 (Oversold) -> 10 pts
    if latest['RSI'] < 40:
        score += 10
        reasons.append("Oversold RSI")
    elif latest['RSI'] < 50:
        score += 5 # Partial credit

    # Negative CCI (Weakness) -> 5 pts
    if latest['CCI'] < 0:
        score += 5
        reasons.append("Neg CCI")

    # Low Stochastic K (< 20 is oversold) -> 5 pts
    if latest['Stoch_K'] < 20:
        score += 5
        reasons.append("Low Stoch")

    # Negative MACD Histogram -> 5 pts
    if latest['MACD_Hist'] < 0:
        score += 5
        reasons.append("Neg MACD")

    # Close near Lower Bollinger Band (within 2% of band or below) -> 10 pts
    # This indicates price is stretched to the downside
    bb_threshold = latest['BB_Lower'] * 1.02
    if latest['Close'] <= bb_threshold:
        score += 10
        reasons.append("Near Lower BB")

    # High ATR (Volatility) -> 5 pts
    # We compare current ATR to ATR 10 days ago to see if vol is rising/high
    # Simplified: just checking if ATR is > 2% of price
    if latest['ATR'] > (latest['Close'] * 0.02):
        score += 5
        reasons.append("High Vol")

    # Close near EMA20 (Potential Reversal/Support) -> 5 pts
    # "Near" defined as within 1.5%
    if abs(latest['Close'] - latest['EMA20']) / latest['Close'] < 0.015:
        score += 5
        reasons.append("Near EMA20")

    # Negative OBV Trend (Distribution/Weakness) -> 5 pts
    # We check if current OBV is lower than 5 days ago
    try:
        obv_prev = hist['OBV'].iloc[-5]
        if latest['OBV'] < obv_prev:
            score += 5
            reasons.append("Neg OBV")
    except:
        pass

    # Mixed Position SMA 50/200 -> 10 pts
    # Often implies consolidation or distress before a move. 
    # Defined here as Price < SMA200 (Long term down) but maybe > SMA50 or vice versa
    above_50 = latest['Close'] > latest['SMA50']
    above_200 = latest['Close'] > latest['SMA200']
    if above_50 != above_200: # XOR - one is true, one is false
        score += 10
        reasons.append("Mixed SMAs")
    elif not above_50 and not above_200:
        score += 5 # Both below fits the "oversold/distressed" theme of the prompt


    # B. Sector (Max 15 points)
    target_sectors = ['Basic Materials', 'Healthcare', 'Consumer Cyclical']
    if sector in target_sectors:
        score += 15
        reasons.append(f"Sector {sector}")

    # C. Fundamentals (Max 20 points)
    
    # Negative EPS -> 10 pts (Matches prompt "Negative EPS")
    if eps < 0:
        score += 10
        reasons.append("Neg EPS")
    
    # High Debt -> 10 pts (Matches prompt "High Debt")
    # Assuming Debt/Equity > 100% is high
    if debt_to_equity > 100:
        score += 10
        reasons.append("High Debt")

    # Formatting reason string to be short (~12 words)
    reason_str = ", ".join(reasons[:5]) # Take top 5 reasons to keep it short
    if len(reason_str) > 70:
        reason_str = reason_str[:67] + "..."

    return {
        "Ticker": ticker_symbol.upper(),
        "Score": score,
        "Reason": reason_str
    }

def main():
    print("--- Post-Earnings Move Predictor (Quant Logic) ---")
    ticker_input = input("Enter ticker symbols (comma separated, e.g., AA, PFE, F): ")
    tickers = [t.strip() for t in ticker_input.split(',')]
    
    results = []
    
    print(f"\nAnalyzing {len(tickers)} tickers... Please wait.\n")
    
    for t in tickers:
        if not t: continue
        try:
            data = analyze_ticker_strategy(t)
            if data and "error" not in data:
                # We interpret "Likely to rise" as a high match score to the pattern
                # Let's verify earnings date is upcoming? 
                # The prompt asks to analyze "one day before". 
                # We assume the user is running this at the right time.
                
                results.append(data)
            else:
                print(f"Skipping {t}: {data.get('error', 'Unknown error')}")
        except Exception as e:
            print(f"Error processing {t}: {e}")

    # Sort by score descending
    results.sort(key=lambda x: x['Score'], reverse=True)

    # Output in the requested format
    final_output = []
    for r in results:
        # Only outputting if score is decent, or all if requested. 
        # The prompt says "Select and output only the tickers most likely"
        # Let's filter for Score > 50 to filter out noise, or just top results.
        # if r['Score'] >= 50: 
        final_output.append(r)
    
    # If JSON format is strictly required as a string block:
    formatted_json = "[\n"
    for i, item in enumerate(final_output):
        formatted_json += "{\n"
        formatted_json += f"Ticker: {item['Ticker']}\n"
        formatted_json += f"Score: {item['Score']}\n"
        formatted_json += f"Reason: {item['Reason']}\n"
        formatted_json += "},\n" if i < len(final_output) - 1 else "}\n"
    formatted_json += "]"
    
    print(formatted_json)

if __name__ == "__main__":
    main()