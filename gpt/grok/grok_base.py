"""Grok AI API client implementation.

Grok API Documentation: https://docs.x.ai/
Grok uses OpenAI-compatible Chat Completions API.

API Endpoint: https://api.x.ai/v1/chat/completions
Authentication: Bearer token via Authorization header
"""
import logging
from typing import Optional

import httpx

from common.settings import settings
from gpt.abstracts.gpt_base import GPTBase

logger = logging.getLogger(__name__)


class GrokClient(GPTBase):
    """Grok AI client for generating text using Grok API.
    
    Implements the OpenAI-compatible Chat Completions API pattern.
    Inherits from GPTBase abstract class to provide consistent interface
    with other AI providers (Gemini, etc.).
    
    Configuration:
    - API key: Loaded from .env (GROK_API_KEY)
    - Base URL: Configured in config.yaml
    - Model: Configurable via config.yaml (default: grok-beta)
    """

    def __init__(self, http_client: Optional[httpx.AsyncClient] = None):
        """Initialize the Grok client.
        
        Args:
            http_client: Optional httpx AsyncClient instance. If not provided,
                        a new client will be created for each request.
                        
        Raises:
            ValueError: If API key is not configured in .env
        """
        super().__init__(http_client)
        self._config = settings.grok
        self._http_config = settings.http
        
        # Validate API key is configured
        if not self._config.api_key:
            raise ValueError(
                "Grok API key not configured. "
                "Set GROK_API_KEY in .env file. "
                "Get your key from: https://console.x.ai/"
            )
        
        self._managed_client = http_client is None

    async def generate_text(self, prompt: str) -> str:
        """Generate text using Grok API.
        
        Args:
            prompt: The text prompt to send to Grok.
            
        Returns:
            Generated text response from Grok.
            
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
            logger.error(f"Error generating text with Grok: {str(e)}", exc_info=True)
            raise

    async def _call_api(self, client: httpx.AsyncClient, prompt: str) -> str:
        """Call the Grok Chat Completions API.
        
        Uses OpenAI-compatible Chat Completions format:
        POST /v1/chat/completions
        
        Args:
            client: httpx AsyncClient for making requests.
            prompt: The text prompt to send.
            
        Returns:
            Generated text from Grok.
            
        Raises:
            httpx.HTTPStatusError: If API request fails.
        """
        url = f"{self._config.base_url}/chat/completions"
        
        headers = {
            "Authorization": f"Bearer {self._config.api_key}",
            "Content-Type": "application/json",
        }
        
        payload = {
            "model": self._config.model,
            "messages": [
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            "max_tokens": self._config.max_tokens,
            "temperature": 0.7,
        }
        
        logger.debug(f"Calling Grok API with model: {self._config.model}")
        
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
            raise ValueError(f"Unexpected Grok API response format: {str(e)}")
