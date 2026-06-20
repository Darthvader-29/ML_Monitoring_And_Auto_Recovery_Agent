"""DETECT — sudden anomalies vs a metric's own recent history.

Implements detection_methods.md §3: robust z-score (median/MAD, the preferred
default), Tukey IQR fences, and an EWMA control chart for sustained level shifts.
Stateful across ticks (per-metric rolling buffers). Answers "is this metric
behaving abnormally vs its own history?" — distinct from the static-bound
threshold detector. Defaults are the §7 values.

Results are mapped onto the canonical schemas.DetectionResult: `metric` carries
the signal_type vocabulary key, `score` the test statistic, `message` the method
and (for EWMA / repeated breaches) the spike-vs-sustained pattern the
severity_classifier reads.
"""
from __future__ import annotations

import math
from collections import deque

import config
from schemas import DetectionResult

_DET = "anomaly_detector"

# Metrics tracked, and whether higher is worse (confidence is lower-worse).
_METRICS = {
    "error_rate": True,
    "avg_latency_ms": True,
    "p95_latency_ms": True,
    "inference_failure_rate": True,
    "avg_confidence": False,
}

W = 50          # rolling history length (§3.2)
W_MIN = 12      # min samples before scoring
K_R = 3.5       # robust z threshold (§3.4)
IQR_C = 1.5     # Tukey fence multiplier (§3.5)
EWMA_L = 3.0    # EWMA control-limit width (§3.6)
N_SUSTAIN = 5   # consecutive breaches => "sustained" (§3.8)


def _median(xs: list[float]) -> float:
    s = sorted(xs)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def _percentile(xs: list[float], q: float) -> float:
    s = sorted(xs)
    if not s:
        return 0.0
    idx = q * (len(s) - 1)
    lo = int(math.floor(idx))
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (idx - lo)


class AnomalyDetector:
    def __init__(self) -> None:
        self._hist: dict[str, deque] = {m: deque(maxlen=W) for m in _METRICS}
        self._ewma: dict[str, float] = {}
        self._consec: dict[str, int] = {m: 0 for m in _METRICS}
        self._alpha = config.settings.ewma_alpha

    def evaluate(self, metrics: dict) -> list[DetectionResult]:
        results: list[DetectionResult] = []
        for metric, higher_worse in _METRICS.items():
            value = metrics.get(metric)
            if value is None:
                continue
            hist = self._hist[metric]
            past = list(hist)
            hist.append(float(value))

            if len(past) < W_MIN:
                continue  # warm-up

            breached, score, method = self._score(metric, float(value), past, higher_worse)
            self._consec[metric] = self._consec[metric] + 1 if breached else 0
            if not breached:
                continue
            pattern = "sustained" if self._consec[metric] >= N_SUSTAIN else "spike"
            results.append(DetectionResult(
                detector=_DET, anomaly_detected=True,
                # Carry the actual offending metric + the method/pattern that fired,
                # instead of a hardcoded "robust_zscore_spike" that lied when the
                # breach came from IQR/EWMA or was a *sustained* shift, and erased
                # which metric (error_rate vs latency vs confidence) tripped.
                metric=f"{metric}_{pattern}",
                observed=round(float(value), 6), threshold=K_R, score=round(score, 4),
                message=f"{method} on {metric}={value:.4g} pattern={pattern} "
                        f"(consec={self._consec[metric]})",
            ))
        return results

    def _score(self, metric: str, x: float, past: list[float],
               higher_worse: bool) -> tuple[bool, float, str]:
        """Robust z-score primary; IQR + EWMA as confirmation. Returns
        (breached, score, method)."""
        med = _median(past)
        mad = _median([abs(p - med) for p in past])
        if mad > 0:
            z_r = 0.6745 * (x - med) / mad
        else:
            z_r = 0.0
        directional = z_r if higher_worse else -z_r  # positive => abnormal in bad direction
        z_break = directional >= K_R

        # Tukey IQR fence (distribution-free), in the bad direction only.
        q1, q3 = _percentile(past, 0.25), _percentile(past, 0.75)
        iqr = q3 - q1
        iqr_break = False
        if iqr > 0:
            if higher_worse and x > q3 + IQR_C * iqr:
                iqr_break = True
            if (not higher_worse) and x < q1 - IQR_C * iqr:
                iqr_break = True

        # EWMA control chart for sustained shifts.
        prev = self._ewma.get(metric, med)
        s_t = self._alpha * x + (1 - self._alpha) * prev
        self._ewma[metric] = s_t
        sigma = (sum((p - med) ** 2 for p in past) / max(len(past) - 1, 1)) ** 0.5
        sigma_s = sigma * (self._alpha / (2 - self._alpha)) ** 0.5
        ewma_break = False
        if sigma_s > 0:
            if higher_worse and s_t > med + EWMA_L * sigma_s:
                ewma_break = True
            if (not higher_worse) and s_t < med - EWMA_L * sigma_s:
                ewma_break = True

        method = "robust_zscore" if z_break else ("iqr" if iqr_break else "ewma")
        return (z_break or iqr_break or ewma_break, abs(directional), method)


# Module-level convenience for the no-arg call site used in Phase 2; the agent now
# owns a stateful instance and calls .evaluate() (see agent.py).
def detect(*_args, **_kwargs) -> list[DetectionResult]:
    return []
