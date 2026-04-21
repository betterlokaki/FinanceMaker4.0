"""Model training/inference utilities for base + ticker-adapter forecasting."""
from __future__ import annotations

from dataclasses import asdict
from typing import Any, Iterable

import numpy as np
import pandas as pd

from backtests.forecasting.config import ForecastConfig, ModelBundle

try:
    from sklearn.model_selection import TimeSeriesSplit
    from sklearn.multioutput import MultiOutputRegressor
except Exception as exc:  # pragma: no cover - runtime dependency gate
    raise RuntimeError("Missing dependency `scikit-learn`.") from exc

try:
    from xgboost import XGBRegressor
except Exception as exc:  # pragma: no cover - runtime dependency gate
    raise RuntimeError("Missing dependency `xgboost`.") from exc


def build_adapter_sample_weights(
    tickers: Iterable[str],
    *,
    target_ticker: str,
    target_weight: float,
    other_weight: float,
) -> np.ndarray:
    """Build per-row sample weights for ticker-specialized adapter training."""
    target_ticker = str(target_ticker).upper()
    out = []
    for t in tickers:
        if str(t).upper() == target_ticker:
            out.append(float(target_weight))
        else:
            out.append(float(other_weight))
    return np.asarray(out, dtype=float)


def _make_base_estimator(cfg: ForecastConfig) -> XGBRegressor:
    return XGBRegressor(
        objective="reg:squarederror",
        n_estimators=int(cfg.xgb_n_estimators),
        max_depth=int(cfg.xgb_max_depth),
        learning_rate=float(cfg.xgb_learning_rate),
        subsample=float(cfg.xgb_subsample),
        colsample_bytree=float(cfg.xgb_colsample_bytree),
        reg_alpha=float(cfg.xgb_reg_alpha),
        reg_lambda=float(cfg.xgb_reg_lambda),
        random_state=int(cfg.xgb_random_state),
        n_jobs=-1,
    )


def _make_multi_output_model(cfg: ForecastConfig) -> MultiOutputRegressor:
    return MultiOutputRegressor(_make_base_estimator(cfg))


def _timestamps_to_cv_masks(
    timestamps: pd.Series,
    n_splits: int,
) -> list[tuple[np.ndarray, np.ndarray]]:
    """Create rolling timestamp masks with split points on unique times."""
    unique_ts = pd.Index(sorted(pd.Series(timestamps).dropna().unique()))
    if len(unique_ts) < (n_splits + 2):
        return []

    splitter = TimeSeriesSplit(n_splits=n_splits)
    masks: list[tuple[np.ndarray, np.ndarray]] = []
    for train_idx, valid_idx in splitter.split(unique_ts):
        train_times = set(unique_ts[train_idx])
        valid_times = set(unique_ts[valid_idx])
        ts_arr = pd.Series(timestamps).to_numpy()
        train_mask = np.array([ts in train_times for ts in ts_arr], dtype=bool)
        valid_mask = np.array([ts in valid_times for ts in ts_arr], dtype=bool)
        if train_mask.any() and valid_mask.any():
            masks.append((train_mask, valid_mask))
    return masks


def _build_calibration(
    *,
    X: pd.DataFrame,
    y: pd.DataFrame,
    tickers: pd.Series,
    timestamps: pd.Series,
    target_columns: list[str],
    cfg: ForecastConfig,
) -> dict[str, Any]:
    """Build residual sigma from rolling OOF predictions for EV probability logic."""
    oof_residual_rows: list[pd.DataFrame] = []

    masks = _timestamps_to_cv_masks(timestamps=timestamps, n_splits=max(2, int(cfg.cv_splits)))
    if not masks:
        # Fallback: use in-sample residuals if dataset is too short for OOF splits.
        base = _make_multi_output_model(cfg)
        base.fit(X, y)
        pred = base.predict(X)
        residual = y.to_numpy(dtype=float) - np.asarray(pred, dtype=float)
        out = pd.DataFrame(residual, columns=target_columns)
        out["ticker"] = tickers.to_numpy()
        return _residual_sigma_dict(out, target_columns=target_columns)

    for train_mask, valid_mask in masks:
        X_train = X.loc[train_mask]
        y_train = y.loc[train_mask]
        X_valid = X.loc[valid_mask]
        y_valid = y.loc[valid_mask]
        valid_tickers = tickers.loc[valid_mask]

        fold_model = _make_multi_output_model(cfg)
        fold_model.fit(X_train, y_train)
        pred_valid = fold_model.predict(X_valid)

        residual = y_valid.to_numpy(dtype=float) - np.asarray(pred_valid, dtype=float)
        fold_residual = pd.DataFrame(residual, columns=target_columns, index=y_valid.index)
        fold_residual["ticker"] = valid_tickers.to_numpy()
        oof_residual_rows.append(fold_residual)

    if not oof_residual_rows:
        return {
            "global_sigma": {col: 0.01 for col in target_columns},
            "ticker_sigma": {},
            "source": "empty_fallback",
        }

    all_residuals = pd.concat(oof_residual_rows, axis=0).sort_index()
    return _residual_sigma_dict(all_residuals, target_columns=target_columns)


