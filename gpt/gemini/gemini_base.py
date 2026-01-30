"""Google Gemini API client with Deep Research capabilities.

Uses the native google-genai SDK for:
- Deep Research Agent (autonomous multi-step research with web search)
- Code execution tool for data analysis
"""
import asyncio
import logging
from typing import Final

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

DEEP_RESEARCH_AGENT: Final[str] = "deep-research-pro-preview-12-2025"
POLL_INTERVAL_SECONDS: Final[int] = 10
MAX_RESEARCH_TIME_MINUTES: Final[int] = 60


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
        
        logger.info("Starting Deep Research Agent task...")
        logger.debug(f"Input prompt: {prompt[:200]}...")
        
        loop = asyncio.get_event_loop()
        
        # Create interaction with Deep Research Agent
        try:
            interaction =self._client.interactions.create(  # type: ignore[attr-defined]
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
                
                status = interaction.status  # type: ignore[attr-defined]
                logger.debug(f"Interaction status: {status} (poll {poll_count + 1}/{max_polls})")
                
                if status == "completed":
                    outputs = interaction.outputs  # type: ignore[attr-defined]
                    if outputs and len(outputs) > 0:  # type: ignore[arg-type]
                        result_text: str = str(outputs[-1].text)  # type: ignore[attr-defined]
                        logger.info(f"Deep Research completed: {len(result_text)} chars")
                        return result_text
                    else:
                        logger.warning("Interaction completed but no outputs found")
                        return ""
                        
                elif status == "failed":
                    error_msg = getattr(interaction, 'error', 'Unknown error')  # type: ignore[attr-defined]
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

