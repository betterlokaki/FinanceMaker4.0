"""Google Gemini API client with Google Search grounding and configurable thinking.

Uses the native google-genai SDK for:
- Regular generate_content API (NOT Deep Research)
- Google Search grounding tool for real-time web data
- Configurable thinking levels / budgets for enhanced reasoning
- Model selection (any Gemini model)
"""
import logging
from enum import StrEnum
from typing import Final

import httpx
from google import genai
from google.genai import types

from common.settings import settings
from gpt.abstracts.gpt_base import GPTBase
from gpt.gemini.model_pricing import DEFAULT_PRICING, MODEL_PRICING_MAP, ModelPricing

logger: logging.Logger = logging.getLogger(__name__)

DEFAULT_SYSTEM_PROMPT: Final[str] = (
    "You are a financial stock analyst. When you analyze stocks, "
    "use real-time market data, recent news, and historical context. "
    "Search the web for relevant articles, financial reports, and market data. "
    "Cite each source (with URL and date) when possible."
)


class ThinkingLevel(StrEnum):
    """Thinking level presets for Gemini models.

    Gemini 3 models: use thinking_level directly ("low", "medium", "high").
    Gemini 2.5 models: maps to thinking_budget token counts.
    """
    OFF = "off"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


# Maps ThinkingLevel to approximate thinking_budget tokens for Gemini 2.5 models.
# Gemini 3 models use thinking_level string directly via the API.
_THINKING_BUDGET_MAP: Final[dict[ThinkingLevel, int]] = {
    ThinkingLevel.OFF: 0,
    ThinkingLevel.LOW: 1024,
    ThinkingLevel.MEDIUM: 8192,
    ThinkingLevel.HIGH: 24576,
}