def _residual_sigma_dict(residual_df: pd.DataFrame, *, target_columns: list[str]) -> dict[str, Any]:
    global_sigma = {}
    for col in target_columns:
        sigma = float(pd.to_numeric(residual_df[col], errors="coerce").std(ddof=0))
        if not np.isfinite(sigma) or sigma <= 1e-8:
            sigma = 0.01
        global_sigma[col] = sigma

    ticker_sigma: dict[str, dict[str, float]] = {}
    if "ticker" in residual_df.columns:
        for ticker, group in residual_df.groupby("ticker"):
            values = {}
            for col in target_columns:
                sigma = float(pd.to_numeric(group[col], errors="coerce").std(ddof=0))
                if not np.isfinite(sigma) or sigma <= 1e-8:
                    sigma = global_sigma[col]
                values[col] = sigma
            ticker_sigma[str(ticker).upper()] = values

    return {
        "global_sigma": global_sigma,
        "ticker_sigma": ticker_sigma,
        "source": "rolling_oof",
    }


def train_forecast_models(
    *,
    X: pd.DataFrame,
    y: pd.DataFrame,
    tickers: pd.Series,
    timestamps: pd.Series,
    feature_columns: list[str],
    target_columns: list[str],
    run_id: str,
    cfg: ForecastConfig,
) -> ModelBundle:
    """Train base pooled forecaster and per-ticker residual adapters."""
    if X.empty or y.empty:
        raise ValueError("Training matrices must be non-empty.")

    X_train = X[feature_columns].astype(float)
    y_train = y[target_columns].astype(float)

    calibration = _build_calibration(
        X=X_train,
        y=y_train,
        tickers=tickers,
        timestamps=timestamps,
        target_columns=target_columns,
        cfg=cfg,
    )

    base_model = _make_multi_output_model(cfg)
    base_model.fit(X_train, y_train)
    base_pred = np.asarray(base_model.predict(X_train), dtype=float)

    residual = y_train.to_numpy(dtype=float) - base_pred
    residual_df = pd.DataFrame(residual, columns=target_columns, index=y_train.index)

    adapters: dict[str, Any] = {}
    ticker_series = tickers.astype(str).str.upper()
    for ticker in sorted(set(ticker_series.to_list())):
        sample_weight = build_adapter_sample_weights(
            ticker_series.to_list(),
            target_ticker=ticker,
            target_weight=float(cfg.adapter_weight_target),
            other_weight=float(cfg.adapter_weight_other),
        )
        adapter = _make_multi_output_model(cfg)
        adapter.fit(X_train, residual_df[target_columns], sample_weight=sample_weight)
        adapters[ticker] = adapter

    metadata = {
        "run_id": run_id,
        "horizon": int(cfg.horizon),
        "tickers": sorted(set(ticker_series.to_list())),
        "rows": int(len(X_train)),
        "model_family": "xgboost_multioutput_with_residual_adapters",
        "config": asdict(cfg),
    }

    return ModelBundle(
        run_id=run_id,
        feature_columns=list(feature_columns),
        target_columns=list(target_columns),
        base_model=base_model,
        adapter_models=adapters,
        calibration=calibration,
        metadata=metadata,
    )


def predict_with_bundle(
    bundle: ModelBundle,
    *,
    X: pd.DataFrame,
    ticker: str,
) -> np.ndarray:
    """Predict next-N OHLC returns using base + optional ticker adapter."""
    features = X[bundle.feature_columns].astype(float)
    base_pred = np.asarray(bundle.base_model.predict(features), dtype=float)
    adapter = bundle.adapter_models.get(str(ticker).upper())
    if adapter is None:
        return base_pred
    residual_pred = np.asarray(adapter.predict(features), dtype=float)
    return base_pred + residual_pred
