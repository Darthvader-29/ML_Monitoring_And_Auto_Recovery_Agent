# Detection Methods Reference

> Module: `control-plane/agent_core/detection/`
> Stage in the agent loop: **DETECT** (the second stage of *Observe → Detect → Decide → Act → Verify*).
> Audience: engineers implementing or tuning the detectors; reviewers reasoning about correctness.

This document is the authoritative, implementation-ready reference for the three detector
modules that turn raw observations into **normalized `DetectionResult` objects**. It specifies,
for every method: what it detects, its inputs, the exact algorithm/formula, parameters and
recommended default thresholds, the output, pseudocode, strengths/limitations, and how each maps
onto the unified `DetectionResult` schema.

Scope boundaries (do not duplicate elsewhere):

- This doc does **not** define how the operational/feature data is produced or how labels arrive —
  see [`data_simulation.md`](./data_simulation.md).
- This doc does **not** define how a `severity_hint` becomes a final severity or an action — see
  [`agent_logic.md`](./agent_logic.md). Detectors emit a *hint* only; the decision engine
  (`decision_engine/severity_classifier.py`, `policy_rules.py`) owns the final mapping.
- For the REST contracts that feed observations in and ship `DetectionResult`s out, see
  [`api_contracts.md`](./api_contracts.md).

Design constraint from the problem statement: **"accuracy of detection is more important than
complexity."** Therefore every method here is *lightweight* — pure `numpy`/`scipy`/`sklearn`, no
deep learning, no streaming infra. Batch (per-tick) evaluation is acceptable and assumed.

---

## 1. Overview

### 1.1 The three detector modules

| Module | Class (suggested) | Detects | Primary signal source |
|---|---|---|---|
| `threshold_detector.py` | `ThresholdDetector` | Operational threshold breaches: error rate, latency (avg / p95), inference-failure rate, confidence floor. **SYSTEM** signals. | `monitoring/model_probe.py` (`/metrics`, `/health`) |
| `anomaly_detector.py` | `AnomalyDetector` | Sudden spikes / outliers in any operational metric time-series. **SUDDEN ANOMALIES**. | rolling history of the same metrics |
| `drift_detector.py` | `DriftDetector` | **DATA DRIFT** (input feature distribution shift) and **CONCEPT DRIFT** (accuracy/F1 drop with stable inputs). | `monitoring/prediction_probe.py` inputs + delayed labels |

A clean separation: the **threshold** detector answers *"is a metric out of bounds right now?"*,
the **anomaly** detector answers *"is this metric behaving abnormally vs its own recent history?"*,
and the **drift** detector answers *"has the world the model sees changed, or has the model gotten
worse?"*. They are complementary; the same tick can fire several of them.

### 1.2 The detection pipeline per tick

The agent loop calls a single `run_detection(observation)` orchestrator once per tick. A *tick* is
one pass of the observe→detect cycle (e.g. every 30–60 s; the cadence itself lives in `config.py`).

```
                ┌──────────────────────── monitoring/ ────────────────────────┐
observation  →  │ model metrics (latency, errors)   prediction inputs   labels│
                └──────────────────────────────┬───────────────────────────────┘
                                                │
                            ┌───────────────────┼────────────────────┐
                            ▼                   ▼                    ▼
                   ThresholdDetector      AnomalyDetector       DriftDetector
                   (current snapshot)     (snapshot + history)  (ref vs current window
                                                                 + delayed labels)
                            │                   │                    │
                            └─────────► list[DetectionResult] ◄───────┘
                                                │
                                   aggregate / dedup / attach
                                   persistence counters (§6)
                                                │
                                                ▼
                                      decision_engine (DECIDE)
```

Per tick the orchestrator:

1. Pulls the latest **snapshot** of operational metrics and appends them to per-metric rolling
   buffers (the detectors are stateful across ticks for their windows; the buffers live in the
   detector instances).
2. Runs each detector. Each returns **zero or more** `DetectionResult`s (e.g. the drift detector
   emits one per drifted feature plus one aggregate result).
3. Updates **persistence counters** (§6) so transient noise can be distinguished from a persistent
   problem before anything is escalated.
4. Returns the combined `list[DetectionResult]` to the decision engine.

```python
def run_detection(observation, state) -> list[DetectionResult]:
    results = []
    results += threshold_detector.evaluate(observation.metrics)
    results += anomaly_detector.evaluate(observation.metrics)        # uses internal history
    if observation.has_inputs:
        results += drift_detector.evaluate_data_drift(observation.inputs)
    if observation.has_labels:                                       # labels arrive lagged
        results += drift_detector.evaluate_concept_drift(observation.labels, observation.preds)
    results = apply_persistence(results, state)                      # §6
    return results
```

### 1.3 Unified `DetectionResult` schema

Every method in every module emits this single normalized record. Severity is **not** decided here;
only a coarse `severity_hint` is attached. (`schemas.py` should hold the canonical definition; the
shape below is authoritative.)

