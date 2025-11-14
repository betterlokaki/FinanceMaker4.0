"""Google Gemini API client implementation.

Gemini API Documentation: https://ai.google.dev/
Gemini offers both REST API and OpenAI-compatible Chat Completions API.
This implementation uses the OpenAI-compatible endpoint for consistency.

API Endpoint: https://generativelanguage.googleapis.com/v1beta/openai/
Authentication: Bearer token via Authorization header
"""
import logging
from typing import Optional

import httpx

from common.settings import settings
from gpt.abstracts.gpt_base import GPTBase

logger = logging.getLogger(__name__)


class GeminiClient(GPTBase):
    """Google Gemini client for generating text using Gemini API.
    
    Implements the OpenAI-compatible Chat Completions API pattern for consistency
    with other AI providers (Grok, etc.).
    Inherits from GPTBase abstract class.
    
    Configuration:
    - API key: Loaded from .env (GEMINI_API_KEY)
    - Base URL: Configured in config.yaml
    - Model: Configurable via config.yaml (default: gemini-2.0-flash)
    """

    def __init__(self, http_client: Optional[httpx.AsyncClient] = None):
        """Initialize the Gemini client.
        
        Args:
            http_client: Optional httpx AsyncClient instance. If not provided,
                        a new client will be created for each request.
                        
        Raises:
            ValueError: If API key is not configured in .env
        """
        super().__init__(http_client)
        self._config = settings.gemini
        self._http_config = settings.http
        
        # Validate API key is configured
        if not self._config.api_key:
            raise ValueError(
                "Gemini API key not configured. "
                "Set GEMINI_API_KEY in .env file. "
                "Get your key from: https://ai.google.dev/"
            )
        
        self._managed_client = http_client is None

    async def generate_text(self, prompt: str) -> str:
        """Generate text using Gemini API.
        
        Args:
            prompt: The text prompt to send to Gemini.
            
        Returns:
            Generated text response from Gemini.
            
        Raises:
            httpx.HTTPStatusError: If API request fails.
            ValueError: If response format is unexpected.
        """
        try:
            # Use provided client or create temporary client
            client = self._http_client or httpx.AsyncClient(
                timeout=self._config.timeout,
                follow_redirects=True
            )
            
            try:
                response = await self._call_api(client, prompt)
                return response
            finally:
                # Close client if we created it
                if self._managed_client:
                    await client.aclose()
                    
        except Exception as e:
            logger.error(f"Error generating text with Gemini: {str(e)}", exc_info=True)
            raise

    async def _call_api(self, client: httpx.AsyncClient, prompt: str) -> str:
        """Call the Gemini Chat Completions API.
        
        Uses OpenAI-compatible Chat Completions format for consistency:
        POST /v1beta/openai/chat/completions
        
        Args:
            client: httpx AsyncClient for making requests.
            prompt: The text prompt to send.
            
        Returns:
            Generated text from Gemini.
            
        Raises:
            httpx.HTTPStatusError: If API request fails.
        """
        url = f"{self._config.base_url}chat/completions"
        
        headers = {
            "Authorization": f"Bearer {self._config.api_key}",
            "Content-Type": "application/json",
        }
        
        payload = {
            "model": self._config.model,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a financial stock analyst. Analyze the provided earnings stocks using deep research and real-time market data. Provide your top recommendations with stock tickers."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            "max_tokens": self._config.max_tokens,
            "temperature": 0.3,
        }
        
        logger.debug(f"Calling Gemini API with model: {self._config.model}")
        
        response = await client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        
        # Parse response
        response_json = response.json()
        
        # Extract generated text from OpenAI-compatible response format
        # Response format: {"choices": [{"message": {"content": "..."}}]}
        try:
            generated_text = response_json["choices"][0]["message"]["content"]
            logger.debug(f"Generated text length: {len(generated_text)}")
            return generated_text
        except (KeyError, IndexError) as e:
            logger.error(f"Unexpected API response format: {response_json}")
            raise ValueError(f"Unexpected Gemini API response format: {str(e)}")
