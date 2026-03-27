"""Unit tests for Big7 EMA+slope target-search runner."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

import backtests.run_big7_last_year_ema_slope_target_search as search


class Big7EmaSlopeTargetSearchTests(unittest.TestCase):
    def _candidate(self, ema: int, slope: int) -> search.SearchCandidate:
        return search.SearchCandidate(
            ema_period=ema,
            slope_len=slope,
            band=0.0,
            stop_loss_pct=0.02,
            take_profit_pct=0.08,
            trade_direction="Both",
            use_limit_entry=False,
            close_on_neutral_signal=True,
        )

    def test_early_stop_triggers_when_target_hit(self) -> None:
        c1 = self._candidate(10, 8)
        c2 = self._candidate(12, 8)
        c3 = self._candidate(14, 8)
        seen: list[search.SearchCandidate] = []

        def evaluator(candidate: search.SearchCandidate) -> search.CandidateResult:
            seen.append(candidate)
            ret = 300.0 if candidate == c1 else 10.0
            return search.CandidateResult(
                candidate=candidate,
                return_pct=ret,
                max_drawdown_pct=5.0,
                trades=10,
                wins=7,
                losses=3,
                skipped_entries=0,
                final_equity=10_000.0,
            )

        outcome = search.run_parameter_search(
            grid=[c1, c2, c3],
            stage_a_samples=0,  # deterministic ordered pass over full provided grid
            stage_b_samples=50,
            refine_top_k=2,
            target_return_pct=250.0,
            seed=7,
            evaluator=evaluator,
        )

        self.assertTrue(outcome.target_hit)
        self.assertEqual(outcome.hit_stage, "stage_a")
        self.assertEqual(outcome.stage_a_evaluated, 1)
        self.assertEqual(outcome.stage_b_evaluated, 0)
        self.assertEqual(len(seen), 1)

    def test_same_seed_produces_same_ranking(self) -> None:
        grid = [self._candidate(10, 8), self._candidate(12, 8), self._candidate(14, 8)]

        def evaluator(candidate: search.SearchCandidate) -> search.CandidateResult:
            score = (
                candidate.ema_period * 0.7
                + candidate.slope_len * 0.3
                - candidate.stop_loss_pct * 100
                + candidate.take_profit_pct * 100
            )
            return search.CandidateResult(
                candidate=candidate,
                return_pct=score,
                max_drawdown_pct=10.0,
                trades=5,
                wins=3,
                losses=2,
                skipped_entries=0,
                final_equity=10_000.0 + score,
            )

        outcome_a = search.run_parameter_search(
            grid=grid,
            stage_a_samples=2,
            stage_b_samples=2,
            refine_top_k=2,
            target_return_pct=1_000.0,
            seed=123,
            evaluator=evaluator,
        )
        outcome_b = search.run_parameter_search(
            grid=grid,
            stage_a_samples=2,
            stage_b_samples=2,
            refine_top_k=2,
            target_return_pct=1_000.0,
            seed=123,
            evaluator=evaluator,
        )

        ranking_a = [(item.candidate, item.return_pct) for item in outcome_a.ranked_results]
        ranking_b = [(item.candidate, item.return_pct) for item in outcome_b.ranked_results]
        self.assertEqual(ranking_a, ranking_b)

    def test_main_returns_two_and_writes_report_when_target_missed(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "search-report.json"
            candidate = self._candidate(10, 8)
            mocked_outcome = search.SearchOutcome(
                target_hit=False,
                hit_stage=None,
                hit_result=None,
                stage_a_evaluated=1,
                stage_b_evaluated=0,
                ranked_results=(
                    search.CandidateResult(
                        candidate=candidate,
                        return_pct=12.5,
                        max_drawdown_pct=8.0,
                        trades=4,
                        wins=2,
                        losses=2,
                        skipped_entries=0,
                        final_equity=11_250.0,
                    ),
                ),
            )

            with (
                patch.object(
                    search,
                    "_fetch_strategy_data",
                    return_value={
                        "AAPL": pd.DataFrame(
                            {"Open": [1.0] * 100, "High": [1.0] * 100, "Low": [1.0] * 100,
                             "Close": [1.0] * 100, "Volume": [100] * 100}
                        )
                    },
                ),
                patch.object(search, "_build_search_grid", return_value=[candidate]),
                patch.object(search, "run_parameter_search", return_value=mocked_outcome),
            ):
                exit_code = search.main(
                    ["--output-json", str(output_path), "--stage-a-samples", "1", "--no-plot"]
                )

            self.assertEqual(exit_code, 2)
            self.assertTrue(output_path.exists())
            report = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertFalse(report["summary"]["target_hit"])
            self.assertAlmostEqual(
                float(report["summary"]["best_result"]["return_pct"]),
                12.5,
                places=8,
            )
            self.assertEqual(str(report["constraints"]["run_mode"]), "shared")

    def test_isolated_evaluator_scores_average_and_min_return(self) -> None:
        candidate = self._candidate(10, 8)
        mock_stats = {
            "AAPL": pd.Series(
                {
                    "Return [%]": 300.0,
                    "Max. Drawdown [%]": -21.0,
                    "# Trades": 10,
                    "Win Rate [%]": 60.0,
                    "Equity Final [$]": 40_000.0,
                }
            ),
            "MSFT": pd.Series(
                {
                    "Return [%]": 200.0,
                    "Max. Drawdown [%]": -12.0,
                    "# Trades": 4,
                    "Win Rate [%]": 50.0,
                    "Equity Final [$]": 30_000.0,
                }
            ),
        }

        with patch.object(
            search, "run_isolated_backtests_from_data", return_value=(mock_stats, {})
        ):
            result = search._evaluate_candidate_isolated(
                candidate=candidate,
                data_by_ticker={
                    "AAPL": pd.DataFrame({"Close": [1.0] * 120}),
                    "MSFT": pd.DataFrame({"Close": [1.0] * 120}),
                },
                initial_capital=10_000.0,
                leverage=1.0,
                notional_per_trade=10_000.0,
                commission_per_side=0.5,
            )

        self.assertEqual(result.scoring_mode, "isolated")
        self.assertEqual(result.ticker_count, 2)
        self.assertAlmostEqual(result.return_pct, 250.0, places=8)
        self.assertAlmostEqual(result.min_ticker_return_pct, 200.0, places=8)
        self.assertAlmostEqual(result.median_ticker_return_pct, 250.0, places=8)
        self.assertEqual(result.trades, 14)
        self.assertEqual(result.wins, 8)
        self.assertEqual(result.losses, 6)
        self.assertAlmostEqual(result.max_drawdown_pct, 21.0, places=8)
        self.assertEqual(result.per_ticker_return_pct, (("AAPL", 300.0), ("MSFT", 200.0)))

    def test_main_isolated_report_marks_run_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "isolated-search-report.json"
            candidate = self._candidate(10, 8)
            mocked_outcome = search.SearchOutcome(
                target_hit=False,
                hit_stage=None,
                hit_result=None,
                stage_a_evaluated=1,
                stage_b_evaluated=0,
                ranked_results=(
                    search.CandidateResult(
                        candidate=candidate,
                        return_pct=260.0,
                        max_drawdown_pct=20.0,
                        trades=10,
                        wins=7,
                        losses=3,
                        skipped_entries=0,
                        final_equity=36_000.0,
                        scoring_mode="isolated",
                        ticker_count=7,
                        min_ticker_return_pct=205.0,
                        median_ticker_return_pct=255.0,
                        per_ticker_return_pct=(("AAPL", 260.0),),
                    ),
                ),
            )

            with (
                patch.object(
                    search,
                    "_fetch_strategy_data",
                    return_value={
                        "AAPL": pd.DataFrame(
                            {"Open": [1.0] * 100, "High": [1.0] * 100, "Low": [1.0] * 100,
                             "Close": [1.0] * 100, "Volume": [100] * 100}
                        )
                    },
                ),
                patch.object(search, "_build_search_grid", return_value=[candidate]),
                patch.object(search, "run_parameter_search", return_value=mocked_outcome),
            ):
                exit_code = search.main(
                    [
                        "--run-mode",
                        "isolated",
                        "--output-json",
                        str(output_path),
                        "--stage-a-samples",
                        "1",
                        "--no-plot",
                    ]
                )

            self.assertEqual(exit_code, 2)
            report = json.loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(str(report["constraints"]["run_mode"]), "isolated")
            self.assertAlmostEqual(
                float(report["summary"]["best_result"]["min_ticker_return_pct"]),
                205.0,
                places=8,
            )


if __name__ == "__main__":
    unittest.main()
