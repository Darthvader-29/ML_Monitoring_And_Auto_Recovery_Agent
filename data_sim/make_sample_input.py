"""Refresh the committed sample_input.csv files from the baseline distribution.

Implements docs/data_simulation.md §8: a small, hand-checkable sample (no label
column) drawn from the baseline distribution, defining the exact column order the
/predict endpoint accepts. Writes to BOTH model service directories.

Also emits sample_input_drift.csv — a drifted variant (sudden device_risk shift +
amount inflation, per the §5.2 sudden-drift recipe) — used by Phase 2+ to exercise
the recovery loop and by drift detector tests.
"""
from __future__ import annotations

import random
import uuid
from pathlib import Path

import numpy as np

from common import FEATURE_COLS, generate_baseline

REPO_ROOT = Path(__file__).resolve().parents[1]
MODEL_DIRS = [
    REPO_ROOT / "model-services" / "model_a",
    REPO_ROOT / "model-services" / "model_b",
]
N_SAMPLE = 25


def _with_request_ids(df, seed: int):
    r = random.Random(seed)  # stdlib RNG handles a full 128-bit draw deterministically
    ids = [str(uuid.UUID(int=r.getrandbits(128))) for _ in range(len(df))]
    out = df[FEATURE_COLS].copy()
    out.insert(0, "request_id", ids)
    return out


def main() -> None:
    # Baseline sample (drawn from the no-drift generator, distinct seed from training).
    baseline = generate_baseline(N_SAMPLE, seed=101)
    baseline = _with_request_ids(baseline, seed=101)

    # Drifted variant: sudden device_risk shift (0.30 -> ~0.65) + +60% amount
    # inflation (data_simulation.md §5.1/§5.2). Same rows, perturbed inputs.
    drift = generate_baseline(N_SAMPLE, seed=202)
    rng = np.random.default_rng(202)
    drift["device_risk"] = np.clip(rng.normal(0.65, 0.15, len(drift)), 0, 1)
    drift["amount"] = np.clip(drift["amount"] * 1.60, 0, 100_000)
    drift = _with_request_ids(drift, seed=202)

    for d in MODEL_DIRS:
        baseline.to_csv(d / "sample_input.csv", index=False)
        drift.to_csv(d / "sample_input_drift.csv", index=False)
        print(f"Wrote {d / 'sample_input.csv'} and sample_input_drift.csv")


if __name__ == "__main__":
    main()
