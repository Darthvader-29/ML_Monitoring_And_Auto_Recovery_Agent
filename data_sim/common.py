"""Shared data-generation primitives for the Transaction Risk Scoring task.

Single source of truth for the feature schema and the baseline batch generator,
implementing docs/data_simulation.md §2 (schema) and §3.1 (reference generation).
Imported by generate_reference.py, train_models.py and make_sample_input.py so the
schema is defined exactly once.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.datasets import make_classification

# ---- Schema (data_simulation.md §2.1) -----------------------------------

NUMERIC_COLS = [
    "amount",
    "account_age_days",
    "num_txn_24h",
    "avg_txn_amount",
    "time_since_last_min",
    "device_risk",
]
CATEGORICAL_COLS = ["country", "channel"]
FEATURE_COLS = NUMERIC_COLS + CATEGORICAL_COLS
LABEL_COL = "label"

COUNTRIES = ["US", "IN", "GB", "NG", "OTHER"]
COUNTRY_P = [0.55, 0.20, 0.10, 0.08, 0.07]
CHANNELS = ["web", "mobile", "api"]
CHANNEL_P = [0.50, 0.40, 0.10]

RANDOM_SEED = 42       # reference dataset + model_a (data_simulation.md §10.1)
N_REF = 20_000         # reference window size (§3.1)


def _zscore(col: np.ndarray) -> np.ndarray:
    """Standardize a latent column (data_simulation.md §3.1)."""
    std = col.std()
    return (col - col.mean()) / std if std > 0 else col - col.mean()


def generate_baseline(n: int, seed: int) -> pd.DataFrame:
    """Generate `n` baseline (no-drift) rows + labels per data_simulation.md §3.1.

    The label is driven by latent informative features from make_classification;
    a couple of VISIBLE features (device_risk, amount) are coupled to those latents
    so the schema features carry real signal and the model is learnable.
    """
    rng = np.random.default_rng(seed)

    # 1) latent informative space -> drives the LABEL. shuffle=False keeps the first
    #    n_informative columns informative so we can map them onto visible features.
    x_lat, y = make_classification(
        n_samples=n,
        n_features=10,
        n_informative=6,
        n_redundant=2,
        n_classes=2,
        weights=[0.70, 0.30],
        class_sep=1.2,
        flip_y=0.01,
        shuffle=False,
        random_state=seed,
    )
    lat = [_zscore(x_lat[:, i]) for i in range(6)]  # 6 standardized informative latents

    # 2) map latents -> human-readable schema features (baseline marginals from §2.1,
    #    each carrying one informative latent so the schema features hold real signal —
    #    "deterministic transforms of the informative latents plus semantic noise", §2.1).
    df = pd.DataFrame({
        "amount": np.clip(rng.lognormal(4.0, 0.9, n) * np.exp(0.50 * lat[0]), 0, 100_000),
        "account_age_days": np.clip(rng.gamma(2.0, 180, n) + 250 * lat[1], 0, 7300).astype(int),
        "num_txn_24h": np.clip(rng.poisson(4, n) + np.round(3 * lat[2]), 0, 200).astype(int),
        "avg_txn_amount": np.clip(rng.normal(180, 60, n) + 60 * lat[3], 0, 50_000),
        "time_since_last_min": np.clip(rng.exponential(120, n) + 100 * lat[4], 0, 100_000),
        "device_risk": np.clip(rng.normal(0.30, 0.15, n) + 0.18 * lat[5], 0, 1),
        "country": rng.choice(COUNTRIES, n, p=COUNTRY_P),
        "channel": rng.choice(CHANNELS, n, p=CHANNEL_P),
    })

    df[LABEL_COL] = y.astype(int)
    return df