```python
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

@dataclass
class DetectionResult:
    detector:      Literal["threshold", "anomaly", "drift"]
    signal_type:   str            # see vocabulary below
    score:         float          # the numeric statistic the threshold is compared against
    threshold:     float          # the decision boundary used for this evaluation
    breached:      bool           # score crossed threshold in the alerting direction
    drifted_features: list[str] = field(default_factory=list)   # populated by drift only
    severity_hint: Literal["NONE", "LOW", "MEDIUM", "HIGH"] = "NONE"   # Severity values
    evidence:      dict[str, Any] = field(default_factory=dict)  # method-specific metadata
    timestamp:     datetime = None  # ISO-8601 UTC of the evaluation
```

> The canonical form of this schema is the pydantic `BaseModel` in `conventions.md` (§2), where
> `timestamp` is a `datetime`. Canonical schemas/enums/defaults live in `conventions.md`
> (authoritative).

**`signal_type` vocabulary** (stable string keys downstream policies match on):

| `detector` | `signal_type` values |
|---|---|
| `threshold` | `error_rate`, `avg_latency_ms`, `p95_latency_ms`, `inference_failure_rate`, `confidence_floor` |
| `anomaly` | `zscore_spike`, `robust_zscore_spike`, `iqr_outlier`, `ewma_shift`, `iforest_outlier` |
| `drift` | `data_drift_psi`, `data_drift_ks`, `data_drift_chi2`, `data_drift_aggregate`, `concept_drift_perf` |

Conventions:

- `score` is always oriented so that **higher = more abnormal** *unless* the metric is intrinsically
  "higher is better" (e.g. confidence, accuracy), in which case `breached = score < threshold` and
  `evidence["direction"] = "below"`.
- `evidence` carries everything a human or downstream rule needs to understand the firing without
  recomputation (e.g. PSI per-bin contributions, KS p-value, window sizes, observed vs baseline).
- `drifted_features` is empty for `threshold` and `anomaly`; for per-feature drift it holds the
  single feature, and for the aggregate drift result it holds **all** drifted features.

A non-breaching evaluation may still be emitted (`breached=False`, `severity_hint="NONE"`) so the
control plane can log/plot the score history. Implementations may instead suppress non-breaches to
reduce volume; either is acceptable as long as it is consistent.

---

## 2. Threshold Detection — `threshold_detector.py`

### 2.1 What it detects

Hard operational limits on **SYSTEM** signals. These are the fastest, cheapest, most interpretable
checks and act as the first line of defense against an unhealthy serving model.

| `signal_type` | Source field | Direction | Meaning |
|---|---|---|---|
| `error_rate` | `errors / requests` over the metrics window | higher-worse | fraction of 5xx / exception responses |
| `avg_latency_ms` | mean request latency | higher-worse | average end-to-end inference latency |
| `p95_latency_ms` | 95th-percentile latency | higher-worse | tail latency |
| `inference_failure_rate` | `failed_predictions / attempts` | higher-worse | predict calls that raised / returned invalid output |
| `confidence_floor` | mean (or p05) of model confidence/`max(proba)` | **lower-worse** | model is unsure → possible degradation |

### 2.2 Inputs

A metrics snapshot dict for the active model, e.g.:

```python
{ "requests": 412, "errors": 9, "avg_latency_ms": 137.4, "p95_latency_ms": 410.0,
  "failed_predictions": 3, "attempts": 412, "mean_confidence": 0.81, "ts": "..." }
```

Latency/error metrics are themselves windowed *inside* `model_probe`/`metrics.py` over a short
horizon (e.g. last 60 s). The detector consumes the already-aggregated values.

### 2.3 Static thresholds

Each signal `m` has a configured limit `T_m`. For higher-worse signals:

```
breached(m) = value(m) >= T_m
```

For the lower-worse confidence floor:

```
breached(confidence) = value(confidence) <= T_confidence
```

**Recommended static defaults** (override in `config.py` per model/SLA):

| Signal | Default threshold | Notes |
|---|---|---|
| `error_rate` | `0.10` (10 %) | fraction in `[0,1]` |
| `avg_latency_ms` | `300` ms | service SLA dependent |
| `p95_latency_ms` | `500` ms | tail SLA |
| `inference_failure_rate` | `0.05` (5 %) | invalid/failed predicts |
| `confidence_floor` | `0.60` | mean `max(proba)`; binary classifier |

### 2.4 Adaptive thresholds (rolling mean ± k·std)

Static limits are brittle when normal behavior shifts (e.g. warm-up, daily load patterns). The
adaptive variant derives the limit from the metric's own recent history buffer `H` (length `W`):

```
mu     = mean(H)
sigma  = std(H, ddof=1)
T_adaptive = mu + k * sigma            # higher-worse
T_adaptive = mu - k * sigma            # lower-worse (confidence)
```

Default `k = 3.0`, window `W = 30` ticks. The **effective threshold** used for the comparison can be
the *stricter* of static and adaptive, so a slowly-creeping metric is caught by the adaptive bound
while a hard ceiling is still enforced by the static bound:

```
T_eff = min(T_static, T_adaptive)   # higher-worse  (alert sooner)
T_eff = max(T_static, T_adaptive)   # lower-worse
```

Require `len(H) >= W_min` (default `W_min = 10`) before trusting the adaptive bound; until then fall
back to the static threshold.

### 2.5 Consecutive-breach counting / debounce (anti-flapping)

A single noisy snapshot should not escalate. Maintain a per-signal counter `consec[m]`:

```
if breached_now(m):  consec[m] += 1
else:                consec[m]  = 0
fired(m) = consec[m] >= N_consec
```

