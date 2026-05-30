# Data Simulation & Drift-Injection Design

> Part of the **Autonomous ML Monitoring & Auto-Recovery Agent**.
> Related docs: [`architecture.md`](./architecture.md), [`api_contracts.md`](./api_contracts.md),
> [`agent_logic.md`](./agent_logic.md), [`detection_methods.md`](./detection_methods.md),
> [`failure_scenarios.md`](./failure_scenarios.md).

This document is the **single source of truth** for *what data the system processes, how it is
generated, and how every kind of degradation is injected*. The agent's whole job is
**Observe → Detect → Decide → Act → Verify**, and *every* detector and demo scenario in this
project consumes data produced by the generators specified here. If you change a feature name, a
distribution, or a window size, change it **here first** and propagate.

---

## 1. Goals & Design Principles

| Principle | What it means concretely |
|-----------|--------------------------|
| **Simulated but realistic** | Data is synthetic (no PII, no external dependency) but mimics a plausible tabular fraud/risk-scoring problem with mixed numeric + categorical features and realistic value ranges. |
| **Reproducible** | Every generator is driven by an explicit integer **seed**. Re-running with the same seed + schedule yields byte-identical datasets. Seeds are recorded in the manifest (§10). |
| **Controllable drift** | Drift is *injected on purpose* through a declarative **drift schedule** (§7), not by chance. Each drift type has parameterized knobs (magnitude, start tick, duration). |
| **Supports data drift AND concept drift** | We separate the **input generator** (P(X)) from the **labeling function** (P(y\|X)). Shifting the former = data/covariate drift; shifting the latter = concept drift. They are independent dials. |
| **Batch-oriented** | The live stream is emitted as **discrete batches ("ticks")**. Real-time streaming is explicitly out of scope; a tick is the unit of the agent loop. |
| **Detector-agnostic** | The simulator produces raw feature rows + (delayed) labels. It does **not** compute PSI/KS/accuracy — that is the detectors' job (see `detection_methods.md`). This keeps the simulator a pure data source. |

**Non-goals:** scale/throughput, real Kafka/streaming infra, real datasets, feature stores. We
optimize for *correct, observable, deterministic behavior* so the agent's closed loop can be
demonstrated end-to-end.

---

## 2. The Anchor ML Problem & Feature Schema

We anchor the entire project on a **synthetic binary classification** task:

> **"Transaction Risk Scoring"** — given features of a financial transaction, predict whether it is
> **high-risk (`1`)** or **low-risk (`0`)**.

It is generated with `sklearn.datasets.make_classification` augmented with a few hand-crafted
features so we have human-readable semantics and explicit, driftable distributions. This is the
schema that **`sample_input.csv`** must conform to and that the **`POST /predict` contract**
(`api_contracts.md`) accepts.

### 2.1 Feature schema (the contract)

8 features. Names are stable identifiers used by `data_loader.py`, the model services, and every
drift detector.

| # | Feature name      | Type        | Reference distribution (baseline)            | Valid range            | Notes |
|---|-------------------|-------------|----------------------------------------------|------------------------|-------|
| 1 | `amount`          | float       | LogNormal(μ=4.0, σ=0.9) → ~ \$10–\$5000        | `[0.0, 100000.0]`      | Transaction amount in USD. Right-skewed. |
| 2 | `account_age_days`| int         | Gamma(k=2.0, θ=180) clipped                   | `[0, 7300]`            | Age of the account (≤ 20 yrs). |
| 3 | `num_txn_24h`     | int         | Poisson(λ=4)                                  | `[0, 200]`             | Transactions in the last 24h. |
| 4 | `avg_txn_amount`  | float       | Normal(μ=180, σ=60)                           | `[0.0, 50000.0]`       | Rolling average txn amount. |
| 5 | `time_since_last_min` | float   | Exponential(scale=120)                        | `[0.0, 100000.0]`      | Minutes since previous txn. |
| 6 | `device_risk`     | float       | Normal(μ=0.30, σ=0.15) clipped to [0,1]       | `[0.0, 1.0]`           | Pre-computed device risk score. |
| 7 | `country`         | categorical | Categorical: `US`=0.55,`IN`=0.20,`GB`=0.10,`NG`=0.08,`OTHER`=0.07 | enum (5 levels) | Origin country (one-hot or label-encoded at model boundary). |
| 8 | `channel`         | categorical | Categorical: `web`=0.50,`mobile`=0.40,`api`=0.10 | enum (3 levels)     | Channel used. |

