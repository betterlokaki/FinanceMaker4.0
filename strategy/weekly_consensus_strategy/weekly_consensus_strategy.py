"""Live strategy: Grok double-consensus using Yahoo data only."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
from zoneinfo import ZoneInfo

import pandas as pd

from common.cache.abstracts.i_ticker_cache import ITickerCache
from common.helpers.live_weekly_ai_consensus import (
    AITradeIdea,
    ConsensusTradeIdea,
    parse_trade_ideas,
)
from common.models.candlestick import CandleStick
from common.models.period import Period
from common.models.portfolio import Portfolio
from common.models.pricing_data import PricingData
from common.models.strategy_input import StrategyInputModel
from common.settings import OrderParamsConfig, PortfolioAllocationConfig
from common.trading.position_sizing import PositionSizer
from gpt.abstracts.gpt_base import GPTBase
from interactive_borkers import BracketOrderPlan, InteractiveBorkersOrderAdapter
from publishers.abstracts.i_broker import IBroker
from pullers.market.abstracts.i_market_provider import IMarketProvider
from pullers.realtime.abstracts.i_realtime_provider import IRealtimeProvider
from strategy.abstracts.realtime_trading_base import RealTimeTradingBase

logger: logging.Logger = logging.getLogger(__name__)

NY_TZ: ZoneInfo = ZoneInfo("America/New_York")

PROMPT_TEMPLATE: str = """
Which tickers to buy for next week?
Based your asnwer on earning stocks vibes x.com twits Iran war with america AI China and everything else make it a score base and OF COURSE technical analysis (use polygon api for extracting real data please)
just give me the ticker give it a score between 1- 100 and a sentence that explain why and which price to buy stop loss and take profit

