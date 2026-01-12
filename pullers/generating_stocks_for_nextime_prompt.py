import json
import yfinance_cache as yf
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
import requests
from lxml import html
import re
from common.helpers.yfinance_cache_manager import init_yfinance_cache

init_yfinance_cache()

# Headers for web requests
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
    'Referer': 'https://finviz.com/',
}


def parse_finviz_earnings_date(earnings_str):
    """Parse Finviz earnings date string like 'Nov 19 AMC' or 'Feb 24' to a datetime.date"""
    if not earnings_str:
        return None
    
    # Remove AMC/BMO suffix
    clean = re.sub(r'\s*(AMC|BMO)\s*$', '', earnings_str).strip()
    
    try:
        today = datetime.now()
        # Parse without year
        parsed = datetime.strptime(clean, '%b %d')
        # Add current year
        result = parsed.replace(year=today.year)
        
        # If the date is more than 6 months in the past, assume next year
        if (today - result).days > 180:
            result = result.replace(year=today.year + 1)
        # If the date is more than 6 months in the future, assume last year
        elif (result - today).days > 180:
            result = result.replace(year=today.year - 1)
            
        return result.date()
    except ValueError:
        return None


def get_earnings_date_finviz(ticker):
    """Get earnings date from Finviz quote page - more reliable than yfinance"""
    url = f'https://finviz.com/quote.ashx?t={ticker}&ta=1&p=d&ty=ea'
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            return None
        
        tree = html.fromstring(resp.text)
        print(tree)
        # Look for the earnings date link pattern
        earnings_links = tree.xpath('//script[@id="route-init-data"]')[0].text
        earnings_links_json = json.loads(earnings_links)
        earning_date = earnings_links_json["earningsDate"]
        return datetime.fromisoformat(earning_date)
        
    except Exception:
        pass
    
    return None


def get_earnings_gainers(tickers):
    """
    Takes a list of tickers and returns a list of dictionaries for stocks that had earnings this month (past dates only)
    and gained at least 8% after the earnings call, based on price change from close before earnings date to close on earnings date.
    """
    results = []
    today = datetime.now()
    current_month = today.month
    current_year = today.year
    for ticker in tickers:
        stock = yf.Ticker(ticker)
        try:
            # Get earnings date from Finviz (more reliable than yfinance get_earnings_dates)
            edate = get_earnings_date_finviz(ticker)
            if edate is None:
                continue
            # Check if past and this month
            if edate >= today:
                continue

            # Get historical prices around the date
            start = edate - timedelta(days=5)
            end = edate + timedelta(days=5)
            hist = stock.history(start=start, end=end)
            if hist.empty:
                continue

            # Find close before: last close before edate

            before_df = hist[hist.index.date < edate.date()]
            if before_df.empty:
                continue
            close_before = before_df['Close'].iloc[-1]

            # Find close on or after: first close on or after edate
            after_df = hist[hist.index.date >= edate.date()]
            if after_df.empty:
                continue
            close_after = after_df['Close'].iloc[0]

            # Calculate percent change
            percent = ((close_after - close_before) / close_before) * 100
            if percent >= 8:
                results.append({
                    "Ticker": ticker,
                    "EarningDate": edate.strftime("%Y-%m-%d"),
                    "Percent": f"{percent:.2f}"
                })
        except Exception as e:
            print(f"Error processing {ticker}: {e}")
            pass  # Skip on error

    return results


