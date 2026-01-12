"""Abstract base class for pulling trade ideas."""
from abc import ABC, abstractmethod

import httpx

from common.models.idea_params import IdeaParams
from common.models.simple_idea import SimpleIdea
from pullers.ideas.abstracts.i_idea_puller import IIdeaPuller


class IdeaPullerBase(IIdeaPuller, ABC):
    """Abstract base class implementing the idea puller contract."""

    def __init__(self, http_client: httpx.AsyncClient):
        """Inject the async HTTP client dependency."""
        if http_client is None:
            raise ValueError("http_client is required")

        self._http_client: httpx.AsyncClient = http_client

    @abstractmethod
    async def pull_ideas(self, params: IdeaParams) -> list[SimpleIdea]:
        """Fetch trade ideas from a remote source using the given parameters."""
        pass