> **Internal latent features:** `make_classification` also generates a set of `n_informative`
> latent Gaussian features that drive the *label*. We **do not expose** these raw; instead the
> 8 schema features above are deterministic, invertible-ish transforms of (a subset of) the
> informative latents plus added semantic noise. The key point for drift: **the labeling function
> reads the latents**, so we can move the *visible* features without moving labels (pure covariate
> drift) and vice versa (pure concept drift).

### 2.2 Target / label

| Field   | Type | Domain | Meaning |
|---------|------|--------|---------|
| `label` | int  | `{0,1}`| `1` = high-risk transaction, `0` = low-risk. |

Baseline class balance: **~30% positive** (`weights=[0.70, 0.30]` in `make_classification`). This is
the **prior** P(y=1); prior-probability shift (§5.3) moves this number.

### 2.3 Row envelope (metadata columns)

Every emitted row carries bookkeeping columns so the agent can match predictions to delayed labels:

| Column        | Type   | Meaning |
|---------------|--------|---------|
| `request_id`  | string (UUID) | Unique per inference request; primary join key for delayed labels. |
| `tick`        | int    | Batch index in which the row was emitted. |
| `emitted_at`  | ISO-8601 timestamp | Simulated wall-clock at emission. |

`label` is **never** present in the inference payload sent to `/predict`; it arrives later through
the label channel (§6).

---

## 3. Training / Reference Dataset

### 3.1 Generation

A single deterministic generator produces the **reference (training) dataset** `reference.csv` of
`N_REF = 20_000` rows under the baseline distribution (no drift), seed `RANDOM_SEED = 42`.

```python
# data_sim/generate_reference.py  (pseudocode)
import numpy as np, pandas as pd
from sklearn.datasets import make_classification

RANDOM_SEED = 42
N_REF = 20_000
rng = np.random.default_rng(RANDOM_SEED)

# 1) latent informative space -> drives the LABEL
X_lat, y = make_classification(
    n_samples=N_REF, n_features=10, n_informative=6, n_redundant=2,
    n_classes=2, weights=[0.70, 0.30], class_sep=1.2,
    flip_y=0.01, random_state=RANDOM_SEED,
)

# 2) map latents -> human-readable schema features (baseline params from §2.1)
df = pd.DataFrame({
    "amount":            np.clip(rng.lognormal(4.0, 0.9, N_REF), 0, 100_000),
    "account_age_days":  np.clip(rng.gamma(2.0, 180, N_REF).astype(int), 0, 7300),
    "num_txn_24h":       np.clip(rng.poisson(4, N_REF), 0, 200),
    "avg_txn_amount":    np.clip(rng.normal(180, 60, N_REF), 0, 50_000),
    "time_since_last_min": np.clip(rng.exponential(120, N_REF), 0, 100_000),
    "device_risk":       np.clip(rng.normal(0.30, 0.15, N_REF), 0, 1),
    "country":           rng.choice(["US","IN","GB","NG","OTHER"], N_REF, p=[.55,.20,.10,.08,.07]),
    "channel":           rng.choice(["web","mobile","api"], N_REF, p=[.50,.40,.10]),
})

# 3) couple a few VISIBLE features to the latents so the model is learnable
#    (so device_risk / amount actually carry signal, not pure noise)
df["device_risk"] = np.clip(df["device_risk"] + 0.15 * _zscore(X_lat[:,0]), 0, 1)
df["amount"]      = np.clip(df["amount"] * (1 + 0.10 * _zscore(X_lat[:,1])), 0, 100_000)

df["label"] = y
df.to_parquet("artifacts/reference.parquet")        # canonical
df.to_csv("artifacts/reference.csv", index=False)    # human-readable mirror
```

`_zscore` standardizes a latent column. The coupling in step (3) is what makes the model *useful*:
the visible features partially encode the label-driving latents.

### 3.2 Role as drift baseline (reference window)

`reference.csv` is **frozen** and shipped as the agent's **reference window** for distributional
detectors. Its per-feature summaries (histograms / bin edges / empirical CDFs) are precomputed and
cached so PSI and KS (see `detection_methods.md`) can be computed cheaply against each live window
(§9). The reference window is **fixed**, not sliding (rationale in §9).

