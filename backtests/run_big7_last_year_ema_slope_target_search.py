#!/usr/bin/env python3
"""Search Big7 EMA+slope parameters for last-year no-leverage target return."""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import random
import sys
from typing import Callable

VENV_EXEC_HINT = (
    "Use the project virtualenv, e.g. "
    "`.venv/bin/python backtests/run_big7_last_year_ema_slope_target_search.py --target-return-pct 250 --no-plot`."
)


def _missing_dependency_message(package_name: str) -> str:
    return f"Missing dependency `{package_name}`. {VENV_EXEC_HINT}"


try:
    import pandas as pd
except ImportError as exc:  # pragma: no cover - runtime dependency gate
    raise SystemExit(_missing_dependency_message("pandas")) from exc

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROOT_PATH = str(PROJECT_ROOT)
if ROOT_PATH not in sys.path:
    sys.path.insert(0, ROOT_PATH)

from backtests.run_big7_last_year_ema_slope_backtest import (
    _build_single_buy_and_hold_equity,
    _fetch_all_data,
    _filter_regular_session,
    _plot_isolated_ticker_equity_curves,
    _period_from_arg,
    _resolve_tickers,
    _validate_backtest_inputs,
    run_isolated_backtests_from_data,
    run_shared_backtest_from_data,
)
from common.models.period import Period

DEFAULT_OUTPUT_PATH = (
    PROJECT_ROOT / "backtests" / "results" / "big7_ema_slope_last_year_search.json"
)
DEFAULT_ISOLATED_OUTPUT_PATH = (
    PROJECT_ROOT / "backtests" / "results" / "big7_ema_slope_last_year_isolated_search.json"
)
EMA_VALUES: tuple[int, ...] = (2, 3, 4, 6, 8, 10, 12, 14, 16, 20, 24, 28, 32, 40, 48)
SLOPE_VALUES: tuple[int, ...] = (2, 4, 6, 8, 12, 16, 20, 24, 30, 36, 48, 60, 72, 96)
BAND_VALUES: tuple[float, ...] = (0.0, 0.001, 0.002, 0.003, 0.005, 0.008, 0.012, 0.016, 0.02, 0.022, 0.025)
STOP_VALUES: tuple[float, ...] = (0.0, 0.005, 0.01, 0.015, 0.02, 0.025, 0.03, 0.04, 0.06)
TAKE_PROFIT_VALUES: tuple[float, ...] = (0.0, 0.03, 0.04, 0.05, 0.06, 0.08, 0.10, 0.12, 0.16, 0.18, 0.22, 0.30)
TRADE_DIRECTIONS: tuple[str, ...] = ("Both", "Long Only", "Short Only")
USE_LIMIT_ENTRY_VALUES: tuple[bool, ...] = (False, True)
CLOSE_ON_NEUTRAL_VALUES: tuple[bool, ...] = (True, False)


@dataclass(frozen=True)
class SearchCandidate:
    ema_period: int
    slope_len: int
    band: float
    stop_loss_pct: float
    take_profit_pct: float
    trade_direction: str
    use_limit_entry: bool
    close_on_neutral_signal: bool


@dataclass(frozen=True)
class CandidateResult:
    candidate: SearchCandidate
    return_pct: float
    max_drawdown_pct: float
    trades: int
    wins: int
    losses: int
    skipped_entries: int
    final_equity: float
    scoring_mode: str = "shared"
    ticker_count: int = 0
    min_ticker_return_pct: float = 0.0
    median_ticker_return_pct: float = 0.0
    per_ticker_return_pct: tuple[tuple[str, float], ...] = tuple()


