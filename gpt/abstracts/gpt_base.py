"""Abstract GPT client base class for AI text generation."""
import abc

import httpx


class GPTBase(abc.ABC):
    """Abstract base class for GPT/AI text generation clients.
    
    Provides an interface for implementing different AI provider clients
    (e.g., Grok, Gemini, OpenAI) that generate text from prompts.
    
    All AI client implementations should inherit from this class and implement
    the generate_text method. This class implements the IGPTClient protocol.
    """
    
    def __init__(self, http_client: httpx.AsyncClient):
        """Initialize the GPT client.
        
        Args:
            http_client: httpx AsyncClient instance for HTTP requests.
                        May not be used by all implementations (e.g., SDK-based clients).
        """
        self._http_client: httpx.AsyncClient = http_client
        
    @abc.abstractmethod
    async def generate_text(self, prompt: str) -> str:
        """Generate text based on the given prompt.
        
        Args:
            prompt: The text prompt to send to the AI provider.
            
        Returns:
            Generated text response from the AI provider.
        """
        pass