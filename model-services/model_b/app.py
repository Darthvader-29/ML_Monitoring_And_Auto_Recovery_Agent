"""model_b — BACKUP inference service (FastAPI).

Thin entrypoint over the shared app factory in model-services/_common. All endpoint
logic lives there; this file only supplies model_b's identity and artifact path.
(model_a is the same entrypoint with a different MODEL_NAME / MODEL_VERSION.)
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Make the shared `_common` package importable both locally (it is a sibling dir of
# this service) and in the container (copied alongside this file).
_HERE = Path(__file__).resolve()
for _p in (_HERE.parents[0], _HERE.parents[1]):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from _common.service import create_app  # noqa: E402

app = create_app(
    model_name=os.environ.get("MODEL_NAME", "model_b"),
    model_version=os.environ.get("MODEL_VERSION", "0.9.0"),
    model_path=_HERE.parent / "model.pkl",
)
