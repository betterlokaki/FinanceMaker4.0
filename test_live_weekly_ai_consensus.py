"""Tests for weekly AI consensus parsing helpers."""
from common.helpers.live_weekly_ai_consensus import (
    find_four_way_consensus,
    parse_trade_ideas,
)


def test_parse_trade_ideas_keeps_only_valid_high_scores() -> None:
    response = """
TICKER: NVDA
SCORE: 92
WHY? AI infrastructure momentum and strong trend.
BUY: 910.5
SELL: 980
STOP: 875

TICKER: AAPL
SCORE: 77
WHY? Below threshold.
BUY: 190
SELL: 205
STOP: 182
"""
    parsed = parse_trade_ideas(
        response=response,
        source="test",
        min_score=80,
        valid_tickers=["NVDA", "AAPL"],
    )

    assert set(parsed.keys()) == {"NVDA"}
    assert parsed["NVDA"].score == 92
    assert parsed["NVDA"].buy == 910.5


def test_find_four_way_consensus_intersection() -> None:
    run1 = parse_trade_ideas(
        """
TICKER: NVDA
SCORE: 90
WHY? one
BUY: 900
SELL: 960
STOP: 870
TICKER: AMD
SCORE: 89
WHY? one
BUY: 180
SELL: 195
STOP: 170
""",
        source="run1",
        valid_tickers=["NVDA", "AMD"],
    )
    run2 = parse_trade_ideas(
        """
TICKER: NVDA
SCORE: 88
WHY? two
BUY: 905
SELL: 970
STOP: 875
""",
        source="run2",
        valid_tickers=["NVDA", "AMD"],
    )
    run3 = parse_trade_ideas(
        """
TICKER: NVDA
SCORE: 93
WHY? three
BUY: 899
SELL: 965
STOP: 868
""",
        source="run3",
        valid_tickers=["NVDA", "AMD"],
    )
    run4 = parse_trade_ideas(
        """
TICKER: NVDA
SCORE: 91
WHY? four
BUY: 902
SELL: 968
STOP: 872
TICKER: AMD
SCORE: 90
WHY? four
BUY: 181
SELL: 196
STOP: 171
""",
        source="run4",
        valid_tickers=["NVDA", "AMD"],
    )

    consensus = find_four_way_consensus([run1, run2, run3, run4])
    assert set(consensus.keys()) == {"NVDA"}
    assert consensus["NVDA"].buy == 901.0
