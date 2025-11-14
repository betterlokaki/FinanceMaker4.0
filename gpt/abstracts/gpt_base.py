
import abc
from typing import Optional

import httpx


class GPTBase(abc.ABC):
    
    def __init__(self, http_client: Optional[httpx.AsyncClient] = None):
        self._http_client = http_client
        
    @abc.abstractmethod
    async def generate_text(self, prompt: str) -> str:
        """Generate text based on the given prompt."""
        pass
