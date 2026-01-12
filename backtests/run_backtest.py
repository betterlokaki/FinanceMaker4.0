#!/usr/bin/env python3
"""Run backtest for supply and demand zone strategy.

Entry point script for backtesting the supply/demand zone strategy
across a list of tickers with configurable parameters.
"""
from pathlib import Path
import sys
from datetime import date, timedelta


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROOT_PATH = str(PROJECT_ROOT)
if ROOT_PATH not in sys.path:
    sys.path.insert(0, ROOT_PATH)

from backtesting.engines.vectorbt_engine import VectorBTEngine
from backtesting.models.backtest_params import BacktestParams
from backtesting.strategies.supply_demand_strategy import SupplyDemandStrategy


# Tickers from the generating_stocks_for_nextime_prompt.py file
TICKERS = [
    'A', 'AAOI', 'AAON', 'ABAT', 'ABCL', 'ABEO', 'ABNB', 'ABSI', 'ABTC', 'ABUS',
    'ACAD', 'ACB', 'ACDC', 'ACET', 'ACHC', 'ACHR', 'ACHV', 'ACM', 'ACMR', 'ACRS',
    'ACRV', 'ACVA', 'ADI', 'ADM', 'ADMA', 'ADNT', 'ADPT', 'ADSK', 'ADT', 'ADTN',
    'AEE', 'AES', 'AESI', 'AEVA', 'AFL', 'AFRM', 'AG', 'AGL', 'AHR', 'AIG',
    'AIOT', 'AIRE', 'AIRS', 'AISP', 'AIV', 'AKAM', 'AKBA', 'AKRO', 'AL', 'ALAB',
    'ALB', 'ALC', 'ALEC', 'ALH', 'ALIT', 'ALL', 'ALLO', 'ALM', 'ALT', 'ALTO',
    'AMAT', 'AMBR', 'AMC', 'AMCR', 'AMD', 'AMGN', 'AMIX', 'AMLX', 'AMPL', 'AMPX',
    'AMPY', 'AMSC', 'AMTM', 'AMTX', 'AMZE', 'ANET', 'ANF', 'ANNX', 'ANRO', 'ANVS',
    'APA', 'APD', 'APLE', 'APLT', 'APO', 'APP', 'APPS', 'APVO', 'AQMS', 'AQN',
    'AQST', 'ARBE', 'ARCO', 'ARCT', 'AREC', 'ARES', 'ARHS', 'ARLO', 'ARM', 'ARMK',
    'ARRY', 'ARTV', 'ARVN', 'ARWR', 'ARX', 'AS', 'ASM', 'ASNS', 'ASPN', 'ASTS',
    'ATAI', 'ATAT', 'ATUS', 'ATXS', 'ATYR', 'AU', 'AUID', 'AUPH', 'AUTL', 'AVAH',
    'AVDL', 'AVPT', 'AVXL', 'AXIA', 'AXL', 'AZN', 'AZTR', 'B', 'BABA', 'BAK',
    'BALL', 'BAM', 'BBAI', 'BBAR', 'BBWI', 'BBY', 'BCAB', 'BCE', 'BCRX', 'BDX',
    'BEAM', 'BEKE', 'BEN', 'BENF', 'BG', 'BGC', 'BGS', 'BHF', 'BHVN', 'BIAF',
    'BIDU', 'BILI', 'BILL', 'BITF', 'BJ', 'BKD', 'BKKT', 'BKSY', 'BKYI', 'BLDP',
    'BLMN', 'BLND', 'BLNE', 'BLNK', 'BLSH', 'BMBL', 'BMEA', 'BMNR', 'BN', 'BORR',
    'BOXL', 'BP', 'BRBR', 'BRK-B', 'BRKR', 'BROS', 'BRSL', 'BRY', 'BSY', 'BTAI',
    'BTBT', 'BTCS', 'BTDR', 'BTG', 'BTM', 'BTOC', 'BTQ', 'BULL', 'BUR', 'BURL',
    'BW', 'BWXT', 'BXSL', 'BYND', 'BZ', 'BZAI', 'CABA', 'CAN', 'CAPR', 'CARG',
    'CART', 'CATX', 'CAVA', 'CC', 'CCCC', 'CCJ', 'CCO', 'CCOI', 'CDLX', 'CDNA',
    'CDTX', 'CDW', 'CDZI', 'CE', 'CEG', 'CELC', 'CELH', 'CENX', 'CERS', 'CERT',
    'CF', 'CGC', 'CGEM', 'CGON', 'CGTX', 'CHGG', 'CHRS', 'CHYM', 'CIFR', 'CIG',
    'CISS', 'CIVI', 'CLBT', 'CLDX', 'CLNE', 'CLOV', 'CLPT', 'CLSK', 'CLX', 'CMBT',
    'CMCT', 'CMPS', 'CMPX', 'CNDT', 'CNH', 'CNK', 'CNQ', 'CNTA', 'COCH', 'COCP',
    'CODX', 'COGT', 'COHR', 'COLD', 'COMP', 'COP', 'COR', 'COTY', 'CPNG', 'CPRI',
    'CPRT', 'CPRX', 'CPT', 'CRBG', 'CRBU', 'CRCL', 'CRGY', 'CRH', 'CRK', 'CRL',
    'CRMD', 'CRNC', 'CRNX', 'CRON', 'CRSP', 'CRWV', 'CSAI', 'CSAN', 'CSCO', 'CSIQ',
    'CTKB', 'CTLP', 'CTM', 'CTMX', 'CTRA', 'CTRE', 'CTRI', 'CTVA', 'CWAN', 'CWD',
    'CWEN', 'CXW', 'CYPH', 'CYTK', 'DASH', 'DAVA', 'DAWN', 'DBX', 'DCGO', 'DD',
    'DDD', 'DDOG', 'DE', 'DEFT', 'DEI', 'DELL', 'DENN', 'DFDV', 'DFLI', 'DGXX',
    'DIS', 'DJT', 'DK', 'DKNG', 'DKS', 'DLO', 'DNA', 'DNLI', 'DNN', 'DNOW', 'DNUT',
    'DOCN', 'DOCS', 'DOLE', 'DPRO', 'DRCT', 'DRH', 'DRVN', 'DT', 'DUK', 'DUOL',
    'DV', 'DVAX', 'DVLT', 'DVN', 'DYN', 'EC', 'ECC', 'ECVT', 'ECX', 'ED', 'EDIT',
    'EFC', 'EH', 'ELAN', 'ELBM', 'ELDN', 'ELF', 'EMBJ', 'EMN', 'EMR', 'ENB', 'ENR',
    'ENVX', 'EOG', 'EOLS', 'EONR', 'EOSE', 'EQH', 'EQX', 'ERAS', 'ES', 'ESPR',
    'ESTC', 'ET', 'ETHZ', 'ETN', 'ETOR', 'EU', 'EVAX', 'EVEX', 'EVGO', 'EVH', 'EVLV',
    'EVRG', 'EVTL', 'EWTX', 'EXAS', 'EXC', 'EXEL', 'EXK', 'EXPD', 'EXPE', 'EXPI',
    'EYE', 'EYPT', 'FANG', 'FATE', 'FEMY', 'FFAI', 'FIG', 'FIGR', 'FIGS', 'FINV',
    'FIS', 'FIVN', 'FLNC', 'FLO', 'FLR', 'FLUT', 'FLY', 'FLYW', 'FNF', 'FNKO',
    'FOLD', 'FOSL', 'FOUR', 'FRMI', 'FRO', 'FROG', 'FRPT', 'FRSH', 'FSCO', 'FSK',
    'FSLY', 'FSM', 'FTNT', 'FTRE', 'FUBO', 'FUN', 'FUTU', 'FWONK', 'FWRG', 'G',
    'GAME', 'GAP', 'GAU', 'GBDC', 'GBTG', 'GCTK', 'GDRX', 'GDS', 'GEMI', 'GEN',
    'GENI', 'GEO', 'GERN', 'GETY', 'GEVO', 'GFL', 'GFS', 'GGAL', 'GLBE', 'GLMD',
    'GLNG', 'GLOB', 'GLTO', 'GLUE', 'GMAB', 'GMED', 'GMHS', 'GNL', 'GNW', 'GO',
    'GOGO', 'GORO', 'GOSS', 'GPCR', 'GPK', 'GPN', 'GPRE', 'GPRK', 'GPRO', 'GRAB',
    'GRAL', 'GRI', 'GRND', 'GROY', 'GRPN', 'GRRR', 'GSAT', 'GSM', 'GT', 'GTBP',
    'GTM', 'GTN', 'GUTS', 'GWH', 'GXO', 'HAIN', 'HALO', 'HASI', 'HBI', 'HBIO',
    'HBM', 'HD', 'HE', 'HESM', 'HI', 'HIMS', 'HIMX', 'HIVE', 'HL', 'HLF', 'HLLY',
    'HNGE', 'HNST', 'HOG', 'HOLX', 'HOOD', 'HOUR', 'HOUS', 'HP', 'HPP', 'HPQ',
    'HRB', 'HRTX', 'HSAI', 'HSDT', 'HSIC', 'HST', 'HTCR', 'HTHT', 'HTZ', 'HUM',
    'HUMA', 'HUN', 'HUT', 'HUYA', 'HYLN', 'HYMC', 'IAC', 'IAG', 'IAS', 'IAUX',
    'IBIO', 'IBRX', 'ICCM', 'ICU', 'IDYA', 'IFF', 'IHRT', 'IMNM', 'IMRX', 'IMUX',
    'IMVT', 'INDI', 'INFA', 'INO', 'INOD', 'INSP', 'INTR', 'INTS', 'INTU', 'INVZ',
    'IOBT', 'IONQ', 'IOVA', 'IPG', 'IQ', 'IRBT', 'IREN', 'IRM', 'IRWD', 'IT',
    'ITRG', 'ITUB', 'IVVD', 'IXHL', 'J', 'JACK', 'JAMF', 'JBI', 'JBS', 'JCI',
    'JD', 'JELD', 'JHX', 'JKHY', 'JMIA', 'JOBY', 'KALA', 'KALV', 'KC', 'KD',
    'KEYS', 'KGC', 'KGS', 'KITT', 'KKR', 'KLAR', 'KMPR', 'KNTK', 'KODK', 'KOPN',
    'KOS', 'KRMN', 'KSS', 'KT', 'KTOS', 'KULR', 'KURA', 'KVUE', 'KVYO', 'LAB',
    'LAC', 'LAR', 'LAZR', 'LBRDK', 'LCID', 'LCTX', 'LDI', 'LEGN', 'LEU', 'LFMD',
    'LFST', 'LGN', 'LGO', 'LI', 'LIDR', 'LINE', 'LION', 'LITE', 'LMFA', 'LMND',
    'LNT', 'LNTH', 'LNW', 'LOW', 'LPTH', 'LQDA', 'LRMR', 'LSCC', 'LTBR', 'LTH',
    'LTM', 'LUCD', 'LUNG', 'LUNR', 'LX', 'LXEO', 'LXRX', 'LYFT', 'LYV', 'LZ',
    'MAC', 'MAPS', 'MAR', 'MARA', 'MBC', 'MBOT', 'MBRX', 'MBX', 'MCD', 'MCHP',
    'MDT', 'MDU', 'MET', 'MFA', 'MFC', 'MFG', 'MGNI', 'MIST', 'MKSI', 'MLCO',
    'MLSS', 'MLTX', 'MLYS', 'MNDY', 'MNKD', 'MNMD', 'MNST', 'MODG', 'MOS', 'MP',
    'MPC', 'MPLX', 'MQ', 'MREO', 'MRNA', 'MRVI', 'MSAI', 'MSGM', 'MT', 'MTCH',
    'MTSI', 'MTVA', 'MUFG', 'MUR', 'MUX', 'MVIS', 'MVST', 'MWA', 'MYGN', 'MYO',
    'NAGE', 'NAK', 'NAKA', 'NAMS', 'NAT', 'NB', 'NBIS', 'NBP', 'NBY', 'NCLH',
    'NEON', 'NERV', 'NEXT', 'NFE', 'NFGC', 'NIO', 'NIQ', 'NKLR', 'NMRA', 'NN',
    'NNDM', 'NNN', 'NNOX', 'NOG', 'NOMD', 'NPWR', 'NRG', 'NRGV', 'NSA', 'NTAP',
    'NTLA', 'NTNX', 'NTR', 'NTRA', 'NU', 'NUAI', 'NUVB', 'NVAX', 'NVDA', 'NVO',
    'NVRI', 'NVTS', 'NVVE', 'NWS', 'NWSA', 'NXDR', 'NXE', 'NXXT', 'NYT', 'O',
    'OBDC', 'OC', 'OCGN', 'OCUL', 'ODP', 'OGN', 'OI', 'OKLO', 'OLMA', 'OMER',
    'ON', 'ONCY', 'ONDS', 'ONON', 'ONTO', 'OPAD', 'OPEN', 'OPTU', 'OR', 'ORGN',
    'ORGO', 'ORIC', 'ORLA', 'OS', 'OSCR', 'OTEX', 'OUST', 'OUT', 'OVID', 'OVV',
    'OXY', 'PAA', 'PAAS', 'PACB', 'PACS', 'PAGP', 'PAGS', 'PALI', 'PANW', 'PARR',
    'PAYO', 'PBA', 'PBR', 'PBR-A', 'PCOR', 'PCSA', 'PCT', 'PCVX', 'PD', 'PDD',
    'PDYN', 'PEB', 'PEG', 'PENN', 'PEPG', 'PFE', 'PFGC', 'PFLT', 'PGEN', 'PGNY',
    'PGY', 'PHIO', 'PINC', 'PINS', 'PLNT', 'PLTK', 'PLTR', 'PLUG', 'PMVP', 'PNW',
    'POET', 'PONY', 'POWI', 'PPBT', 'PPL', 'PPTA', 'PR', 'PRCH', 'PRCT', 'PRGO',
    'PRKS', 'PRLD', 'PRMB', 'PRME', 'PROK', 'PROP', 'PRPH', 'PRSO', 'PRTS', 'PSEC',
    'PSKY', 'PSN', 'PSNL', 'PSNY', 'PSQH', 'PTCT', 'PTGX', 'PTLO', 'PTON', 'PTRN',
    'PZZA', 'Q', 'QBTS', 'QCOM', 'QDEL', 'QFIN', 'QGEN', 'QMCO', 'QRVO', 'QSI',
    'QTWO', 'QUBT', 'QURE', 'QXO', 'RAL', 'RANI', 'RARE', 'RBA', 'RC', 'RCAT',
    'RCKT', 'RDW', 'REAL', 'REI', 'REKR', 'RELI', 'RELY', 'REPL', 'RERE', 'REZI',
    'RGLD', 'RGTI', 'RIVN', 'RKLB', 'RLAY', 'RLJ', 'RLMD', 'RLX', 'RMTI', 'RNA',
    'RNG', 'RNW', 'ROIV', 'ROST', 'RPAY', 'RPD', 'RPRX', 'RUM', 'RUN', 'RVLV',
    'RVMD', 'RVPH', 'RVYL', 'RXO', 'RXRX', 'RXT', 'RYN', 'RZLT', 'SA', 'SABR',
    'SANA', 'SARO', 'SATS', 'SAVA', 'SBAC', 'SBET', 'SBH', 'SBLK', 'SBRA', 'SBS',
    'SCWO', 'SDGR', 'SE', 'SEDG', 'SEE', 'SEI', 'SEMR', 'SERV', 'SES', 'SFL',
    'SG', 'SGHC', 'SGI', 'SGML', 'SGMO', 'SGRY', 'SHC', 'SHLS', 'SHO', 'SHOO',
    'SHOP', 'SID', 'SIDU', 'SITC', 'SJM', 'SKIN', 'SKYT', 'SLDB', 'SLDE', 'SLDP',
    'SLE', 'SLI', 'SLNH', 'SLNO', 'SLQT', 'SLS', 'SM', 'SMCI', 'SMFG', 'SMLR',
    'SMR', 'SMTC', 'SMTK', 'SN', 'SNAP', 'SNDK', 'SNDL', 'SNDX', 'SNGX', 'SOC',
    'SOLS', 'SOLV', 'SONO', 'SONY', 'SOPA', 'SOUN', 'SPCE', 'SPG', 'SPHR', 'SPOT',
    'SPRY', 'SPT', 'SQM', 'SRAD', 'SRE', 'SRFM', 'SRPT', 'SRRK', 'SSKN', 'SSP',
    'SSRM', 'STEX', 'STGW', 'STI', 'STIM', 'STKL', 'STNE', 'STOK', 'STUB', 'STWD',
    'SU', 'SUIG', 'SUPV', 'SUZ', 'SVC', 'SVM', 'SVRA', 'SWK', 'SWKS', 'SXC',
    'SY', 'SYM', 'TAC', 'TALO', 'TAP', 'TBLA', 'TCOM', 'TDC', 'TDUP', 'TE',
    'TEAD', 'TECH', 'TEM', 'TERN', 'TEVA', 'TGB', 'TGNA', 'TGT', 'TGTX', 'THS',
    'TIC', 'TJX', 'TKC', 'TKO', 'TLPH', 'TLS', 'TMC', 'TME', 'TNDM', 'TNGX',
    'TNYA', 'TOI', 'TOST', 'TOVX', 'TPG', 'TPR', 'TREX', 'TRGP', 'TRI', 'TRIP',
    'TRMB', 'TROX', 'TRP', 'TRVI', 'TRX', 'TSEM', 'TSHA', 'TSN', 'TSSI', 'TTD',
    'TTEK', 'TTWO', 'TU', 'TUYA', 'TWST', 'TXG', 'TXRH', 'U', 'UA', 'UAA',
    'UAMY', 'UBER', 'UGI', 'UGP', 'ULCC', 'UMAC', 'UNIT', 'UNM', 'UP', 'UPST',
    'UPWK', 'UPXI', 'URBN', 'URG', 'URGN', 'USAR', 'USAS', 'USFD', 'UUU', 'UUUU',
    'UWMC', 'VECO', 'VEEE', 'VEEV', 'VERA', 'VERI', 'VERX', 'VET', 'VFF', 'VG',
    'VGZ', 'VIK', 'VIPS', 'VIR', 'VITL', 'VNET', 'VNO', 'VNOM', 'VNRX', 'VOD',
    'VOYG', 'VRDN', 'VRTX', 'VSAT', 'VSEE', 'VSH', 'VST', 'VSTM', 'VTEX', 'VTRS',
    'VTYX', 'VUZI', 'VVV', 'VYNE', 'VYX', 'WB', 'WBD', 'WDAY', 'WEN', 'WES',
    'WHWK', 'WIX', 'WKHS', 'WMB', 'WMG', 'WMT', 'WOOF', 'WPM', 'WRBY', 'WRD',
    'WRN', 'WSC', 'WSM', 'WTI', 'WTRG', 'WTTR', 'WULF', 'WVE', 'WWR', 'WWW',
    'WYFI', 'WYNN', 'XAIR', 'XERS', 'XHLD', 'XIFR', 'XNCR', 'XP', 'XPEV', 'XPON',
    'XRAY', 'XRTX', 'XTIA', 'XXII', 'XYZ', 'YETI', 'YMM', 'YOU', 'YPF', 'YUM',
    'YUMC', 'ZBH', 'ZENA', 'ZETA', 'ZIM', 'ZM', 'ZS', 'ZSPC', 'ZTO', 'ZTS',
    'ZURA', 'ZVRA', 'ZYME', 'ZYXI'
][:300]


