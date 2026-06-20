"""Precompute the frozen reference-window summary for the drift detector.

Reads data_sim/artifacts/reference.csv and writes a compact, COMMITTED JSON
(control-plane/agent_core/detection/reference_window.json) so drift_detector.py
works on a fresh checkout without regenerating the 20k-row reference set.

Per detection_methods.md §4.1 it stores, per feature:
  - continuous: 10 quantile bin edges + reference bin proportions (PSI) and a
    1000-row value sample (KS two-sample);
  - categorical: category frequencies (PSI + chi-square).
Plus the model_a baseline accuracy/F1 for concept-drift comparison (§5.2).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import numpy as np
import pandas as pd

from common import CATEGORICAL_COLS, NUMERIC_COLS

REPO_ROOT = Path(__file__).resolve().parents[1]
REFERENCE_CSV = Path(__file__).resolve().parent / "artifacts" / "reference.csv"
# Default writes the committed artifact the detector reads on a fresh checkout;
# overridable so this build step is not hardcoded to the agent_core tree.
OUT = Path(os.environ.get(
    "REFERENCE_WINDOW_OUT",
    REPO_ROOT / "control-plane" / "agent_core" / "detection" / "reference_window.json"))

PSI_BINS = 10
KS_SAMPLE = 1000


def main() -> None:
    if not REFERENCE_CSV.exists():
        raise SystemExit(f"{REFERENCE_CSV} not found — run generate_reference.py first.")
    df = pd.read_csv(REFERENCE_CSV)
    rng = np.random.default_rng(42)

    summary: dict = {"continuous": {}, "categorical": {}, "n_ref": len(df)}

    for col in NUMERIC_COLS:
        values = df[col].to_numpy(dtype=float)
        # Quantile bin edges from the reference; dedup; outer edges -> +-inf.
        quantiles = np.linspace(0, 1, PSI_BINS + 1)
        edges = np.unique(np.quantile(values, quantiles))
        counts, _ = np.histogram(values, bins=edges)
        props = (counts / counts.sum()).tolist()
        sample = rng.choice(values, size=min(KS_SAMPLE, len(values)), replace=False)
        summary["continuous"][col] = {
            "edges": edges.tolist(),          # inner edges; detector extends to +-inf
            "ref_props": props,
            "sample": sorted(float(v) for v in sample),
        }

    for col in CATEGORICAL_COLS:
        freq = df[col].value_counts(normalize=True).to_dict()
        summary["categorical"][col] = {k: float(v) for k, v in freq.items()}

    # Baseline performance for concept drift (held-out accuracy/F1 of model_a, §5.2).
    summary["baseline"] = {"accuracy": 0.846, "f1": 0.731}

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(summary))
    print(f"Wrote {OUT} ({OUT.stat().st_size // 1024} KB)")


if __name__ == "__main__":
    main()