@dataclass(frozen=True)
class SearchOutcome:
    target_hit: bool
    hit_stage: str | None
    hit_result: CandidateResult | None
    stage_a_evaluated: int
    stage_b_evaluated: int
    ranked_results: tuple[CandidateResult, ...]


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Search Big7 EMA+slope params for last-year no-leverage target."
    )
    parser.add_argument(
        "--tickers",
        nargs="+",
        default=None,
        help="Ticker symbols (comma or space separated).",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=475,
        help="Lookback window in days from now.",
    )
    parser.add_argument(
        "--period",
        choices=["minute", "hour", "daily"],
        default="hour",
        help="Data interval.",
    )
    parser.add_argument(
        "--regular-session-only",
        action=argparse.BooleanOptionalAction,
        default=False,
        help=(
            "If enabled for intraday periods, keep only regular US session "
            "(09:30-16:00 ET)."
        ),
    )
    parser.add_argument(
        "--initial-capital",
        type=float,
        default=10_000.0,
        help="Shared portfolio starting cash.",
    )
    parser.add_argument(
        "--leverage",
        type=float,
        default=1.0,
        help="Must remain 1.0 for strict no-leverage search.",
    )
    parser.add_argument(
        "--notional-per-trade",
        type=float,
        default=10_000.0,
        help="Target notional per position.",
    )
    parser.add_argument(
        "--round-trip-commission",
        type=float,
        default=1.0,
        help="Fixed commission per completed trade pair (entry+exit).",
    )
    parser.add_argument(
        "--short-borrow-fee-apr",
        type=float,
        default=0.03,
        help="Annualized borrow fee applied to short positions in shared portfolio.",
    )
    parser.add_argument(
        "--compounding-position-sizing",
        action=argparse.BooleanOptionalAction,
        default=True,
        help=(
            "If enabled, each new trade is sized from current portfolio cash "
            "(no leverage increase; capped by shared capacity)."
        ),
    )
    parser.add_argument(
        "--position-size-cash-fraction",
        type=float,
        default=0.85,
        help="Fraction of current portfolio cash allocated to each new entry (0-1).",
    )
    parser.add_argument(
        "--target-return-pct",
        type=float,
        default=170.0,
        help=(
            "Return threshold for early success exit. "
            "In isolated mode this is the average per-ticker return threshold."
        ),
    )
    parser.add_argument(
        "--target-min-ticker-return-pct",
        type=float,
        default=None,
        help=(
            "Optional strict per-ticker threshold in isolated mode. "
            "If set, a hit requires every ticker return to be >= this value."
        ),
    )
    parser.add_argument(
        "--isolated-objective",
        choices=["avg", "min"],
        default="avg",
        help=(
            "Objective used to rank isolated-mode candidates: "
            "avg=average return, min=maximize worst ticker return."
        ),
    )
    parser.add_argument(
        "--run-mode",
        choices=["shared", "isolated"],
        default="shared",
        help=(
            "shared: optimize shared-portfolio return across tickers. "
            "isolated: optimize average standalone ticker return with all-in sizing."
        ),
    )
    parser.add_argument(
        "--benchmark-ticker",
        type=str,
        default="SPY",
        help=(
            "Ticker used as buy-and-hold benchmark line when plotting isolated results."
        ),
    )
    parser.add_argument(
        "--fixed-stop-loss-pct",
        type=float,
        default=None,
        help=(
            "Optional fixed stop-loss fraction for the entire search grid. "
            "Must be used together with --fixed-take-profit-pct."
        ),
    )
    parser.add_argument(
        "--fixed-take-profit-pct",
        type=float,
        default=None,
        help=(
            "Optional fixed take-profit fraction for the entire search grid. "
            "Must be used together with --fixed-stop-loss-pct."
        ),
    )
    parser.add_argument(
        "--stage-a-samples",
        type=int,
        default=600,
        help="Random sample count from bounded grid.",
    )
    parser.add_argument(
        "--stage-b-samples",
        type=int,
        default=200,
        help="Refinement sample count around top candidates.",
    )
    parser.add_argument(
        "--refine-top-k",
        type=int,
        default=20,
        help="Number of top stage-A candidates used for local refinement.",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=42,
        help="Deterministic RNG seed.",
    )
    parser.add_argument(
        "--leaderboard-size",
        type=int,
        default=10,
        help="Number of top configurations printed at the end.",
    )
    parser.add_argument(
        "--output-json",
        type=str,
        default=str(DEFAULT_OUTPUT_PATH),
        help="Where to write search results JSON.",
    )
    parser.add_argument(
        "--no-plot",
        action="store_true",
        help="Compatibility flag; plotting is not used in this search runner.",
    )
    return parser.parse_args(argv)


def _build_search_grid(
    *,
    fixed_stop_loss_pct: float | None = None,
    fixed_take_profit_pct: float | None = None,
) -> list[SearchCandidate]:
    stop_values = (
        (float(fixed_stop_loss_pct),)
        if fixed_stop_loss_pct is not None
        else STOP_VALUES
    )
    take_profit_values = (
        (float(fixed_take_profit_pct),)
        if fixed_take_profit_pct is not None
        else TAKE_PROFIT_VALUES
    )
    return [
        SearchCandidate(
            ema_period=ema,
            slope_len=slope,
            band=band,
            stop_loss_pct=stop_loss,
            take_profit_pct=take_profit,
            trade_direction=trade_direction,
            use_limit_entry=use_limit_entry,
            close_on_neutral_signal=close_on_neutral_signal,
        )
        for ema in EMA_VALUES
        for slope in SLOPE_VALUES
        for band in BAND_VALUES
        for stop_loss in stop_values
        for take_profit in take_profit_values
        for trade_direction in TRADE_DIRECTIONS
        for use_limit_entry in USE_LIMIT_ENTRY_VALUES
        for close_on_neutral_signal in CLOSE_ON_NEUTRAL_VALUES
    ]


def _neighbor_values[T](values: tuple[T, ...], current: T, radius: int = 1) -> tuple[T, ...]:
    idx = values.index(current)
    start = max(0, idx - max(0, radius))
    end = min(len(values), idx + max(0, radius) + 1)
    return values[start:end]


