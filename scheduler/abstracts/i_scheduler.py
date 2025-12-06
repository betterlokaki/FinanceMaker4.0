"""Scheduler interface protocol."""
from typing import Protocol


class IScheduler(Protocol):
    """Scheduler interface - defines contract for trading schedulers.
    
    Manages the lifecycle of trading strategies based on market hours.
    """

    async def start(self) -> None:
        """Start the scheduler.
        
        Begins the main scheduling loop that:
        - Waits for market open (pre-market)
        - Initializes and runs strategies
        - Shuts down at market close (after-hours)
        - Repeats on next market day
        """
        ...

    async def stop(self) -> None:
        """Stop the scheduler gracefully.
        
        Shuts down all running strategies and exits the scheduling loop.
        """
        ...

    @property
    def is_running(self) -> bool:
        """Check if the scheduler is currently running."""
        ...