def get_supply_demand_zones(
    df: pd.DataFrame,
    atr_period: int = 200,
    atr_multiplier: float = 2.0,
    volume_period: int = 1000,
    lookback_bars: int = 5,
    consecutive_candles: int = 3,
    max_zones: int = 5,
) -> list[dict]:
    """
    Calculate supply and demand zones from daily candles.
    
    Based on the Pine Script "Supply and Demand Zones [BigBeluga]" indicator.
    
    Args:
        df: DataFrame with columns: Open, High, Low, Close, Volume (yfinance format).
        atr_period: Period for ATR calculation (default 200).
        atr_multiplier: Multiplier for ATR to set zone height (default 2.0).
        volume_period: Period for average volume calculation (default 1000).
        lookback_bars: Max bars to look back for trigger candle (default 5).
        consecutive_candles: Required consecutive candles for pattern (default 3).
        max_zones: Maximum zones per type to keep (default 5).
        
    Returns:
        List of zone dicts with keys: type, top, bottom, bar_index, state, delta.
    """
    if len(df) < atr_period:
        return []
    
    df = df.copy().reset_index(drop=True)
    
    # Calculate ATR
    high = df['High']
    low = df['Low']
    close = df['Close']
    prev_close = close.shift(1)
    
    tr1 = high - low
    tr2 = (high - prev_close).abs()
    tr3 = (low - prev_close).abs()
    true_range = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    atr = true_range.rolling(window=atr_period).mean() * atr_multiplier
    
    # Calculate rolling average volume
    avg_volume = df['Volume'].rolling(window=min(volume_period, len(df))).mean()
    
    # Determine candle types
    bull_candle = df['Close'] > df['Open']
    bear_candle = df['Close'] < df['Open']
    extra_vol = df['Volume'] > avg_volume
    
    supply_zones = []  # bear/supply zones
    demand_zones = []  # bull/demand zones
    
    count_bear = 0
    count_bull = 0
    
    # Iterate through bars
    for i in range(consecutive_candles, len(df)):
        current_atr = atr.iloc[i]
        if pd.isna(current_atr):
            continue
            
        # Check for supply zone (3 consecutive bear candles with extra volume on middle one)
        is_bear_pattern = all(bear_candle.iloc[i - j] for j in range(consecutive_candles))
        has_extra_vol_bear = extra_vol.iloc[i - 1] if i >= 1 else False
        
        if is_bear_pattern and has_extra_vol_bear and count_bear == 0:
            delta = 0.0
            for j in range(lookback_bars + 1):
                idx = i - j
                if idx < 0:
                    break
                if bull_candle.iloc[idx]:
                    # Found trigger candle - create supply zone
                    zone_low = low.iloc[idx]
                    supply_zones.append({
                        "type": "supply",
                        "top": zone_low + current_atr,
                        "bottom": zone_low,
                        "bar_index": idx,
                        "state": "active",
                        "delta": delta,
                    })
                    count_bear = 1
                    break
                delta += -df['Volume'].iloc[idx] if bear_candle.iloc[idx] else df['Volume'].iloc[idx]
        
        if count_bear >= 1:
            count_bear += 1
        if count_bear >= 15:
            count_bear = 0
            
        # Check for demand zone (3 consecutive bull candles with extra volume on middle one)
        is_bull_pattern = all(bull_candle.iloc[i - j] for j in range(consecutive_candles))
        has_extra_vol_bull = extra_vol.iloc[i - 1] if i >= 1 else False
        
        if is_bull_pattern and has_extra_vol_bull and count_bull == 0:
            delta = 0.0
            for j in range(lookback_bars + 1):
                idx = i - j
                if idx < 0:
                    break
                if bear_candle.iloc[idx]:
                    # Found trigger candle - create demand zone
                    zone_high = high.iloc[idx]
                    demand_zones.append({
                        "type": "demand",
                        "top": zone_high,
                        "bottom": zone_high - current_atr,
                        "bar_index": idx,
                        "state": "active",
                        "delta": delta,
                    })
                    count_bull = 1
                    break
                delta += df['Volume'].iloc[idx] if bull_candle.iloc[idx] else -df['Volume'].iloc[idx]
        
        if count_bull >= 1:
            count_bull += 1
        if count_bull >= 15:
            count_bull = 0
        
        # Update zone states based on current price
        current_close = close.iloc[i]
        current_high = high.iloc[i]
        current_low = low.iloc[i]
        
        # Update supply zones
        for zone in supply_zones[:]:
            top = zone["top"]
            bot = zone["bottom"]
            
            # Zone broken if close > top
            if current_close > top:
                zone["state"] = "broken"
            # Zone tested if price touched it
            elif zone["state"] == "active" and (i - zone["bar_index"] - 15) > 20:
                if current_high > bot and current_low < bot:
                    zone["state"] = "tested"
        
        # Update demand zones
        for zone in demand_zones[:]:
            top = zone["top"]
            bot = zone["bottom"]
            
            # Zone broken if close < bottom
            if current_close < bot:
                zone["state"] = "broken"
            # Zone tested if price touched it
            elif zone["state"] == "active" and (i - zone["bar_index"] - 15) > 20:
                if current_low < top and current_high > top:
                    zone["state"] = "tested"
    
    # Remove broken zones
    supply_zones = [z for z in supply_zones if z["state"] != "broken"]
    demand_zones = [z for z in demand_zones if z["state"] != "broken"]
    
    # Remove overlapping zones (keep more recent)
    def remove_overlapping(zones):
        if len(zones) <= 1:
            return zones
        result = []
        for i, zone in enumerate(zones):
            is_overlapped = False
            for j, other in enumerate(zones):
                if i == j:
                    continue
                # Check if other zone overlaps with this one
                if zone["type"] == "supply":
                    if other["top"] < zone["top"] and other["top"] > zone["bottom"]:
                        if other["bar_index"] > zone["bar_index"]:
                            is_overlapped = True
                            break
                else:  # demand
                    if other["bottom"] < zone["top"] and other["bottom"] > zone["bottom"]:
                        if other["bar_index"] > zone["bar_index"]:
                            is_overlapped = True
                            break
            if not is_overlapped:
                result.append(zone)
        return result
    
    supply_zones = remove_overlapping(supply_zones)
    demand_zones = remove_overlapping(demand_zones)
    
    # Keep only max_zones most recent
    supply_zones = supply_zones[-max_zones:] if len(supply_zones) > max_zones else supply_zones
    demand_zones = demand_zones[-max_zones:] if len(demand_zones) > max_zones else demand_zones
    
    return supply_zones + demand_zones


