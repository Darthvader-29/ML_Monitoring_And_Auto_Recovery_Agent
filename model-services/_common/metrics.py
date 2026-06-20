"""In-process rolling metrics for the model service.

Implements docs/monitoring_and_metrics.md §3.1: lightweight, memory-only counters
updated on every /predict call and exposed at GET /metrics. No external store —
just a bounded ring buffer of the most recent requests (default 200, §4.1) over
which latency percentiles, error rate and mean confidence are computed.

This module is intentionally self-contained (no cross-service imports): each model
service owns its own copy ("HTTP everywhere", architecture.md §1.3).
"""
from __future__ import annotations

import math
import threading
import time
from collections import deque
from dataclasses import dataclass

WINDOW_SIZE = 200  # last N requests (monitoring_and_metrics.md §4.1)


@dataclass
class _Sample:
    latency_ms: float
    confidence: float
    is_error: bool


def _nearest_rank(sorted_vals: list[float], q: float) -> float:
    """Nearest-rank percentile: value at index ceil(q*n)-1 (monitoring §2.1)."""
    if not sorted_vals:
        return 0.0
    idx = max(0, math.ceil(q * len(sorted_vals)) - 1)
    return sorted_vals[idx]


class MetricsTracker:
    """Thread-safe rolling counters over the last WINDOW_SIZE requests."""

    def __init__(self, window_size: int = WINDOW_SIZE) -> None:
        self._window = deque(maxlen=window_size)
        self._lock = threading.Lock()
        self._start_time = time.time()
        self._total_requests = 0
        self._total_errors = 0

    def record(self, latency_ms: float, confidence: float, is_error: bool) -> None:
        with self._lock:
            self._total_requests += 1
            if is_error:
                self._total_errors += 1
            self._window.append(_Sample(latency_ms, confidence, is_error))

    @property
    def uptime_seconds(self) -> float:
        return time.time() - self._start_time

    def snapshot(self, window_label: str = "5m") -> dict:
        """Return the GET /metrics body (api_contracts.md §A.3), computed over the
        in-memory request window. `window_label` is echoed for contract compliance;
        the actual window is the ring buffer."""
        with self._lock:
            samples = list(self._window)
            total_requests = self._total_requests
            total_errors = self._total_errors

        request_count = len(samples)
        error_count = sum(1 for s in samples if s.is_error)
        error_rate = error_count / request_count if request_count else 0.0

        ok = [s for s in samples if not s.is_error]
        # Latency stats normally use successful samples. But when EVERY request in
        # the window errored (or there are no successes), fall back to the error
        # samples' latencies so a slow/failing service is not reported as 0.0
        # (monitoring_and_metrics.md §2.1: latency must reflect actual failures).
        latency_source = ok if ok else samples
        latencies = sorted(
            s.latency_ms for s in latency_source if not math.isnan(s.latency_ms)
        )
        confidences = [s.confidence for s in ok if not math.isnan(s.confidence)]

        avg_latency = sum(latencies) / len(latencies) if latencies else 0.0
        avg_conf = sum(confidences) / len(confidences) if confidences else 0.0

        return {
            "window": window_label,
            "request_count": request_count,
            "error_count": error_count,
            "error_rate": round(error_rate, 6),
            "avg_latency_ms": round(avg_latency, 3),
            "p50_latency_ms": round(_nearest_rank(latencies, 0.50), 3),
            "p95_latency_ms": round(_nearest_rank(latencies, 0.95), 3),
            "p99_latency_ms": round(_nearest_rank(latencies, 0.99), 3),
            "avg_confidence": round(avg_conf, 6),
            "uptime_seconds": round(self.uptime_seconds, 3),
            # Cumulative since start (the window above is bounded at WINDOW_SIZE);
            # previously tracked but never surfaced.
            "lifetime_requests": total_requests,
            "lifetime_errors": total_errors,
        }
