"""Parameters for pulling trade ideas."""
from dataclasses import dataclass


@dataclass(frozen=True)
class IdeaParams:
    """Parameters controlling idea retrieval.

    Attributes:
        ticker: Optional ticker filter for idea sources.
    """
    ticker: str

    def __post_init__(self) -> None:
        """Validate provided parameters."""
        if not self.ticker:
            raise ValueError("ticker is required")
