"""Generate the frozen reference (training) dataset.

Implements docs/data_simulation.md §3.1: 20,000 baseline rows under seed 42,
written to data_sim/artifacts/reference.csv. This file is the agent's fixed
reference window for the drift detectors (§9) and the training set for both models.
"""
from __future__ import annotations

from pathlib import Path

from common import N_REF, RANDOM_SEED, generate_baseline

ARTIFACTS = Path(__file__).resolve().parent / "artifacts"
REFERENCE_CSV = ARTIFACTS / "reference.csv"


def main() -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    df = generate_baseline(N_REF, RANDOM_SEED)
    df.to_csv(REFERENCE_CSV, index=False)
    pos_rate = df["label"].mean()
    print(f"Wrote {REFERENCE_CSV} ({len(df)} rows, positive rate {pos_rate:.3f})")


if __name__ == "__main__":
    main()