def is_in_demand_zone(ticker: str, period: str = "5y") -> bool:
    """
    Check if a ticker's current price is inside an active demand zone.
    
    Args:
        ticker: Stock ticker symbol (e.g., "AAPL").
        period: Historical data period for zone calculation (default "5y").
        
    Returns:
        True if current price is within a demand zone, False otherwise.
    """
    try:
        stock = yf.Ticker(ticker)
        df = stock.history(period=period)
        if df.empty:
            return False
        
        zones = get_supply_demand_zones(df)
        current_price = df['Close'].iloc[-1]
        
        for zone in zones:
            if zone["type"] == "demand" and zone["state"] in ("active", "tested"):
                if zone["bottom"] <= current_price <= zone["top"]:
                    return True
        return False
    except Exception:
        return False


def is_in_supply_zone(ticker: str, period: str = "5y") -> bool:
    """
    Check if a ticker's current price is inside an active supply zone.
    
    Args:
        ticker: Stock ticker symbol (e.g., "AAPL").
        period: Historical data period for zone calculation (default "5y").
        
    Returns:
        True if current price is within a supply zone, False otherwise.
    """
    try:
        stock = yf.Ticker(ticker)
        df = stock.history(period=period)
        if df.empty:
            return False
        
        zones = get_supply_demand_zones(df)
        current_price = df['Close'].iloc[-1]
        
        for zone in zones:
            if zone["type"] == "supply" and zone["state"] in ("active", "tested"):
                if zone["bottom"] <= current_price <= zone["top"]:
                    return True
        return False
    except Exception:
        return False


def filter_tickers_in_demand_zone(tickers: list[str], period: str = "5y") -> list[str]:
    """
    Filter a list of tickers to return only those with current price in a demand zone.
    
    Args:
        tickers: List of stock ticker symbols (e.g., ["AAPL", "MSFT", "GOOGL"]).
        period: Historical data period for zone calculation (default "5y").
        
    Returns:
        List of tickers whose current price is within an active demand zone.
    """
    result = []
    for ticker in tickers:
        try:
            if is_in_demand_zone(ticker, period):
                result.append(ticker)
                print(f"✓ {ticker} is in demand zone")
            else:
                print(f"✗ {ticker} is NOT in demand zone")
        except Exception as e:
            print(f"Error processing {ticker}: {e}")
    return result


