"""Google Gemini API client with Deep Research capabilities.

Uses the native google-genai SDK for:
- Deep Thinking (ThinkingConfig with HIGH reasoning)
- Google Search grounding for real-time market data
"""
import asyncio
import logging
from typing import Optional

import httpx
from google import genai
from google.genai import types

from common.settings import settings
from gpt.abstracts.gpt_base import GPTBase

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a financial stock analyst. Analyze the provided earnings stocks using deep research and real-time market data. Provide your top recommendations with stock tickers. When you talk about stock you ***deep Research*** the web news, market data history and realtime for both interday and daily data."""

MODEL_ID = "gemini-3-pro-preview"


class GeminiClient(GPTBase):
    """Google Gemini client with Deep Research (Thinking + Google Search)."""

    def __init__(self, http_client: Optional[httpx.AsyncClient] = None):
        super().__init__(http_client)
        self._config = settings.gemini
        
        if not self._config.api_key:
            raise ValueError(
                "Gemini API key not configured. "
                "Set GEMINI_API_KEY in .env file."
            )
        
        self._client = genai.Client(api_key=self._config.api_key)

    async def generate_text(self, prompt: str) -> str:
        """Generate text using Gemini with Deep Research."""
        
        config = types.GenerateContentConfig(
            temperature=1.0,
            thinking_config=types.ThinkingConfig(
                thinking_level="HIGH",
                include_thoughts=True
            ),
            tools=[types.Tool(google_search=types.GoogleSearch())]
        )
        
        contents = [
            {"role": "user", "parts": [{"text": SYSTEM_PROMPT}]},
            {"role": "model", "parts": [{"text": "Understood. I will analyze stocks with deep research using real-time market data and news."}]},
            {"role": "user", "parts": [{"text": prompt}]}
        ]
        
        # Run sync SDK call in executor to keep async interface
        loop = asyncio.get_event_loop()
        response = await loop.run_in_executor(
            None,
            lambda: self._client.models.generate_content(
                model=MODEL_ID,
                contents=contents,
                config=config
            )
        )
        
        # Parse response - extract final text, log thoughts
        result_text = ""
        if response.candidates and response.candidates[0].content.parts:
            for part in response.candidates[0].content.parts:
                if hasattr(part, 'thought') and part.thought:
                    print(f"\n{'='*60}")
                    print(f"[Deep Think Process]:")
                    print(f"{'='*60}")
                    print(part.text)
                    print(f"{'='*60}\n")
                elif part.text:
                    result_text += part.text
        
        return result_text
