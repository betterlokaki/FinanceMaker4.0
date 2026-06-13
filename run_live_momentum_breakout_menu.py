"""Run the isolated momentum breakout strategy menu/runner."""
from __future__ import annotations

import asyncio

from strategy.momentum_breakout_strategy.menu import main


if __name__ == "__main__":
    asyncio.run(main())