def main() -> None:
    """Run the backtest and print results."""
    print("=" * 60)
    print("SUPPLY & DEMAND ZONE BACKTEST")
    print("=" * 60)
    print(f"Tickers to test: {len(TICKERS)}")
    print("=" * 60)
    
    # Use explicit date range instead of period string
    start_date = date.today() - timedelta(days=5 * 365)
    end_date = date.today()
    
    params = BacktestParams(
        initial_capital=3000.0,
        commission_per_trade=2.5,
        position_size_pct=0.5,
        take_profit_pct=0.08,
        stop_loss_pct=0.01,
        supply_skip_distance_pct=0.08,
        start_date=start_date,
        end_date=end_date,
    )
    
    print("\nBacktest Parameters:")
    print(f"  Initial Capital:     ${params.initial_capital:,.2f}")
    print(f"  Commission/Trade:    ${params.commission_per_trade:.2f}")
    print(f"  Position Size:       {params.position_size_pct*100:.0f}% of capital")
    print(f"  Take Profit:         {params.take_profit_pct*100:.0f}%")
    print(f"  Stop Loss:           {params.stop_loss_pct*100:.0f}% below zone")
    print(f"  Supply Skip:         {params.supply_skip_distance_pct*100:.0f}%")
    print(f"  Date Range:          {params.start_date} to {params.end_date}")
    print("=" * 60)
    
    strategy = SupplyDemandStrategy()
    engine = VectorBTEngine(strategy=strategy)
    
    print(f"\nRunning backtest with {strategy.name}...")
    print("This may take several minutes...\n")
    
    result = engine.run(tickers=TICKERS, params=params)
    
    print(result.summary())
    
    _print_trade_details(result)


def _print_trade_details(result) -> None:
    """Print detailed trade information."""
    if not result.trades:
        print("No trades executed.")
        return
    
    print("\nTOP 10 WINNING TRADES:")
    print("-" * 60)
    winners = sorted(
        [t for t in result.trades if t.is_winner],
        key=lambda x: x.pnl,
        reverse=True,
    )[:10]
    
    for t in winners:
        print(
            f"  {t.ticker:6} | Entry: ${t.entry_price:8.2f} | "
            f"Exit: ${t.exit_price:8.2f} | P&L: ${t.pnl:+8.2f} ({t.pnl_pct:+.1f}%)"
        )
    
    print("\nTOP 10 LOSING TRADES:")
    print("-" * 60)
    losers = sorted(
        [t for t in result.trades if not t.is_winner],
        key=lambda x: x.pnl,
    )[:10]
    
    for t in losers:
        print(
            f"  {t.ticker:6} | Entry: ${t.entry_price:8.2f} | "
            f"Exit: ${t.exit_price:8.2f} | P&L: ${t.pnl:+8.2f} ({t.pnl_pct:+.1f}%)"
        )


if __name__ == "__main__":
    main()
