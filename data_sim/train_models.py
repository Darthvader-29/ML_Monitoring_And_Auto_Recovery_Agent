"""Train and persist model_a (ACTIVE) and model_b (BACKUP).

Implements docs/data_simulation.md §3.3:
  - model_a: GradientBoosting on the FULL 20k reference  (strong, seed 42)
  - model_b: LogisticRegression on a 60% subsample        (weaker fallback, seed 7)
Each is a sklearn Pipeline (StandardScaler on numerics + OneHotEncoder on
categoricals) dumped to its service directory as model.pkl, so the model service
loads it at startup. The deliberate accuracy gap makes model_b a meaningful
failover target.
"""
from __future__ import annotations

from pathlib import Path

import joblib
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, f1_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from common import CATEGORICAL_COLS, FEATURE_COLS, LABEL_COL, NUMERIC_COLS

REPO_ROOT = Path(__file__).resolve().parents[1]
REFERENCE_CSV = Path(__file__).resolve().parent / "artifacts" / "reference.csv"
MODEL_A_PKL = REPO_ROOT / "model-services" / "model_a" / "model.pkl"
MODEL_B_PKL = REPO_ROOT / "model-services" / "model_b" / "model.pkl"


def _preprocessor() -> ColumnTransformer:
    return ColumnTransformer([
        ("num", StandardScaler(), NUMERIC_COLS),
        ("cat", OneHotEncoder(handle_unknown="ignore"), CATEGORICAL_COLS),
    ])


def main() -> None:
    if not REFERENCE_CSV.exists():
        raise SystemExit(
            f"{REFERENCE_CSV} not found — run generate_reference.py first."
        )

    df = pd.read_csv(REFERENCE_CSV)
    X = df[FEATURE_COLS]
    y = df[LABEL_COL]

    # Held-out split only for reporting baseline accuracy (models still train on all
    # the data they are specified to use below).
    X_tr, X_te, y_tr, y_te = train_test_split(
        X, y, test_size=0.2, random_state=42, stratify=y
    )

    # model_a: ACTIVE — stronger, full reference set
    model_a = Pipeline([
        ("pre", _preprocessor()),
        ("clf", GradientBoostingClassifier(n_estimators=300, max_depth=3, random_state=42)),
    ])
    model_a.fit(X, y)
    joblib.dump(model_a, MODEL_A_PKL)

    # model_b: BACKUP — older/simpler, 60% subsample (seed 7)
    Xb = X.sample(frac=0.60, random_state=7)
    yb = y.loc[Xb.index]
    model_b = Pipeline([
        ("pre", _preprocessor()),
        ("clf", LogisticRegression(max_iter=1000, C=0.5, random_state=7)),
    ])
    model_b.fit(Xb, yb)
    joblib.dump(model_b, MODEL_B_PKL)

    # Report held-out performance so the accuracy gap is visible.
    for name, model in (("model_a", model_a), ("model_b", model_b)):
        pred = model.predict(X_te)
        acc = accuracy_score(y_te, pred)
        f1 = f1_score(y_te, pred)
        print(f"{name}: held-out accuracy={acc:.3f} f1={f1:.3f}")
    print(f"Wrote {MODEL_A_PKL}")
    print(f"Wrote {MODEL_B_PKL}")


if __name__ == "__main__":
    main()