Default `N_consec = 3` consecutive ticks. The `DetectionResult` is emitted on every breaching tick
(so it is logged), but `severity_hint` is only raised above `"LOW"` once `fired(m)` is true; the
count is carried in `evidence["consecutive_breaches"]`. This is the detector-local half of the
persistence logic described in §6.

### 2.6 Output → `DetectionResult` mapping

```python
DetectionResult(
    detector="threshold",
    signal_type="p95_latency_ms",
    score=value,                 # observed metric
    threshold=T_eff,
    breached=(value >= T_eff),
    severity_hint=("MEDIUM" if fired else "LOW") if breached else "NONE",
    evidence={
        "static_threshold": T_static, "adaptive_threshold": T_adaptive,
        "rolling_mean": mu, "rolling_std": sigma, "k": k,
        "consecutive_breaches": consec, "window": W, "direction": "above",
    },
    timestamp=now_iso(),
)
```

### 2.7 Pseudocode

```python
def evaluate(self, metrics) -> list[DetectionResult]:
    out = []
    for sig, cfg in self.signals.items():           # cfg: {static, k, w, direction}
        v = metrics.get(sig)
        if v is None:
            continue
        self.hist[sig].append(v)                     # bounded deque, maxlen=W
        T_adapt = self._adaptive(sig, cfg)           # None if too few samples
        T_eff   = self._combine(cfg.static, T_adapt, cfg.direction)
        breached = (v >= T_eff) if cfg.direction=="above" else (v <= T_eff)
        self.consec[sig] = self.consec[sig]+1 if breached else 0
        fired = self.consec[sig] >= self.N_consec
        out.append(self._result(sig, v, T_eff, breached, fired, ...))
    return out
```

### 2.8 Strengths / limitations

- **Strengths:** trivially cheap, fully interpretable, catches hard SLA violations and total outages
  immediately, no labels or reference distribution needed.
- **Limitations:** thresholds need tuning; static limits miss gradual creep; cannot distinguish a
  *cause* (drift vs infra). Confidence floor is only meaningful if the model exposes calibrated
  probabilities.

### 2.9 Confidence-based action thresholds (Phase 8 bonus — `CONFIDENCE_GATING_ENABLED`)

The static **confidence floor** (§2.3) watches only the *mean* confidence, which a degrading model
can keep healthy-looking while a growing tail of predictions drifts toward the decision boundary.
When `CONFIDENCE_GATING_ENABLED=true` the agent adds a second, leading confidence channel:

- `prediction_probe.py` computes `low_confidence_ratio` — the share of a tick's predictions whose
  confidence falls below `LOW_CONFIDENCE_CUTOFF` (default `0.60`).
- `threshold_detector.py` emits a `low_confidence_ratio` `DetectionResult` once that share crosses
  `LOW_CONFIDENCE_RATIO_MED` (default `0.20`); the raw ratio rides on `score`.
- `severity_classifier.py` bands it: `>= LOW_CONFIDENCE_RATIO_HIGH` (default `0.40`) → **HIGH**,
  `>= …_MED` → **MEDIUM**. HIGH feeds the same confirm-N / cooldown gate as any other signal, so a
  confidence collapse can drive an autonomous switch without a hard error-rate or latency breach.

The flag defaults **off**, so the Phase 7 baseline is unchanged; the mean-confidence band edges were
also lifted into config (`CONFIDENCE_NOTABLE_FLOOR`, `CONFIDENCE_MED_FLOOR`) with their previous
values as defaults.

---

## 3. Anomaly Detection — `anomaly_detector.py`

### 3.1 What it detects

**SUDDEN ANOMALIES**: a metric value that is abnormal *relative to its own recent history*, even if
no static threshold is crossed. Operates on the same operational metric time-series as §2 plus any
auxiliary metrics (request volume, invalid-input rate, queue depth).

### 3.2 Inputs

Per metric, a rolling buffer `H = [x_{t-W+1}, ..., x_t]` of the last `W` snapshots (default
`W = 50`, minimum usable `W_min = 12`). The detector is stateful across ticks.

### 3.3 Method A — Z-score (Gaussian)

```
mu = mean(H[:-1]);  sigma = std(H[:-1], ddof=1)
z  = (x_t - mu) / sigma            (sigma == 0  → z = 0)
anomaly = |z| >= k_z
```

Default `k_z = 3.0`. Simple but sensitive to the very outliers it is meant to detect (mean and std
are contaminated). Use the robust variant when the metric is spiky.

### 3.4 Method B — Robust z-score (median & MAD) — *preferred default*

```
med  = median(H[:-1])
MAD  = median(|H[:-1] - med|)
z_r  = 0.6745 * (x_t - med) / MAD      (MAD == 0 → z_r = 0)
anomaly = |z_r| >= k_r
```

`0.6745` makes MAD a consistent estimator of σ for normal data. Default `k_r = 3.5`. Robust to up to
~50 % contamination, so prior spikes don't blind the detector.

### 3.5 Method C — Rolling IQR outlier rule

```
Q1 = percentile(H, 25);  Q3 = percentile(H, 75);  IQR = Q3 - Q1
lower = Q1 - c * IQR;     upper = Q3 + c * IQR
anomaly = x_t < lower  or  x_t > upper
```

