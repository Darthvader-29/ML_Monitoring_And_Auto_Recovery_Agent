"""OBSERVE — load input feature rows for the prediction probe.

For the MVP this reads the committed sample_input.csv (data_simulation.md §8) that
ships beside each model service; later phases swap in the scripted live stream
(artifacts/stream/tick_*.jsonl). Returns plain feature dicts ready to POST to
/predict (request_id stripped — it is request envelope, not a feature).
"""
from __future__ import annotations

import csv
import os
from pathlib import Path

# 8-feature Transaction Risk Scoring schema — declared once in feature_schema.
from feature_schema import CATEGORICAL_COLS, FEATURE_COLS, NUMERIC_COLS

# Repo root: AGENT_DATA_ROOT override (used when the agent runs in a container with
# the repo data mounted, deployment_and_devops.md §3), else inferred from this file.
_REPO_ROOT = Path(os.environ.get("AGENT_DATA_ROOT") or Path(__file__).resolve().parents[3])
_DEFAULT_SAMPLE = (_REPO_ROOT / "model-services" / "model_a" / "sample_input.csv")
_DRIFT_SAMPLE = (_REPO_ROOT / "model-services" / "model_a" / "sample_input_drift.csv")
# Larger varied batches (>=200 rows) for the drift detector (detection_methods.md §4.1).
_FIXTURES = _REPO_ROOT / "data_sim" / "fixtures"
_BASELINE_BATCH = _FIXTURES / "baseline_batch.csv"
_DRIFT_BATCH = _FIXTURES / "drift_batch.csv"


def _coerce(row: dict) -> dict:
    features = {}
    for col in FEATURE_COLS:
        value = row.get(col)
        if col in NUMERIC_COLS:
            try:
                value = float(value)
            except (TypeError, ValueError):
                value = None
        features[col] = value
    return features


def load_rows(path: Path | str | None = None, drift: bool = False) -> list[dict]:
    """Load the small hand-checkable sample (25 rows) for the prediction probe.
    `drift=True` loads the drifted variant."""
    if path is None:
        path = _DRIFT_SAMPLE if drift else _DEFAULT_SAMPLE
    path = Path(path)
    if not path.exists():
        return []
    with path.open(newline="") as fh:
        return [_coerce(row) for row in csv.DictReader(fh)]


def load_batch(drift: bool = False) -> list[dict]:
    """Load a large varied batch (>=200 rows) for the drift detector. Falls back to
    the small sample if the fixtures have not been generated (`make data`)."""
    path = _DRIFT_BATCH if drift else _BASELINE_BATCH
    if not path.exists():
        return load_rows(drift=drift)
    with path.open(newline="") as fh:
        return [_coerce(row) for row in csv.DictReader(fh)]
