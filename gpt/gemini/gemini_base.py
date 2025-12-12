"""Google Gemini API client with Deep Research capabilities.

Uses the native google-genai SDK for:
- Deep Thinking (ThinkingConfig with HIGH reasoning)
- Google Search grounding for real-time market data
"""
import asyncio
import logging
from typing import Any, Final

import httpx
from google import genai
from google.genai import types

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

MODEL_ID: Final[str] = "gemini-3-pro-preview"


class GeminiClient(GPTBase):
    """Google Gemini client with Deep Research (Thinking + Google Search)."""

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
        """Generate text using Gemini with Deep Research.
        
        Args:
            prompt: The text prompt to send to Gemini.
            
        Returns:
            Generated text response from Gemini.
        """
        config = types.GenerateContentConfig(
            temperature=1.0,
            thinking_config=types.ThinkingConfig(
                thinking_level=types.ThinkingLevel.HIGH,
                include_thoughts=True,
            ),
            
            tools=[types.Tool(google_search=types.GoogleSearch())], 
        )
        
        contents: list[dict[str, Any]] = [
            {"role": "user", "parts": [{"text": SYSTEM_PROMPT}]},
            {"role": "model", "parts": [{"text": "Deep Research Acknowledged. at list 13000 sources"}]},
            {"role": "user", "parts": [{"text": prompt}]}
        ]
        
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: self._client.models.generate_content(
                model=MODEL_ID,
                contents=contents,  # type: ignore[arg-type]
                config=config
            )
        )
        
        result_text: str = ""
        if response.candidates:
            candidate = response.candidates[0]
            if candidate.content and candidate.content.parts:
                for part in candidate.content.parts:
                    if hasattr(part, 'thought') and part.thought:
                        logger.info("=" * 60)
                        logger.info("[Deep Think Process]:")
                        logger.info("=" * 60)
                        logger.debug(part.text)
                        logger.info("=" * 60)
                    elif part.text:
                        result_text += part.text
        
        logger.info(f"Gemini response completed: {len(result_text)} chars")
        return result_text

