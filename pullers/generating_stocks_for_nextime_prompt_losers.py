import json
from datetime import datetime, timedelta

import requests
import yfinance_cache as yf
from lxml import html

from common.helpers.yfinance_cache_manager import init_yfinance_cache

init_yfinance_cache()

# Headers for web requests
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": "https://finviz.com/",
}


def get_earnings_date_finviz(ticker: str) -> datetime | None:
    """Get earnings date from Finviz quote page."""
    url = f"https://finviz.com/quote.ashx?t={ticker}&ta=1&p=d&ty=ea"
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        if resp.status_code != 200:
            return None

        tree = html.fromstring(resp.text)
        route_init_data = tree.xpath('//script[@id="route-init-data"]')
        if not route_init_data:
            return None

        earnings_links_json = json.loads(route_init_data[0].text)
        earning_date = earnings_links_json.get("earningsDate")
        if not earning_date:
            return None

        return datetime.fromisoformat(earning_date)
    except Exception:
        return None


def get_earnings_losers(tickers: list[str]) -> list[dict]:
    """
    Return tickers that lost 8% or more by the 2nd trading day after earnings.

    Percent change is calculated from:
    - close_before: last close before earnings date
    - close_after_day2: close on the second trading day after earnings date
    """
    results: list[dict] = []
    today = datetime.now()

    for ticker in tickers:
        stock = yf.Ticker(ticker)
        try:
            edate = get_earnings_date_finviz(ticker)
            if edate is None or edate >= today:
                continue

            start = edate - timedelta(days=5)
            end = edate + timedelta(days=10)
            hist = stock.history(start=start, end=end)
            if hist.empty:
                continue

            before_df = hist[hist.index.date < edate.date()]
            if before_df.empty:
                continue
            close_before = before_df["Close"].iloc[-1]

            after_df = hist[hist.index.date > edate.date()]
            if len(after_df) < 2:
                continue
            close_after_day2 = after_df["Close"].iloc[1]

            percent = ((close_after_day2 - close_before) / close_before) * 100
            if percent <= -8:
                results.append(
                    {
                        "Ticker": ticker,
                        "EarningDate": edate.strftime("%Y-%m-%d"),
                        "Percent": f"{percent:.2f}",
                    }
                )
        except Exception as error:
            print(f"Error processing {ticker}: {error}")

    return results


