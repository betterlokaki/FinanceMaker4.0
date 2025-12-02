"""Grok AI API client implementation using xAI SDK.

Grok API Documentation: https://docs.x.ai/
Uses the official xAI SDK with web_search, x_search, and code_execution tools.

API Key: XAI_API_KEY environment variable
Model: grok-4-fast (reasoning model with server-side tools)
"""
import logging
import os
import re
from typing import Optional

import httpx
from xai_sdk import Client
from xai_sdk.chat import user
from xai_sdk.tools import web_search, x_search, code_execution

from common.settings import settings
from gpt.abstracts.gpt_base import GPTBase

logger = logging.getLogger(__name__)


class GrokClient(GPTBase):
    """Grok AI client for generating text using xAI SDK.
    
    Uses the official xAI SDK with hardcoded tools:
    - web_search: Search the web for real-time data
    - x_search: Search X (Twitter) for market sentiment
    - code_execution: Execute code for analysis
    
    Configuration:
    - API key: Loaded from .env (XAI_API_KEY)
    - Model: grok-4-fast (reasoning model)
    """

    def __init__(self, http_client: Optional[httpx.AsyncClient] = None):
        """Initialize the Grok client with xAI SDK.
        
        Args:
            http_client: Ignored (kept for GPTBase interface compatibility)
                        
        Raises:
            ValueError: If XAI_API_KEY is not configured in .env
        """
        super().__init__(http_client)
        
        # Get API key from environment
        api_key = os.getenv("GROK_API_KEY")
        if not api_key:
            raise ValueError(
                "Grok API key not configured. "
                "Set GROK_API_KEY in .env file. "
                "Get your key from: https://console.x.ai/"
            )
        
        # Initialize xAI client with tools
        self._client = Client(api_key=api_key)

    async def generate_text(self, prompt: str) -> str:
        """Generate text using Grok with tools.
        
        Uses hardcoded tools: web_search, x_search, code_execution
        Streams response and prints tool calls in real-time.
        
        Args:
            prompt: The text prompt to send to Grok.
            
        Returns:
            Generated text response from Grok (final answer only).
            
        Raises:
            Exception: If API request fails.
        """
        try:
            logger.debug(f"Generating text with prompt: {prompt[:100]}...")
            
            # Create chat with hardcoded tools
            chat = self._client.chat.create(
                model="grok-4-1-fast-reasoning-latest",
                tools=[
                    web_search(),
                    x_search(),
                    code_execution(),
                ],
            )
            
            # Append user message
            chat.append(user(prompt))
            
            # Stream response and collect final text
            final_response = ""
            is_thinking = True
            
            for response, chunk in chat.stream():
                # Print tool calls as they happen
                for tool_call in chunk.tool_calls:
                    print(f"\nCalling tool: {tool_call.function.name} with arguments: {tool_call.function.arguments}")
                
                # Print thinking progress
                if response.usage.reasoning_tokens and is_thinking:
                    print(f"\rThinking... ({response.usage.reasoning_tokens} tokens)", end="", flush=True)
                
                # Print final response start
                if chunk.content and is_thinking:
                    print("\n\nFinal Response:")
                    is_thinking = False
                
                # Collect final response
                if chunk.content and not is_thinking:
                    print(chunk.content, end="", flush=True)
                    final_response += chunk.content
            
            print("\n")
            
            # Extract tickers from response
            tickers = self._extract_tickers(final_response)
            logger.debug(f"Extracted tickers: {tickers}")
            
            return final_response
            
        except Exception as e:
            logger.error(f"Error generating text with Grok: {str(e)}", exc_info=True)
            raise

    def _extract_tickers(self, text: str) -> list[str]:
        """Extract stock tickers from response text.
        
        Looks for patterns like $AAPL or AAPL mentioned in context of stocks.
        
        Args:
            text: The response text to extract tickers from.
            
        Returns:
            List of unique stock ticker symbols found.
        """
        # Match $TICKER or TICKER in stock context
        ticker_pattern = r'\$([A-Z]{1,5})\b|(?:ticker|symbol|stock)[:\s]+([A-Z]{1,5})\b'
        matches = re.findall(ticker_pattern, text, re.IGNORECASE)
        
        # Flatten results and remove empty strings
        tickers = [m[0] or m[1] for m in matches if m[0] or m[1]]
        
        return list(set(tickers))  # Return unique tickers

