"""AI conclusion generation and response parsing."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from gpt.abstracts import IGPTClient


class AIConclusionRunner:
    """Run conclusion prompts through multiple AI providers."""

    def __init__(self, clients: dict[str, IGPTClient]) -> None:
        self._clients = clients

    async def run(self, prompt: str) -> dict[str, Any]:
        """Run all configured providers concurrently."""
        results = await asyncio.gather(
            *[
                self._run_provider(provider, client, prompt)
                for provider, client in self._clients.items()
            ]
        )
        providers = {result["provider"]: result for result in results}
        return {
            **providers,
            "combined": self._combined_summary(providers),
        }

    async def _run_provider(
        self,
        provider: str,
        client: IGPTClient,
        prompt: str,
    ) -> dict[str, Any]:
        try:
            raw_response = await client.generate_text(prompt)
        except Exception as exc:
            return {
                "provider": provider,
                "status": "failed",
                "parse_status": "not_run",
                "raw_response": "",
                "parsed": None,
                "error": str(exc),
            }

        parsed = _try_parse_json_object(raw_response)
        return {
            "provider": provider,
            "status": "completed",
            "parse_status": "parsed_json" if parsed is not None else "raw_text",
            "raw_response": raw_response,
            "parsed": parsed,
            "error": None,
        }

    def _combined_summary(self, providers: dict[str, dict[str, Any]]) -> dict[str, Any]:
        completed = [
            name for name, result in providers.items() if result.get("status") == "completed"
        ]
        recommendation_sets = [
            self._normalized_recommendations(result)
            for result in providers.values()
            if result.get("parsed")
        ]
        shared = set.intersection(*recommendation_sets) if recommendation_sets else set()
        return {
            "providers_completed": completed,
            "providers_failed": [
                name for name, result in providers.items() if result.get("status") != "completed"
            ],
            "shared_recommendations": sorted(shared),
            "parsed_provider_count": sum(
                1 for result in providers.values() if result.get("parse_status") == "parsed_json"
            ),
        }

    @staticmethod
    def _normalized_recommendations(result: dict[str, Any]) -> set[str]:
        parsed = result.get("parsed")
        if not isinstance(parsed, dict):
            return set()
        values = parsed.get("recommended_actions", [])
        if not isinstance(values, list):
            return set()
        return {str(value).strip().lower() for value in values if str(value).strip()}


def _try_parse_json_object(raw_response: str) -> dict[str, Any] | None:
    """Parse a JSON object from raw model output, including fenced blocks."""
    stripped = raw_response.strip()
    for candidate in _json_candidates(stripped):
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def _json_candidates(raw_response: str) -> list[str]:
    candidates = [raw_response]
    candidates.extend(
        match.group(1).strip()
        for match in re.finditer(
            r"```(?:json)?\s*(.*?)```",
            raw_response,
            flags=re.DOTALL | re.IGNORECASE,
        )
    )

    decoder = json.JSONDecoder()
    for index, char in enumerate(raw_response):
        if char != "{":
            continue
        try:
            _, end = decoder.raw_decode(raw_response[index:])
        except json.JSONDecodeError:
            continue
        candidates.append(raw_response[index : index + end])
        break

    return candidates
