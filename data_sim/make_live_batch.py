"""Generate committed live-batch fixtures for the drift detector.

Data drift needs a varied current window of >=200 rows (detection_methods.md §4.1);
the small hand-checkable sample_input.csv (25 rows) is for /predict demos only.
This writes two 600-row batches drawn from the SAME generator as the reference
(so the baseline batch matches the reference distribution and must NOT flag drift):

  data_sim/fixtures/baseline_batch.csv  — no drift (quiet)
  data_sim/fixtures/drift_batch.csv     — 4 features shifted (HIGH data drift)

Feature columns only (+ request_id); labels are withheld (inference payload, §8).
"""
from __future__ import annotations

import random
import uuid
from pathlib import Path

import numpy as np

from common import FEATURE_COLS, generate_baseline

FIXTURES = Path(__file__).resolve().parent / "fixtures"
N_BATCH = 600


def _with_ids(df, seed: int):
    r = random.Random(seed)
    out = df[FEATURE_COLS].copy()
    out.insert(0, "request_id", [str(uuid.UUID(int=r.getrandbits(128))) for _ in range(len(df))])
    return out


def main() -> None:
    FIXTURES.mkdir(parents=True, exist_ok=True)

    baseline = _with_ids(generate_baseline(N_BATCH, seed=303), seed=303)
    baseline.to_csv(FIXTURES / "baseline_batch.csv", index=False)

    drift = generate_baseline(N_BATCH, seed=404)
    rng = np.random.default_rng(404)
    drift["device_risk"] = np.clip(rng.normal(0.65, 0.15, len(drift)), 0, 1)
    drift["amount"] = np.clip(drift["amount"] * 1.60, 0, 100_000)
    drift["avg_txn_amount"] = np.clip(drift["avg_txn_amount"] * 1.8 + 200, 0, 50_000)
    drift["num_txn_24h"] = np.clip(drift["num_txn_24h"] + 9, 0, 200)
    drift = _with_ids(drift, seed=404)
    drift.to_csv(FIXTURES / "drift_batch.csv", index=False)

    print(f"Wrote {FIXTURES/'baseline_batch.csv'} and drift_batch.csv ({N_BATCH} rows each)")


if __name__ == "__main__":
    main()
