"""User-Agent management utilities."""
import random
from typing import List


class UserAgentManager:
    """Manages a collection of browser user-agent strings.
    
    Provides rotation and selection of realistic browser user-agents
    to avoid detection and rate-limiting when making HTTP requests.
    """

    # Popular browser user-agents for realistic HTTP requests
    _USER_AGENTS: List[str] = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
    ]

    @classmethod
    def get_random_user_agent(cls) -> str:
        """Get a random user-agent string from the pool.
        
        Returns:
            A random browser user-agent string.
        """
        return random.choice(cls._USER_AGENTS)

    @classmethod
    def add_user_agent(cls, user_agent: str) -> None:
        """Add a custom user-agent to the pool.
        
        Args:
            user_agent: User-agent string to add to the rotation pool.
        """
        if user_agent and user_agent not in cls._USER_AGENTS:
            cls._USER_AGENTS.append(user_agent)