# tickers = ['A', 'AAOI', 'AAON', 'ABAT', 'ABCL', 'ABEO', 'ABNB', 'ABSI', 'ABTC', 'ABUS', 'ACAD', 'ACB', 'ACDC', 'ACET', 'ACHC', 'ACHR', 'ACHV', 'ACM', 'ACMR', 'ACRS', 'ACRV', 'ACVA', 'ADI', 'ADM', 'ADMA', 'ADNT', 'ADPT', 'ADSK', 'ADT', 'ADTN', 'AEE', 'AES', 'AESI', 'AEVA', 'AFL', 'AFRM', 'AG', 'AGL', 'AHR', 'AIG', 'AIOT', 'AIRE', 'AIRS', 'AISP', 'AIV', 'AKAM', 'AKBA', 'AKRO', 'AL', 'ALAB', 'ALB', 'ALC', 'ALEC', 'ALH', 'ALIT', 'ALL', 'ALLO', 'ALM', 'ALT', 'ALTO', 'AMAT', 'AMBR', 'AMC', 'AMCR', 'AMD', 'AMGN', 'AMIX', 'AMLX', 'AMPL', 'AMPX', 'AMPY', 'AMSC', 'AMTM', 'AMTX', 'AMZE', 'ANET', 'ANF', 'ANNX', 'ANRO', 'ANVS', 'APA', 'APD', 'APLE', 'APLT', 'APO', 'APP', 'APPS', 'APVO', 'AQMS', 'AQN', 'AQST', 'ARBE', 'ARCO', 'ARCT', 'AREC', 'ARES', 'ARHS', 'ARLO', 'ARM', 'ARMK', 'ARRY', 'ARTV', 'ARVN', 'ARWR', 'ARX', 'AS', 'ASM', 'ASNS', 'ASPN', 'ASTS', 'ATAI', 'ATAT', 'ATUS', 'ATXS', 'ATYR', 'AU', 'AUID', 'AUPH', 'AUTL', 'AVAH', 'AVDL', 'AVPT', 'AVXL', 'AXIA', 'AXL', 'AZN', 'AZTR', 'B', 'BABA', 'BAK', 'BALL', 'BAM', 'BBAI', 'BBAR', 'BBWI', 'BBY', 'BCAB', 'BCE', 'BCRX', 'BDX', 'BEAM', 'BEKE', 'BEN', 'BENF', 'BG', 'BGC', 'BGS', 'BHF', 'BHVN', 'BIAF', 'BIDU', 'BILI', 'BILL', 'BITF', 'BJ', 'BKD', 'BKKT', 'BKSY', 'BKYI', 'BLDP', 'BLMN', 'BLND', 'BLNE', 'BLNK', 'BLSH', 'BMBL', 'BMEA', 'BMNR', 'BN', 'BORR', 'BOXL', 'BP', 'BRBR', 'BRK-B', 'BRKR', 'BROS', 'BRSL', 'BRY', 'BSY', 'BTAI', 'BTBT', 'BTCS', 'BTDR', 'BTG', 'BTM', 'BTOC', 'BTQ', 'BULL', 'BUR', 'BURL', 'BW', 'BWXT', 'BXSL', 'BYND', 'BZ', 'BZAI', 'CABA', 'CAN', 'CAPR', 'CARG', 'CART', 'CATX', 'CAVA', 'CC', 'CCCC', 'CCJ', 'CCO', 'CCOI', 'CDLX', 'CDNA', 'CDTX', 'CDW', 'CDZI', 'CE', 'CEG', 'CELC', 'CELH', 'CENX', 'CERS', 'CERT', 'CF', 'CGC', 'CGEM', 'CGON', 'CGTX', 'CHGG', 'CHRS', 'CHYM', 'CIFR', 'CIG', 'CISS', 'CIVI', 'CLBT', 'CLDX', 'CLNE', 'CLOV', 'CLPT', 'CLSK', 'CLX', 'CMBT', 'CMCT', 'CMPS', 'CMPX', 'CNDT', 'CNH', 'CNK', 'CNQ', 'CNTA', 'COCH', 'COCP', 'CODX', 'COGT', 'COHR', 'COLD', 'COMP', 'COP', 'COR', 'COTY', 'CPNG', 'CPRI', 'CPRT', 'CPRX', 'CPT', 'CRBG', 'CRBU', 'CRCL', 'CRGY', 'CRH', 'CRK', 'CRL', 'CRMD', 'CRNC', 'CRNX', 'CRON', 'CRSP', 'CRWV', 'CSAI', 'CSAN', 'CSCO', 'CSIQ', 'CTKB', 'CTLP', 'CTM', 'CTMX', 'CTRA', 'CTRE', 'CTRI', 'CTVA', 'CWAN', 'CWD', 'CWEN', 'CXW', 'CYPH', 'CYTK', 'DASH', 'DAVA', 'DAWN', 'DBX', 'DCGO', 'DD', 'DDD', 'DDOG', 'DE', 'DEFT', 'DEI', 'DELL', 'DENN', 'DFDV', 'DFLI', 'DGXX', 'DIS', 'DJT', 'DK', 'DKNG', 'DKS', 'DLO', 'DNA', 'DNLI', 'DNN', 'DNOW', 'DNUT', 'DOCN', 'DOCS', 'DOLE', 'DPRO', 'DRCT', 'DRH', 'DRVN', 'DT', 'DUK', 'DUOL', 'DV', 'DVAX', 'DVLT', 'DVN', 'DYN', 'EC', 'ECC', 'ECVT', 'ECX', 'ED', 'EDIT', 'EFC', 'EH', 'ELAN', 'ELBM', 'ELDN', 'ELF', 'EMBJ', 'EMN', 'EMR', 'ENB', 'ENR', 'ENVX', 'EOG', 'EOLS', 'EONR', 'EOSE', 'EQH', 'EQX', 'ERAS', 'ES', 'ESPR', 'ESTC', 'ET', 'ETHZ', 'ETN', 'ETOR', 'EU', 'EVAX', 'EVEX', 'EVGO', 'EVH', 'EVLV', 'EVRG', 'EVTL', 'EWTX', 'EXAS', 'EXC', 'EXEL', 'EXK', 'EXPD', 'EXPE', 'EXPI', 'EYE', 'EYPT', 'FANG', 'FATE', 'FEMY', 'FFAI', 'FIG', 'FIGR', 'FIGS', 'FINV', 'FIS', 'FIVN', 'FLNC', 'FLO', 'FLR', 'FLUT', 'FLY', 'FLYW', 'FNF', 'FNKO', 'FOLD', 'FOSL', 'FOUR', 'FRMI', 'FRO', 'FROG', 'FRPT', 'FRSH', 'FSCO', 'FSK', 'FSLY', 'FSM', 'FTNT', 'FTRE', 'FUBO', 'FUN', 'FUTU', 'FWONK', 'FWRG', 'G', 'GAME', 'GAP', 'GAU', 'GBDC', 'GBTG', 'GCTK', 'GDRX', 'GDS', 'GEMI', 'GEN', 'GENI', 'GEO', 'GERN', 'GETY', 'GEVO', 'GFL', 'GFS', 'GGAL', 'GLBE', 'GLMD', 'GLNG', 'GLOB', 'GLTO', 'GLUE', 'GMAB', 'GMED', 'GMHS', 'GNL', 'GNW', 'GO', 'GOGO', 'GORO', 'GOSS', 'GPCR', 'GPK', 'GPN', 'GPRE', 'GPRK', 'GPRO', 'GRAB', 'GRAL', 'GRI', 'GRND', 'GROY', 'GRPN', 'GRRR', 'GSAT', 'GSM', 'GT', 'GTBP', 'GTM', 'GTN', 'GUTS', 'GWH', 'GXO', 'HAIN', 'HALO', 'HASI', 'HBI', 'HBIO', 'HBM', 'HD', 'HE', 'HESM', 'HI', 'HIMS', 'HIMX', 'HIVE', 'HL', 'HLF', 'HLLY', 'HNGE', 'HNST', 'HOG', 'HOLX', 'HOOD', 'HOUR', 'HOUS', 'HP', 'HPP', 'HPQ', 'HRB', 'HRTX', 'HSAI', 'HSDT', 'HSIC', 'HST', 'HTCR', 'HTHT', 'HTZ', 'HUM', 'HUMA', 'HUN', 'HUT', 'HUYA', 'HYLN', 'HYMC', 'IAC', 'IAG', 'IAS', 'IAUX', 'IBIO', 'IBRX', 'ICCM', 'ICU', 'IDYA', 'IFF', 'IHRT', 'IMNM', 'IMRX', 'IMUX', 'IMVT', 'INDI', 'INFA', 'INO', 'INOD', 'INSP', 'INTR', 'INTS', 'INTU', 'INVZ', 'IOBT', 'IONQ', 'IOVA', 'IPG', 'IQ', 'IRBT', 'IREN', 'IRM', 'IRWD', 'IT', 'ITRG', 'ITUB', 'IVVD', 'IXHL', 'J', 'JACK', 'JAMF', 'JBI', 'JBS', 'JCI', 'JD', 'JELD', 'JHX', 'JKHY', 'JMIA', 'JOBY', 'KALA', 'KALV', 'KC', 'KD', 'KEYS', 'KGC', 'KGS', 'KITT', 'KKR', 'KLAR', 'KMPR', 'KNTK', 'KODK', 'KOPN', 'KOS', 'KRMN', 'KSS', 'KT', 'KTOS', 'KULR', 'KURA', 'KVUE', 'KVYO', 'LAB', 'LAC', 'LAR', 'LAZR', 'LBRDK', 'LCID', 'LCTX', 'LDI', 'LEGN', 'LEU', 'LFMD', 'LFST', 'LGN', 'LGO', 'LI', 'LIDR', 'LINE', 'LION', 'LITE', 'LMFA', 'LMND', 'LNT', 'LNTH', 'LNW', 'LOW', 'LPTH', 'LQDA', 'LRMR', 'LSCC', 'LTBR', 'LTH', 'LTM', 'LUCD', 'LUNG', 'LUNR', 'LX', 'LXEO', 'LXRX', 'LYFT', 'LYV', 'LZ', 'MAC', 'MAPS', 'MAR', 'MARA', 'MBC', 'MBOT', 'MBRX', 'MBX', 'MCD', 'MCHP', 'MDT', 'MDU', 'MET', 'MFA', 'MFC', 'MFG', 'MGNI', 'MIST', 'MKSI', 'MLCO', 'MLSS', 'MLTX', 'MLYS', 'MNDY', 'MNKD', 'MNMD', 'MNST', 'MODG', 'MOS', 'MP', 'MPC', 'MPLX', 'MQ', 'MREO', 'MRNA', 'MRVI', 'MSAI', 'MSGM', 'MT', 'MTCH', 'MTSI', 'MTVA', 'MUFG', 'MUR', 'MUX', 'MVIS', 'MVST', 'MWA', 'MYGN', 'MYO', 'NAGE', 'NAK', 'NAKA', 'NAMS', 'NAT', 'NB', 'NBIS', 'NBP', 'NBY', 'NCLH', 'NEON', 'NERV', 'NEXT', 'NFE', 'NFGC', 'NIO', 'NIQ', 'NKLR', 'NMRA', 'NN', 'NNDM', 'NNN', 'NNOX', 'NOG', 'NOMD', 'NPWR', 'NRG', 'NRGV', 'NSA', 'NTAP', 'NTLA', 'NTNX', 'NTR', 'NTRA', 'NU', 'NUAI', 'NUVB', 'NVAX', 'NVDA', 'NVO', 'NVRI', 'NVTS', 'NVVE', 'NWS', 'NWSA', 'NXDR', 'NXE', 'NXXT', 'NYT', 'O', 'OBDC', 'OC', 'OCGN', 'OCUL', 'ODP', 'OGN', 'OI', 'OKLO', 'OLMA', 'OMER', 'ON', 'ONCY', 'ONDS', 'ONON', 'ONTO', 'OPAD', 'OPEN', 'OPTU', 'OR', 'ORGN', 'ORGO', 'ORIC', 'ORLA', 'OS', 'OSCR', 'OTEX', 'OUST', 'OUT', 'OVID', 'OVV', 'OXY', 'PAA', 'PAAS', 'PACB', 'PACS', 'PAGP', 'PAGS', 'PALI', 'PANW', 'PARR', 'PAYO', 'PBA', 'PBR', 'PBR-A', 'PCOR', 'PCSA', 'PCT', 'PCVX', 'PD', 'PDD', 'PDYN', 'PEB', 'PEG', 'PENN', 'PEPG', 'PFE', 'PFGC', 'PFLT', 'PGEN', 'PGNY', 'PGY', 'PHIO', 'PINC', 'PINS', 'PLNT', 'PLTK', 'PLTR', 'PLUG', 'PMVP', 'PNW', 'POET', 'PONY', 'POWI', 'PPBT', 'PPL', 'PPTA', 'PR', 'PRCH', 'PRCT', 'PRGO', 'PRKS', 'PRLD', 'PRMB', 'PRME', 'PROK', 'PROP', 'PRPH', 'PRSO', 'PRTS', 'PSEC', 'PSKY', 'PSN', 'PSNL', 'PSNY', 'PSQH', 'PTCT', 'PTGX', 'PTLO', 'PTON', 'PTRN', 'PZZA', 'Q', 'QBTS', 'QCOM', 'QDEL', 'QFIN', 'QGEN', 'QMCO', 'QRVO', 'QSI', 'QTWO', 'QUBT', 'QURE', 'QXO', 'RAL', 'RANI', 'RARE', 'RBA', 'RC', 'RCAT', 'RCKT', 'RDW', 'REAL', 'REI', 'REKR', 'RELI', 'RELY', 'REPL', 'RERE', 'REZI', 'RGLD', 'RGTI', 'RIVN', 'RKLB', 'RLAY', 'RLJ', 'RLMD', 'RLX', 'RMTI', 'RNA', 'RNG', 'RNW', 'ROIV', 'ROST', 'RPAY', 'RPD', 'RPRX', 'RUM', 'RUN', 'RVLV', 'RVMD', 'RVPH', 'RVYL', 'RXO', 'RXRX', 'RXT', 'RYN', 'RZLT', 'SA', 'SABR', 'SANA', 'SARO', 'SATS', 'SAVA', 'SBAC', 'SBET', 'SBH', 'SBLK', 'SBRA', 'SBS', 'SCWO', 'SDGR', 'SE', 'SEDG', 'SEE', 'SEI', 'SEMR', 'SERV', 'SES', 'SFL', 'SG', 'SGHC', 'SGI', 'SGML', 'SGMO', 'SGRY', 'SHC', 'SHLS', 'SHO', 'SHOO', 'SHOP', 'SID', 'SIDU', 'SITC', 'SJM', 'SKIN', 'SKYT', 'SLDB', 'SLDE', 'SLDP', 'SLE', 'SLI', 'SLNH', 'SLNO', 'SLQT', 'SLS', 'SM', 'SMCI', 'SMFG', 'SMLR', 'SMR', 'SMTC', 'SMTK', 'SN', 'SNAP', 'SNDK', 'SNDL', 'SNDX', 'SNGX', 'SOC', 'SOLS', 'SOLV', 'SONO', 'SONY', 'SOPA', 'SOUN', 'SPCE', 'SPG', 'SPHR', 'SPOT', 'SPRY', 'SPT', 'SQM', 'SRAD', 'SRE', 'SRFM', 'SRPT', 'SRRK', 'SSKN', 'SSP', 'SSRM', 'STEX', 'STGW', 'STI', 'STIM', 'STKL', 'STNE', 'STOK', 'STUB', 'STWD', 'SU', 'SUIG', 'SUPV', 'SUZ', 'SVC', 'SVM', 'SVRA', 'SWK', 'SWKS', 'SXC', 'SY', 'SYM', 'TAC', 'TALO', 'TAP', 'TBLA', 'TCOM', 'TDC', 'TDUP', 'TE', 'TEAD', 'TECH', 'TEM', 'TERN', 'TEVA', 'TGB', 'TGNA', 'TGT', 'TGTX', 'THS', 'TIC', 'TJX', 'TKC', 'TKO', 'TLPH', 'TLS', 'TMC', 'TME', 'TNDM', 'TNGX', 'TNYA', 'TOI', 'TOST', 'TOVX', 'TPG', 'TPR', 'TREX', 'TRGP', 'TRI', 'TRIP', 'TRMB', 'TROX', 'TRP', 'TRVI', 'TRX', 'TSEM', 'TSHA', 'TSN', 'TSSI', 'TTD', 'TTEK', 'TTWO', 'TU', 'TUYA', 'TWST', 'TXG', 'TXRH', 'U', 'UA', 'UAA', 'UAMY', 'UBER', 'UGI', 'UGP', 'ULCC', 'UMAC', 'UNIT', 'UNM', 'UP', 'UPST', 'UPWK', 'UPXI', 'URBN', 'URG', 'URGN', 'USAR', 'USAS', 'USFD', 'UUU', 'UUUU', 'UWMC', 'VECO', 'VEEE', 'VEEV', 'VERA', 'VERI', 'VERX', 'VET', 'VFF', 'VG', 'VGZ', 'VIK', 'VIPS', 'VIR', 'VITL', 'VNET', 'VNO', 'VNOM', 'VNRX', 'VOD', 'VOYG', 'VRDN', 'VRTX', 'VSAT', 'VSEE', 'VSH', 'VST', 'VSTM', 'VTEX', 'VTRS', 'VTYX', 'VUZI', 'VVV', 'VYNE', 'VYX', 'WB', 'WBD', 'WDAY', 'WEN', 'WES', 'WHWK', 'WIX', 'WKHS', 'WMB', 'WMG', 'WMT', 'WOOF', 'WPM', 'WRBY', 'WRD', 'WRN', 'WSC', 'WSM', 'WTI', 'WTRG', 'WTTR', 'WULF', 'WVE', 'WWR', 'WWW', 'WYFI', 'WYNN', 'XAIR', 'XERS', 'XHLD', 'XIFR', 'XNCR', 'XP', 'XPEV', 'XPON', 'XRAY', 'XRTX', 'XTIA', 'XXII', 'XYZ', 'YETI', 'YMM', 'YOU', 'YPF', 'YUM', 'YUMC', 'ZBH', 'ZENA', 'ZETA', 'ZIM', 'ZM', 'ZS', 'ZSPC', 'ZTO', 'ZTS', 'ZURA', 'ZVRA', 'ZYME', 'ZYXI']
# tickers = []
# if not tickers:
#     # Loop through multiple pages (r parameter)
#     for r in [1, 1001]:
#         finviz_request = requests.get(
#             f"https://finviz.com/screener.ashx?v=411&f=earningsdate_thismonth,sh_avgvol_o1000&ft=4&r={r}",
#             headers={
#                 "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:116.0) Gecko/20100101 Firefox/116.0",
#                 "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
#                 "Accept-Language": "en-US,en;q=0.5",
#                 "Referer": "https://finviz.com/",
#                 "Connection": "keep-alive",
#                 "Upgrade-Insecure-Requests": "1",
#             }
#         )
        