Default `c = 1.5` (Tukey fences; `c = 3.0` for "extreme" outliers only). Distribution-free, good for
skewed metrics like latency.

### 3.6 Method D — EWMA control chart (optional, for sustained shift)

Exponentially-weighted moving average tracks the metric's level; control limits widen the smoothing:

```
S_t = lambda * x_t + (1 - lambda) * S_{t-1}        (S_0 = mean of warm-up window)
sigma_S = sigma * sqrt( lambda / (2 - lambda) * (1 - (1-lambda)^(2t)) )
UCL = mu + L * sigma_S ;   LCL = mu - L * sigma_S
shift = S_t > UCL  or  S_t < LCL
```

Defaults `lambda = 0.3`, `L = 3.0`, `mu, sigma` from a stable warm-up window. EWMA is the tool for
detecting a **small but persistent level shift** that point tests (A–C) would miss.

### 3.7 Method E — IsolationForest (optional lightweight-ML)

For *multivariate* anomalies across a metric vector
`v_t = [error_rate, avg_latency, p95, failure_rate, ...]`, fit `sklearn.ensemble.IsolationForest`
on a window of recent vectors and score the current one:

```
clf = IsolationForest(n_estimators=100, contamination=0.02, random_state=0).fit(V_window)
s   = -clf.score_samples([v_t])[0]        # higher = more anomalous
anomaly = clf.predict([v_t])[0] == -1
```

Default `contamination = 0.02`, refit every `R = 50` ticks (or on a sliding window). Use only when
single-metric methods are insufficient; it is harder to explain, hence kept optional.

### 3.8 Spike vs sustained-shift distinction

- **Spike** — point methods (A/B/C) fire for a single tick and recover. Tag
  `evidence["pattern"]="spike"` when the anomaly counter resets within `N_consec` ticks.
- **Sustained shift** — EWMA (D) fires, or a point method fires for `>= N_sustain` consecutive ticks
  (default `N_sustain = 5`). Tag `evidence["pattern"]="sustained"` and raise `severity_hint`. A
  sustained shift is more likely a real regression than transient load noise; this distinction is a
  hint for the decision engine, which makes the final call (see `agent_logic.md`).

### 3.9 Output → `DetectionResult`

```python
DetectionResult(
    detector="anomaly",
    signal_type="robust_zscore_spike",     # or iqr_outlier / ewma_shift / iforest_outlier
    score=abs(z_r),                          # the test statistic
    threshold=k_r,
    breached=(abs(z_r) >= k_r),
    severity_hint="LOW" if breached else "NONE",   # raised to "MEDIUM" if pattern=="sustained"
    evidence={"metric": "avg_latency_ms", "value": x_t, "median": med, "mad": MAD,
              "method": "robust_zscore", "pattern": pattern, "window": W},
    timestamp=now_iso(),
)
```

### 3.10 Strengths / limitations

- **Strengths:** needs no fixed threshold or labels; adapts to each metric's natural scale; robust
  variants tolerate noisy histories; catches problems below static SLA limits.
- **Limitations:** detects *that* something changed, not *why*; cold-start needs a warm-up window;
  during a genuine prolonged regression the rolling baseline can drift toward the bad state
  (mitigate by freezing the baseline once `pattern="sustained"` is confirmed).

---

## 4. Data Drift Detection — `drift_detector.py` (core)

### 4.1 Reference vs current window

- **Reference window** `R`: the **training distribution** of the active model's input features.
  Computed once (offline) and cached as per-feature summaries — bin edges, bin reference proportions,
  raw sorted values for KS, category frequencies for chi-square. Size: the full training set or a
  representative sample of `n_ref >= 1000` rows. (How `R` is produced: see `data_simulation.md`.)
- **Current window** `C`: the most recent live inference inputs, collected from
  `prediction_probe.py`. Default size `n_cur = 500` rows, evaluated as a sliding/tumbling batch.
  Require `n_cur >= 200` before testing (smaller → unstable, hold and accumulate).

Features are read from the **prediction inputs** (the feature vectors sent to `/predict`), keyed by
the model's feature schema. Continuous features → PSI + KS; categorical features → PSI + chi-square.

### 4.2 Population Stability Index (PSI) — per feature

**Bucketing scheme.** Define `B` bins (default `B = 10`) from the **reference** distribution using
its quantiles, so each reference bin holds ~`1/B` of the mass:

```
edges = quantile(R_feature, [0, 0.1, 0.2, ..., 1.0])     # B+1 edges, deduplicated
```

The *same* edges are applied to `C`. Outer edges are extended to `±inf` so live values outside the
reference range fall into the end bins. For categorical features each category (plus an `__OTHER__`
bucket for unseen categories) is a bin.

**Proportions.** For bin `i`:

```
ref_i = count_R(bin_i) / N_R
cur_i = count_C(bin_i) / N_C
```

Apply a small floor `eps = 1e-4` to any zero proportion to avoid `log(0)` / division by zero:
`ref_i = max(ref_i, eps)`, `cur_i = max(cur_i, eps)`.

**PSI formula.**

```
            B
PSI(f)  =  Σ   (cur_i - ref_i) * ln(cur_i / ref_i)
           i=1
```

