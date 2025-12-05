"""GPT client interface protocol."""
from typing import Protocol


class IGPTClient(Protocol):
    """GPT client interface - defines contract for all AI text generation clients.
    
    This protocol defines the interface that all GPT/AI client implementations
    must follow. Use this for type hints instead of concrete classes.
    """

    async def generate_text(self, prompt: str) -> str:
        """Generate text based on the given prompt.
        
        Args:
            prompt: The text prompt to send to the AI provider.
            
        Returns:
            Generated text response from the AI provider.
        """
        ...