#         # Parse the HTML
#         tree = html.fromstring(finviz_request.text)
        
#         # Find all span elements with the specified xpath
#         spans = tree.xpath("//span[contains(@onclick, \"window.location='quote.ashx?t=\")]")
        
#         # Extract inner text and append to tickers list
#         page_tickers = [span.text_content().replace('\xa0', '').replace('&nbsp;', '').strip() for span in spans]
#         tickers.extend(page_tickers)
#     # Parse the HTML
#     tree = html.fromstring(finviz_request.text)

#     # Find all span elements with the specified xpath
#     spans = tree.xpath("//span[contains(@onclick, \"window.location='quote.ashx?t=\")]")
# print(tickers)
# # ['ABVX', 'ACN', 'ADBE', 'AEO', 'AI', 'ASAN', 'ASO', 'ASPI', 'AVAV', 'AVGO', 'BB', 'BETA', 'BF-B', 'BIRK', 'BNS', 'BOX', 'BRZE', 'CAG', 'CAL', 'CBRL', 'CCL', 'CHWY', 'CIEN', 'CNM', 'COO', 'COST', 'CPB', 'CRDO', 'CRE', 'CRM', 'CRWD', 'CTAS', 'CXM', 'DBI', 'DC', 'DG', 'DLTR', 'DOCU', 'DRI', 'ENLV', 'FCEL', 'FDX', 'FERG', 'FIVE', 'GIS', 'GME', 'GTLB', 'HAFN', 'HPE', 'HRL', 'IE', 'IOT', 'JBL', 'KBH', 'KMX', 'KR', 'KTTA', 'LEN', 'LULU', 'LW', 'M', 'MDB', 'MIRA', 'MOMO', 'MRVL', 'MU', 'NAVN', 'NCNO', 'NCPL', 'NEUP', 'NKE', 'NNE', 'NTSK', 'OKTA', 'OLLI', 'OPTT', 'ORCL', 'OSRH', 'PATH', 'PAYX', 'PHR', 'PL', 'PLAB', 'PLAY', 'POM', 'PSTG', 'RBRK', 'RCT', 'RH', 'RR', 'RY', 'S', 'SAIL', 'SFIX', 'SNOW', 'SNPS', 'SUNC', 'TBH', 'TD', 'TIGR', 'TOL', 'TRX', 'TTAN', 'UEC', 'UNFI', 'UROY', 'VSCO', 'VSTS', 'VZLA']
# # Extract inner text and remove &nbsp;
# result_of_grok_code = get_earnings_gainers(tickers)