Each summand is the **per-bin contribution**; store the top contributors in `evidence` to explain
*where* the shift is.

**Interpretation bands (standard):**

| PSI(f) | Interpretation | `breached` | `severity_hint` |
|---|---|---|---|
| `< 0.10` | no significant drift | False | NONE |
| `0.10 – 0.25` | moderate drift — monitor | True | LOW / MEDIUM |
| `> 0.25` | significant drift | True | MEDIUM / HIGH |

Default decision threshold `PSI_THRESHOLD = 0.25`; a secondary "watch" threshold `0.10`.

### 4.3 Kolmogorov–Smirnov two-sample test (continuous features)

Tests whether `R` and `C` come from the same continuous distribution.

```
D = sup_x | F_R(x) - F_C(x) |          # max gap between empirical CDFs
```

Use `scipy.stats.ks_2samp(R_feature, C_feature)` → `(D, p_value)`.

```
drift = p_value < alpha                # default alpha = 0.05
```

`D ∈ [0,1]` is reported as `score`; the larger `D`, the stronger the shift. KS is most sensitive to
shifts in **location/shape** of continuous variables and is parameter-free.

### 4.4 Chi-square test (categorical features)

Build the `2 × k` contingency table of category counts (reference vs current), then:

```
chi2 = Σ_cells (O - E)^2 / E ,   E = row_total * col_total / grand_total
dof  = k - 1
drift = p_value < alpha                # alpha = 0.05  (scipy.stats.chi2_contingency)
```

Merge categories with expected count `E < 5` into `__OTHER__` before testing (chi-square validity).

### 4.5 Optional alternative distances

- **Jensen–Shannon divergence** on the binned distributions (symmetric, bounded in `[0, ln 2]`):
  `JSD(P‖Q) = ½ KL(P‖M) + ½ KL(Q‖M)`, `M = ½(P+Q)`. Threshold ≈ `0.1`. Stable, no p-value.
- **Wasserstein-1 (earth-mover) distance** for continuous features
  (`scipy.stats.wasserstein_distance`): magnitude of shift in the feature's own units; threshold is
  feature-scale dependent, so normalize first. Mentioned as alternatives; PSI + KS remain the
  defaults because their bands/p-values are well understood.

### 4.6 Multivariate / aggregate drift

Combine the per-feature verdicts into one aggregate `DetectionResult`:

```
drifted_features = [ f for f in features if drifted(f) ]      # PSI>thr OR KS p<alpha
share_drifted    = len(drifted_features) / len(features)
overall_score    = max over f of normalized_drift(f)          # e.g. min(PSI/0.25, 1) per feature
aggregate_breach = share_drifted >= SHARE_THRESHOLD           # default 0.30 (≥30% of features)
```

Default `SHARE_THRESHOLD = 0.30`. The aggregate result carries the full `drifted_features` list and
per-feature scores in `evidence`.

**Optional domain-classifier (lightweight-ML).** Train a classifier to separate reference (label 0)
from current (label 1) rows on the raw features; evaluate via cross-validated AUC:

```
X = vstack(R_sample, C);  y = [0]*nR + [1]*nC
auc = cross_val_score(LogisticRegression()|RandomForest, X, y, scoring="roc_auc", cv=5).mean()
drift = auc >= AUC_THRESHOLD          # AUC ≈ 0.5 → indistinguishable → no drift
```

Default `AUC_THRESHOLD = 0.65` (≥0.65 means the two sets are meaningfully separable → multivariate
drift, including interaction shifts univariate tests miss). Feature importances localize the drift.

### 4.7 Output → `DetectionResult`

Per feature:

```python
DetectionResult(
    detector="drift", signal_type="data_drift_psi",
    score=psi_value, threshold=0.25, breached=(psi_value > 0.25),
    drifted_features=[feature_name],
    severity_hint="MEDIUM" if psi_value > 0.25 else ("LOW" if psi_value > 0.10 else "NONE"),
    evidence={"feature": feature_name, "bins": B, "ref_props": [...], "cur_props": [...],
              "top_bin_contributions": [...], "ks_D": D, "ks_p": p, "n_ref": N_R, "n_cur": N_C},
    timestamp=now_iso(),
)
```

Aggregate:

```python
DetectionResult(
    detector="drift", signal_type="data_drift_aggregate",
    score=share_drifted, threshold=0.30, breached=(share_drifted >= 0.30),
    drifted_features=drifted_features,
    severity_hint="HIGH" if share_drifted >= 0.5 else ("MEDIUM" if share_drifted >= 0.3 else "LOW"),
    evidence={"per_feature_psi": {...}, "per_feature_ks_p": {...},
              "domain_clf_auc": auc, "n_features": len(features)},
    timestamp=now_iso(),
)
```

### 4.8 Pseudocode

```python
def evaluate_data_drift(self, current_inputs) -> list[DetectionResult]:
    if len(current_inputs) < self.n_cur_min:
        return []                                  # accumulate more first
    results, drifted = [], []
    for f in self.features:
        cur = current_inputs[f]
        if self.is_continuous(f):
            psi = self._psi(self.ref_edges[f], self.ref_props[f], cur)   # §4.2
            D, p = ks_2samp(self.ref_values[f], cur)                     # §4.3
            d = (psi > self.PSI_THRESHOLD) or (p < self.alpha)
        else:
            psi = self._psi_categorical(self.ref_freq[f], cur)
            _, p, _, _ = chi2_contingency(self._table(self.ref_freq[f], cur))
            d = (psi > self.PSI_THRESHOLD) or (p < self.alpha)
        if d: drifted.append(f)
        results.append(self._feature_result(f, psi, D, p, d))
    results.append(self._aggregate_result(drifted, len(self.features)))  # §4.6
    return results
```