### 3.3 Training model_a and model_b

Both models train on `reference.csv`, but **model_b is deliberately weaker/older** so it is a
meaningful fallback target for the `switch_to_backup` action.

```python
# data_sim/train_models.py  (pseudocode)
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.compose import ColumnTransformer
from sklearn.preprocessing import OneHotEncoder, StandardScaler
import joblib

X, y = load_reference()  # 8 schema features + label

pre = ColumnTransformer([
    ("num", StandardScaler(), NUMERIC_COLS),
    ("cat", OneHotEncoder(handle_unknown="ignore"), ["country", "channel"]),
])

# model_a: ACTIVE — stronger, trained on the FULL reference set
model_a = Pipeline([("pre", pre),
                    ("clf", GradientBoostingClassifier(n_estimators=300,
                                                       max_depth=3, random_state=42))])
model_a.fit(X, y)
joblib.dump(model_a, "model-services/model_a/model.pkl")

# model_b: BACKUP — older/simpler, trained on a 60% SUBSAMPLE + fewer features.
#   Lower ceiling accuracy, BUT different inductive bias => often more robust to
#   the specific concept drift we inject, which is exactly why it makes a good fallback.
Xb = X.sample(frac=0.60, random_state=7)
model_b = Pipeline([("pre", pre),
                    ("clf", LogisticRegression(max_iter=1000, C=0.5, random_state=7))])
model_b.fit(Xb, y.loc[Xb.index])
joblib.dump(model_b, "model-services/model_b/model.pkl")
```

| | model_a (ACTIVE, :8001) | model_b (BACKUP, :8002) |
|--|------------------------|--------------------------|
| Algorithm | GradientBoosting (300 trees) | LogisticRegression |
| Training data | full 20k reference | 60% subsample |
| Baseline accuracy (on held-out reference) | ~0.91 | ~0.84 |
| Role | primary; high accuracy | fallback; lower peak accuracy, different failure profile |
| Seed | 42 | 7 |

The accuracy gap is intentional: under **no** drift model_a wins, but the recovery story is "model_a
degrades under concept drift → agent switches to model_b which is unaffected by *that* boundary
rotation." The decision logic that uses this is in `agent_logic.md` / `failure_scenarios.md`.

---

## 4. Live Inference Stream Simulator

The live stream replays the agent loop tick-by-tick.

| Parameter | Value | Notes |
|-----------|-------|-------|
| Unit | **tick** = one batch | drives one Observe→…→Verify cycle |
| `BATCH_SIZE` | **200 rows / tick** | enough for stable PSI/KS estimates |
| `N_TICKS` | **120** (configurable) | a full demo run |
| Cadence | **1 tick / 5 s** (wall) or "as fast as possible" | batch mode; not true streaming |
| Emission format | **JSON Lines** per tick (one object per row) + a CSV mirror | matches `POST /predict` body |
| Output path | `artifacts/stream/tick_{NNNN}.jsonl` | one file per tick |

```python
# data_sim/stream.py  (pseudocode)
def emit_tick(tick: int, schedule: DriftSchedule, rng) -> list[dict]:
    n = BATCH_SIZE
    # 1) base batch under baseline params
    batch = generate_batch(n, rng)                 # same generator as §3.1
    # 2) apply every drift active at this tick (data + concept)
    batch = apply_active_drifts(batch, tick, schedule, rng)
    # 3) attach envelope, strip labels into the delayed-label buffer
    rows, labels = [], []
    for i in range(n):
        rid = str(uuid4())
        rows.append({"request_id": rid, "tick": tick,
                     "emitted_at": clock(tick), **batch.features[i]})
        labels.append({"request_id": rid, "label": batch.label[i],
                       "available_at_tick": tick + LABEL_LAG_TICKS})  # see §6
    write_jsonl(f"artifacts/stream/tick_{tick:04d}.jsonl", rows)
    label_buffer.extend(labels)
    return rows
```

`data_loader.py` (in `agent_core/monitoring/`) reads the current tick's `.jsonl`, and
`prediction_probe.py` POSTs each row to `/predict`, recording `(request_id, prediction, score)` for
later matching against labels (§6).

---

## 5. Drift-Injection Mechanisms

All injectors are **pure functions** `f(batch, params, rng) -> batch` selected by the schedule
(§7). Below, `x` is a feature column over a batch; `t` is the current tick; `t0`/`t1` are the
injection start/end ticks.