return like this only if score is above 80
TICKER: ticker
SCORE:
WHY?
BUY: put here what price to buy
SELL:
STOP:
""".strip()


@dataclass(frozen=True)
class LiveConsensusPlan:
    """Final per-ticker order plan."""

    ticker: str
    score: float
    why: str
    entry_price: float
    take_profit: float
    stop_loss: float
    ai_buy: float
    ai_sell: float
    ai_stop: float


class WeeklyDoubleConsensusLiveStrategy(RealTimeTradingBase):
    """Live strategy that requires Grok agreement across two runs."""

    def __init__(
        self,
        realtime_provider: IRealtimeProvider,
        market_provider: IMarketProvider,
        broker: IBroker,
        grok_client: GPTBase,
        ticker_cache: ITickerCache,
        portfolio_allocation_config: PortfolioAllocationConfig,
        order_params_config: OrderParamsConfig,
        min_ai_score: float = 80.0,
        rr_ratio: float = 2.5,
        direction_preference: str = "long",
        strategy_input: StrategyInputModel | None = None,
    ) -> None:
        super().__init__(realtime_provider)
        self._market_provider = market_provider
        self._broker = broker
        self._grok_client = grok_client
        self._ticker_cache = ticker_cache
        self._portfolio_allocation_config = portfolio_allocation_config
        self._order_params_config = order_params_config
        self._min_ai_score = min_ai_score
        self._rr_ratio = rr_ratio
        self._direction_preference = direction_preference
        self._strategy_input = strategy_input or StrategyInputModel(
            portfolio_pct_per_trade=portfolio_allocation_config.strategy_allocation_pct,
            risk_pct=0.0,
            reward_pct=0.0,
        )

        self._order_adapter = InteractiveBorkersOrderAdapter(
            buy_limit_rth=self._order_params_config.buy_limit_rth,
            take_profit_rth=self._order_params_config.take_profit_rth,
            stop_loss_rth=self._order_params_config.stop_loss_rth,
        )

        self._plans: dict[str, LiveConsensusPlan] = {}
        self._processed_tickers: set[str] = set()
        self._orders_placed: set[str] = set()
        self._buying_power_per_ticker: float = 0.0

    async def load_tickers(self) -> list[str]:
        """Build 2-pass Grok consensus tickers and precompute order plans."""
        prompt = PROMPT_TEMPLATE
        logger.info("Running Grok consensus (2 passes) using raw prompt")

        grok_run1, grok_run2 = await asyncio.gather(
            self._grok_client.generate_text(prompt),
            self._grok_client.generate_text(prompt),
        )

        parsed_grok_1 = parse_trade_ideas(grok_run1, "grok_run_1", self._min_ai_score)
        parsed_grok_2 = parse_trade_ideas(grok_run2, "grok_run_2", self._min_ai_score)
        consensus = self._find_two_pass_consensus(parsed_grok_1, parsed_grok_2)

        self._plans = await self._build_live_plans(consensus)
        if not self._plans:
            logger.warning("No tradable plans after consensus + Yahoo technical validation")
            return []

        total_buying_power = await self._broker.get_buying_power()
        self._buying_power_per_ticker = total_buying_power * self._strategy_input.portfolio_pct_per_trade
        if self._strategy_input.max_notional_per_trade is not None:
            self._buying_power_per_ticker = min(
                self._buying_power_per_ticker,
                self._strategy_input.max_notional_per_trade,
            )

        self._orders_placed = {
            order.ticker.upper()
            for order in self._broker.portfolio.open_orders
        }
        self._ticker_cache.save_tickers(sorted(self._plans.keys()), datetime.now(NY_TZ).date())

        logger.info(
            "Weekly consensus plans ready: %d tickers, buying_power=$%.2f, per trade=$%.2f",
            len(self._plans),
            total_buying_power,
            self._buying_power_per_ticker,
        )
        return sorted(self._plans.keys())

    async def on_tick(self, data: PricingData) -> None:
        """Place order on first tick per ticker."""
        ticker = data.id.upper()
        if ticker in self._processed_tickers or ticker in self._orders_placed:
            return

        plan = self._plans.get(ticker)
        if plan is None:
            return

        portfolio = self._broker.portfolio
        if portfolio.has_position(ticker) or portfolio.has_open_order(ticker):
            self._processed_tickers.add(ticker)
            await self._safe_unsubscribe([ticker])
            return

        sizing_portfolio = Portfolio(
            cash_balance=self._buying_power_per_ticker,
            buying_power=self._buying_power_per_ticker,
        )
        quantity = PositionSizer.quantity_for_entry(
            portfolio=sizing_portfolio,
            entry_price=plan.entry_price,
            strategy_input=StrategyInputModel(
                portfolio_pct_per_trade=1.0,
                risk_pct=self._strategy_input.risk_pct,
                reward_pct=self._strategy_input.reward_pct,
                max_notional_per_trade=self._strategy_input.max_notional_per_trade,
            ),
        )
        if quantity < 1:
            logger.warning(
                "Skipping %s due to insufficient buying power. Entry=%.2f, per-ticker BP=%.2f",
                ticker,
                plan.entry_price,
                self._buying_power_per_ticker,
            )
            self._processed_tickers.add(ticker)
            await self._safe_unsubscribe([ticker])
            return

        order_plan = BracketOrderPlan(
            ticker=ticker,
            quantity=quantity,
            entry_price=plan.entry_price,
            take_profit_price=plan.take_profit,
            stop_price=plan.stop_loss,
        )

        self._orders_placed.add(ticker)
        self._processed_tickers.add(ticker)
        try:
            response = await self._order_adapter.place_day_limit_with_gtc_exits(
                broker=self._broker,
                plan=order_plan,
            )

            logger.info(
                "✅ %s order placed | score=%.1f | entry=%.2f tp=%.2f stop=%.2f | order_id=%s status=%s",
                ticker,
                plan.score,
                plan.entry_price,
                plan.take_profit,
                plan.stop_loss,
                response.order_id,
                response.status,
            )
            await self._safe_unsubscribe([ticker])
        except Exception as exc:
            self._orders_placed.discard(ticker)
            self._processed_tickers.discard(ticker)
            logger.error("Order placement failed for %s: %s", ticker, exc, exc_info=True)

    async def on_candle(self, ticker: str, candle: CandleStick) -> None:
        """Unused for this strategy (tick-driven)."""
        return

    async def shutdown(self) -> None:
        """Shutdown strategy and clear in-memory state."""
        await super().shutdown()
        self._plans.clear()
        self._processed_tickers.clear()
        self._orders_placed.clear()

    async def _safe_unsubscribe(self, tickers: list[str]) -> None:
        try:
            await self._realtime_provider.unsubscribe(tickers, self.on_tick)
        except Exception as exc:
            logger.debug("Unsubscribe failed for %s: %s", tickers, exc)

    async def _build_live_plans(
        self,
        consensus: dict[str, ConsensusTradeIdea],
    ) -> dict[str, LiveConsensusPlan]:
        """Turn consensus ideas into executable order plans."""
        plans: dict[str, LiveConsensusPlan] = {}

        for ticker, idea in consensus.items():
            try:
                candles = await self._fetch_daily_candles(ticker=ticker, lookback_days=420)
            except Exception as exc:
                logger.warning("Skipping %s due to Yahoo candle fetch error: %s", ticker, exc)
                continue

            if not candles:
                continue

            current_price = float(candles[-1]["close"])
            setup = self._suggest_swing_limit_order(
                candles=candles,
                current_price=current_price,
                rr_ratio=self._rr_ratio,
                direction_preference=self._direction_preference,
            )

            entry_price = float(idea.buy)
            take_profit = float(idea.sell)
            stop_loss = float(idea.stop)

            if setup.get("setup_found") and setup.get("direction") == "LONG":
                entry_price = float(setup["entry_price"])
                take_profit = float(setup["take_profit"])
                stop_loss = float(setup["stop_loss"])

            if not (stop_loss < entry_price < take_profit):
                logger.info(
                    "Skipping %s: invalid price structure entry=%.2f tp=%.2f stop=%.2f",
                    ticker,
                    entry_price,
                    take_profit,
                    stop_loss,
                )
                continue

            plans[ticker] = LiveConsensusPlan(
                ticker=ticker,
                score=idea.score,
                why=idea.why,
                entry_price=round(entry_price, 2),
                take_profit=round(take_profit, 2),
                stop_loss=round(stop_loss, 2),
                ai_buy=idea.buy,
                ai_sell=idea.sell,
                ai_stop=idea.stop,
            )

        return plans

    async def _fetch_daily_candles(
        self,
        ticker: str,
        lookback_days: int = 420,
    ) -> list[dict]:
        """Fetch daily candles via Yahoo market provider."""
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(days=lookback_days)
        df = await self._market_provider.get_prices(
            ticker=ticker,
            start_time=start_time,
            end_time=end_time,
            period=Period.DAILY,
        )
        if df is None or df.empty:
            return []

        required_cols = {"open", "high", "low", "close", "volume"}
        if not required_cols.issubset(df.columns):
            return []

        frame = df.copy().sort_index().dropna(subset=list(required_cols))
        candles: list[dict] = []
        for idx, row in frame.iterrows():
            ts = pd.Timestamp(idx)
            if ts.tzinfo is None:
                ts = ts.tz_localize("UTC")
            candles.append(
                {
                    "datetime": ts.isoformat(),
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row["volume"]),
                }
            )
        return candles

    @staticmethod
    def _find_two_pass_consensus(
        run_a: dict[str, AITradeIdea],
        run_b: dict[str, AITradeIdea],
    ) -> dict[str, ConsensusTradeIdea]:
        """Return only tickers present in both Grok runs."""
        common = set(run_a.keys()).intersection(run_b.keys())
        result: dict[str, ConsensusTradeIdea] = {}
        for ticker in sorted(common):
            idea_a = run_a[ticker]
            idea_b = run_b[ticker]
            # Keep stable consensus values by averaging both runs.
            score = round((idea_a.score + idea_b.score) / 2.0, 2)
            buy = round((idea_a.buy + idea_b.buy) / 2.0, 4)
            sell = round((idea_a.sell + idea_b.sell) / 2.0, 4)
            stop = round((idea_a.stop + idea_b.stop) / 2.0, 4)
            why = idea_a.why if len(idea_a.why) >= len(idea_b.why) else idea_b.why
            result[ticker] = ConsensusTradeIdea(
                ticker=ticker,
                score=score,
                why=why,
                buy=buy,
                sell=sell,
                stop=stop,
                sources=(idea_a.source, idea_b.source),
            )
        return result

    @staticmethod
    def _suggest_swing_limit_order(
        candles: list[dict],
        current_price: float,
        rr_ratio: float = 2.5,
        direction_preference: str = "both",
    ) -> dict:
        """Generate limit entry + SL + TP from daily candles."""
        if len(candles) < 220:
            return {"setup_found": False, "reason": "Need ~200+ daily candles"}

        df = pd.DataFrame(candles)
        df["datetime"] = pd.to_datetime(df["datetime"], utc=True)
        df = df.sort_values("datetime").reset_index(drop=True)

        df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
        df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()
        df["ema200"] = df["close"].ewm(span=200, adjust=False).mean()

        hl = df["high"] - df["low"]
        hc = (df["high"] - df["close"].shift(1)).abs()
        lc = (df["low"] - df["close"].shift(1)).abs()
        tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
        df["atr"] = tr.rolling(window=14).mean()

        last = df.iloc[-1]

        bullish = (
            (last["close"] > last["ema50"] > last["ema200"])
            and (last["ema20"] > last["ema50"])
        )
        bearish = (
            (last["close"] < last["ema50"] < last["ema200"])
            and (last["ema20"] < last["ema50"])
        )

        direction = None
        if bullish and direction_preference in ["long", "both"]:
            direction = "long"
        elif bearish and direction_preference in ["short", "both"]:
            direction = "short"

        if not direction:
            return {
                "setup_found": False,
                "reason": "No clear trend matching your preference",
            }

        recent = df.iloc[-40:]

        if direction == "long":
            swing_support = recent["low"].min()
            ema_support = last["ema20"]
            entry_zone = max(swing_support, ema_support * 0.992)

            if current_price > entry_zone * 1.085:
                return {"setup_found": False, "reason": "Price too extended above support"}

            limit_price = round(entry_zone * 1.002, 4)
            sl_price = round(swing_support - last["atr"] * 0.6, 4)

            risk = max(limit_price - sl_price, 0.0001)
            if risk / limit_price > 0.06:
                return {"setup_found": False, "reason": "Risk too wide"}

            tp_price = round(limit_price + risk * rr_ratio, 4)
            reason = "Bullish pullback to EMA20 / swing support"
        else:
            swing_res = recent["high"].max()
            ema_res = last["ema20"]
            entry_zone = min(swing_res, ema_res * 1.008)

            if current_price < entry_zone * 0.915:
                return {"setup_found": False, "reason": "Price too extended below resistance"}

            limit_price = round(entry_zone * 0.998, 4)
            sl_price = round(swing_res + last["atr"] * 0.6, 4)

            risk = max(sl_price - limit_price, 0.0001)
            if risk / limit_price > 0.06:
                return {"setup_found": False, "reason": "Risk too wide"}

            tp_price = round(limit_price - risk * rr_ratio, 4)
            reason = "Bearish rally into EMA20 / resistance"

        return {
            "setup_found": True,
            "direction": direction.upper(),
            "order_type": "LIMIT",
            "entry_price": limit_price,
            "stop_loss": sl_price,
            "take_profit": tp_price,
            "risk_reward_ratio": rr_ratio,
            "risk_per_share": round(risk, 4),
            "risk_percent": round((risk / limit_price) * 100, 2),
            "current_price": current_price,
            "atr": round(float(last["atr"]), 4),
            "key_level_used": round(entry_zone, 4),
            "reason": reason,
            "note": "Place as LIMIT + Bracket/OCO (TP + SL). Never risk >1% of account.",
        }
