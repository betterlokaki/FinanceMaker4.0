#!/usr/bin/env python3
"""Nested validation for Mag7 5-minute pooled ML fixed-RR strategy."""
from __future__ import annotations

from pathlib import Path
import sys
from typing import Any

import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ROOT_PATH = str(PROJECT_ROOT)
if ROOT_PATH not in sys.path:
    sys.path.insert(0, ROOT_PATH)

from sklearn.ensemble import HistGradientBoostingClassifier

from backtests.backtesting_py.mag7_adaptive_long_short_strategy import (
    MAG7_TICKERS,
    SleevePortfolioResult,
    SleeveTrade,
)
from backtests.backtesting_py.mag7_intraday_ml_rr_strategy import MlRrParams
from backtests.backtesting_py.mag7_intraday_orb_strategy import prepare_intraday_frame
from backtests.run_mag7_intraday_orb_backtest import (
    _anchored_monthly_returns,
    _fetch_alpaca_5min_cached,
    _parse_args,
    _passes_summary,
    _print_summary,
    _result_summary,
    _write_json_summary,
    parse_date_range_utc,
)


DEFAULT_OUTPUT_PATH = PROJECT_ROOT / "backtests" / "results" / "mag7_intraday_ml_rr_backtest.json"


def main() -> int:
    args = _parse_args()
    if Path(str(args.output_json)).name == "mag7_intraday_orb_backtest.json":
        args.output_json = str(DEFAULT_OUTPUT_PATH)

    subtrain_start, subtrain_end_exclusive = parse_date_range_utc(
        start_date=str(args.dev_start_date),
        end_date="2025-12-31",
    )
    validation_start, validation_end_exclusive = parse_date_range_utc(
        start_date="2026-01-01",
        end_date=str(args.dev_end_date),
    )
    holdout_start, holdout_end_exclusive = parse_date_range_utc(
        start_date=str(args.holdout_start_date),
        end_date=str(args.holdout_end_date),
    )
    dev_start, dev_end_exclusive = parse_date_range_utc(
        start_date=str(args.dev_start_date),
        end_date=str(args.dev_end_date),
    )

    tickers = list(MAG7_TICKERS)
    benchmark = str(args.benchmark_ticker).strip().upper() or "SPY"
    fetch_tickers = tickers + ([] if benchmark in tickers else [benchmark])
    target = float(args.target_monthly_return_pct)
    initial_capital = float(args.initial_capital)
    round_trip_commission = float(args.round_trip_commission)
    cache_dir = Path(args.cache_dir).expanduser().resolve()

    print("=" * 112, flush=True)
    print("MAG7 5-MINUTE POOLED ML FIXED-RR NESTED VALIDATION", flush=True)
    print("=" * 112, flush=True)
    print(f"Subtrain: {args.dev_start_date} to 2025-12-31", flush=True)
    print(f"Validation: 2026-01-01 to {args.dev_end_date}", flush=True)
    print(f"Final holdout: {args.holdout_start_date} to {args.holdout_end_date}", flush=True)
    print("=" * 112, flush=True)

    dev_data = _fetch_alpaca_5min_cached(
        tickers=fetch_tickers,
        start_time=dev_start,
        end_time=dev_end_exclusive,
        cache_dir=cache_dir,
        label="dev",
        feed=str(args.alpaca_feed),
        refresh=bool(args.refresh_cache),
    )
    holdout_data = _fetch_alpaca_5min_cached(
        tickers=fetch_tickers,
        start_time=holdout_start,
        end_time=holdout_end_exclusive,
        cache_dir=cache_dir,
        label="holdout",
        feed=str(args.alpaca_feed),
        refresh=bool(args.refresh_cache),
    )

    frames: dict[str, pd.DataFrame] = {}
    features: dict[str, pd.DataFrame] = {}
    for idx, ticker in enumerate(tickers):
        raw = pd.concat([dev_data[ticker], holdout_data[ticker]]).sort_index()
        frame, feature = _feature_frame(raw, ticker_code=idx)
        frames[ticker] = frame
        features[ticker] = feature

    subtrain_start_ts = pd.Timestamp(subtrain_start).tz_convert(None)
    subtrain_end_ts = pd.Timestamp(subtrain_end_exclusive - pd.Timedelta(seconds=1)).tz_convert(None)
    validation_start_ts = pd.Timestamp(validation_start).tz_convert(None)
    validation_end_ts = pd.Timestamp(validation_end_exclusive - pd.Timedelta(seconds=1)).tz_convert(None)
    holdout_start_ts = pd.Timestamp(holdout_start).tz_convert(None)
    holdout_end_ts = pd.Timestamp(holdout_end_exclusive - pd.Timedelta(seconds=1)).tz_convert(None)

    candidates = _candidate_params(max_candidates=int(args.max_candidates))
    print(f"Scoring {len(candidates)} ML candidates...", flush=True)
    scored: list[dict[str, Any]] = []
    thresholds = (0.45, 0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9)

    for idx, params in enumerate(candidates, start=1):
        models = _train_models(
            frames=frames,
            features=features,
            tickers=tickers,
            params=params,
            train_end=subtrain_end_ts,
        )
        best_threshold: float | None = None
        best_validation: SleevePortfolioResult | None = None
        best_score = -1e18
        for threshold in thresholds:
            validation = _run_portfolio(
                frames=frames,
                features=features,
                tickers=tickers,
                params=params,
                models=models,
                threshold=threshold,
                start_time=validation_start_ts,
                end_time=validation_end_ts,
                initial_capital=initial_capital,
                round_trip_commission=round_trip_commission,
                target_monthly_return_pct=target,
            )
            validation_summary = _result_summary(result=validation, target=target)
            score = _score_summary(validation_summary)
            if score > best_score:
                best_score = score
                best_threshold = threshold
                best_validation = validation
        if best_threshold is None or best_validation is None:
            continue
        subtrain = _run_portfolio(
            frames=frames,
            features=features,
            tickers=tickers,
            params=params,
            models=models,
            threshold=best_threshold,
            start_time=subtrain_start_ts,
            end_time=subtrain_end_ts,
            initial_capital=initial_capital,
            round_trip_commission=round_trip_commission,
            target_monthly_return_pct=target,
        )
        sub_summary = _result_summary(result=subtrain, target=target)
        val_summary = _result_summary(result=best_validation, target=target)
        nested_score = _nested_score(sub_summary=sub_summary, val_summary=val_summary)
        scored.append(
            {
                "score": nested_score,
                "threshold": best_threshold,
                "params": params.to_dict(),
                "subtrain": sub_summary,
                "validation": val_summary,
            }
        )
        print(
            f"  tested {idx:>3}/{len(candidates)} | H={params.horizon_bars} "
            f"stop={params.stop_pct:.4f} th={best_threshold:.2f} | "
            f"val mean={val_summary['mean_monthly_return_pct']:+.2f}% | "
            f"val min_iso={val_summary['isolated_min_mean_monthly_return_pct']:+.2f}% | "
            f"val rr={val_summary['average_win_to_average_loss']:.2f}:1",
            flush=True,
        )

    scored.sort(key=lambda row: float(row["score"]), reverse=True)
    if not scored:
        raise SystemExit("No ML candidates could be scored.")

    best_params = MlRrParams(**scored[0]["params"])
    best_threshold = float(scored[0]["threshold"])
    best_models = _train_models(
        frames=frames,
        features=features,
        tickers=tickers,
        params=best_params,
        train_end=subtrain_end_ts,
    )
    full_dev = _run_portfolio(
        frames=frames,
        features=features,
        tickers=tickers,
        params=best_params,
        models=best_models,
        threshold=best_threshold,
        start_time=subtrain_start_ts,
        end_time=validation_end_ts,
        initial_capital=initial_capital,
        round_trip_commission=round_trip_commission,
        target_monthly_return_pct=target,
    )
    holdout = _run_portfolio(
        frames=frames,
        features=features,
        tickers=tickers,
        params=best_params,
        models=best_models,
        threshold=best_threshold,
        start_time=holdout_start_ts,
        end_time=holdout_end_ts,
        initial_capital=initial_capital,
        round_trip_commission=round_trip_commission,
        target_monthly_return_pct=target,
    )
    full_dev_summary = _result_summary(result=full_dev, target=target)
    holdout_summary = _result_summary(result=holdout, target=target)
    holdout_anchored = _anchored_monthly_returns(equity=holdout.equity_curve, start_time=holdout_start_ts)

    print("\nBEST SUBTRAIN RESULT", flush=True)
    print("-" * 112, flush=True)
    _print_summary(scored[0]["subtrain"])
    print("\nBEST VALIDATION RESULT", flush=True)
    print("-" * 112, flush=True)
    _print_summary(scored[0]["validation"])
    print("Params:", best_params.to_dict() | {"probability_threshold": best_threshold}, flush=True)
    print("\nFULL PRE-HOLDOUT RESULT", flush=True)
    print("-" * 112, flush=True)
    _print_summary(full_dev_summary)
    print("\nSTRICT 5-MINUTE HOLDOUT RESULT", flush=True)
    print("-" * 112, flush=True)
    _print_summary(holdout_summary)
    print("Anchored holdout month returns:", {k: round(v, 4) for k, v in holdout_anchored.items()}, flush=True)

    payload = {
        "strategy": "mag7_intraday_ml_rr",
        "tickers": tickers,
        "subtrain_window": {"start": str(args.dev_start_date), "end": "2025-12-31"},
        "validation_window": {"start": "2026-01-01", "end": str(args.dev_end_date)},
        "holdout_window": {
            "start": str(args.holdout_start_date),
            "end": str(args.holdout_end_date),
            "anchored_monthly_returns_pct": holdout_anchored,
        },
        "initial_capital": initial_capital,
        "target_monthly_return_pct": target,
        "round_trip_commission": round_trip_commission,
        "best_params": best_params.to_dict() | {"probability_threshold": best_threshold},
        "best_subtrain": scored[0]["subtrain"],
        "best_validation": scored[0]["validation"],
        "development": full_dev_summary,
        "holdout": holdout_summary,
        "top_candidates": scored[:25],
    }
    _write_json_summary(path=str(args.output_json), payload=payload)

    dev_pass = _passes_summary(full_dev_summary, target=target)
    holdout_pass = _passes_summary(holdout_summary, target=target) and all(
        value >= target for value in holdout_anchored.values()
    )
    print("\nGATE", flush=True)
    print("-" * 112, flush=True)
    print(f"Development gate: {'PASS' if dev_pass else 'MISS'}", flush=True)
    print(f"Holdout gate: {'PASS' if holdout_pass else 'MISS'}", flush=True)
    print(f"Live conversion allowed: {'YES' if dev_pass and holdout_pass else 'NO'}", flush=True)
    print("=" * 112, flush=True)
    return 0