class GeminiSearchClient(GPTBase):
    """Google Gemini client with Google Search grounding and configurable thinking.

    Uses the standard generate_content API (NOT Deep Research) with:
    - Google Search tool for real-time web grounding
    - Configurable thinking level for enhanced reasoning
    - Selectable model (defaults to settings.gemini.model)

    This is a lightweight alternative to the Deep Research agent,
    suitable for faster queries that still benefit from web search.
    """

    # ── Class-level (static) cost tracking ──────────────────────────────
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost_usd: float = 0.0
    total_requests: int = 0

    @classmethod
    def reset_cost_tracking(cls) -> None:
        """Reset the cumulative cost counters back to zero."""
        cls.total_input_tokens = 0
        cls.total_output_tokens = 0
        cls.total_cost_usd = 0.0
        cls.total_requests = 0

    def __init__(
        self,
        http_client: httpx.AsyncClient,
        model: str | None = None,
        thinking_level: ThinkingLevel = ThinkingLevel.OFF,
        thinking_budget: int | None = None,
        system_prompt: str | None = None,
    ):
        """Initialize the Gemini Search client.

        Args:
            http_client: httpx AsyncClient (kept for interface compatibility).
            model: Gemini model name to use (e.g. "gemini-2.5-flash", "gemini-3-flash").
                   Defaults to settings.gemini.model if not provided.
            thinking_level: Thinking level preset (OFF, LOW, MEDIUM, HIGH).
                            Ignored if thinking_budget is explicitly provided.
            thinking_budget: Explicit thinking token budget (0-24576).
                             Overrides thinking_level when provided.
                             None means thinking_level is used instead.
            system_prompt: Optional custom system prompt.
                           Defaults to the financial analyst prompt.

        Raises:
            ValueError: If GEMINI_API_KEY is not configured in .env
        """
        super().__init__(http_client)
        self._gemini_config = settings.gemini

        if not self._gemini_config.api_key:
            raise ValueError(
                "Gemini API key not configured. "
                "Set GEMINI_API_KEY in .env file."
            )

        self._client = genai.Client(api_key=self._gemini_config.api_key)
        self._model: str = model or self._gemini_config.model
        self._thinking_level: ThinkingLevel = thinking_level
        self._thinking_budget: int | None = thinking_budget
        self._system_prompt: str = system_prompt or DEFAULT_SYSTEM_PROMPT

    @property
    def model(self) -> str:
        """The Gemini model this client is using."""
        return self._model

    @property
    def effective_thinking_budget(self) -> int:
        """The effective thinking budget in tokens.

        Returns thinking_budget if explicitly set, otherwise resolves from thinking_level.
        """
        if self._thinking_budget is not None:
            return self._thinking_budget
        return _THINKING_BUDGET_MAP[self._thinking_level]

    async def generate_text(self, prompt: str) -> str:
        """Generate text using Gemini with Google Search grounding.

        Uses the standard generate_content API with Google Search tool
        for real-time web grounding and configurable thinking.

        Args:
            prompt: The text prompt to send to Gemini.

        Returns:
            Generated text response from Gemini.

        Raises:
            Exception: If the API request fails.
        """
        budget = self.effective_thinking_budget
        logger.info(
            "Generating content with Gemini Search (model=%s, thinking_budget=%d)...",
            self._model,
            budget,
        )
        logger.debug("Input prompt: %s...", prompt[:200])

        try:
            config = self._build_config(budget)

            response = await self._client.aio.models.generate_content(  # type: ignore[attr-defined]
                model=self._model,
                contents=prompt,
                config=config,
            )

            result_text: str = response.text or ""  # type: ignore[attr-defined]
            logger.info("Gemini Search completed: %d chars", len(result_text))

            self._track_cost(response)

            return result_text

        except Exception as e:
            logger.error("Error generating text with Gemini Search: %s", str(e), exc_info=True)
            raise

    # ── Cost tracking helpers ──────────────────────────────────────────

    def _resolve_pricing(self) -> ModelPricing:
        """Return the pricing entry for the current model, with fuzzy matching."""
        # Try exact match first
        if self._model in MODEL_PRICING_MAP:
            return MODEL_PRICING_MAP[self._model]

        # Try prefix matching (e.g. "gemini-2.5-flash-preview-05-20" → "gemini-2.5-flash-preview")
        for key, pricing in MODEL_PRICING_MAP.items():
            if self._model.startswith(key):
                return pricing

        logger.warning(
            "No pricing entry for model '%s' – using default ($%.2f/$%.2f per 1M tokens).",
            self._model,
            DEFAULT_PRICING.input_price,
            DEFAULT_PRICING.output_price,
        )
        return DEFAULT_PRICING

    def _track_cost(self, response: types.GenerateContentResponse) -> None:
        """Extract token counts from the response and accumulate cost.

        Updates the class-level counters and prints a cost summary.
        """
        usage = response.usage_metadata
        if usage is None:
            logger.debug("No usage_metadata in response – skipping cost tracking.")
            return

        input_tokens: int = usage.prompt_token_count or 0
        output_tokens: int = usage.candidates_token_count or 0

        pricing = self._resolve_pricing()
        request_cost = (
            (input_tokens / 1_000_000) * pricing.input_price
            + (output_tokens / 1_000_000) * pricing.output_price
        )

        # Accumulate on the class (shared across all instances)
        cls = type(self)
        cls.total_input_tokens += input_tokens
        cls.total_output_tokens += output_tokens
        cls.total_cost_usd += request_cost
        cls.total_requests += 1

        print(
            f"\n💰 Gemini Cost Tracker (model={self._model})\n"
            f"   This request  → in: {input_tokens:,} tokens, out: {output_tokens:,} tokens  |  ${request_cost:.6f}\n"
            f"   Session total → in: {cls.total_input_tokens:,} tokens, out: {cls.total_output_tokens:,} tokens  "
            f"|  ${cls.total_cost_usd:.6f}  ({cls.total_requests} request(s))\n"
        )

    def _build_config(self, thinking_budget: int) -> types.GenerateContentConfig:
        """Build the GenerateContentConfig with search tool and thinking.

        Args:
            thinking_budget: Resolved thinking token budget.

        Returns:
            Configured GenerateContentConfig instance.
        """
        config_kwargs: dict = {
            "tools": [types.Tool(google_search=types.GoogleSearch())],
            "system_instruction": self._system_prompt,
            "max_output_tokens": self._gemini_config.max_tokens,
        }

        if thinking_budget > 0:
            config_kwargs["thinking_config"] = types.ThinkingConfig(
                thinking_budget=thinking_budget,
            )

        return types.GenerateContentConfig(**config_kwargs)
