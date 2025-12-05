import json
import yfinance as yf
from datetime import datetime, timedelta
import pandas as pd
import requests
from lxml import html
import re

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

tickers = ['A', 'AAOI', 'AAON', 'ABAT', 'ABCL', 'ABEO', 'ABNB', 'ABSI', 'ABTC', 'ABUS', 'ACAD', 'ACB', 'ACDC', 'ACET', 'ACHC', 'ACHR', 'ACHV', 'ACM', 'ACMR', 'ACRS', 'ACRV', 'ACVA', 'ADI', 'ADM', 'ADMA', 'ADNT', 'ADPT', 'ADSK', 'ADT', 'ADTN', 'AEE', 'AES', 'AESI', 'AEVA', 'AFL', 'AFRM', 'AG', 'AGL', 'AHR', 'AIG', 'AIOT', 'AIRE', 'AIRS', 'AISP', 'AIV', 'AKAM', 'AKBA', 'AKRO', 'AL', 'ALAB', 'ALB', 'ALC', 'ALEC', 'ALH', 'ALIT', 'ALL', 'ALLO', 'ALM', 'ALT', 'ALTO', 'AMAT', 'AMBR', 'AMC', 'AMCR', 'AMD', 'AMGN', 'AMIX', 'AMLX', 'AMPL', 'AMPX', 'AMPY', 'AMSC', 'AMTM', 'AMTX', 'AMZE', 'ANET', 'ANF', 'ANNX', 'ANRO', 'ANVS', 'APA', 'APD', 'APLE', 'APLT', 'APO', 'APP', 'APPS', 'APVO', 'AQMS', 'AQN', 'AQST', 'ARBE', 'ARCO', 'ARCT', 'AREC', 'ARES', 'ARHS', 'ARLO', 'ARM', 'ARMK', 'ARRY', 'ARTV', 'ARVN', 'ARWR', 'ARX', 'AS', 'ASM', 'ASNS', 'ASPN', 'ASTS', 'ATAI', 'ATAT', 'ATUS', 'ATXS', 'ATYR', 'AU', 'AUID', 'AUPH', 'AUTL', 'AVAH', 'AVDL', 'AVPT', 'AVXL', 'AXIA', 'AXL', 'AZN', 'AZTR', 'B', 'BABA', 'BAK', 'BALL', 'BAM', 'BBAI', 'BBAR', 'BBWI', 'BBY', 'BCAB', 'BCE', 'BCRX', 'BDX', 'BEAM', 'BEKE', 'BEN', 'BENF', 'BG', 'BGC', 'BGS', 'BHF', 'BHVN', 'BIAF', 'BIDU', 'BILI', 'BILL', 'BITF', 'BJ', 'BKD', 'BKKT', 'BKSY', 'BKYI', 'BLDP', 'BLMN', 'BLND', 'BLNE', 'BLNK', 'BLSH', 'BMBL', 'BMEA', 'BMNR', 'BN', 'BORR', 'BOXL', 'BP', 'BRBR', 'BRK-B', 'BRKR', 'BROS', 'BRSL', 'BRY', 'BSY', 'BTAI', 'BTBT', 'BTCS', 'BTDR', 'BTG', 'BTM', 'BTOC', 'BTQ', 'BULL', 'BUR', 'BURL', 'BW', 'BWXT', 'BXSL', 'BYND', 'BZ', 'BZAI', 'CABA', 'CAN', 'CAPR', 'CARG', 'CART', 'CATX', 'CAVA', 'CC', 'CCCC', 'CCJ', 'CCO', 'CCOI', 'CDLX', 'CDNA', 'CDTX', 'CDW', 'CDZI', 'CE', 'CEG', 'CELC', 'CELH', 'CENX', 'CERS', 'CERT', 'CF', 'CGC', 'CGEM', 'CGON', 'CGTX', 'CHGG', 'CHRS', 'CHYM', 'CIFR', 'CIG', 'CISS', 'CIVI', 'CLBT', 'CLDX', 'CLNE', 'CLOV', 'CLPT', 'CLSK', 'CLX', 'CMBT', 'CMCT', 'CMPS', 'CMPX', 'CNDT', 'CNH', 'CNK', 'CNQ', 'CNTA', 'COCH', 'COCP', 'CODX', 'COGT', 'COHR', 'COLD', 'COMP', 'COP', 'COR', 'COTY', 'CPNG', 'CPRI', 'CPRT', 'CPRX', 'CPT', 'CRBG', 'CRBU', 'CRCL', 'CRGY', 'CRH', 'CRK', 'CRL', 'CRMD', 'CRNC', 'CRNX', 'CRON', 'CRSP', 'CRWV', 'CSAI', 'CSAN', 'CSCO', 'CSIQ', 'CTKB', 'CTLP', 'CTM', 'CTMX', 'CTRA', 'CTRE', 'CTRI', 'CTVA', 'CWAN', 'CWD', 'CWEN', 'CXW', 'CYPH', 'CYTK', 'DASH', 'DAVA', 'DAWN', 'DBX', 'DCGO', 'DD', 'DDD', 'DDOG', 'DE', 'DEFT', 'DEI', 'DELL', 'DENN', 'DFDV', 'DFLI', 'DGXX', 'DIS', 'DJT', 'DK', 'DKNG', 'DKS', 'DLO', 'DNA', 'DNLI', 'DNN', 'DNOW', 'DNUT', 'DOCN', 'DOCS', 'DOLE', 'DPRO', 'DRCT', 'DRH', 'DRVN', 'DT', 'DUK', 'DUOL', 'DV', 'DVAX', 'DVLT', 'DVN', 'DYN', 'EC', 'ECC', 'ECVT', 'ECX', 'ED', 'EDIT', 'EFC', 'EH', 'ELAN', 'ELBM', 'ELDN', 'ELF', 'EMBJ', 'EMN', 'EMR', 'ENB', 'ENR', 'ENVX', 'EOG', 'EOLS', 'EONR', 'EOSE', 'EQH', 'EQX', 'ERAS', 'ES', 'ESPR', 'ESTC', 'ET', 'ETHZ', 'ETN', 'ETOR', 'EU', 'EVAX', 'EVEX', 'EVGO', 'EVH', 'EVLV', 'EVRG', 'EVTL', 'EWTX', 'EXAS', 'EXC', 'EXEL', 'EXK', 'EXPD', 'EXPE', 'EXPI', 'EYE', 'EYPT', 'FANG', 'FATE', 'FEMY', 'FFAI', 'FIG', 'FIGR', 'FIGS', 'FINV', 'FIS', 'FIVN', 'FLNC', 'FLO', 'FLR', 'FLUT', 'FLY', 'FLYW', 'FNF', 'FNKO', 'FOLD', 'FOSL', 'FOUR', 'FRMI', 'FRO', 'FROG', 'FRPT', 'FRSH', 'FSCO', 'FSK', 'FSLY', 'FSM', 'FTNT', 'FTRE', 'FUBO', 'FUN', 'FUTU', 'FWONK', 'FWRG', 'G', 'GAME', 'GAP', 'GAU', 'GBDC', 'GBTG', 'GCTK', 'GDRX', 'GDS', 'GEMI', 'GEN', 'GENI', 'GEO', 'GERN', 'GETY', 'GEVO', 'GFL', 'GFS', 'GGAL', 'GLBE', 'GLMD', 'GLNG', 'GLOB', 'GLTO', 'GLUE', 'GMAB', 'GMED', 'GMHS', 'GNL', 'GNW', 'GO', 'GOGO', 'GORO', 'GOSS', 'GPCR', 'GPK', 'GPN', 'GPRE', 'GPRK', 'GPRO', 'GRAB', 'GRAL', 'GRI', 'GRND', 'GROY', 'GRPN', 'GRRR', 'GSAT', 'GSM', 'GT', 'GTBP', 'GTM', 'GTN', 'GUTS', 'GWH', 'GXO', 'HAIN', 'HALO', 'HASI', 'HBI', 'HBIO', 'HBM', 'HD', 'HE', 'HESM', 'HI', 'HIMS', 'HIMX', 'HIVE', 'HL', 'HLF', 'HLLY', 'HNGE', 'HNST', 'HOG', 'HOLX', 'HOOD', 'HOUR', 'HOUS', 'HP', 'HPP', 'HPQ', 'HRB', 'HRTX', 'HSAI', 'HSDT', 'HSIC', 'HST', 'HTCR', 'HTHT', 'HTZ', 'HUM', 'HUMA', 'HUN', 'HUT', 'HUYA', 'HYLN', 'HYMC', 'IAC', 'IAG', 'IAS', 'IAUX', 'IBIO', 'IBRX', 'ICCM', 'ICU', 'IDYA', 'IFF', 'IHRT', 'IMNM', 'IMRX', 'IMUX', 'IMVT', 'INDI', 'INFA', 'INO', 'INOD', 'INSP', 'INTR', 'INTS', 'INTU', 'INVZ', 'IOBT', 'IONQ', 'IOVA', 'IPG', 'IQ', 'IRBT', 'IREN', 'IRM', 'IRWD', 'IT', 'ITRG', 'ITUB', 'IVVD', 'IXHL', 'J', 'JACK', 'JAMF', 'JBI', 'JBS', 'JCI', 'JD', 'JELD', 'JHX', 'JKHY', 'JMIA', 'JOBY', 'KALA', 'KALV', 'KC', 'KD', 'KEYS', 'KGC', 'KGS', 'KITT', 'KKR', 'KLAR', 'KMPR', 'KNTK', 'KODK', 'KOPN', 'KOS', 'KRMN', 'KSS', 'KT', 'KTOS', 'KULR', 'KURA', 'KVUE', 'KVYO', 'LAB', 'LAC', 'LAR', 'LAZR', 'LBRDK', 'LCID', 'LCTX', 'LDI', 'LEGN', 'LEU', 'LFMD', 'LFST', 'LGN', 'LGO', 'LI', 'LIDR', 'LINE', 'LION', 'LITE', 'LMFA', 'LMND', 'LNT', 'LNTH', 'LNW', 'LOW', 'LPTH', 'LQDA', 'LRMR', 'LSCC', 'LTBR', 'LTH', 'LTM', 'LUCD', 'LUNG', 'LUNR', 'LX', 'LXEO', 'LXRX', 'LYFT', 'LYV', 'LZ', 'MAC', 'MAPS', 'MAR', 'MARA', 'MBC', 'MBOT', 'MBRX', 'MBX', 'MCD', 'MCHP', 'MDT', 'MDU', 'MET', 'MFA', 'MFC', 'MFG', 'MGNI', 'MIST', 'MKSI', 'MLCO', 'MLSS', 'MLTX', 'MLYS', 'MNDY', 'MNKD', 'MNMD', 'MNST', 'MODG', 'MOS', 'MP', 'MPC', 'MPLX', 'MQ', 'MREO', 'MRNA', 'MRVI', 'MSAI', 'MSGM', 'MT', 'MTCH', 'MTSI', 'MTVA', 'MUFG', 'MUR', 'MUX', 'MVIS', 'MVST', 'MWA', 'MYGN', 'MYO', 'NAGE', 'NAK', 'NAKA', 'NAMS', 'NAT', 'NB', 'NBIS', 'NBP', 'NBY', 'NCLH', 'NEON', 'NERV', 'NEXT', 'NFE', 'NFGC', 'NIO', 'NIQ', 'NKLR', 'NMRA', 'NN', 'NNDM', 'NNN', 'NNOX', 'NOG', 'NOMD', 'NPWR', 'NRG', 'NRGV', 'NSA', 'NTAP', 'NTLA', 'NTNX', 'NTR', 'NTRA', 'NU', 'NUAI', 'NUVB', 'NVAX', 'NVDA', 'NVO', 'NVRI', 'NVTS', 'NVVE', 'NWS', 'NWSA', 'NXDR', 'NXE', 'NXXT', 'NYT', 'O', 'OBDC', 'OC', 'OCGN', 'OCUL', 'ODP', 'OGN', 'OI', 'OKLO', 'OLMA', 'OMER', 'ON', 'ONCY', 'ONDS', 'ONON', 'ONTO', 'OPAD', 'OPEN', 'OPTU', 'OR', 'ORGN', 'ORGO', 'ORIC', 'ORLA', 'OS', 'OSCR', 'OTEX', 'OUST', 'OUT', 'OVID', 'OVV', 'OXY', 'PAA', 'PAAS', 'PACB', 'PACS', 'PAGP', 'PAGS', 'PALI', 'PANW', 'PARR', 'PAYO', 'PBA', 'PBR', 'PBR-A', 'PCOR', 'PCSA', 'PCT', 'PCVX', 'PD', 'PDD', 'PDYN', 'PEB', 'PEG', 'PENN', 'PEPG', 'PFE', 'PFGC', 'PFLT', 'PGEN', 'PGNY', 'PGY', 'PHIO', 'PINC', 'PINS', 'PLNT', 'PLTK', 'PLTR', 'PLUG', 'PMVP', 'PNW', 'POET', 'PONY', 'POWI', 'PPBT', 'PPL', 'PPTA', 'PR', 'PRCH', 'PRCT', 'PRGO', 'PRKS', 'PRLD', 'PRMB', 'PRME', 'PROK', 'PROP', 'PRPH', 'PRSO', 'PRTS', 'PSEC', 'PSKY', 'PSN', 'PSNL', 'PSNY', 'PSQH', 'PTCT', 'PTGX', 'PTLO', 'PTON', 'PTRN', 'PZZA', 'Q', 'QBTS', 'QCOM', 'QDEL', 'QFIN', 'QGEN', 'QMCO', 'QRVO', 'QSI', 'QTWO', 'QUBT', 'QURE', 'QXO', 'RAL', 'RANI', 'RARE', 'RBA', 'RC', 'RCAT', 'RCKT', 'RDW', 'REAL', 'REI', 'REKR', 'RELI', 'RELY', 'REPL', 'RERE', 'REZI', 'RGLD', 'RGTI', 'RIVN', 'RKLB', 'RLAY', 'RLJ', 'RLMD', 'RLX', 'RMTI', 'RNA', 'RNG', 'RNW', 'ROIV', 'ROST', 'RPAY', 'RPD', 'RPRX', 'RUM', 'RUN', 'RVLV', 'RVMD', 'RVPH', 'RVYL', 'RXO', 'RXRX', 'RXT', 'RYN', 'RZLT', 'SA', 'SABR', 'SANA', 'SARO', 'SATS', 'SAVA', 'SBAC', 'SBET', 'SBH', 'SBLK', 'SBRA', 'SBS', 'SCWO', 'SDGR', 'SE', 'SEDG', 'SEE', 'SEI', 'SEMR', 'SERV', 'SES', 'SFL', 'SG', 'SGHC', 'SGI', 'SGML', 'SGMO', 'SGRY', 'SHC', 'SHLS', 'SHO', 'SHOO', 'SHOP', 'SID', 'SIDU', 'SITC', 'SJM', 'SKIN', 'SKYT', 'SLDB', 'SLDE', 'SLDP', 'SLE', 'SLI', 'SLNH', 'SLNO', 'SLQT', 'SLS', 'SM', 'SMCI', 'SMFG', 'SMLR', 'SMR', 'SMTC', 'SMTK', 'SN', 'SNAP', 'SNDK', 'SNDL', 'SNDX', 'SNGX', 'SOC', 'SOLS', 'SOLV', 'SONO', 'SONY', 'SOPA', 'SOUN', 'SPCE', 'SPG', 'SPHR', 'SPOT', 'SPRY', 'SPT', 'SQM', 'SRAD', 'SRE', 'SRFM', 'SRPT', 'SRRK', 'SSKN', 'SSP', 'SSRM', 'STEX', 'STGW', 'STI', 'STIM', 'STKL', 'STNE', 'STOK', 'STUB', 'STWD', 'SU', 'SUIG', 'SUPV', 'SUZ', 'SVC', 'SVM', 'SVRA', 'SWK', 'SWKS', 'SXC', 'SY', 'SYM', 'TAC', 'TALO', 'TAP', 'TBLA', 'TCOM', 'TDC', 'TDUP', 'TE', 'TEAD', 'TECH', 'TEM', 'TERN', 'TEVA', 'TGB', 'TGNA', 'TGT', 'TGTX', 'THS', 'TIC', 'TJX', 'TKC', 'TKO', 'TLPH', 'TLS', 'TMC', 'TME', 'TNDM', 'TNGX', 'TNYA', 'TOI', 'TOST', 'TOVX', 'TPG', 'TPR', 'TREX', 'TRGP', 'TRI', 'TRIP', 'TRMB', 'TROX', 'TRP', 'TRVI', 'TRX', 'TSEM', 'TSHA', 'TSN', 'TSSI', 'TTD', 'TTEK', 'TTWO', 'TU', 'TUYA', 'TWST', 'TXG', 'TXRH', 'U', 'UA', 'UAA', 'UAMY', 'UBER', 'UGI', 'UGP', 'ULCC', 'UMAC', 'UNIT', 'UNM', 'UP', 'UPST', 'UPWK', 'UPXI', 'URBN', 'URG', 'URGN', 'USAR', 'USAS', 'USFD', 'UUU', 'UUUU', 'UWMC', 'VECO', 'VEEE', 'VEEV', 'VERA', 'VERI', 'VERX', 'VET', 'VFF', 'VG', 'VGZ', 'VIK', 'VIPS', 'VIR', 'VITL', 'VNET', 'VNO', 'VNOM', 'VNRX', 'VOD', 'VOYG', 'VRDN', 'VRTX', 'VSAT', 'VSEE', 'VSH', 'VST', 'VSTM', 'VTEX', 'VTRS', 'VTYX', 'VUZI', 'VVV', 'VYNE', 'VYX', 'WB', 'WBD', 'WDAY', 'WEN', 'WES', 'WHWK', 'WIX', 'WKHS', 'WMB', 'WMG', 'WMT', 'WOOF', 'WPM', 'WRBY', 'WRD', 'WRN', 'WSC', 'WSM', 'WTI', 'WTRG', 'WTTR', 'WULF', 'WVE', 'WWR', 'WWW', 'WYFI', 'WYNN', 'XAIR', 'XERS', 'XHLD', 'XIFR', 'XNCR', 'XP', 'XPEV', 'XPON', 'XRAY', 'XRTX', 'XTIA', 'XXII', 'XYZ', 'YETI', 'YMM', 'YOU', 'YPF', 'YUM', 'YUMC', 'ZBH', 'ZENA', 'ZETA', 'ZIM', 'ZM', 'ZS', 'ZSPC', 'ZTO', 'ZTS', 'ZURA', 'ZVRA', 'ZYME', 'ZYXI']
if not tickers:
    # Loop through multiple pages (r parameter)
    for r in [1, 1001]:
        finviz_request = requests.get(
            f"https://finviz.com/screener.ashx?v=411&f=earningsdate_thismonth,sh_avgvol_o1000&ft=4&r={r}",
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:116.0) Gecko/20100101 Firefox/116.0",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
                "Referer": "https://finviz.com/",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
            }
        )
        
        # Parse the HTML
        tree = html.fromstring(finviz_request.text)
        
        # Find all span elements with the specified xpath
        spans = tree.xpath("//span[contains(@onclick, \"window.location='quote.ashx?t=\")]")
        
        # Extract inner text and append to tickers list
        page_tickers = [span.text_content().replace('\xa0', '').replace('&nbsp;', '').strip() for span in spans]
        tickers.extend(page_tickers)
    # Parse the HTML
    tree = html.fromstring(finviz_request.text)

    # Find all span elements with the specified xpath
    spans = tree.xpath("//span[contains(@onclick, \"window.location='quote.ashx?t=\")]")
# Extract inner text and remove &nbsp;
result_of_grok_code = get_earnings_gainers(tickers)


print(result_of_grok_code)