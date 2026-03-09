"""Unit tests for Grok weekly setup scanner parsing and normalization helpers."""
from __future__ import annotations

from datetime import datetime
import json

import pandas as pd

from run_grok_weekly_setup_scan import (
    normalize_daily_candles,
    parse_stage1_candidates,
    parse_stage2_setup_response,
)


def test_parse_stage1_candidates_accepts_clean_json() -> None:
    raw = json.dumps(
        {
            "candidates": [
                {
                    "ticker": "NVDA",
                    "company": "NVIDIA Corp",
                    "exchange": "NASDAQ",
                    "market_cap_usd": 2_900_000_000_000,
                    "momentum_score": 9.2,
                    "bullish_catalysts": ["AI demand"],
                    "social_sentiment": {
                        "x_summary": "Bullish trend",
                        "stocktwits_summary": "Strong chatter",
                        "key_influencers": ["account_1"],
                    },
                    "narrative": "Strong AI demand narrative.",
                    "risk_factors": ["Valuation"],
                }
            ]
        }
    )

    candidates, errors = parse_stage1_candidates(raw, top_n=10)

    assert not errors
    assert len(candidates) == 1
    assert candidates[0].ticker == "NVDA"
    assert candidates[0].exchange == "NASDAQ"


def test_parse_stage1_candidates_accepts_fenced_json() -> None:
    raw = """
    Here are the results:
    ```json
    {
      "candidates": [
        {
          "ticker": "AAPL",
          "company": "Apple Inc",
          "exchange": "NASDAQ",
          "market_cap_usd": 3000000000000,
          "momentum_score": 8.0,
          "bullish_catalysts": ["Services growth"],
          "social_sentiment": {
            "x_summary": "Positive",
            "stocktwits_summary": "Positive",
            "key_influencers": ["acct"]
          },
          "narrative": "Narrative",
          "risk_factors": ["Macro"]
        }
      ]
    }
    ```
    """

    candidates, errors = parse_stage1_candidates(raw, top_n=10)
    assert not errors
    assert [c.ticker for c in candidates] == ["AAPL"]


def test_parse_stage1_candidates_filters_invalid_and_deduplicates() -> None:
    raw = json.dumps(
        {
            "candidates": [
                {
                    "ticker": "abc",
                    "company": "ABC",
                    "exchange": "NASDAQ",
                    "market_cap_usd": 2_000_000_000,
                    "momentum_score": 7,
                },
                {
                    "ticker": "TSLA",
                    "company": "Tesla",
                    "exchange": "NASDAQ",
                    "market_cap_usd": 900_000_000,
                    "momentum_score": 9,
                },
                {
                    "ticker": "AMD",
                    "company": "AMD",
                    "exchange": "NASDAQ",
                    "market_cap_usd": 250_000_000_000,
                    "momentum_score": 8,
                },
                {
                    "ticker": "AMD",
                    "company": "AMD",
                    "exchange": "NASDAQ",
                    "market_cap_usd": 250_000_000_000,
                    "momentum_score": 9,
                },
                {
                    "ticker": "MSFT",
                    "company": "Microsoft",
                    "exchange": "NYSE",
                    "market_cap_usd": 2_000_000_000_000,
                    "momentum_score": 8.5,
                },
                {
                    "ticker": "QQQQQQ",
                    "company": "Invalid",
                    "exchange": "NASDAQ",
                    "market_cap_usd": 2_000_000_000,
                    "momentum_score": 10,
                },
            ]
        }
    )

    candidates, _ = parse_stage1_candidates(raw, top_n=2)
    assert [c.ticker for c in candidates] == ["AMD", "MSFT"]


def test_parse_stage2_setup_response_valid_setup() -> None:
    raw = json.dumps(
        {
            "ticker": "NVDA",
            "decision": "SETUP",
            "setup_type": "pullback_to_support",
            "confidence": 81,
            "entry": {"type": "limit", "price": 900.5},
            "stop_loss": 880.0,
            "take_profit": 950.0,
            "risk_reward": 2.4,
            "reasons": ["Trend and support alignment"],
            "invalidations": ["Support break"],
            "next_step": "Place a limit order near support.",
        }
    )

    parsed, errors = parse_stage2_setup_response(raw, requested_ticker="NVDA")

    assert not errors
    assert parsed is not None
    assert parsed.decision == "SETUP"
    assert parsed.entry is not None
    assert parsed.entry["price"] == 900.5


def test_parse_stage2_setup_response_rejects_wait_with_levels() -> None:
    raw = json.dumps(
        {
            "ticker": "NVDA",
            "decision": "WAIT",
            "setup_type": "wait_for_breakout",
            "confidence": 65,
            "entry": {"type": "limit", "price": 900.5},
            "stop_loss": None,
            "take_profit": None,
            "risk_reward": None,
            "reasons": ["Needs confirmation"],
            "invalidations": [],
            "next_step": "Wait for breakout close.",
        }
    )

    parsed, errors = parse_stage2_setup_response(raw, requested_ticker="NVDA")
    assert parsed is None
    assert any("requires entry/stop_loss/take_profit/risk_reward to be null" in err for err in errors)


def test_normalize_daily_candles_handles_naive_index_and_missing_rows() -> None:
    frame = pd.DataFrame(
        {
            "open": [100, 101],
            "high": [102, 103],
            "low": [99, 100],
            "close": [101, None],
            "volume": [1_000_000, 1_100_000],
        },
        index=[datetime(2026, 1, 2), datetime(2026, 1, 3)],
    )

    candles = normalize_daily_candles(frame)

    assert len(candles) == 1
    assert candles[0]["close"] == 101.0
    assert candles[0]["time"].endswith("+00:00")