tickers = ['ABVX', 'ACN', 'ADBE', 'AEO', 'AI', 'ASAN', 'ASO', 'ASPI', 'AVAV', 'AVGO', 'BB', 'BETA', 'BF-B', 'BIRK', 'BNS', 'BOX', 'BRZE', 'CAG', 'CAL', 'CBRL', 'CCL', 'CHWY', 'CIEN', 'CNM', 'COO', 'COST', 'CPB', 'CRDO', 'CRE', 'CRM', 'CRWD', 'CTAS', 'CXM', 'DBI', 'DC', 'DG', 'DLTR', 'DOCU', 'DRI', 'ENLV', 'FCEL', 'FDX', 'FERG', 'FIVE', 'GIS', 'GME', 'GTLB', 'HAFN', 'HPE', 'HRL', 'IE', 'IOT', 'JBL', 'KBH', 'KMX', 'KR', 'KTTA', 'LEN', 'LULU', 'LW', 'M', 'MDB', 'MIRA', 'MOMO', 'MRVL', 'MU', 'NAVN', 'NCNO', 'NCPL', 'NEUP', 'NKE', 'NNE', 'NTSK', 'OKTA', 'OLLI', 'OPTT', 'ORCL', 'OSRH', 'PATH', 'PAYX', 'PHR', 'PL', 'PLAB', 'PLAY', 'POM', 'PSTG', 'RBRK', 'RCT', 'RH', 'RR', 'RY', 'S', 'SAIL', 'SFIX', 'SNOW', 'SNPS', 'SUNC', 'TBH', 'TD', 'TIGR', 'TOL', 'TRX', 'TTAN', 'UEC', 'UNFI', 'UROY', 'VSCO', 'VSTS', 'VZLA']
demand_zone_tickers = filter_tickers_in_demand_zone(tickers)
print("Tickers in Demand Zones:", demand_zone_tickers)
# print(result_of_grok_code)