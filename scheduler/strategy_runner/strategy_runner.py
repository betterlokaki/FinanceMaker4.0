"""Strategy runner compatibility wrapper."""

from common.models.strategy_input import DEFAULT_STRATEGY_INPUT, StrategyInputModel
from common.runners.common_strategy_runner import CommonStrategyRunner
from strategy.abstracts.i_trading_strategy import ITradingStrategy


class StrategyRunner(CommonStrategyRunner):
    """Backward-compatible name for the shared injected strategy runner."""

    def __init__(
        self,
        strategies: list[ITradingStrategy],
        strategy_input: StrategyInputModel = DEFAULT_STRATEGY_INPUT,
        max_retries: int = 3,
        retry_delay: float = 5.0,
    ) -> None:
        super().__init__(
            strategies=strategies,
            strategy_input=strategy_input,
            max_retries=max_retries,
            retry_delay=retry_delay,
        )