### 5.1 Gradual data drift (slow mean/variance shift)

Linearly ramp a feature's location and/or scale between `t0` and `t1`.

```
progress p(t) = clip((t - t0) / (t1 - t0), 0, 1)
x' = x + p(t) * delta_mean                       # mean shift
x' = mean(x) + (x - mean(x)) * (1 + p(t)*delta_scale)   # variance shift
```

Example recipe — slowly inflate `amount` by up to +60% mean over 30 ticks:
`{feature: amount, kind: gradual_mean, delta_mean_pct: 0.60, t0: 20, t1: 50}`.
PSI on `amount` climbs smoothly; this is the canonical "gradual data drift" scenario in
`failure_scenarios.md`.

### 5.2 Sudden data drift (step change)

At `t0`, hard-swap the generating distribution.

```
if t >= t0:
    x ~ NewDist(new_params)          # e.g. device_risk Normal(0.30,0.15) -> Normal(0.65,0.15)
```

Example: `{feature: device_risk, kind: sudden_shift, new_mean: 0.65, new_std: 0.15, t0: 70}`.
KS test spikes in a single tick; threshold-style detectors fire immediately.

### 5.3 Covariate shift vs prior-probability shift

These are the two *distinct* ways P(X,y) can move while leaving the **boundary** P(y\|X) intact:

- **Covariate shift** — move **P(X)** only. Use §5.1/§5.2 injectors on input features. The
  labeling function is untouched, so the model is still *correct*, but the inputs look unfamiliar.
  Detected by PSI/KS on features; **accuracy may barely move**.
- **Prior-probability (label) shift** — move **P(y)** only, keeping class-conditional P(X\|y) fixed.
  Implemented by **resampling within the batch** to hit a target positive rate:
  ```
  target_pos_rate p* ; resample positives/negatives (with replacement) so mean(label)==p*
  ```
  Example: `{kind: prior_shift, target_pos_rate: 0.55, t0: 40, t1: 90}` lifts P(y=1) from 0.30→0.55.
  Detected as a shift in the *predicted-positive rate* and in label statistics, **without** any
  single feature's PSI moving much.

### 5.4 Missing values / NaNs / corrupted fields

Randomly null/garble a fraction `rho` of cells for chosen features.

```
mask = rng.random(n) < rho
x[mask] = NaN                       # missing
# corruption variant:
x[mask] = wrong_type_or_garbage     # e.g. "" , "N/A", -1 sentinel, str in a numeric col
```

Example: `{feature: avg_txn_amount, kind: missing, rho: 0.25, t0: 55, t1: 65}`.
Drives the **data-quality / anomaly** path (`anomaly_detector.py`) and the model service's
input-validation error rate (`metrics.py`).

### 5.5 Out-of-range / invalid values

Inject values outside the valid range from §2.1 for a fraction of rows.

```
mask = rng.random(n) < rho
x[mask] = sample_outside_valid_range(feature)      # e.g. amount = -50 , device_risk = 3.7
# categorical variant: country = "ZZ" (unseen level)
```

Example: `{feature: device_risk, kind: out_of_range, rho: 0.10, low: 1.5, high: 4.0, t0: 80}`.
Surfaces as `/predict` 4xx/422 spikes and invalid-input counters — a **sudden anomaly** scenario.

### 5.6 Concept drift (rotate / flip the decision boundary)

The subtle one: **inputs P(X) stay on the baseline distribution**, but the **true X→y relationship
changes**, so a *fixed* model's accuracy drops while feature-distribution detectors see *nothing*.

We implement this by mutating the **labeling function**, never the input generator. Because labels
come from the informative latents `X_lat` (§3.1), we rotate/flip the latent decision hyperplane:

```python
# baseline boundary: score = w . X_lat ; label = 1 if score > b
# concept drift = rotate w in the latent plane and/or flip sign of selected weights
def labeling_function(X_lat, t, schedule):
    w = BASELINE_W.copy()
    cd = schedule.active_concept_drift(t)
    if cd:
        theta = cd.max_angle * progress(t, cd.t0, cd.t1)     # gradual rotation
        w = rotate(w, plane=cd.plane, angle=theta)           # rotate boundary
        if cd.flip_features:                                 # hard flip variant
            w[cd.flip_features] *= -1
    score = X_lat @ w
    return (score > BASELINE_B).astype(int)
```