### 4.9 Strengths / limitations

- **Strengths:** detects input-distribution shift **without labels** (the common, early signal of
  degradation); PSI is industry-standard and explainable per bin; KS/chi-square give principled
  p-values; aggregate + domain-classifier catch multivariate drift.
- **Limitations:** data drift ≠ performance drop (the model may be robust to it) — confirm impact via
  concept drift when labels arrive; PSI is sensitive to bin count and small `n`; many features →
  multiple-testing inflation (§9); needs a representative, frozen reference.

---

## 5. Concept Drift Detection

### 5.1 What it detects

**CONCEPT DRIFT**: the model's predictive quality drops even though inputs may be stable — the
relationship `P(y | x)` changed. Requires **labels**, which arrive with lag (delayed/feedback
labels — see `data_simulation.md`). Evaluated on the subset of recent predictions whose true labels
have now arrived.

### 5.2 Performance-based detection

Maintain a rolling window of recently-labeled predictions (default `W_perf = 200` labeled samples,
minimum `W_min = 50`). Compute the task metric:

- **Classification (anchored task):** rolling **accuracy** and **F1** (`sklearn.metrics`).
- **Regression (if applicable):** rolling **RMSE**.

Compare against a **baseline** captured at deployment / last verified-good state
(`baseline_accuracy`, `baseline_f1`), stored in `config.py`/state.

```
abs_drop = baseline_accuracy - current_accuracy
rel_drop = abs_drop / baseline_accuracy
drift = (abs_drop >= DROP_ABS) or (rel_drop >= DROP_REL)
```

**Defaults:** `DROP_ABS = 0.05` (5 absolute accuracy points), `DROP_REL = 0.10` (10 % relative). Use
the same shape for F1; for RMSE use `current_rmse >= baseline_rmse * (1 + DROP_REL)`.

**Sustained requirement.** To avoid reacting to a single unlucky window, require the drop to hold for
`N_perf_consec = 2` consecutive labeled windows before raising `severity_hint` above `"LOW"` (the
persistence idea of §6 applied to performance).

Optional statistical confirmation: a two-proportion z-test (or McNemar on paired correct/incorrect)
comparing baseline-window vs current-window accuracy, flagging drift when `p < 0.05`. Reported in
`evidence` as supporting evidence, not the primary trigger (operational drop thresholds are simpler
and the problem statement favors clarity over complexity).

### 5.3 Sequential detectors (optional, high level)

For a streaming alternative to fixed windows, mention:

- **DDM (Drift Detection Method):** monitors the online error rate `p_t` and its std `s_t`; warns
  when `p_t + s_t >= p_min + 2·s_min` and signals drift at `>= p_min + 3·s_min`.
- **ADWIN (ADaptive WINdowing):** keeps an adaptive window and drops the older sub-window when the
  means of two adjacent sub-windows differ beyond a Hoeffding bound.

These are noted as upgrades; the default implementation is the windowed performance comparison in
§5.2 because labels are batched and lagged here (not a true high-rate stream).

### 5.4 Output → `DetectionResult`

```python
DetectionResult(
    detector="drift", signal_type="concept_drift_perf",
    score=abs_drop, threshold=DROP_ABS, breached=drift,
    severity_hint="HIGH" if abs_drop >= 0.10 else ("MEDIUM" if drift else "NONE"),
    evidence={"metric": "accuracy", "baseline": baseline_accuracy,
              "current": current_accuracy, "abs_drop": abs_drop, "rel_drop": rel_drop,
              "f1_current": f1, "n_labeled": W_perf, "consec": consec, "ztest_p": p},
    timestamp=now_iso(),
)
```

### 5.5 Concept drift vs data drift — decision matrix

| Inputs drifted? (§4) | Performance dropped? (§5) | Interpretation | Typical hint |
|---|---|---|---|
| No | No | Healthy — no action | NONE |
| **Yes** | No | **Data drift only** — covariate shift the model tolerates; watch, may pre-empt | LOW / MEDIUM |
| No | **Yes** | **Concept drift** — `P(y\|x)` changed; inputs look normal; needs retrain/switch | MEDIUM / HIGH |
| **Yes** | **Yes** | **Combined drift** — strongest signal; input shift is hurting the model | HIGH |

This matrix is computed by the orchestrator from the two drift signals and attached as
`evidence["drift_diagnosis"]` on the aggregate result. The **action** taken from each cell is decided
in `agent_logic.md`, not here.

---

## 6. Combining detectors per tick

### 6.1 Aggregation

Per tick the orchestrator returns a `list[DetectionResult]` — it does **not** collapse them into one
verdict (the decision engine wants the granularity). It does:

1. **Dedup / cap:** collapse identical repeated per-feature non-breaches; cap volume by emitting only
   breaching results plus the aggregates when in "quiet" mode.