if __name__ == "__main__":
    tickers = ['A', 'AAOI', 'AAP', 'ABAT', 'ABBV', 'ABCL', 'ABEV', 'ABNB', 'ABR', 'ABTC', 'ACAD', 'ACB', 'ACGL', 'ACH', 'ACHC', 'ACM', 'ACMR', 'ACVA', 'ADC', 'ADI', 'ADM', 'ADMA', 'ADNT', 'ADPT', 'ADSK', 'ADTN', 'AEE', 'AEG', 'AEM', 'AEP', 'AER', 'AES', 'AESI', 'AEVA', 'AFL', 'AFRM', 'AG', 'AGI', 'AGIO', 'AGL', 'AHCO', 'AHH', 'AHR', 'AI', 'AIG', 'AIOT', 'AKAM', 'AKBA', 'AKR', 'AL', 'ALAB', 'ALB', 'ALC', 'ALEC', 'ALGN', 'ALHC', 'ALIT', 'ALKS', 'ALKT', 'ALL', 'ALNY', 'AM', 'AMAT', 'AMBP', 'AMC', 'AMCR', 'AMD', 'AME', 'AMGN', 'AMH', 'AMKR', 'AMN', 'AMPL', 'AMRX', 'AMRZ', 'AMSC', 'AMT', 'AMTM', 'AMX', 'AMZN', 'ANET', 'ANGI', 'APA', 'APG', 'APLE', 'APLS', 'APO', 'APP', 'APPN', 'APPS', 'APTV', 'AR', 'ARAY', 'ARBE', 'ARCC', 'ARDX', 'ARES', 'ARHS', 'ARI', 'ARLO', 'ARM', 'ARMK', 'AROC', 'ARQT', 'ARR', 'ARRY', 'ARWR', 'AS', 'ASPN', 'ASX', 'ATEC', 'ATI', 'ATO', 'ATOM', 'AU', 'AUPH', 'AUR', 'AVB', 'AVPT', 'AVTR', 'AVXL', 'AWK', 'AXTA', 'AXTI', 'B', 'BALL', 'BAM', 'BARK', 'BAX', 'BBBY', 'BBD', 'BBIO', 'BBNX', 'BBVA', 'BCE', 'BCRX', 'BCS', 'BDN', 'BDSX', 'BDX', 'BE', 'BEAM', 'BFLY', 'BG', 'BGC', 'BHC', 'BHP', 'BIDU', 'BIIB', 'BILL', 'BIRK', 'BKD', 'BKH', 'BKSY', 'BLDR', 'BLMN', 'BLSH', 'BMRN', 'BMY', 'BN', 'BNL', 'BNS', 'BORR', 'BP', 'BR', 'BRBR', 'BRKR', 'BROS', 'BRSL', 'BRX', 'BSX', 'BSY', 'BTDR', 'BTG', 'BTI', 'BTSG', 'BTU', 'BUD', 'BUR', 'BVN', 'BWA', 'BWIN', 'BXMT', 'BXSL', 'CAH', 'CAI', 'CAKE', 'CALY', 'CAN', 'CARG', 'CARR', 'CART', 'CAVA', 'CB', 'CBRE', 'CC', 'CCC', 'CCEP', 'CCI', 'CCJ', 'CCK', 'CCO', 'CCOI', 'CDE', 'CDNS', 'CDP', 'CDW', 'CE', 'CEG', 'CELH', 'CENX', 'CERT', 'CETX', 'CF', 'CFLT', 'CG', 'CGAU', 'CGC', 'CGNX', 'CHGG', 'CHKP', 'CHYM', 'CI', 'CIFR', 'CISS', 'CLBT', 'CLF', 'CLMT', 'CLNE', 'CLOV', 'CLSK', 'CLVT', 'CLX', 'CM', 'CMBT', 'CME', 'CMG', 'CMRC', 'CMS', 'CNC', 'CNDT', 'CNH', 'CNK', 'CNP', 'CODI', 'COGT', 'COHR', 'COIN', 'COLD', 'COMP', 'COP', 'COR', 'CORT', 'COTY', 'COUR', 'CPNG', 'CPRI', 'CPRT', 'CPRX', 'CPT', 'CRBG', 'CRCL', 'CRDF', 'CRGY', 'CRH', 'CRI', 'CRK', 'CRM', 'CRNC', 'CRNX', 'CRON', 'CROX', 'CRSP', 'CRSR', 'CRWV', 'CSCO', 'CSGP', 'CSTM', 'CTRA', 'CTRE', 'CTRI', 'CTSH', 'CTVA', 'CUBE', 'CUZ', 'CVE', 'CVI', 'CVNA', 'CVS', 'CWAN', 'CWEN', 'CWH', 'CWK', 'CX', 'CYH', 'CYTK', 'CZR', 'D', 'DAN', 'DAR', 'DASH', 'DAWN', 'DBRG', 'DBX', 'DC', 'DCH', 'DD', 'DDOG', 'DE', 'DEI', 'DELL', 'DEO', 'DFTX', 'DGX', 'DHC', 'DHT', 'DINO', 'DIS', 'DK', 'DKNG', 'DLR', 'DNOW', 'DNUT', 'DOC', 'DOCN', 'DOCS', 'DOX', 'DRH', 'DRS', 'DT', 'DTE', 'DUK', 'DUOL', 'DV', 'DVA', 'DVN', 'DXCM', 'EA', 'EBAY', 'ECC', 'ECL', 'ECVT', 'ECX', 'ED', 'EDSA', 'EFC', 'EFX', 'EGHT', 'EGO', 'EHC', 'EIX', 'EL', 'ELAN', 'ELF', 'EMR', 'ENB', 'ENPH', 'ENR', 'ENTG', 'ENVX', 'EOG', 'EOSE', 'EPD', 'EPRT', 'EQH', 'EQNR', 'EQR', 'EQT', 'EQX', 'ES', 'ESI', 'ESRT', 'ESTC', 'ET', 'ETN', 'ETOR', 'ETR', 'ETSY', 'EVH', 'EVRG', 'EW', 'EXAS', 'EXC', 'EXE', 'EXEL', 'EXK', 'EXLS', 'EXPD', 'EXPE', 'EXR', 'EZPW', 'F', 'FANG', 'FATN', 'FBIN', 'FE', 'FER', 'FERG', 'FIG', 'FIGR', 'FIGS', 'FIP', 'FIS', 'FISV', 'FIVN', 'FLEX', 'FLNC', 'FLO', 'FLR', 'FLS', 'FLUT', 'FLY', 'FLYW', 'FMC', 'FND', 'FNF', 'FOLD', 'FORM', 'FOUR', 'FOX', 'FOXA', 'FR', 'FRO', 'FROG', 'FRPT', 'FRSH', 'FSK', 'FSLR', 'FSLY', 'FSM', 'FTAI', 'FTI', 'FTNT', 'FTRE', 'FTV', 'FUBO', 'FULC', 'FUN', 'FVRR', 'FWONK', 'FWRG', 'G', 'GAU', 'GBDC', 'GDDY', 'GDRX', 'GEHC', 'GEMI', 'GEN', 'GEO', 'GERN', 'GFI', 'GFL', 'GFS', 'GGB', 'GH', 'GIL', 'GILD', 'GLBE', 'GLDD', 'GLNG', 'GLOB', 'GLPI', 'GLXY', 'GMAB', 'GMED', 'GNL', 'GNRC', 'GNW', 'GOGO', 'GOOG', 'GOOGL', 'GP', 'GPC', 'GPK', 'GPN', 'GPRE', 'GRAB', 'GRAL', 'GRMN', 'GRND', 'GSBD', 'GSK', 'GSM', 'GT', 'GTES', 'GTM', 'GTN', 'GTX', 'GVH', 'GXO', 'HAFN', 'HAIN', 'HALO', 'HAS', 'HASI', 'HAYW', 'HBM', 'HD', 'HE', 'HESM', 'HIMS', 'HIMX', 'HIVE', 'HIW', 'HL', 'HLF', 'HLIT', 'HLMN', 'HLN', 'HLT', 'HLX', 'HMC', 'HNGE', 'HNST', 'HOG', 'HOOD', 'HP', 'HPP', 'HPQ', 'HR', 'HRB', 'HRL', 'HRTX', 'HSBC', 'HSIC', 'HST', 'HSY', 'HTGC', 'HTZ', 'HUBS', 'HUM', 'HUN', 'HUT', 'HWM', 'IAC', 'IAG', 'IAUX', 'IBIO', 'IBRX', 'ICE', 'ICL', 'IE', 'IEP', 'IFF', 'IHS', 'ILMN', 'IMAX', 'IMVT', 'INBS', 'INCY', 'INDI', 'INDV', 'INOD', 'INSM', 'INSP', 'INTA', 'INTR', 'INTU', 'INVH', 'INVZ', 'IONQ', 'IONS', 'IOVA', 'IQ', 'IQV', 'IR', 'IRDM', 'IREN', 'IRM', 'IRT', 'IRWD', 'IT', 'ITT', 'ITUB', 'ITW', 'JCI', 'JELD', 'JHX', 'JMIA', 'JOBY', 'KBR', 'KD', 'KDP', 'KEYS', 'KGC', 'KGS', 'KHC', 'KIM', 'KKR', 'KLAR', 'KMT', 'KNTK', 'KO', 'KRC', 'KRG', 'KT', 'KTOS', 'KVUE', 'KVYO', 'KW', 'LAB', 'LAES', 'LAUR', 'LBRDK', 'LBTYA', 'LBTYK', 'LCID', 'LEG', 'LEU', 'LFST', 'LIN', 'LINE', 'LION', 'LITE', 'LKQ', 'LLY', 'LMND', 'LNC', 'LNG', 'LNT', 'LOW', 'LPTH', 'LPX', 'LSCC', 'LTH', 'LTM', 'LUCD', 'LUMN', 'LYFT', 'LYV', 'LZ', 'MAA', 'MAC', 'MAR', 'MARA', 'MAS', 'MASI', 'MAT', 'MBC', 'MBOT', 'MCD', 'MCHP', 'MCO', 'MCW', 'MDLN', 'MDLZ', 'MDT', 'MDU', 'MET', 'METC', 'MFA', 'MFC', 'MFG', 'MGA', 'MGM', 'MGNI', 'MGY', 'MHK', 'MIAX', 'MICC', 'MIR', 'MKSI', 'MLCO', 'MLTX', 'MNDY', 'MNKD', 'MNST', 'MOD', 'MOH', 'MOS', 'MP', 'MPC', 'MPLX', 'MPT', 'MQ', 'MRK', 'MRNA', 'MRP', 'MRVI', 'MSI', 'MSTR', 'MT', 'MTCH', 'MTDR', 'MTG', 'MTSI', 'MUFG', 'MWA', 'MYGN', 'NABL', 'NAT', 'NBIS', 'NBIX', 'NE', 'NEM', 'NEO', 'NESR', 'NET', 'NEWP', 'NI', 'NIQ', 'NMRK', 'NNE', 'NNN', 'NOG', 'NOMD', 'NOV', 'NRDY', 'NRG', 'NSA', 'NSP', 'NTAP', 'NTLA', 'NTNX', 'NTR', 'NTRA', 'NTST', 'NU', 'NVAX', 'NVCR', 'NVDA', 'NVO', 'NVRI', 'NVS', 'NVST', 'NVT', 'NVTS', 'NWG', 'NWL', 'NWS', 'NWSA', 'NXDR', 'NXPI', 'NYT', 'O', 'OBDC', 'OC', 'OCUL', 'ODFL', 'OGE', 'OGI', 'OGN', 'OHI', 'OI', 'OII', 'OKE', 'OMC', 'OMF', 'ON', 'OPAD', 'OPCH', 'OPEN', 'OPK', 'OPTU', 'OR', 'ORGO', 'ORIC', 'ORLY', 'OS', 'OSCR', 'OSW', 'OTEX', 'OTF', 'OTLK', 'OUT', 'OVV', 'OWL', 'OXY', 'PAA', 'PAAS', 'PACB', 'PACS', 'PAGP', 'PANW', 'PARR', 'PAYC', 'PAYO', 'PBA', 'PBF', 'PBI', 'PCG', 'PCOR', 'PCT', 'PCVX', 'PEB', 'PEG', 'PEGA', 'PENN', 'PEP', 'PFE', 'PFG', 'PFGC', 'PFLT', 'PGNY', 'PGY', 'PHAT', 'PICS', 'PINS', 'PK', 'PLAB', 'PLNT', 'PLTK', 'PLTR', 'PM', 'PNR', 'PNW', 'POR', 'POWI', 'PPL', 'PR', 'PRCH', 'PRCT', 'PRGO', 'PRKS', 'PRMB', 'PRU', 'PSA', 'PSEC', 'PSKY', 'PSN', 'PSNL', 'PSTG', 'PSX', 'PTC', 'PTCT', 'PTEN', 'PTLO', 'PTON', 'PUMP', 'PURR', 'PWR', 'PYPL', 'Q', 'QBTS', 'QCOM', 'QDEL', 'QGEN', 'QS', 'QSR', 'QXO', 'RAL', 'RARE', 'RBA', 'RBLX', 'RC', 'RCUS', 'RDDT', 'RDN', 'RDW', 'REAL', 'REG', 'RELX', 'RELY', 'REPL', 'RES', 'REXR', 'REZI', 'RIG', 'RIO', 'RITM', 'RIVN', 'RKLB', 'RKT', 'RLAY', 'RLJ', 'RMBS', 'RNG', 'RNW', 'ROIV', 'ROKU', 'ROL', 'RPD', 'RPRX', 'RRC', 'RSG', 'RSI', 'RUN', 'RVLV', 'RVMD', 'RVTY', 'RWT', 'RXO', 'RXRX', 'RXT', 'RY', 'RYAN', 'RYN', 'RZLT', 'SABR', 'SAN', 'SARO', 'SBET', 'SBH', 'SBLK', 'SBRA', 'SBSW', 'SDGR', 'SEDG', 'SEI', 'SFL', 'SFM', 'SG', 'SGHC', 'SGI', 'SHAK', 'SHC', 'SHEL', 'SHLS', 'SHO', 'SHOO', 'SHOP', 'SIRI', 'SITC', 'SJM', 'SKM', 'SKYT', 'SLDE', 'SLDP', 'SLNO', 'SLQT', 'SM', 'SMCI', 'SMMT', 'SMR', 'SN', 'SNAP', 'SNCY', 'SNDX', 'SNOW', 'SNPS', 'SO', 'SOLS', 'SOLV', 'SON', 'SONO', 'SONY', 'SOUN', 'SPG', 'SPGI', 'SPOT', 'SPT', 'SQM', 'SRE', 'SRPT', 'SRXH', 'SSL', 'SSNC', 'SSRM', 'ST', 'STAG', 'STLA', 'STNG', 'STWD', 'SU', 'SUIG', 'SUUN', 'SUZ', 'SVC', 'SVM', 'SW', 'SWK', 'SWKS', 'SXC', 'SYM', 'TAC', 'TALK', 'TALO', 'TAP', 'TBLA', 'TCOM', 'TD', 'TDAY', 'TDC', 'TDOC', 'TDS', 'TE', 'TEAM', 'TECH', 'TECK', 'TEM', 'TENB', 'TER', 'TEX', 'TGB', 'TGTX', 'TIGO', 'TJX', 'TMHC', 'TMQ', 'TMUS', 'TNDM', 'TOL', 'TOST', 'TPG', 'TPH', 'TPR', 'TREX', 'TRGP', 'TRI', 'TRIN', 'TRIP', 'TRMB', 'TROW', 'TROX', 'TRP', 'TRU', 'TS', 'TSEM', 'TSN', 'TTD', 'TTE', 'TTI', 'TTMI', 'TTWO', 'TU', 'TVTX', 'TW', 'TWLO', 'TWO', 'TWST', 'TXG', 'U', 'UA', 'UAA', 'UBER', 'UBS', 'UDMY', 'UDR', 'UGI', 'UL', 'ULCC', 'ULS', 'UNM', 'UP', 'UPST', 'UPWK', 'UPXI', 'URBN', 'USFD', 'UTZ', 'UUUU', 'UWMC', 'VAL', 'VALE', 'VERX', 'VICI', 'VIPS', 'VIR', 'VISN', 'VIST', 'VITL', 'VKTX', 'VLN', 'VLTO', 'VMC', 'VNDA', 'VNO', 'VNOM', 'VNT', 'VRE', 'VRNS', 'VRRM', 'VRSK', 'VRT', 'VRTX', 'VSAT', 'VSH', 'VST', 'VSTS', 'VTGN', 'VTR', 'VTRS', 'VVV', 'VYX', 'W', 'WAY', 'WBD', 'WCN', 'WDAY', 'WEAV', 'WEC', 'WELL', 'WEN', 'WERN', 'WES', 'WFRD', 'WH', 'WLK', 'WMB', 'WMG', 'WMT', 'WOLF', 'WPC', 'WRBY', 'WSC', 'WTRG', 'WTTR', 'WU', 'WULF', 'WVE', 'WWW', 'WYFI', 'WYNN', 'XAIR', 'XEL', 'XIFR', 'XP', 'XPO', 'XPRO', 'XRAY', 'XYL', 'XYZ', 'YCBD', 'YELP', 'YETI', 'YOU', 'YPF', 'YUM', 'YUMC', 'Z', 'ZBH', 'ZETA', 'ZG', 'ZIP', 'ZM', 'ZS', 'ZTS', 'ZVIA']
    earnings_losers = get_earnings_losers(tickers)
    print("Tickers down 8%+ by day 2 after earnings:", earnings_losers)