def _build_refinement_candidates(
    top_candidates: list[SearchCandidate],
    seen: set[SearchCandidate],
) -> list[SearchCandidate]:
    refined: set[SearchCandidate] = set()
    for candidate in top_candidates:
        ema_neighbors = _neighbor_values(EMA_VALUES, candidate.ema_period, radius=1)
        slope_neighbors = _neighbor_values(SLOPE_VALUES, candidate.slope_len, radius=1)
        band_neighbors = _neighbor_values(BAND_VALUES, candidate.band, radius=1)
        stop_neighbors = _neighbor_values(STOP_VALUES, candidate.stop_loss_pct, radius=1)
        tp_neighbors = _neighbor_values(TAKE_PROFIT_VALUES, candidate.take_profit_pct, radius=1)
        direction_neighbors = _neighbor_values(
            TRADE_DIRECTIONS, candidate.trade_direction, radius=1
        )
        use_limit_neighbors = USE_LIMIT_ENTRY_VALUES
        close_neutral_neighbors = CLOSE_ON_NEUTRAL_VALUES
        for ema in ema_neighbors:
            for slope in slope_neighbors:
                for band in band_neighbors:
                    for stop_loss in stop_neighbors:
                        for take_profit in tp_neighbors:
                            for direction in direction_neighbors:
                                for use_limit_entry in use_limit_neighbors:
                                    for close_on_neutral_signal in close_neutral_neighbors:
                                        refined_candidate = SearchCandidate(
                                            ema_period=ema,
                                            slope_len=slope,
                                            band=band,
                                            stop_loss_pct=stop_loss,
                                            take_profit_pct=take_profit,
                                            trade_direction=direction,
                                            use_limit_entry=use_limit_entry,
                                            close_on_neutral_signal=close_on_neutral_signal,
                                        )
                                        if refined_candidate not in seen:
                                            refined.add(refined_candidate)
    return sorted(refined, key=lambda c: (c.ema_period, c.slope_len, c.trade_direction))


def _rank_results(results: list[CandidateResult]) -> list[CandidateResult]:
    return sorted(
        results,
        key=lambda item: (
            item.return_pct,
            item.min_ticker_return_pct,
            item.median_ticker_return_pct,
            -item.max_drawdown_pct,
            item.final_equity,
            -item.skipped_entries,
        ),
        reverse=True,
    )


def _isolated_min_rank_results(results: list[CandidateResult]) -> list[CandidateResult]:
    return sorted(
        results,
        key=lambda item: (
            item.min_ticker_return_pct,
            item.return_pct,
            item.median_ticker_return_pct,
            -item.max_drawdown_pct,
            item.final_equity,
            -item.skipped_entries,
        ),
        reverse=True,
    )


def run_parameter_search(
    *,
    grid: list[SearchCandidate],
    stage_a_samples: int,
    stage_b_samples: int,
    refine_top_k: int,
    target_return_pct: float,
    seed: int,
    evaluator: Callable[[SearchCandidate], CandidateResult],
    ranker: Callable[[list[CandidateResult]], list[CandidateResult]] = _rank_results,
    hit_predicate: Callable[[CandidateResult], bool] | None = None,
) -> SearchOutcome:
    if not grid:
        return SearchOutcome(
            target_hit=False,
            hit_stage=None,
            hit_result=None,
            stage_a_evaluated=0,
            stage_b_evaluated=0,
            ranked_results=tuple(),
        )

    rng = random.Random(seed)
    deduped_grid = list(dict.fromkeys(grid))
    sample_a_size = min(max(0, stage_a_samples), len(deduped_grid))
    stage_a_candidates = (
        rng.sample(deduped_grid, sample_a_size) if sample_a_size > 0 else list(deduped_grid)
    )

    evaluated: dict[SearchCandidate, CandidateResult] = {}
    stage_a_evaluated = 0
    stage_b_evaluated = 0
    hit_stage: str | None = None
    hit_result: CandidateResult | None = None

    is_hit = hit_predicate or (lambda result: result.return_pct >= target_return_pct)

    for candidate in stage_a_candidates:
        result = evaluator(candidate)
        evaluated[candidate] = result
        stage_a_evaluated += 1
        if is_hit(result):
            hit_stage = "stage_a"
            hit_result = result
            break

    if hit_result is None and stage_b_samples > 0:
        ranked_a = ranker(list(evaluated.values()))
        if ranked_a:
            base_candidates = [item.candidate for item in ranked_a[: max(1, refine_top_k)]]
        else:
            base_count = min(max(1, refine_top_k), len(deduped_grid))
            base_candidates = rng.sample(deduped_grid, base_count)

        refine_pool = _build_refinement_candidates(base_candidates, set(evaluated.keys()))
        sample_b_size = min(max(0, stage_b_samples), len(refine_pool))
        stage_b_candidates = rng.sample(refine_pool, sample_b_size) if sample_b_size > 0 else []

        for candidate in stage_b_candidates:
            result = evaluator(candidate)
            evaluated[candidate] = result
            stage_b_evaluated += 1
            if is_hit(result):
                hit_stage = "stage_b"
                hit_result = result
                break

    ranked = tuple(ranker(list(evaluated.values())))
    return SearchOutcome(
        target_hit=hit_result is not None,
        hit_stage=hit_stage,
        hit_result=hit_result,
        stage_a_evaluated=stage_a_evaluated,
        stage_b_evaluated=stage_b_evaluated,
        ranked_results=ranked,
    )


