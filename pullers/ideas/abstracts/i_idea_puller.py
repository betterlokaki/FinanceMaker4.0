"""Idea puller interface protocol."""
from typing import Protocol

from common.models.idea_params import IdeaParams
from common.models.simple_idea import SimpleIdea


class IIdeaPuller(Protocol):
    """Defines the contract for pulling trade ideas."""

    async def pull_ideas(self, params: IdeaParams) -> list[SimpleIdea]:
        """Retrieve a list of trade ideas for the given parameters."""
        ...