- **Gradual concept drift:** rotate `w` by an angle that ramps from 0 to `max_angle` (e.g. 60°)
  between `t0`,`t1`. Model_a's live accuracy decays smoothly.
- **Sudden concept drift:** flip the sign of one informative weight at `t0` (e.g. high
  `device_risk` that *was* risky now means *safe*). Accuracy steps down.

Crucially, **`generate_batch` still draws features from the baseline distributions**, so PSI/KS on
all 8 features stay flat. The *only* signal is **rising error against arriving labels** — which is
exactly why concept drift requires the delayed-label machinery in §6.

Example: `{kind: concept_drift_rotate, plane: [0,3], max_angle_deg: 60, t0: 95, t1: 115}`.

---

## 6. Label Arrival / Ground Truth (delayed labels)

**The crux of concept-drift detection:** at inference time we do **not** know the truth. In reality
the outcome ("was this transaction actually fraud?") is confirmed later — a chargeback, a manual
review, a settlement. We simulate that lag.

### 6.1 Model

- When the simulator emits a row at tick `t`, it computes the **true label** (§5.6) but withholds
  it. The label becomes **available** at tick `t + LABEL_LAG_TICKS`.
- `LABEL_LAG_TICKS` default = **5** (configurable). A *jittered* variant draws the per-row lag from
  `Poisson(λ=5)` so labels trickle in (more realistic).
- Optionally, a fraction `label_dropout` of labels never arrive (e.g. 10%) — accuracy is then
  computed over the subset that did arrive.

### 6.2 Matching predictions to late labels

```python
# agent_core: live metric assembly (pseudocode)
# 1) prediction store: request_id -> (tick_pred, prediction, score)
# 2) at the START of each tick t, drain the label_buffer for entries with
#    available_at_tick <= t
arrived = label_buffer.pop_due(t)                  # [{request_id, label}, ...]
for lab in arrived:
    pred = prediction_store.get(lab.request_id)    # join on request_id
    if pred:
        labeled_window.append((pred.prediction, lab.label, pred.score))

# 3) compute LIVE accuracy / F1 over a sliding labeled window (e.g. last 1000 labeled rows)
live_accuracy = accuracy(labeled_window)
live_f1       = f1(labeled_window)
```

The join key is `request_id`. Because labels lag, **the live accuracy curve is delayed** relative to
the injected concept drift — the agent observes the drop ~`LABEL_LAG_TICKS` ticks *after* it starts.
This lag is a feature, not a bug: it's the real-world reason concept drift is hard, and the
`failure_scenarios.md` "concept drift" scenario asserts the agent eventually detects it despite the
delay.

### 6.3 Batch-mode realization

In pure batch replay, "later" simply means a higher tick. The `label_buffer` is a list of
`{request_id, label, available_at_tick}`; each tick we release the due entries. No real clock or
message queue is needed — the lag is an integer offset, fully deterministic given the seed.

---

## 7. Drift Schedule (scenario timeline)

A declarative file scripts the *entire run* so demos are deterministic and reviewers can see exactly
what should happen when. One injected drift entry == one row in the timeline == (typically) one
scenario in `failure_scenarios.md`.

### 7.1 Example `drift_schedule.yaml`

```yaml
# data_sim/drift_schedule.yaml
seed: 42
n_ticks: 120
batch_size: 200
label_lag_ticks: 5
label_lag_jitter: poisson      # poisson | fixed
label_dropout: 0.10

# Each entry is one injected degradation. `id` is referenced by failure_scenarios.md.
drifts:
  - id: SC-01_gradual_amount
    kind: gradual_mean
    feature: amount
    delta_mean_pct: 0.60
    t0: 20
    t1: 50

  - id: SC-02_prior_shift
    kind: prior_shift
    target_pos_rate: 0.55
    t0: 40
    t1: 90

  - id: SC-03_missing_avg_txn
    kind: missing
    feature: avg_txn_amount
    rho: 0.25
    t0: 55
    t1: 65

  - id: SC-04_sudden_device_risk
    kind: sudden_shift
    feature: device_risk
    new_mean: 0.65
    new_std: 0.15
    t0: 70
    t1: 120

  - id: SC-05_out_of_range_device_risk
    kind: out_of_range
    feature: device_risk
    rho: 0.10
    low: 1.5
    high: 4.0
    t0: 80
    t1: 85

  - id: SC-06_concept_rotate
    kind: concept_drift_rotate
    plane: [0, 3]          # rotate latent boundary in the (latent_0, latent_3) plane
    max_angle_deg: 60
    t0: 95
    t1: 115
```