def _candidate_params(*, max_candidates: int) -> list[MlRrParams]:
    values = [
        MlRrParams(horizon_bars=6, stop_pct=0.004),
        MlRrParams(horizon_bars=6, stop_pct=0.006),
        MlRrParams(horizon_bars=12, stop_pct=0.004),
        MlRrParams(horizon_bars=12, stop_pct=0.006),
        MlRrParams(horizon_bars=24, stop_pct=0.006),
        MlRrParams(horizon_bars=24, stop_pct=0.008),
    ]
    return values[: max(1, min(int(max_candidates), len(values)))]


def _feature_frame(raw: pd.DataFrame, *, ticker_code: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    frame = prepare_intraday_frame(raw)
    close = frame["Close"].astype(float)
    open_ = frame["Open"].astype(float)
    high = frame["High"].astype(float)
    low = frame["Low"].astype(float)
    volume = frame["Volume"].astype(float)
    feature = pd.DataFrame(index=frame.index)
    for bars in (1, 2, 3, 6, 12, 24, 39):
        feature[f"ret_{bars}"] = close.pct_change(bars)
    feature["body_pct"] = (close - open_) / open_.replace(0.0, np.nan)
    feature["range_pct"] = (high - low) / close.replace(0.0, np.nan)
    feature["vwap_dist"] = (close - frame["VWAP"].astype(float)) / close.replace(0.0, np.nan)
    feature["open_ret"] = close.groupby(frame["SessionDate"]).transform(lambda series: (series / series.iloc[0]) - 1.0)
    feature["bar_fraction"] = frame["BarInSession"].astype(float) / 78.0
    feature["volume_z20"] = (volume - volume.rolling(20).mean()) / volume.rolling(20).std().replace(0.0, np.nan)
    feature["volatility_20"] = close.pct_change().rolling(20).std()
    feature["ema12_dist"] = close.ewm(span=12, adjust=False).mean() / close - 1.0
    feature["ema39_dist"] = close.ewm(span=39, adjust=False).mean() / close - 1.0
    feature["ticker_code"] = float(ticker_code) / 10.0
    return frame, feature.replace([np.inf, -np.inf], np.nan).fillna(0.0)


def _build_labels(frame: pd.DataFrame, *, params: MlRrParams) -> tuple[np.ndarray, np.ndarray]:
    count = len(frame)
    opens = frame["Open"].to_numpy(dtype=float)
    highs = frame["High"].to_numpy(dtype=float)
    lows = frame["Low"].to_numpy(dtype=float)
    long_labels = np.zeros(count, dtype=int)
    short_labels = np.zeros(count, dtype=int)
    horizon = max(1, int(params.horizon_bars))
    stop_pct = max(0.0001, float(params.stop_pct))
    rr = max(0.1, float(params.risk_reward_ratio))
    for idx in range(count - horizon - 1):
        entry = float(opens[idx + 1])
        if not np.isfinite(entry) or entry <= 0.0:
            continue
        risk = entry * stop_pct
        for future_idx in range(idx + 1, min(count, idx + 1 + horizon)):
            if lows[future_idx] <= entry - risk:
                break
            if highs[future_idx] >= entry + risk * rr:
                long_labels[idx] = 1
                break
        for future_idx in range(idx + 1, min(count, idx + 1 + horizon)):
            if highs[future_idx] >= entry + risk:
                break
            if lows[future_idx] <= entry - risk * rr:
                short_labels[idx] = 1
                break
    return long_labels, short_labels


def _train_models(
    *,
    frames: dict[str, pd.DataFrame],
    features: dict[str, pd.DataFrame],
    tickers: list[str],
    params: MlRrParams,
    train_end: pd.Timestamp,
) -> dict[str, Any]:
    feature_rows: list[pd.DataFrame] = []
    long_labels: list[np.ndarray] = []
    short_labels: list[np.ndarray] = []
    for ticker in tickers:
        labels_long, labels_short = _build_labels(frames[ticker], params=params)
        frame_index = pd.DatetimeIndex(frames[ticker].index)
        cutoff_pos = int(frame_index.searchsorted(pd.Timestamp(train_end), side="right")) - 1
        last_eligible_label_pos = cutoff_pos - max(1, int(params.horizon_bars))
        eligible_label_mask = np.zeros(len(frame_index), dtype=bool)
        if last_eligible_label_pos >= 0:
            eligible_label_mask[: last_eligible_label_pos + 1] = True
        train_mask = (frame_index <= pd.Timestamp(train_end)) & eligible_label_mask
        feature_rows.append(features[ticker].loc[train_mask])
        long_labels.append(labels_long[train_mask])
        short_labels.append(labels_short[train_mask])
    X = pd.concat(feature_rows, axis=0)
    y_long = np.concatenate(long_labels)
    y_short = np.concatenate(short_labels)
    return {
        "long": _fit_classifier(X, y_long, params=params),
        "short": _fit_classifier(X, y_short, params=params),
    }


def _fit_classifier(X: pd.DataFrame, y: np.ndarray, *, params: MlRrParams) -> HistGradientBoostingClassifier:
    positives = int(y.sum())
    negatives = len(y) - positives
    weights = np.ones(len(y), dtype=float)
    weights[y == 1] = max(1.0, min(8.0, negatives / max(1, positives)))
    model = HistGradientBoostingClassifier(
        max_iter=int(params.max_iter),
        max_leaf_nodes=int(params.max_leaf_nodes),
        learning_rate=float(params.learning_rate),
        l2_regularization=float(params.l2_regularization),
        random_state=int(params.random_state),
    )
    model.fit(X, y, sample_weight=weights)
    return model


def _run_portfolio(
    *,
    frames: dict[str, pd.DataFrame],
    features: dict[str, pd.DataFrame],
    tickers: list[str],
    params: MlRrParams,
    models: dict[str, Any],
    threshold: float,
    start_time: pd.Timestamp,
    end_time: pd.Timestamp,
    initial_capital: float,
    round_trip_commission: float,
    target_monthly_return_pct: float,
) -> SleevePortfolioResult:
    sleeve_capital = float(initial_capital) / float(len(tickers))
    sleeve_equity: dict[str, pd.Series] = {}
    trades_by_ticker: dict[str, tuple[SleeveTrade, ...]] = {}
    for ticker in tickers:
        p_long = models["long"].predict_proba(features[ticker])[:, 1]
        p_short = models["short"].predict_proba(features[ticker])[:, 1]
        equity, trades = _simulate_ticker(
            ticker=ticker,
            frame=frames[ticker],
            p_long=p_long,
            p_short=p_short,
            params=params,
            threshold=threshold,
            start_time=start_time,
            end_time=end_time,
            initial_capital=sleeve_capital,
            round_trip_commission=round_trip_commission,
        )
        sleeve_equity[ticker] = equity
        trades_by_ticker[ticker] = tuple(trades)
    common = _common_index({ticker: equity.to_frame("equity") for ticker, equity in sleeve_equity.items()})
    portfolio_equity = sum(equity.reindex(common).ffill() for equity in sleeve_equity.values())
    monthly = portfolio_equity.resample("ME").last().pct_change().dropna() * 100.0
    all_trades = [trade for trades in trades_by_ticker.values() for trade in trades]
    wins = [trade for trade in all_trades if trade.net_pnl > 0.0]
    losses = [trade for trade in all_trades if trade.net_pnl <= 0.0]
    gross_win = sum(trade.net_pnl for trade in wins)
    gross_loss = abs(sum(trade.net_pnl for trade in losses))
    profit_factor = (gross_win / gross_loss) if gross_loss > 0.0 else (999.0 if gross_win else 0.0)
    final_equity = float(portfolio_equity.iloc[-1]) if not portfolio_equity.empty else float(initial_capital)
    return SleevePortfolioResult(
        initial_capital=float(initial_capital),
        final_equity=final_equity,
        return_pct=((final_equity / float(initial_capital)) - 1.0) * 100.0,
        mean_monthly_return_pct=float(monthly.mean()) if not monthly.empty else 0.0,
        max_drawdown_pct=float(((portfolio_equity / portfolio_equity.cummax()) - 1.0).min()) * 100.0,
        trades=len(all_trades),
        winning_trades=len(wins),
        losing_trades=len(losses),
        win_rate_pct=(len(wins) / len(all_trades) * 100.0) if all_trades else 0.0,
        profit_factor=float(profit_factor),
        months=len(monthly),
        months_at_or_above_target=int((monthly >= float(target_monthly_return_pct)).sum()),
        monthly_returns_pct=monthly,
        equity_curve=portfolio_equity,
        sleeve_equity_curves=sleeve_equity,
        trades_by_ticker=trades_by_ticker,
    )


def _simulate_ticker(
    *,
    ticker: str,
    frame: pd.DataFrame,
    p_long: np.ndarray,
    p_short: np.ndarray,
    params: MlRrParams,
    threshold: float,
    start_time: pd.Timestamp,
    end_time: pd.Timestamp,
    initial_capital: float,
    round_trip_commission: float,
) -> tuple[pd.Series, list[SleeveTrade]]:
    cash = float(initial_capital)
    position: dict[str, Any] | None = None
    trades: list[SleeveTrade] = []
    equity_points: list[tuple[pd.Timestamp, float]] = []
    commission_per_side = max(0.0, float(round_trip_commission) / 2.0)
    opens = frame["Open"].to_numpy(dtype=float)
    highs = frame["High"].to_numpy(dtype=float)
    lows = frame["Low"].to_numpy(dtype=float)
    closes = frame["Close"].to_numpy(dtype=float)
    times = pd.DatetimeIndex(frame.index)
    horizon = max(1, int(params.horizon_bars))

    for idx, timestamp in enumerate(times):
        if timestamp < start_time or timestamp > end_time:
            continue
        if position is not None:
            exit_price, exit_reason = _bracket_exit(high=float(highs[idx]), low=float(lows[idx]), position=position)
            if exit_price is None and idx - int(position["entry_index"]) + 1 >= horizon:
                exit_price, exit_reason = float(closes[idx]), "max_hold"
            if exit_price is not None:
                cash, trade = _close_position(
                    ticker=ticker,
                    position=position,
                    cash=cash,
                    exit_time=timestamp,
                    exit_price=exit_price,
                    exit_reason=exit_reason,
                    commission_per_side=commission_per_side,
                )
                trades.append(trade)
                position = None

        if position is None and idx > 0 and idx < len(times) - 1:
            long_prob = float(p_long[idx - 1])
            short_prob = float(p_short[idx - 1])
            direction = 0
            if max(long_prob, short_prob) >= float(threshold):
                direction = 1 if long_prob >= short_prob else -1
            if direction:
                entry = float(opens[idx])
                shares = int((cash * float(params.leverage)) / max(1e-9, entry))
                if shares > 0:
                    risk = entry * max(0.0001, float(params.stop_pct))
                    if direction > 0:
                        cash -= shares * entry + commission_per_side
                        stop = entry - risk
                        target = entry + risk * float(params.risk_reward_ratio)
                    else:
                        cash += shares * entry - commission_per_side
                        stop = entry + risk
                        target = entry - risk * float(params.risk_reward_ratio)
                    position = {
                        "direction": direction,
                        "shares": shares,
                        "entry_time": timestamp,
                        "entry_price": entry,
                        "entry_index": idx,
                        "stop_price": stop,
                        "target_price": target,
                        "entry_commission": commission_per_side,
                    }
        equity_points.append((timestamp, _mark_to_market(cash=cash, position=position, close=float(closes[idx]))))
    if position is not None and equity_points:
        timestamp = equity_points[-1][0]
        cash, trade = _close_position(
            ticker=ticker,
            position=position,
            cash=cash,
            exit_time=timestamp,
            exit_price=float(closes[-1]),
            exit_reason="finalize",
            commission_per_side=commission_per_side,
        )
        trades.append(trade)
        equity_points[-1] = (timestamp, cash)
    equity = pd.Series(
        [point[1] for point in equity_points],
        index=pd.DatetimeIndex([point[0] for point in equity_points]),
        dtype=float,
    )
    return equity, trades


def _close_position(
    *,
    ticker: str,
    position: dict[str, Any],
    cash: float,
    exit_time: pd.Timestamp,
    exit_price: float,
    exit_reason: str,
    commission_per_side: float,
) -> tuple[float, SleeveTrade]:
    direction = int(position["direction"])
    shares = int(position["shares"])
    entry = float(position["entry_price"])
    if direction > 0:
        cash += shares * float(exit_price) - commission_per_side
        gross = (float(exit_price) - entry) * shares
    else:
        cash -= shares * float(exit_price) + commission_per_side
        gross = (entry - float(exit_price)) * shares
    net = gross - float(position["entry_commission"]) - commission_per_side
    return cash, SleeveTrade(
        ticker=ticker,
        direction="Long" if direction > 0 else "Short",
        entry_time=pd.Timestamp(position["entry_time"]).isoformat(),
        exit_time=pd.Timestamp(exit_time).isoformat(),
        entry_price=round(entry, 6),
        exit_price=round(float(exit_price), 6),
        shares=shares,
        net_pnl=round(float(net), 6),
        net_return_pct=round((net / max(1.0, shares * entry)) * 100.0, 6),
        exit_reason=exit_reason,
    )


def _bracket_exit(*, high: float, low: float, position: dict[str, Any]) -> tuple[float | None, str]:
    direction = int(position["direction"])
    stop = float(position["stop_price"])
    target = float(position["target_price"])
    if direction > 0:
        if low <= stop:
            return stop, "stop"
        if high >= target:
            return target, "take_profit"
        return None, ""
    if high >= stop:
        return stop, "stop"
    if low <= target:
        return target, "take_profit"
    return None, ""


def _mark_to_market(*, cash: float, position: dict[str, Any] | None, close: float) -> float:
    if position is None:
        return float(cash)
    shares = int(position["shares"])
    if int(position["direction"]) > 0:
        return float(cash) + shares * float(close)
    return float(cash) - shares * float(close)


def _common_index(data_by_ticker: dict[str, pd.DataFrame]) -> pd.DatetimeIndex:
    common: pd.DatetimeIndex | None = None
    for frame in data_by_ticker.values():
        if frame is None or frame.empty:
            continue
        index = pd.DatetimeIndex(frame.index).sort_values()
        common = index if common is None else common.intersection(index)
    return pd.DatetimeIndex([]) if common is None else common.sort_values()


def _score_summary(summary: dict[str, Any]) -> float:
    mean_monthly = float(summary["mean_monthly_return_pct"])
    min_iso = float(summary["isolated_min_mean_monthly_return_pct"])
    drawdown = abs(float(summary["max_drawdown_pct"]))
    trades = int(summary["trades"])
    return mean_monthly + min(mean_monthly, min_iso) + min_iso - drawdown / 4.0 + min(2.0, trades / 500.0)


def _nested_score(*, sub_summary: dict[str, Any], val_summary: dict[str, Any]) -> float:
    return _score_summary(val_summary) + min(4.0, float(sub_summary["mean_monthly_return_pct"])) / 2.0


if __name__ == "__main__":
    raise SystemExit(main())
