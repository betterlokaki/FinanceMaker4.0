"""Artifact persistence helpers for forecasting model bundles and reports."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backtests.forecasting.config import ModelBundle

try:
    import joblib
except Exception as exc:  # pragma: no cover - runtime dependency gate
    raise RuntimeError("Missing dependency `joblib`.") from exc


def _mkdir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def save_model_bundle(bundle: ModelBundle, model_dir: str | Path) -> Path:
    """Persist base/adapters + metadata for load-only inference later."""
    path = Path(model_dir).resolve()
    _mkdir(path)

    joblib.dump(bundle.base_model, path / "base_model.joblib")
    for ticker, model in sorted(bundle.adapter_models.items()):
        joblib.dump(model, path / f"adapter_{ticker.upper()}.joblib")

    with (path / "metadata.json").open("w", encoding="utf-8") as f:
        json.dump(bundle.metadata, f, indent=2, sort_keys=True)

    with (path / "feature_columns.json").open("w", encoding="utf-8") as f:
        json.dump(bundle.feature_columns, f, indent=2)

    with (path / "target_columns.json").open("w", encoding="utf-8") as f:
        json.dump(bundle.target_columns, f, indent=2)

    with (path / "calibration.json").open("w", encoding="utf-8") as f:
        json.dump(bundle.calibration, f, indent=2, sort_keys=True)

    return path


def load_model_bundle(model_dir: str | Path) -> ModelBundle:
    """Load a persisted model bundle from disk."""
    path = Path(model_dir).resolve()
    if not path.exists():
        raise FileNotFoundError(f"Model directory not found: {path}")

    base_model = joblib.load(path / "base_model.joblib")

    adapters = {}
    for file in sorted(path.glob("adapter_*.joblib")):
        ticker = file.stem.replace("adapter_", "").upper()
        adapters[ticker] = joblib.load(file)

    with (path / "metadata.json").open("r", encoding="utf-8") as f:
        metadata: dict[str, Any] = json.load(f)

    with (path / "feature_columns.json").open("r", encoding="utf-8") as f:
        feature_columns = json.load(f)

    target_path = path / "target_columns.json"
    if target_path.exists():
        with target_path.open("r", encoding="utf-8") as f:
            target_columns = json.load(f)
    else:
        # Backward-compatible fallback if target columns are absent.
        horizon = int(metadata.get("horizon", 3))
        target_columns = []
        for step in range(1, horizon + 1):
            target_columns.extend([f"target_o{step}", f"target_h{step}", f"target_l{step}", f"target_c{step}"])

    with (path / "calibration.json").open("r", encoding="utf-8") as f:
        calibration: dict[str, Any] = json.load(f)

    return ModelBundle(
        run_id=str(metadata.get("run_id", path.name)),
        feature_columns=list(feature_columns),
        target_columns=list(target_columns),
        base_model=base_model,
        adapter_models=adapters,
        calibration=calibration,
        metadata=metadata,
    )


def save_json(data: dict[str, Any], path: str | Path) -> Path:
    out = Path(path).resolve()
    _mkdir(out.parent)
    with out.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
    return out


def save_csv(df, path: str | Path) -> Path:
    out = Path(path).resolve()
    _mkdir(out.parent)
    df.to_csv(out, index=False)
    return out