### 7.2 Timeline summary (ticks 0–120)

```
tick:  0        20      40   50  55 65 70  80 85   95     115 120
       |  clean | grad amount→  |          |        |             |
                       | prior shift 0.30→0.55      |             |
                              |miss|                 |            |
                                     | sudden device_risk shift → |
                                          |OOR|                    |
                                                  | concept rotate→|
```

Ticks 0–19 are guaranteed clean (baseline) so detectors can warm up and establish that **no false
positives** fire on healthy data — an important correctness check for the agent.

---

## 8. `sample_input.csv` Format & Example Rows

`sample_input.csv` (shipped under both `model-services/model_a/` and `model-services/model_b/`) is a
small, hand-checkable sample drawn from the **baseline** distribution. It defines the exact column
order/types the `/predict` endpoint accepts. **No `label` column** (inference payload only; the
envelope columns are optional for a stored sample but shown here for completeness).

```csv
request_id,amount,account_age_days,num_txn_24h,avg_txn_amount,time_since_last_min,device_risk,country,channel
a1f3c0de-0001,54.20,365,3,182.50,42.0,0.28,US,web
a1f3c0de-0002,1280.75,90,11,540.10,4.5,0.61,NG,mobile
a1f3c0de-0003,12.99,1825,1,75.00,1440.0,0.12,GB,web
a1f3c0de-0004,8990.00,30,27,2100.40,0.8,0.74,OTHER,api
a1f3c0de-0005,233.10,730,5,210.00,180.0,0.35,IN,mobile
```

Corresponding `POST /predict` body (see `api_contracts.md` for the full contract):

```json
{
  "request_id": "a1f3c0de-0002",
  "amount": 1280.75,
  "account_age_days": 90,
  "num_txn_24h": 11,
  "avg_txn_amount": 540.10,
  "time_since_last_min": 4.5,
  "device_risk": 0.61,
  "country": "NG",
  "channel": "mobile"
}
```

Expected response shape: `{"request_id": "...", "prediction": 0|1, "score": 0.0..1.0,
"model_version": "model_a:1.0"}`.

---

## 9. Reference vs Current Window (for the drift detector)

The distributional detectors (`drift_detector.py`, algorithms in `detection_methods.md`) compare a
**reference window** to a **current window**:

| Window | Definition | Default size | Sliding? |
|--------|------------|--------------|----------|
| **Reference** | The frozen training set `reference.csv` (§3) | 20,000 rows | **Fixed** (never updates) |
| **Current** | The most recent live ticks | last **5 ticks = 1,000 rows** | **Sliding** (advances every tick) |
| **Labeled window** (concept drift) | Most recent rows with arrived labels (§6) | last 1,000 labeled rows | Sliding |

**Why a fixed reference (not sliding):** the reference *is* the training distribution that the model
was fit on. If the reference slid along with the live data, slow drift would be "absorbed" and never
detected. A fixed reference guarantees PSI/KS measure deviation from the world the model actually
knows. (A sliding reference is only appropriate for *change-point* style detectors, which are out of
scope here.)

Bin edges / empirical CDFs for the reference are precomputed once and cached so each tick's PSI/KS
computation is O(current window) — see `detection_methods.md` for the exact PSI binning (10
quantile bins) and KS procedure.

---

## 10. Reproducibility, Versioning & Regeneration

### 10.1 Seeds

| Artifact | Seed |
|----------|------|
| Reference dataset + model_a | `42` |
| model_b subsample/training | `7` |
| Live stream generator | `schedule.seed` (default `42`) |
| Per-tick RNG | `np.random.default_rng(schedule.seed + tick)` (independent, reproducible per tick) |

### 10.2 Manifest

Every generation writes `artifacts/manifest.json` recording exact provenance:

```json
{
  "generated_at": "2026-05-30T00:00:00Z",
  "schema_version": "1.0",
  "reference": {"path": "artifacts/reference.parquet", "n_rows": 20000, "seed": 42,
                "sha256": "<hash>"},
  "models": {"model_a": {"sha256": "<hash>", "seed": 42, "algo": "GradientBoosting"},
             "model_b": {"sha256": "<hash>", "seed": 7,  "algo": "LogisticRegression"}},
  "schedule": "data_sim/drift_schedule.yaml",
  "schedule_sha256": "<hash>",
  "library_versions": {"numpy": "x.y.z", "scikit-learn": "x.y.z"}
}
```