def _fetch_strategy_data(
    *,
    tickers: list[str],
    start_time: datetime,
    end_time: datetime,
    period: Period,
    regular_session_only: bool,
) -> dict[str, pd.DataFrame]:
    fetched_data = asyncio.run(
        _fetch_all_data(
            tickers=tickers,
            start_time=start_time,
            end_time=end_time,
            period=period,
        )
    )
    data_by_ticker = {ticker: fetched_data[ticker] for ticker in tickers if ticker in fetched_data}
    if regular_session_only and period in (Period.MINUTE, Period.HOUR):
        data_by_ticker = {
            ticker: _filter_regular_session(df)
            for ticker, df in data_by_ticker.items()
        }
    return {
        ticker: df
        for ticker, df in data_by_ticker.items()
        if df is not None and not df.empty and len(df) >= 100
    }


def _evaluate_candidate_shared(
    *,
    candidate: SearchCandidate,
    data_by_ticker: dict[str, pd.DataFrame],
    initial_capital: float,
    leverage: float,
    notional_per_trade: float,
    commission_per_side: float,
    use_compounding_position_sizing: bool,
    position_size_cash_fraction: float,
    short_borrow_fee_apr: float,
) -> CandidateResult:
    shared, _, _ = run_shared_backtest_from_data(
        data_by_ticker=data_by_ticker,
        initial_capital=initial_capital,
        leverage=leverage,
        notional_per_trade=notional_per_trade,
        ema_period=candidate.ema_period,
        slope_len=candidate.slope_len,
        band=candidate.band,
        stop_loss_pct=candidate.stop_loss_pct,
        take_profit_pct=candidate.take_profit_pct,
        trade_direction=candidate.trade_direction,
        commission_per_side=commission_per_side,
        use_limit_entry=bool(candidate.use_limit_entry),
        close_on_neutral_signal=bool(candidate.close_on_neutral_signal),
        use_compounding_position_sizing=use_compounding_position_sizing,
        position_size_cash_fraction=position_size_cash_fraction,
        short_borrow_fee_apr=short_borrow_fee_apr,
        print_symbol_results=False,
    )
    return CandidateResult(
        candidate=candidate,
        return_pct=shared.total_return_pct,
        max_drawdown_pct=shared.max_drawdown_pct,
        trades=shared.total_trades,
        wins=shared.winning_trades,
        losses=shared.losing_trades,
        skipped_entries=len(shared.skipped_trades),
        final_equity=shared.final_equity,
        scoring_mode="shared",
        ticker_count=len(data_by_ticker),
    )


