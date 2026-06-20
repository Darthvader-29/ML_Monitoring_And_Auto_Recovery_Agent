"""DETECT — data drift (PSI/KS/chi-square) and concept drift (performance).

Implements detection_methods.md §4-5 against the frozen reference window
(reference_window.json, built by data_sim/build_reference_summary.py):
  - continuous features: PSI (10 reference-quantile bins) + KS two-sample;
  - categorical features: PSI + chi-square;
  - an aggregate result over the share of drifted features (§4.6);
  - concept drift from the accuracy/F1 drop vs the deployment baseline (§5.2),
    available for when delayed labels are wired in.

Results map onto schemas.DetectionResult: `metric` carries the signal_type key
("data_drift_psi" / "data_drift_aggregate" / "concept_drift_perf"), `score` the
statistic, `message` the human-readable evidence. The current window slides across
ticks; testing holds until n_cur_min rows accumulate (§9 small-sample guard).
"""
from __future__ import annotations

import json
import math
from collections import deque
from pathlib import Path

from scipy.stats import chi2_contingency, ks_2samp

from feature_schema import CATEGORICAL_COLS, NUMERIC_COLS
from schemas import DetectionResult

_DET = "drift_detector"
_REF_PATH = Path(__file__).resolve().parent / "reference_window.json"

PSI_BINS = 10
PSI_WATCH = 0.10
PSI_SIGNIFICANT = 0.25
KS_ALPHA = 0.05
SHARE_THRESHOLD = 0.30
N_CUR = 500
N_CUR_MIN = 200
EPS = 1e-4
# Concept-drift triggers (§5.2)
DROP_ABS = 0.05
DROP_REL = 0.10


class DriftDetector:
    def __init__(self, n_cur: int = N_CUR, n_cur_min: int = N_CUR_MIN) -> None:
        self._n_cur_min = n_cur_min
        self._window: deque = deque(maxlen=n_cur)
        self._ref = self._load_reference()

    @staticmethod
    def _load_reference() -> dict | None:
        if not _REF_PATH.exists():
            return None
        return json.loads(_REF_PATH.read_text())

    # ---- data drift -----------------------------------------------------

    def evaluate_data_drift(self, rows: list[dict]) -> list[DetectionResult]:
        self._window.extend(r for r in rows if r)
        if self._ref is None or len(self._window) < self._n_cur_min:
            return []

        cur = list(self._window)
        results: list[DetectionResult] = []
        drifted: list[str] = []

        for col in NUMERIC_COLS:
            ref = self._ref["continuous"].get(col)
            if not ref:
                continue
            values = [float(r[col]) for r in cur if r.get(col) is not None]
            if len(values) < self._n_cur_min:
                continue
            psi = self._psi_continuous(ref["edges"], ref["ref_props"], values)
            try:
                _, ks_p = ks_2samp(ref["sample"], values)
            except ValueError:
                ks_p = 1.0
            # Effect-size primary (§9): KS must be corroborated by at least a watch-
            # level PSI, so a lone significant p-value does not flag drift.
            is_drift = psi > PSI_SIGNIFICANT or (ks_p < KS_ALPHA and psi > PSI_WATCH)
            if is_drift:
                drifted.append(col)
            results.append(self._feature_result(col, psi, ks_p, is_drift))

        for col in CATEGORICAL_COLS:
            ref_freq = self._ref["categorical"].get(col)
            if not ref_freq:
                continue
            values = [str(r[col]) for r in cur if r.get(col) is not None]
            psi = self._psi_categorical(ref_freq, values)
            chi_p = self._chi2(ref_freq, values)
            is_drift = psi > PSI_SIGNIFICANT or (chi_p < KS_ALPHA and psi > PSI_WATCH)
            if is_drift:
                drifted.append(col)
            results.append(self._feature_result(col, psi, chi_p, is_drift))

        n_features = len(NUMERIC_COLS) + len(CATEGORICAL_COLS)
        share = len(drifted) / n_features
        results.append(DetectionResult(
            detector=_DET, anomaly_detected=share >= SHARE_THRESHOLD,
            metric="data_drift_aggregate", observed=round(share, 4),
            threshold=SHARE_THRESHOLD, score=round(share, 4),
            message=(f"{len(drifted)}/{n_features} features drifted: "
                     f"{', '.join(drifted) or 'none'}"),
        ))
        # Emit drifted per-feature results + the aggregate (suppress clean features).
        return [r for r in results if r.anomaly_detected] + results[-1:]

    def _feature_result(self, col: str, psi: float, p: float,
                        drift: bool) -> DetectionResult:
        return DetectionResult(
            detector=_DET, anomaly_detected=drift, metric="data_drift_psi",
            observed=round(psi, 4), threshold=PSI_SIGNIFICANT, score=round(psi, 4),
            message=f"{col}: PSI={psi:.3f} p={p:.2e}",
        )

    @staticmethod
    def _psi_continuous(edges: list[float], ref_props: list[float],
                        values: list[float]) -> float:
        bins = [-math.inf] + list(edges[1:-1]) + [math.inf]
        counts = [0] * (len(bins) - 1)
        for v in values:
            for i in range(len(bins) - 1):
                if bins[i] <= v < bins[i + 1] or (i == len(bins) - 2 and v == bins[-1]):
                    counts[i] += 1
                    break
        total = sum(counts) or 1
        cur_props = [c / total for c in counts]
        return _psi(ref_props, cur_props)

    @staticmethod
    def _psi_categorical(ref_freq: dict, values: list[float]) -> float:
        cats = set(ref_freq) | set(values)
        total = len(values) or 1
        cur_freq = {c: values.count(c) / total for c in cats}
        ref = [ref_freq.get(c, 0.0) for c in cats]
        cur = [cur_freq.get(c, 0.0) for c in cats]
        return _psi(ref, cur)

    def _chi2(self, ref_freq: dict, values: list[float]) -> float:
        n_ref = self._ref.get("n_ref", 20000)
        cats = sorted(set(ref_freq) | set(values))
        ref_counts = [max(round(ref_freq.get(c, 0.0) * n_ref), 0) for c in cats]
        cur_counts = [values.count(c) for c in cats]
        table = [[r, c] for r, c in zip(ref_counts, cur_counts) if (r + c) > 0]
        if len(table) < 2:
            return 1.0
        try:
            _, p, _, _ = chi2_contingency(list(zip(*table)))
            return float(p)
        except ValueError:
            return 1.0

    # ---- concept drift (needs delayed labels; ready for label wiring) ----

    def evaluate_concept_drift(self, current_accuracy: float,
                               current_f1: float | None = None) -> DetectionResult:
        base = (self._ref or {}).get("baseline", {}).get("accuracy", 0.846)
        abs_drop = base - current_accuracy
        rel_drop = abs_drop / base if base else 0.0
        drift = abs_drop >= DROP_ABS or rel_drop >= DROP_REL
        return DetectionResult(
            detector=_DET, anomaly_detected=drift, metric="concept_drift_perf",
            observed=round(current_accuracy, 4), threshold=DROP_ABS,
            score=round(abs_drop, 4),
            message=f"accuracy {current_accuracy:.3f} vs baseline {base:.3f} "
                    f"(abs_drop={abs_drop:.3f})",
        )


def _psi(ref: list[float], cur: list[float]) -> float:
    total = 0.0
    for r, c in zip(ref, cur):
        r = max(r, EPS)
        c = max(c, EPS)
        total += (c - r) * math.log(c / r)
    return total


def detect(*_args, **_kwargs) -> list[DetectionResult]:
    return []