Datasets/models are versioned by `schema_version` + content `sha256`. Bumping the schema (adding a
feature, changing a distribution) **must** bump `schema_version` and update §2.1 here first.

### 10.3 Regenerate from scratch

```bash
# 1) build the frozen reference set + train both models (deterministic, seed-locked)
python data_sim/generate_reference.py            # -> artifacts/reference.{parquet,csv}
python data_sim/train_models.py                  # -> model_{a,b}/model.pkl  (+ manifest entries)

# 2) refresh the committed sample inputs from the baseline distribution
python data_sim/make_sample_input.py             # -> model_{a,b}/sample_input.csv

# 3) materialize the full scripted live stream for a demo run
python data_sim/stream.py --schedule data_sim/drift_schedule.yaml
                                                 # -> artifacts/stream/tick_0000.jsonl ...

# 4) (optional) verify reproducibility: same seed must yield identical hashes
python data_sim/verify_manifest.py               # recompute + diff sha256s
```

Same seeds + same `drift_schedule.yaml` ⇒ identical artifacts ⇒ identical agent behavior in the
demo. This determinism is what makes `failure_scenarios.md` assertions testable.

---

## 11. How This Connects to the Rest of the System

### 11.1 → `detection_methods.md`
This document **produces the data**; `detection_methods.md` **defines the math**. The connections:

- PSI / KS are computed **per feature in §2.1** between the **fixed reference window** (§9) and the
  **sliding current window** (§9). Gradual (§5.1) and sudden (§5.2) data drift, covariate shift
  (§5.3), and `device_risk`/`amount` injections are what make those statistics move.
- The **anomaly / data-quality detector** consumes the missing (§5.4) and out-of-range (§5.5)
  injections via null-rate and range-violation counters.
- The **concept-drift detector** consumes the **live accuracy/F1** built from the delayed-label join
  (§6); it does *not* look at feature PSI (which stays flat under §5.6).
- The **prior-shift signal** (§5.3) is read from predicted-positive-rate / label-rate, not feature
  PSI.

Algorithms, thresholds, bin counts, and statistical tests live in `detection_methods.md` and are
**not** duplicated here.

### 11.2 → `failure_scenarios.md`
**Each `id` in the drift schedule (§7) maps 1:1 to a failure scenario.** `SC-01` … `SC-06` are the
deterministic, seed-locked scripts those scenarios assert against (expected detector to fire,
expected severity, expected agent action such as `switch_to_backup` to model_b, and expected
verification outcome). `failure_scenarios.md` references these IDs; it does not redefine the
injection mechanics — those are owned here in §5.

### 11.3 → `architecture.md` / `agent_logic.md` / `api_contracts.md`
- `data_loader.py` reads `artifacts/stream/tick_*.jsonl`; `prediction_probe.py` POSTs rows to
  `/predict` using the §2.1 schema; the §8 `sample_input.csv` defines the `/predict` contract.
- The **tick** defined in §4 is the cadence of the Observe→Detect→Decide→Act→Verify loop in
  `agent_logic.md`.

---

### Appendix A — Drift-type quick reference

| Drift type | Moves | Visible to feature PSI/KS? | Visible to accuracy? | Needs labels? | §  | Schedule `kind` |
|------------|-------|----------------------------|----------------------|---------------|----|-----------------|
| Gradual data drift | P(X) slowly | Yes (rising) | maybe | No | 5.1 | `gradual_mean` |
| Sudden data drift | P(X) step | Yes (spike) | maybe | No | 5.2 | `sudden_shift` |
| Covariate shift | P(X) only | Yes | little | No | 5.3 | (any feature injector) |
| Prior-prob shift | P(y) only | No | via label rate | partial | 5.3 | `prior_shift` |
| Missing / NaN | data quality | indirectly | yes (errors) | No | 5.4 | `missing` |
| Out-of-range | data quality | indirectly | yes (4xx) | No | 5.5 | `out_of_range` |
| Concept drift | P(y\|X) | **No** | **Yes** | **Yes** | 5.6 | `concept_drift_rotate` |
