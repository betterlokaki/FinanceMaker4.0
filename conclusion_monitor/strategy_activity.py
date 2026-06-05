"""Strategy activity classification for conclusion reports."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from strategy.mag7_ema_slope_regime_strategy import Mag7EmaSlopeRegimeLiveStrategy


@dataclass(frozen=True)
class StrategyFamilySpec:
    """Ticker-attribution rules for one strategy family."""

    key: str
    label: str
    configured_tickers: frozenset[str]


class StrategyActivityClassifier:
    """Classify report tickers into known live strategy families."""

    def __init__(
        self,
        known_families: tuple[StrategyFamilySpec, ...] | None = None,
        fallback_key: str = "earnings",
        fallback_label: str = "Earnings Strategy",
    ) -> None:
        self._known_families = known_families or self._default_families()
        self._fallback_key = fallback_key
        self._fallback_label = fallback_label

    @property
    def mag7_tickers(self) -> set[str]:
        """Return the configured MAG7 ticker universe."""
        for family in self._known_families:
            if family.key == "mag7":
                return set(family.configured_tickers)
        return set()

    def classify(
        self,
        broker_activity_tickers: set[str],
    ) -> dict[str, Any]:
        """Return strategy activity inferred only from broker evidence."""
        broker_tickers = self._normalize(broker_activity_tickers)

        payload: dict[str, Any] = {"running_today_inference": []}
        remaining = set(broker_tickers)
        for family in self._known_families:
            observed = sorted(broker_tickers & set(family.configured_tickers))
            remaining -= set(observed)
            payload[family.key] = {
                "label": family.label,
                "configured_tickers": sorted(family.configured_tickers),
                "observed_tickers": observed,
                "evidence": ["broker_activity"] if observed else [],
            }
            if observed:
                payload["running_today_inference"].append(family.key)

        fallback_observed = sorted(remaining)
        payload[self._fallback_key] = {
            "label": self._fallback_label,
            "observed_tickers": fallback_observed,
            "broker_activity_tickers": sorted(remaining),
            "evidence": ["broker_activity"] if remaining else [],
        }
        if fallback_observed:
            payload["running_today_inference"].append(self._fallback_key)
        return payload

    @staticmethod
    def _normalize(tickers: set[str]) -> set[str]:
        return {ticker.strip().upper() for ticker in tickers if ticker and ticker.strip()}

    @staticmethod
    def _default_families() -> tuple[StrategyFamilySpec, ...]:
        return (
            StrategyFamilySpec(
                key="mag7",
                label="MAG7 EMA Slope Regime",
                configured_tickers=frozenset(
                    ticker.upper()
                    for ticker in Mag7EmaSlopeRegimeLiveStrategy.MAG7_TICKERS
                ),
            ),
        )
