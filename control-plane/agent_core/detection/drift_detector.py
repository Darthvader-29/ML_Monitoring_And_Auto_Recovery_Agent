"""DETECT — distribution & performance drift (PSI / KS + accuracy/F1).

Phase 2 STUB: returns no signal. Real implementation lands in Phase 3
(detection_methods.md): per-feature PSI and KS of the live window vs the frozen
reference window (data_simulation.md §9), plus performance drift from the
delayed-label join. Kept as a no-op so the loop already wires the drift channel.
"""
from __future__ import annotations

from schemas import DetectionResult  # noqa: F401  (Phase 3 API)


def detect(*_args, **_kwargs) -> list[DetectionResult]:
    return []