def _evaluate_candidate_isolated(
    *,
    candidate: SearchCandidate,
    data_by_ticker: dict[str, pd.DataFrame],
    initial_capital: float,
    leverage: float,
    notional_per_trade: float,
    commission_per_side: float,
) -> CandidateResult:
    stats_by_ticker, _ = run_isolated_backtests_from_data(
        data_by_ticker=data_by_ticker,
        initial_capital=initial_capital,
        leverage=leverage,
        notional_per_trade=notional_per_trade,
        ema_period=candidate.ema_period,
        slope_len=candidate.slope_len,
        band=candidate.band,
        stop_loss_pct=candidate.stop_loss_pct,
        take_profit_pct=candidate.take_profit_pct,
        trade_direction=candidate.trade_direction,
        commission_per_side=commission_per_side,
        use_limit_entry=bool(candidate.use_limit_entry),
        close_on_neutral_signal=bool(candidate.close_on_neutral_signal),
        print_symbol_results=False,
    )
    if not stats_by_ticker:
        return CandidateResult(
            candidate=candidate,
            return_pct=float("-inf"),
            max_drawdown_pct=100.0,
            trades=0,
            wins=0,
            losses=0,
            skipped_entries=0,
            final_equity=0.0,
            scoring_mode="isolated",
            ticker_count=0,
            min_ticker_return_pct=float("-inf"),
            median_ticker_return_pct=float("-inf"),
            per_ticker_return_pct=tuple(),
        )

    per_ticker_return_pairs: list[tuple[str, float]] = []
    max_drawdown_pct = 0.0
    total_trades = 0
    total_wins = 0
    total_losses = 0
    final_equities: list[float] = []

    for ticker, stats in stats_by_ticker.items():
        ticker_return = float(stats.get("Return [%]", 0.0))
        per_ticker_return_pairs.append((ticker, ticker_return))
        drawdown = abs(float(stats.get("Max. Drawdown [%]", 0.0)))
        max_drawdown_pct = max(max_drawdown_pct, drawdown)

        trades = int(float(stats.get("# Trades", 0)))
        win_rate = max(0.0, min(100.0, float(stats.get("Win Rate [%]", 0.0))))
        wins = int(round((trades * win_rate) / 100.0))
        wins = max(0, min(trades, wins))
        losses = max(0, trades - wins)
        total_trades += trades
        total_wins += wins
        total_losses += losses

        final_equities.append(float(stats.get("Equity Final [$]", initial_capital)))

    per_ticker_return_pairs.sort(key=lambda item: item[0])
    ticker_returns = [ret for _, ret in per_ticker_return_pairs]
    ticker_count = len(ticker_returns)
    average_return = sum(ticker_returns) / ticker_count
    min_ticker_return = min(ticker_returns)
    median_ticker_return = (
        sorted(ticker_returns)[ticker_count // 2]
        if ticker_count % 2 == 1
        else (
            sorted(ticker_returns)[(ticker_count // 2) - 1]
            + sorted(ticker_returns)[ticker_count // 2]
        )
        / 2.0
    )
    average_final_equity = sum(final_equities) / len(final_equities) if final_equities else 0.0

    return CandidateResult(
        candidate=candidate,
        return_pct=average_return,
        max_drawdown_pct=max_drawdown_pct,
        trades=total_trades,
        wins=total_wins,
        losses=total_losses,
        skipped_entries=0,
        final_equity=average_final_equity,
        scoring_mode="isolated",
        ticker_count=ticker_count,
        min_ticker_return_pct=min_ticker_return,
        median_ticker_return_pct=median_ticker_return,
        per_ticker_return_pct=tuple(per_ticker_return_pairs),
    )


def _serialize_result(result: CandidateResult) -> dict[str, object]:
    payload = asdict(result)
    payload["candidate"] = asdict(result.candidate)
    return payload


def _write_report(
    *,
    output_path: Path,
    run_mode: str,
    isolated_objective: str,
    tickers: list[str],
    lookback_days: int,
    period: str,
    regular_session_only: bool,
    initial_capital: float,
    leverage: float,
    notional_per_trade: float,
    round_trip_commission: float,
    short_borrow_fee_apr: float,
    compounding_position_sizing: bool,
    position_size_cash_fraction: float,
    target_return_pct: float,
    target_min_ticker_return_pct: float | None,
    fixed_stop_loss_pct: float | None,
    fixed_take_profit_pct: float | None,
    seed: int,
    stage_a_samples: int,
    stage_b_samples: int,
    refine_top_k: int,
    outcome: SearchOutcome,
    generated_at: datetime,
    top_n: int,
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    ranked_top = list(outcome.ranked_results[: max(1, top_n)])
    payload: dict[str, object] = {
        "generated_at_utc": generated_at.isoformat(),
        "constraints": {
            "run_mode": run_mode,
            "isolated_objective": isolated_objective,
            "tickers": tickers,
            "lookback_days": lookback_days,
            "period": period,
            "regular_session_only": regular_session_only,
            "initial_capital": initial_capital,
            "leverage": leverage,
            "notional_per_trade": notional_per_trade,
            "round_trip_commission": round_trip_commission,
            "short_borrow_fee_apr": short_borrow_fee_apr,
            "compounding_position_sizing": compounding_position_sizing,
            "position_size_cash_fraction": position_size_cash_fraction,
            "target_return_pct": target_return_pct,
            "target_min_ticker_return_pct": target_min_ticker_return_pct,
            "fixed_stop_loss_pct": fixed_stop_loss_pct,
            "fixed_take_profit_pct": fixed_take_profit_pct,
            "seed": seed,
            "stage_a_samples": stage_a_samples,
            "stage_b_samples": stage_b_samples,
            "refine_top_k": refine_top_k,
        },
        "summary": {
            "target_hit": outcome.target_hit,
            "hit_stage": outcome.hit_stage,
            "stage_a_evaluated": outcome.stage_a_evaluated,
            "stage_b_evaluated": outcome.stage_b_evaluated,
            "total_evaluated": outcome.stage_a_evaluated + outcome.stage_b_evaluated,
            "best_result": _serialize_result(outcome.ranked_results[0])
            if outcome.ranked_results
            else None,
        },
        "top_results": [_serialize_result(item) for item in ranked_top],
    }
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return output_path


def _print_leaderboard(results: tuple[CandidateResult, ...], top_n: int) -> None:
    if not results:
        print("No candidates evaluated.")
        return

    print("\nLeaderboard:")
    for rank, result in enumerate(results[: max(1, top_n)], start=1):
        candidate = result.candidate
        if result.scoring_mode == "isolated":
            print(
                f"{rank:>2}. avg_return={result.return_pct:+7.2f}% | "
                f"min_ticker={result.min_ticker_return_pct:+7.2f}% | "
                f"median_ticker={result.median_ticker_return_pct:+7.2f}% | "
                f"tickers={result.ticker_count:>2} | trades={result.trades:>4} | "
                f"max_dd={result.max_drawdown_pct:>6.2f}% | "
                f"ema={candidate.ema_period:>2} slope={candidate.slope_len:>2} "
                f"band={candidate.band:.4f} sl={candidate.stop_loss_pct:.4f} "
                f"tp={candidate.take_profit_pct:.4f} dir={candidate.trade_direction} "
                f"limit={'Y' if candidate.use_limit_entry else 'N'} "
                f"close_neutral={'Y' if candidate.close_on_neutral_signal else 'N'}"
            )
            continue
        print(
            f"{rank:>2}. return={result.return_pct:+7.2f}% | trades={result.trades:>4} | "
            f"max_dd={result.max_drawdown_pct:>6.2f}% | "
            f"ema={candidate.ema_period:>2} slope={candidate.slope_len:>2} "
            f"band={candidate.band:.4f} sl={candidate.stop_loss_pct:.4f} "
            f"tp={candidate.take_profit_pct:.4f} dir={candidate.trade_direction} "
            f"limit={'Y' if candidate.use_limit_entry else 'N'} "
            f"close_neutral={'Y' if candidate.close_on_neutral_signal else 'N'}"
        )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    run_mode = str(args.run_mode).strip().lower()
    tickers = _resolve_tickers(args.tickers)
    benchmark_ticker = str(args.benchmark_ticker).strip().upper() or "SPY"
    period = _period_from_arg(args.period)
    lookback_days = int(args.lookback_days)
    regular_session_only = bool(args.regular_session_only)
    initial_capital = float(args.initial_capital)
    leverage = float(args.leverage)
    notional_per_trade = float(args.notional_per_trade)
    round_trip_commission = float(args.round_trip_commission)
    short_borrow_fee_apr = max(0.0, float(args.short_borrow_fee_apr))
    compounding_position_sizing = bool(args.compounding_position_sizing)
    position_size_cash_fraction = max(0.0, min(1.0, float(args.position_size_cash_fraction)))
    target_return_pct = float(args.target_return_pct)
    target_min_ticker_return_pct = (
        None
        if args.target_min_ticker_return_pct is None
        else float(args.target_min_ticker_return_pct)
    )
    isolated_objective = str(args.isolated_objective).strip().lower()
    fixed_stop_loss_pct = (
        None if args.fixed_stop_loss_pct is None else float(args.fixed_stop_loss_pct)
    )
    fixed_take_profit_pct = (
        None if args.fixed_take_profit_pct is None else float(args.fixed_take_profit_pct)
    )
    commission_per_side = max(0.0, round_trip_commission / 2.0)

    _validate_backtest_inputs(
        lookback_days=lookback_days,
        initial_capital=initial_capital,
        leverage=leverage,
        notional_per_trade=notional_per_trade,
        target_return_pct=target_return_pct,
        run_mode=run_mode,
    )
    if target_min_ticker_return_pct is not None and target_min_ticker_return_pct <= 0:
        raise SystemExit("Invalid --target-min-ticker-return-pct. Value must be > 0.")
    has_fixed_sl = fixed_stop_loss_pct is not None
    has_fixed_tp = fixed_take_profit_pct is not None
    if has_fixed_sl != has_fixed_tp:
        raise SystemExit(
            "Use --fixed-stop-loss-pct and --fixed-take-profit-pct together."
        )
    if fixed_stop_loss_pct is not None and fixed_stop_loss_pct < 0:
        raise SystemExit("Invalid --fixed-stop-loss-pct. Value must be >= 0.")
    if fixed_take_profit_pct is not None and fixed_take_profit_pct < 0:
        raise SystemExit("Invalid --fixed-take-profit-pct. Value must be >= 0.")
    if leverage != 1.0:
        raise SystemExit(
            "Strict no-leverage search requires --leverage 1.0. "
            "Set --leverage 1.0 and rerun."
        )

    stage_a_samples = int(args.stage_a_samples)
    stage_b_samples = int(args.stage_b_samples)
    refine_top_k = int(args.refine_top_k)
    if stage_a_samples < 0 or stage_b_samples < 0 or refine_top_k < 1:
        raise SystemExit(
            "Invalid search controls: --stage-a-samples >= 0, --stage-b-samples >= 0, "
            "--refine-top-k >= 1 are required."
        )

    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=max(1, lookback_days))

    print("=" * 96)
    if run_mode == "isolated":
        print("BIG7 EMA+SLOPE TARGET SEARCH (ISOLATED TICKERS, NO LEVERAGE)")
    else:
        print("BIG7 EMA+SLOPE TARGET SEARCH (SHARED PORTFOLIO, NO LEVERAGE)")
    print("=" * 96)
    print(f"Tickers: {', '.join(tickers)}")
    if run_mode == "isolated":
        print(f"Benchmark: {benchmark_ticker}")
    print(f"Date range: {start_time.date()} to {end_time.date()} | Interval: {period.value}")
    if run_mode == "isolated":
        print(
            f"Initial capital=${initial_capital:,.2f} | Leverage={leverage:.2f}x | "
            "Standalone sizing=ALL-IN (100% each trade)"
        )
    else:
        print(
            f"Initial capital=${initial_capital:,.2f} | Leverage={leverage:.2f}x | "
            f"Notional/trade=${notional_per_trade:,.2f}"
        )
    print(
        f"Target={target_return_pct:.2f}% ({'avg per ticker' if run_mode == 'isolated' else 'shared return'}) | "
        f"Commission=${round_trip_commission:.2f}/pair | "
        f"StageA={stage_a_samples} | StageB={stage_b_samples} | TopK={refine_top_k} | Seed={args.seed}"
    )
    if run_mode == "isolated":
        print(
            f"IsolatedObjective={isolated_objective} | "
            f"TargetMinTicker={target_min_ticker_return_pct if target_min_ticker_return_pct is not None else 'None'}"
        )
    print(f"Regular session only: {regular_session_only}")
    print(
        f"ShortBorrowAPR={short_borrow_fee_apr:.2%} | "
        f"CompoundingSizing={compounding_position_sizing} | "
        f"CashFraction/Trade={position_size_cash_fraction:.2f}"
    )
    if fixed_stop_loss_pct is not None and fixed_take_profit_pct is not None:
        print(
            "Fixed SL/TP: "
            f"SL={fixed_stop_loss_pct:.4f} | TP={fixed_take_profit_pct:.4f}"
        )
    print("=" * 96)

    data_by_ticker = _fetch_strategy_data(
        tickers=tickers,
        start_time=start_time,
        end_time=end_time,
        period=period,
        regular_session_only=regular_session_only,
    )
    if not data_by_ticker:
        print("No strategy-ticker data fetched.")
        return 2

    grid = _build_search_grid(
        fixed_stop_loss_pct=fixed_stop_loss_pct,
        fixed_take_profit_pct=fixed_take_profit_pct,
    )
    if not grid:
        print("Search grid is empty.")
        return 2

    def _evaluator(candidate: SearchCandidate) -> CandidateResult:
        if run_mode == "isolated":
            return _evaluate_candidate_isolated(
                candidate=candidate,
                data_by_ticker=data_by_ticker,
                initial_capital=initial_capital,
                leverage=leverage,
                notional_per_trade=notional_per_trade,
                commission_per_side=commission_per_side,
            )
        return _evaluate_candidate_shared(
            candidate=candidate,
            data_by_ticker=data_by_ticker,
            initial_capital=initial_capital,
            leverage=leverage,
            notional_per_trade=notional_per_trade,
            commission_per_side=commission_per_side,
            use_compounding_position_sizing=compounding_position_sizing,
            position_size_cash_fraction=position_size_cash_fraction,
            short_borrow_fee_apr=short_borrow_fee_apr,
        )

    if run_mode == "isolated" and isolated_objective == "min":
        ranker = _isolated_min_rank_results
    else:
        ranker = _rank_results

    if run_mode == "isolated" and target_min_ticker_return_pct is not None:
        def _hit_predicate(result: CandidateResult) -> bool:
            return (
                result.return_pct >= target_return_pct
                and result.min_ticker_return_pct >= target_min_ticker_return_pct
            )
    else:
        _hit_predicate = None

    outcome = run_parameter_search(
        grid=grid,
        stage_a_samples=stage_a_samples,
        stage_b_samples=stage_b_samples,
        refine_top_k=refine_top_k,
        target_return_pct=target_return_pct,
        seed=int(args.seed),
        evaluator=_evaluator,
        ranker=ranker,
        hit_predicate=_hit_predicate,
    )

    leaderboard_size = max(1, int(args.leaderboard_size))
    _print_leaderboard(outcome.ranked_results, top_n=leaderboard_size)

    output_path = Path(args.output_json)
    if (
        run_mode == "isolated"
        and output_path == DEFAULT_OUTPUT_PATH
    ):
        output_path = DEFAULT_ISOLATED_OUTPUT_PATH

    report_path = _write_report(
        output_path=output_path,
        run_mode=run_mode,
        isolated_objective=isolated_objective,
        tickers=tickers,
        lookback_days=lookback_days,
        period=period.value,
        regular_session_only=regular_session_only,
        initial_capital=initial_capital,
        leverage=leverage,
        notional_per_trade=notional_per_trade,
        round_trip_commission=round_trip_commission,
        short_borrow_fee_apr=short_borrow_fee_apr,
        compounding_position_sizing=compounding_position_sizing,
        position_size_cash_fraction=position_size_cash_fraction,
        target_return_pct=target_return_pct,
        target_min_ticker_return_pct=target_min_ticker_return_pct,
        fixed_stop_loss_pct=fixed_stop_loss_pct,
        fixed_take_profit_pct=fixed_take_profit_pct,
        seed=int(args.seed),
        stage_a_samples=stage_a_samples,
        stage_b_samples=stage_b_samples,
        refine_top_k=refine_top_k,
        outcome=outcome,
        generated_at=datetime.now(timezone.utc),
        top_n=leaderboard_size,
    )
    print(f"\nReport written: {report_path}")

    if outcome.target_hit:
        hit = outcome.hit_result
        stage = outcome.hit_stage or "unknown"
        return_pct = hit.return_pct if hit is not None else 0.0
        if run_mode == "isolated" and not bool(args.no_plot) and outcome.ranked_results:
            best = outcome.ranked_results[0].candidate
            _, equity_by_ticker = run_isolated_backtests_from_data(
                data_by_ticker=data_by_ticker,
                initial_capital=initial_capital,
                leverage=leverage,
                notional_per_trade=notional_per_trade,
                ema_period=best.ema_period,
                slope_len=best.slope_len,
                band=best.band,
                stop_loss_pct=best.stop_loss_pct,
                take_profit_pct=best.take_profit_pct,
                trade_direction=best.trade_direction,
                commission_per_side=commission_per_side,
                use_limit_entry=bool(best.use_limit_entry),
                close_on_neutral_signal=bool(best.close_on_neutral_signal),
                print_symbol_results=False,
            )

            benchmark_df = _fetch_strategy_data(
                tickers=[benchmark_ticker],
                start_time=start_time,
                end_time=end_time,
                period=period,
                regular_session_only=regular_session_only,
            ).get(benchmark_ticker, pd.DataFrame())

            master_index = pd.DatetimeIndex([])
            for series in equity_by_ticker.values():
                if series is not None and not series.empty:
                    master_index = master_index.union(pd.DatetimeIndex(series.index))
            if benchmark_df is not None and not benchmark_df.empty:
                master_index = master_index.union(pd.DatetimeIndex(benchmark_df.index))
            master_index = master_index.sort_values()

            benchmark_bh_equity = None
            if benchmark_df is not None and not benchmark_df.empty and not master_index.empty:
                benchmark_bh_equity = _build_single_buy_and_hold_equity(
                    df=benchmark_df,
                    index=master_index,
                    initial_capital=initial_capital,
                )

            _plot_isolated_ticker_equity_curves(
                equity_by_ticker=equity_by_ticker,
                initial_capital=initial_capital,
                benchmark_bh_equity=benchmark_bh_equity,
                benchmark_ticker=benchmark_ticker,
            )

        if run_mode == "isolated":
            min_return = hit.min_ticker_return_pct if hit is not None else 0.0
            min_target_text = (
                f", min target={target_min_ticker_return_pct:.2f}%"
                if target_min_ticker_return_pct is not None
                else ""
            )
            print(
                f"Target avg {target_return_pct:.2f}% => HIT "
                f"(avg={return_pct:+.2f}%, min_ticker={min_return:+.2f}%{min_target_text}) in {stage}."
            )
        else:
            print(f"Target {target_return_pct:.2f}% => HIT ({return_pct:+.2f}%) in {stage}.")
        return 0

    best_return = outcome.ranked_results[0].return_pct if outcome.ranked_results else 0.0
    if run_mode == "isolated" and not bool(args.no_plot) and outcome.ranked_results:
        best = outcome.ranked_results[0].candidate
        _, equity_by_ticker = run_isolated_backtests_from_data(
            data_by_ticker=data_by_ticker,
            initial_capital=initial_capital,
            leverage=leverage,
            notional_per_trade=notional_per_trade,
            ema_period=best.ema_period,
            slope_len=best.slope_len,
            band=best.band,
            stop_loss_pct=best.stop_loss_pct,
            take_profit_pct=best.take_profit_pct,
            trade_direction=best.trade_direction,
            commission_per_side=commission_per_side,
            use_limit_entry=bool(best.use_limit_entry),
            close_on_neutral_signal=bool(best.close_on_neutral_signal),
            print_symbol_results=False,
        )

        benchmark_df = _fetch_strategy_data(
            tickers=[benchmark_ticker],
            start_time=start_time,
            end_time=end_time,
            period=period,
            regular_session_only=regular_session_only,
        ).get(benchmark_ticker, pd.DataFrame())

        master_index = pd.DatetimeIndex([])
        for series in equity_by_ticker.values():
            if series is not None and not series.empty:
                master_index = master_index.union(pd.DatetimeIndex(series.index))
        if benchmark_df is not None and not benchmark_df.empty:
            master_index = master_index.union(pd.DatetimeIndex(benchmark_df.index))
        master_index = master_index.sort_values()

        benchmark_bh_equity = None
        if benchmark_df is not None and not benchmark_df.empty and not master_index.empty:
            benchmark_bh_equity = _build_single_buy_and_hold_equity(
                df=benchmark_df,
                index=master_index,
                initial_capital=initial_capital,
            )

        _plot_isolated_ticker_equity_curves(
            equity_by_ticker=equity_by_ticker,
            initial_capital=initial_capital,
            benchmark_bh_equity=benchmark_bh_equity,
            benchmark_ticker=benchmark_ticker,
        )

    if run_mode == "isolated":
        best_min = (
            outcome.ranked_results[0].min_ticker_return_pct
            if outcome.ranked_results
            else 0.0
        )
        min_target_text = (
            f", min_target={target_min_ticker_return_pct:.2f}%"
            if target_min_ticker_return_pct is not None
            else ""
        )
        print(
            f"Target avg {target_return_pct:.2f}% => MISS "
            f"(best_avg={best_return:+.2f}%, best_min_ticker={best_min:+.2f}%{min_target_text})."
        )
    else:
        print(f"Target {target_return_pct:.2f}% => MISS (best={best_return:+.2f}%).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
