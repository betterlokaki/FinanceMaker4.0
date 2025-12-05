"""Grok AI API client implementation using xAI SDK.

Grok API Documentation: https://docs.x.ai/
Uses the official xAI SDK with web_search, x_search, and code_execution tools.

API Key: XAI_API_KEY environment variable
Model: grok-4-fast (reasoning model with server-side tools)
"""
import logging
import os

import httpx
from xai_sdk import Client
from xai_sdk.chat import user
from xai_sdk.tools import web_search, x_search, code_execution

from gpt.abstracts.gpt_base import GPTBase

logger: logging.Logger = logging.getLogger(__name__)


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

    def __init__(self, http_client: httpx.AsyncClient):
        """Initialize the Grok client with xAI SDK.
        
        Args:
            http_client: httpx AsyncClient (kept for GPTBase interface compatibility)
                        
        Raises:
            ValueError: If XAI_API_KEY is not configured in .env
        """
        super().__init__(http_client)
        
        api_key: str | None = os.getenv("GROK_API_KEY")
        if not api_key:
            raise ValueError(
                "Grok API key not configured. "
                "Set GROK_API_KEY in .env file. "
                "Get your key from: https://console.x.ai/"
            )
        
        self._client: Client = Client(api_key=api_key)

    async def generate_text(self, prompt: str) -> str:
        """Generate text using Grok with tools.
        
        Uses hardcoded tools: web_search, x_search, code_execution
        Streams response and logs tool calls in real-time.
        
        Args:
            prompt: The text prompt to send to Grok.
            
        Returns:
            Generated text response from Grok (final answer only).
            
        Raises:
            Exception: If API request fails.
        """
        try:
            logger.debug(f"Generating text with prompt: {prompt[:100]}...")
            
            chat = self._client.chat.create(
                model="grok-4-1-fast-reasoning-latest",
                tools=[
                    web_search(),
                    x_search(),
                    code_execution(),
                ],
            )
            
            chat.append(user(prompt))
            
            final_response: str = ""
            is_thinking: bool = True
            
            for response, chunk in chat.stream():
                for tool_call in chunk.tool_calls:
                    logger.info(
                        f"Calling tool: {tool_call.function.name} "
                        f"with arguments: {tool_call.function.arguments}"
                    )
                
                if response.usage.reasoning_tokens and is_thinking:
                    logger.debug(f"Thinking... ({response.usage.reasoning_tokens} tokens)")
                
                if chunk.content and is_thinking:
                    logger.info("Final Response started")
                    is_thinking = False
                
                if chunk.content and not is_thinking:
                    logger.debug(chunk.content)
                    final_response += chunk.content
            
            logger.info(f"Grok response completed: {len(final_response)} chars")
            return final_response
            
        except Exception as e:
            logger.error(f"Error generating text with Grok: {str(e)}", exc_info=True)
            raise