2. **Attach persistence counters** (below).
3. **Order by precedence** so the decision engine sees the most actionable first.

### 6.2 Precedence (ordering of hints, not final severity)

```
concept_drift_perf  >  data_drift_aggregate  >  threshold(hard SLA)  >  anomaly(spike)
```

Rationale: a confirmed performance drop is the most consequential signal; multivariate data drift is
next; hard threshold breaches are urgent but often symptomatic; isolated spikes are weakest. This is
only a **hint ordering** — the authoritative severity→action policy lives in `agent_logic.md`.

### 6.3 Noise vs persistent — persistence counters

Every breaching `signal_type` has a counter incremented on breach and reset to 0 on a clean tick
(threshold §2.5, anomaly §3.8, concept §5.2). A signal is **persistent** once its counter reaches the
signal's `N_consec`/`N_sustain`; only then is `severity_hint` allowed above `"LOW"`. Counters live in
the agent's `state` and are surfaced in each result's `evidence`:

```python
def apply_persistence(results, state):
    for r in results:
        key = (r.detector, r.signal_type, tuple(r.drifted_features))
        state.counters[key] = state.counters.get(key, 0) + 1 if r.breached else 0
        r.evidence["persistence_count"] = state.counters[key]
        if not r.breached or state.counters[key] < state.min_persist(key):
            r.severity_hint = "LOW" if r.breached else "NONE"   # cap until persistent
    return results
```

`min_persist` defaults: threshold/anomaly `3`, sustained-shift/concept `2`. Final severity mapping:
see `agent_logic.md`.

---

## 7. Consolidated parameter & default-threshold table

| Group | Parameter | Default | Meaning / where used |
|---|---|---|---|
| Threshold | `error_rate` | `0.10` | static error-rate ceiling (§2.3) |
| Threshold | `avg_latency_ms` | `300` | static avg latency ceiling |
| Threshold | `p95_latency_ms` | `500` | static tail latency ceiling |
| Threshold | `inference_failure_rate` | `0.05` | static failed-predict ceiling |
| Threshold | `confidence_floor` | `0.60` | min mean confidence (lower-worse) |
| Threshold | adaptive `k` | `3.0` | rolling mean ± k·std band (§2.4) |
| Threshold | window `W` | `30` | ticks in adaptive buffer |
| Threshold | `W_min` | `10` | min samples before adaptive trusted |
| Threshold | `N_consec` | `3` | consecutive breaches before escalation |
| Anomaly | window `W` | `50` | rolling history length (§3.2) |
| Anomaly | `W_min` | `12` | min samples before scoring |
| Anomaly | z-score `k_z` | `3.0` | Gaussian z threshold (§3.3) |
| Anomaly | robust `k_r` | `3.5` | median/MAD z threshold (§3.4) |
| Anomaly | IQR `c` | `1.5` | Tukey fence multiplier (§3.5) |
| Anomaly | EWMA `lambda` | `0.3` | smoothing factor (§3.6) |
| Anomaly | EWMA `L` | `3.0` | control-limit width |
| Anomaly | `N_sustain` | `5` | ticks to call a shift "sustained" |
| Anomaly | IForest `contamination` | `0.02` | expected outlier fraction (§3.7) |
| Anomaly | IForest refit `R` | `50` | ticks between refits |
| Data drift | `n_ref` | `>= 1000` | reference sample size (§4.1) |
| Data drift | `n_cur` | `500` | current window size |
| Data drift | `n_cur_min` | `200` | min current rows to test |
| Data drift | PSI bins `B` | `10` | quantile bins from reference (§4.2) |
| Data drift | PSI watch | `0.10` | moderate-drift band lower edge |
| Data drift | `PSI_THRESHOLD` | `0.25` | significant-drift decision |
| Data drift | KS `alpha` | `0.05` | KS / chi-square significance (§4.3–4.4) |
| Data drift | PSI floor `eps` | `1e-4` | zero-proportion floor |
| Data drift | min `E` (chi2) | `5` | merge categories below this |
| Data drift | `SHARE_THRESHOLD` | `0.30` | share of features drifted → aggregate breach (§4.6) |
| Data drift | `AUC_THRESHOLD` | `0.65` | domain-classifier drift cutoff |
| Data drift | JSD threshold | `0.10` | optional alternative (§4.5) |
| Concept drift | `W_perf` | `200` | labeled samples in perf window (§5.2) |
| Concept drift | `W_min` | `50` | min labeled samples |
| Concept drift | `DROP_ABS` | `0.05` | absolute accuracy/F1 drop trigger |
| Concept drift | `DROP_REL` | `0.10` | relative drop trigger |
| Concept drift | `N_perf_consec` | `2` | consecutive bad windows before escalation |
| Concept drift | perf z-test `alpha` | `0.05` | optional statistical confirmation |
| Combine | `min_persist` (thr/anom) | `3` | persistence before hint > low (§6.3) |
| Combine | `min_persist` (sustained/concept) | `2` | persistence before hint > low |

All values are defaults; override in `control-plane/agent_core/config.py` per model and SLA.
Canonical schemas/enums/defaults live in `conventions.md` (authoritative).

---

## 8. Worked numeric examples

### 8.1 PSI on a 10-bin continuous feature

