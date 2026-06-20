"""SINGLE SOURCE OF TRUTH for the agent-side feature schema.

The 8-feature Transaction Risk Scoring schema (data_simulation.md §2.1) was
previously hand-redeclared in data_loader, drift_detector and config. Declare it
once here and import it everywhere on the agent side, so adding/renaming a feature
is a one-line change that cannot silently desync drift detection from data loading.

(The model services keep their own copy by design — architecture.md §1.3 forbids
cross-service imports — and data_sim/common.py owns the training-side copy.)
"""
from __future__ import annotations

NUMERIC_COLS = ["amount", "account_age_days", "num_txn_24h",
                "avg_txn_amount", "time_since_last_min", "device_risk"]
CATEGORICAL_COLS = ["country", "channel"]
FEATURE_COLS = NUMERIC_COLS + CATEGORICAL_COLS
