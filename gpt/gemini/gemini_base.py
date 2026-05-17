"""Google Gemini API client with Deep Research capabilities.

Uses the native google-genai SDK for:
- Deep Research Agent (autonomous multi-step research with web search)
- Code execution tool for data analysis
"""
import asyncio
from collections.abc import Iterable
import importlib.metadata
import logging
from typing import Any, Final

import httpx
from google import genai

from common.settings import settings
from gpt.abstracts.gpt_base import GPTBase

logger: logging.Logger = logging.getLogger(__name__)

SYSTEM_PROMPT: Final[str] = ( "You are a financial stock analyst. When you analyze stocks, you must perform **deep research** using real-time market data, recent news, and historical context. "
    "Specifically, you should: \n"
    " - Include data and news from the **past 4 months**, plus relevant longer-term history for context. \n"
    " - Search the web for relevant articles, financial reports, and market data (grounded in real sources). \n"
    " - Compare multiple sources, highlight conflicting or corroborating evidence, and cite each source (with URL and date). \n"
    " - When referencing a stock or market-wide trend, include both recent developments and historical performance/history to support your recommendation. \n"
    "Provide your top stock recommendations (tickers) along with rationale based on this deep, up-to-date research."

)

DEEP_RESEARCH_AGENT: Final[str] = "deep-research-pro-preview-12-2025"
POLL_INTERVAL_SECONDS: Final[int] = 10
MAX_RESEARCH_TIME_MINUTES: Final[int] = 60


def _get_field(value: object, field_name: str) -> Any:
    """Read a field from SDK objects, pydantic models, or plain dicts."""
    if isinstance(value, dict):
        return value.get(field_name)

    field_value = getattr(value, field_name, None)
    if field_value is not None:
        return field_value

    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            dumped = model_dump()
        except Exception:
            return None
        if isinstance(dumped, dict):
            return dumped.get(field_name)

    return None


def _as_iterable(value: Any) -> Iterable[Any]:
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes, dict)):
        return value
    return ()


def _extract_text_items(content_items: Any) -> list[str]:
    if isinstance(content_items, dict):
        text = _get_field(content_items, "text")
        return [str(text)] if text else []

    texts: list[str] = []
    for item in _as_iterable(content_items):
        text = _get_field(item, "text")
        if text:
            texts.append(str(text))
    return texts


def _extract_interaction_text(interaction: object) -> str:
    """Extract completed Deep Research text across old and new interaction schemas."""
    steps = _get_field(interaction, "steps")
    for step in reversed(list(_as_iterable(steps))):
        texts = _extract_text_items(_get_field(step, "content"))
        if texts:
            return "\n".join(texts)

    outputs = _get_field(interaction, "outputs")
    texts = _extract_text_items(outputs)
    return "\n".join(texts)


def _interaction_field_names(interaction: object) -> list[str]:
    if isinstance(interaction, dict):
        return sorted(interaction.keys())

    model_fields = getattr(interaction, "model_fields", None)
    if isinstance(model_fields, dict):
        return sorted(model_fields.keys())

    return [name for name in dir(interaction) if not name.startswith("_")]


class GeminiClient(GPTBase):
    """Google Gemini client with Deep Research Agent and Code Execution.
    
    Uses the Deep Research Agent which autonomously plans, executes, and synthesizes
    multi-step research tasks using web search and code execution tools.
    """

    def __init__(self, http_client: httpx.AsyncClient):
        """Initialize the Gemini client.
        
        Args:
            http_client: httpx AsyncClient (kept for interface compatibility).
            
        Raises:
            ValueError: If GEMINI_API_KEY is not configured in .env
        """
        super().__init__(http_client)
        self._config = settings.gemini
        
        if not self._config.api_key:
            raise ValueError(
                "Gemini API key not configured. "
                "Set GEMINI_API_KEY in .env file."
            )
        
        self._client = genai.Client(api_key=self._config.api_key)

    async def generate_text(self, prompt: str) -> str:
        """Generate text using Gemini Deep Research Agent.
        
        Uses the Deep Research Agent which autonomously plans, executes, and synthesizes
        multi-step research tasks. This can take several minutes to complete.
        
        Args:
            prompt: The text prompt to send to Gemini.
            
        Returns:
            Generated text response from Gemini Deep Research Agent.
            
        Raises:
            Exception: If the research task fails or times out.
        """
        # Combine system prompt with user prompt
        full_input = f"{SYSTEM_PROMPT}\n\n{prompt}"
        
        try:
            google_genai_version = importlib.metadata.version("google-genai")
        except importlib.metadata.PackageNotFoundError:
            google_genai_version = "unknown"

        logger.info(
            "Starting Deep Research Agent task with google-genai %s...",
            google_genai_version,
        )
        logger.debug(f"Input prompt: {prompt[:200]}...")
        
        loop = asyncio.get_event_loop()
        
        # Create interaction with Deep Research Agent
        try:
            interaction = self._client.interactions.create(  # type: ignore[attr-defined]
                input=full_input,
                agent=DEEP_RESEARCH_AGENT,
                background=True,
                tools=[
                    {"type": "code_execution"}
                ],
            )

            interaction_id = interaction.id  # type: ignore[attr-defined]
            logger.info(f"Deep Research task started: {interaction_id}")
            logger.info("Research in progress (this may take several minutes)...")
            
        except Exception as e:
            logger.error(f"Failed to create Deep Research interaction: {e}")
            raise Exception(f"Failed to start Deep Research task: {e}") from e
        
        # Poll for completion
        max_polls = (MAX_RESEARCH_TIME_MINUTES * 60) // POLL_INTERVAL_SECONDS
        poll_count = 0
        
        while poll_count < max_polls:
            try:
                interaction = await loop.run_in_executor(  # type: ignore[assignment]
                    None,
                    lambda: self._client.interactions.get(interaction_id)  # type: ignore[attr-defined]
                )
                
                status = _get_field(interaction, "status")
                logger.debug(f"Interaction status: {status} (poll {poll_count + 1}/{max_polls})")
                
                if status == "completed":
                    result_text = _extract_interaction_text(interaction)
                    if result_text:
                        logger.info(f"Deep Research completed: {len(result_text)} chars")
                        return result_text

                    logger.warning(
                        "Interaction completed but no text output found; available fields: %s",
                        _interaction_field_names(interaction),
                    )
                    return ""
                        
                elif status == "failed":
                    error_msg = _get_field(interaction, "error") or "Unknown error"
                    logger.error(f"Deep Research failed: {error_msg}")
                    raise Exception(f"Deep Research task failed: {error_msg}")
                
                # Status is "in_progress" or similar - continue polling
                await asyncio.sleep(POLL_INTERVAL_SECONDS)
                poll_count += 1
                
            except Exception as e:
                logger.error(f"Error while polling interaction status: {e}")
                raise Exception(f"Error during Deep Research polling: {e}") from e
        
        # Timeout reached
        logger.error(f"Deep Research task timed out after {MAX_RESEARCH_TIME_MINUTES} minutes")
        raise Exception(
            f"Deep Research task timed out after {MAX_RESEARCH_TIME_MINUTES} minutes. "
            f"Interaction ID: {interaction_id}"
        )
