"""Tests for Gemini Deep Research interaction response extraction."""
from __future__ import annotations

from types import SimpleNamespace

from gpt.gemini.gemini_base import _extract_interaction_text


def test_extract_interaction_text_from_steps_schema() -> None:
    interaction = SimpleNamespace(
        steps=[
            SimpleNamespace(content=[SimpleNamespace(type="text", text="draft")]),
            SimpleNamespace(content=[SimpleNamespace(type="text", text="final report")]),
        ]
    )

    assert _extract_interaction_text(interaction) == "final report"


def test_extract_interaction_text_from_outputs_schema() -> None:
    interaction = SimpleNamespace(
        outputs=[
            SimpleNamespace(type="text", text="legacy report"),
        ]
    )

    assert _extract_interaction_text(interaction) == "legacy report"


def test_extract_interaction_text_from_dict_schema() -> None:
    interaction = {
        "steps": [
            {
                "content": [
                    {"type": "text", "text": "dict report"},
                ]
            }
        ]
    }

    assert _extract_interaction_text(interaction) == "dict report"