Bin edges fixed from the reference quantiles (each reference bin ≈ 10 %). Observed counts:
`N_R = 1000`, `N_C = 500`.

| Bin | ref count | cur count | `ref_i` | `cur_i` | `cur-ref` | `ln(cur/ref)` | contribution |
|---|---|---|---|---|---|---|---|
| 1 | 100 | 30  | 0.100 | 0.060 | -0.040 | -0.5108 | 0.02043 |
| 2 | 100 | 40  | 0.100 | 0.080 | -0.020 | -0.2231 | 0.00446 |
| 3 | 100 | 55  | 0.100 | 0.110 | +0.010 | +0.0953 | 0.00095 |
| 4 | 100 | 60  | 0.100 | 0.120 | +0.020 | +0.1823 | 0.00365 |
| 5 | 100 | 70  | 0.100 | 0.140 | +0.040 | +0.3365 | 0.01346 |
| 6 | 100 | 65  | 0.100 | 0.130 | +0.030 | +0.2624 | 0.00787 |
| 7 | 100 | 55  | 0.100 | 0.110 | +0.010 | +0.0953 | 0.00095 |
| 8 | 100 | 50  | 0.100 | 0.100 |  0.000 |  0.0000 | 0.00000 |
| 9 | 100 | 40  | 0.100 | 0.080 | -0.020 | -0.2231 | 0.00446 |
| 10| 100 | 35  | 0.100 | 0.070 | -0.030 | -0.3567 | 0.01070 |

`PSI = Σ contributions = 0.02043 + 0.00446 + 0.00095 + 0.00365 + 0.01346 + 0.00787 + 0.00095 + 0 +
0.00446 + 0.01070 ≈ 0.0669`.

**Interpretation:** `PSI ≈ 0.067 < 0.10` → **no significant drift**. `breached = False`,
`severity_hint = "NONE"`. The largest single contributor is bin 1 (mass leaving the lowest bin) —
recorded in `evidence["top_bin_contributions"]`. (If the same shape were exaggerated until
`PSI > 0.25`, the feature would flag as significant drift.)

### 8.2 KS two-sample example

Reference is standard normal; current shifted right by ~0.4σ. With `n_ref = 1000`, `n_cur = 500`,
`scipy.stats.ks_2samp` returns e.g. `D = 0.18`, `p_value = 1.2e-7`.

```
p_value (1.2e-7) < alpha (0.05)  →  drift = True
```

`DetectionResult(detector="drift", signal_type="data_drift_ks", score=0.18, threshold=0.05,
breached=True, ...)` — note `threshold` here records `alpha` and `score` reports `D`; the breach
decision is `p_value < alpha`, with both `D` and `p_value` in `evidence`. The KS `D = 0.18` means the
empirical CDFs differ by at most 18 percentage points — a clear, statistically significant location
shift. As a sanity check, the asymptotic critical value
`D_crit ≈ 1.36·sqrt((n_ref+n_cur)/(n_ref·n_cur)) = 1.36·sqrt(1500/500000) ≈ 0.0745`; observed
`D = 0.18 > 0.0745`, consistent with rejecting the null.

---

## 9. Robustness notes

- **Small samples.** Enforce minimums (`n_cur_min = 200` for data drift, `W_min` for anomaly,
  `W_perf` min `50` for concept). Below these, *hold* (accumulate) rather than emit a noisy verdict.
  PSI and chi-square are unreliable on tiny bins; KS exact p-values degrade for very small `n`.
- **NaNs / missing values.** Drop NaNs per feature before testing **and** track the *missingness
  rate* itself as a metric — a jump in NaNs is its own anomaly/data-quality signal (route it through
  the anomaly detector). Never silently impute inside a drift test, as that masks distribution change.
- **Bin edges.** Build PSI edges from the **reference** quantiles only, deduplicate identical edges
  (low-cardinality / spiky features can collapse bins — fall back to fewer bins or unique values),
  and extend outer edges to `±inf` so out-of-range live values are captured rather than dropped.
  Apply the `eps = 1e-4` floor to avoid `log(0)` / divide-by-zero.
- **Multiple testing across features.** With many features and `alpha = 0.05`, ~5 % of *stable*
  features will spuriously flag. Mitigations: (a) require the **aggregate** `SHARE_THRESHOLD` before
  declaring system-level data drift; (b) apply a Benjamini–Hochberg FDR correction to the per-feature
  KS/chi-square p-values; (c) prefer effect-size (PSI magnitude) over p-value alone for ranking. Per-
  feature results are still emitted for diagnosis, but escalation relies on the corrected aggregate.
- **Zero-variance / constant metrics.** Guard `sigma == 0`, `MAD == 0`, `IQR == 0` → treat as
  no-anomaly (`score = 0`) rather than dividing by zero.
- **Baseline integrity (concept drift).** Freeze the performance baseline at the last verified-good
  state; refresh it only after a successful recovery is confirmed by `verification/` — otherwise the
  baseline silently tracks degradation and concept drift becomes undetectable.

---

*Related docs:* [`architecture.md`](./architecture.md) · [`data_simulation.md`](./data_simulation.md)
· [`agent_logic.md`](./agent_logic.md) · [`api_contracts.md`](./api_contracts.md) ·
[`failure_scenarios.md`](./failure_scenarios.md)
