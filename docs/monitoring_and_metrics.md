# Monitoring & Metrics Reference

> The observability backbone of the **Autonomous ML Monitoring & Auto-Recovery Agent**.
> This document defines **what every metric is**, **where it is computed**, **how often it is
> collected**, **how it is aggregated**, and **which downstream component consumes it**.

This file is the single source of truth for metric **definitions and collection**. It deliberately
does **not** define detection algorithms (see [`detection_methods.md`](./detection_methods.md)), the
decision/recovery policy (see [`agent_logic.md`](./agent_logic.md)), the HTTP request/response
shapes (see [`api_contracts.md`](./api_contracts.md)), the database table DDL (see
[`data_model.md`](./data_model.md)), the dashboard layout (see [`dashboard.md`](./dashboard.md)), or
the synthetic data / delayed-label mechanics (see [`data_simulation.md`](./data_simulation.md)).
Where those topics touch metrics, they are **cross-referenced by name**.

---

## Table of contents

1. [Monitoring philosophy](#1-monitoring-philosophy)
2. [Metric catalogue](#2-metric-catalogue)
3. [Collection architecture](#3-collection-architecture)
4. [Windowing & aggregation](#4-windowing--aggregation)
5. [The `MetricSnapshot` schema](#5-the-metricsnapshot-schema)
6. [Baseline capture & health status](#6-baseline-capture--health-status)
7. [Retention, sampling & storage](#7-retention-sampling--storage)
8. [How metrics power the closed loop](#8-how-metrics-power-the-closed-loop)
9. [Defaults reference table](#9-defaults-reference-table)

---

## 1. Monitoring philosophy

### 1.1 Why monitor an ML model at all

A traditional web service fails **loudly**: a process crashes, a 500 is returned, a health check
goes red. An ML model usually fails **silently**. The HTTP endpoint stays up, latency looks fine,
every request returns a `200 OK` with a confident-looking number — and yet the predictions are
**wrong**. Causes include:

- **Concept drift / data drift** — the live feature distribution moves away from the training
  distribution, so the decision boundary the model learned is no longer correct.
- **Upstream data-quality decay** — missing values, corrupted rows, out-of-range inputs, a renamed
  or dropped column (schema violation).
- **Operational degradation** — the inference path slows down, the model container starts throwing
  intermittent errors, or the data feed stops arriving (staleness).

None of these necessarily move the obvious signals (HTTP status, uptime). The whole point of this
system is to make the *silent* failure *observable*, then act on it autonomously.

### 1.2 What "Observe" means in the loop

The agent runs a closed control loop: **Observe → Detect → Decide → Act → Verify**
(see [`agent_logic.md`](./agent_logic.md)). **Observe** is the first phase and is what this document
covers. On every loop iteration (a **tick**), the OBSERVE phase:

1. Probes each model service's `GET /health` and `GET /metrics` (via `monitoring/model_probe.py`).
2. Sends a batch of inputs to `POST /predict` and records the outcomes — prediction, confidence,
   latency, success/failure (via `monitoring/prediction_probe.py`).
3. Loads the corresponding input rows from CSV and (when available) the **delayed ground-truth
   labels** for *previous* predictions (via `monitoring/data_loader.py`).
4. Computes the **performance** and **data-quality** metric families that require ground truth or
   raw inputs (these cannot be computed inside the model service).
5. Assembles everything into a single immutable **`MetricSnapshot`** object (Section 5),
   timestamps it, and emits it.

Observe produces **exactly one `MetricSnapshot` per tick**. Everything downstream — detection,
decision, verification, dashboard — consumes snapshots. Observe never decides and never acts; it
only measures.

### 1.3 The three metric families

The problem statement groups all signals into three families. Each answers a different question and
each is necessary because no single family catches every silent failure.

| Family | Question it answers | Example failure it catches | Primary owner |
|---|---|---|---|
| **Model performance** | "Are the predictions still *correct*?" | Concept drift slowly destroys accuracy while latency stays flat. | Agent (needs delayed labels) |
| **Data quality** | "Is the *input* still valid and in-distribution?" | A feature pipeline starts emitting nulls / out-of-range values; PSI spikes. | Agent (needs raw inputs) |
| **System signals** | "Is the service *operationally* healthy?" | Container slows down, starts erroring, or the data feed goes stale. | Model service (self-tracked) + Agent |

The families are **complementary and ordered by latency-to-truth**:

- **System signals** are the *fastest* to observe (computed in-process, available every tick) but
  the *shallowest* — a fast, error-free service can still be confidently wrong.
- **Data-quality signals** are a *leading indicator* — drift in the inputs typically precedes the
  drop in accuracy, so they give the agent an early warning before labels confirm the damage.
- **Performance signals** are the *ground truth* of correctness but the *slowest* to confirm,
  because labels arrive with a **lag** (delayed-label simulation; see
  [`data_simulation.md`](./data_simulation.md)). The agent must not wait idly for them.

This is why the agent watches all three: data-quality drift raises an early flag, system signals
catch operational faults instantly, and delayed performance metrics provide the eventual
confirmation used by the VERIFY phase.

---

## 2. Metric catalogue

This is the centerpiece. Every metric the system tracks is listed below with: **name**, **family**,
**definition / formula**, **unit**, **where it is computed**, **collection cadence**, and the
**detector that consumes it** (detectors are defined in
[`detection_methods.md`](./detection_methods.md): `threshold_detector`, `anomaly_detector`,
`drift_detector`).

**Conventions used in the formulas**

- `W` = the rolling window (a sliding set of the most recent observations); window sizes are in
  [Section 4](#4-windowing--aggregation) and [Section 9](#9-defaults-reference-table).
- "Per tick" = computed once per agent loop iteration. "Per request" = updated inside the model
  service on every `/predict` call, then *exposed* (read) per tick.
- `n` = number of `/predict` requests in the window. `TP/FP/TN/FN` = confusion-matrix counts over
  the window of *labelled* predictions.

### 2.1 System signals

These describe operational health of the inference path and the data feed. Most are **self-tracked
inside the model service** (`metrics.py`) as in-process rolling counters and **exposed at
`GET /metrics`**; the agent reads them each tick. A few (data arrival delay/staleness) are
inherently agent-side.

| Metric | Definition / formula | Unit | Computed in | Cadence | Consumed by |
|---|---|---|---|---|---|
| `request_count` | Monotonic count of `/predict` requests handled (total and within `W`). | count | model service `metrics.py` | per request; read per tick | threshold |
| `error_count` | Count of `/predict` requests that returned an error (5xx, exception, timeout) within `W`. | count | model service `metrics.py` | per request; read per tick | threshold |
| `error_rate` | `error_rate = error_count / request_count` over window `W`. Defined as `0.0` when `request_count == 0`. | ratio `[0,1]` | model service (raw); agent re-derives over its own probe window | per tick | threshold, anomaly |
| `inference_failure_rate` | Failure rate **as observed by the agent's own probes**: `failed_predict_calls / attempted_predict_calls` over the agent probe window. Distinct from the service's internal `error_rate` because it also captures connection refused / timeouts where the service never responded. | ratio `[0,1]` | agent `prediction_probe.py` | per tick | threshold, anomaly |
| `avg_latency_ms` | Mean wall-clock time to serve `/predict`: `mean(latency_i for i in W)`. | milliseconds | model service `metrics.py` (and cross-checked by agent probe timing) | per request; read per tick | threshold, anomaly |
| `p50_latency_ms` | 50th percentile (median) of per-request latencies in `W`. | milliseconds | model service `metrics.py` | per request; read per tick | threshold |
| `p95_latency_ms` | 95th percentile of per-request latencies in `W`. Computed by sorting the window samples ascending and taking the value at index `ceil(0.95 * n) - 1` (nearest-rank method). | milliseconds | model service `metrics.py` | per request; read per tick | threshold, anomaly |
| `p99_latency_ms` | 99th percentile of per-request latencies in `W` (nearest-rank, `ceil(0.99 * n) - 1`). | milliseconds | model service `metrics.py` | per request; read per tick | threshold |
| `uptime_seconds` | Seconds since the model service process started: `now - process_start_time`. | seconds | model service `metrics.py` | per tick (read) | threshold (health) |
| `service_up` | Boolean from `GET /health`: `true` if health returns `200 OK` within timeout, else `false`. | bool | agent `model_probe.py` | per tick | threshold |
| `data_arrival_delay_ms` | Time between when an input record was expected/produced and when the agent actually received/loaded it: `load_time - record_expected_time`. | milliseconds | agent `data_loader.py` | per tick | threshold |
| `data_staleness_seconds` | Age of the freshest input the agent has: `now - latest_input_timestamp`. High staleness ⇒ the feed has stopped. | seconds | agent `data_loader.py` | per tick | threshold, anomaly |

> Nearest-rank is chosen for percentiles because it is exact, deterministic, requires no
> interpolation, and is trivial to implement over an in-memory ring buffer of recent latencies.
> See [Section 9](#9-defaults-reference-table) for the configured percentile method.

### 2.2 Model performance

These quantify **correctness**. They depend on **ground-truth labels**, which arrive with a lag, so
they are **owned by the agent** (Section 3.2). Confidence-based metrics need no labels and so can be
computed every tick as a leading indicator.

| Metric | Definition / formula | Unit | Computed in | Cadence | Consumed by |
|---|---|---|---|---|---|
| `rolling_accuracy` | `accuracy = (TP + TN) / (TP + TN + FP + FN)` over the window of **labelled** predictions in `W`. `null` until enough labels have arrived. | ratio `[0,1]` | agent | per tick (over labelled subset) | threshold, anomaly |
| `precision` | `precision = TP / (TP + FP)`. `null` if `TP + FP == 0`. | ratio `[0,1]` | agent | per tick | threshold |
| `recall` | `recall = TP / (TP + FN)`. `null` if `TP + FN == 0`. | ratio `[0,1]` | agent | per tick | threshold |
| `f1_score` | `F1 = 2 * (precision * recall) / (precision + recall)`. `null` if `precision + recall == 0`. The primary correctness KPI for the classification task. | ratio `[0,1]` | agent | per tick | threshold, anomaly |
| `rmse` | **Regression case only:** `RMSE = sqrt( (1/m) * Σ (y_pred_i − y_true_i)^2 )` over the `m` labelled predictions in `W`. `null` for the classification task. | same unit as target | agent | per tick | threshold, anomaly |
| `avg_confidence` | `mean(confidence_i for i in W)`, where `confidence` is the value returned by `/predict` (e.g. max class probability). No labels needed ⇒ available immediately. | ratio `[0,1]` | agent (from `/predict` outputs) | per tick | threshold, anomaly, drift |
| `min_confidence` | `min(confidence_i for i in W)`. | ratio `[0,1]` | agent | per tick | threshold |
| `confidence_distribution` | Histogram of confidences in `W` over fixed bins (e.g. 10 bins of width 0.1): `[c_0, …, c_9]`. Used to spot a shift toward low-confidence predictions. | counts per bin | agent | per tick | drift, anomaly |
| `confidence_drop` | Relative drop versus baseline: `confidence_drop = (baseline_avg_confidence − avg_confidence) / baseline_avg_confidence`. Positive ⇒ confidence has fallen. | ratio | agent | per tick | threshold, anomaly |
| `pred_class_distribution` | Fraction of predictions per class over `W`. For binary: `{0: n0/n, 1: n1/n}`. A sudden collapse to one class is a silent-failure tell. | fractions summing to 1 | agent | per tick | drift, anomaly |
| `prediction_count` | Number of predictions made by the agent's probe in this tick / window. | count | agent | per tick | (context) |
| `labelled_count` | Number of predictions in `W` for which a ground-truth label has now arrived (denominator for accuracy/F1). | count | agent | per tick | (context) |

### 2.3 Data quality

These describe the **inputs** before/while they are scored. Computed by the agent from the raw CSV
inputs (`data_loader.py`) plus a stored **reference (training) distribution**. They are the system's
**leading indicator** of drift.

| Metric | Definition / formula | Unit | Computed in | Cadence | Consumed by |
|---|---|---|---|---|---|
| `missing_value_rate` | `missing_cells / total_cells` across the input batch in `W`. Counts nulls/NaN/empty. | ratio `[0,1]` | agent `data_loader.py` | per tick | threshold |
| `out_of_range_rate` | Fraction of feature values falling outside the `[min, max]` (or allowed-set) bounds learned from the reference data: `out_of_range_cells / total_numeric_cells`. | ratio `[0,1]` | agent | per tick | threshold |
| `schema_violation_count` | Number of structural violations in the batch: missing/extra columns, wrong dtype, unparseable rows. | count | agent | per tick | threshold |
| `feature_drift_psi` | **Per-feature** Population Stability Index versus the reference distribution: `PSI_f = Σ_b ( (a_b − e_b) * ln(a_b / e_b) )`, where for bin `b`, `e_b` = expected (reference) proportion and `a_b` = actual (live) proportion; bins are the reference deciles. Emitted as a map `{feature: psi}`. Rule of thumb: `<0.1` stable, `0.1–0.25` moderate shift, `>0.25` significant shift. | unitless PSI | agent | per tick | drift |
| `max_feature_drift_psi` | `max(feature_drift_psi.values())` — the single most-drifted feature. | unitless PSI | agent | per tick | drift, threshold |
| `drifted_feature_share` | Share of features whose PSI exceeds the drift threshold (default `0.25`): `count(psi_f > τ) / num_features`. | ratio `[0,1]` | agent | per tick | drift |
| `data_quality_score` | Convenience composite in `[0,1]`, `1.0 = pristine`: `1 − clip(missing_value_rate + out_of_range_rate + drifted_feature_share, 0, 1)`. Used for the dashboard gauge; detectors should prefer the underlying raw metrics. | ratio `[0,1]` | agent | per tick | (dashboard) |

> The reference/training distribution (bin edges and proportions per feature, plus `[min,max]`
> bounds) is captured once during **baseline capture** (Section 6) and persisted so PSI and
> out-of-range checks are reproducible across ticks and across restarts.

---

## 3. Collection architecture

Collection is split deliberately between the model service (which can only see *its own* request
stream) and the agent (which can see ground truth, raw inputs, and *both* models at once).

### 3.1 What the model service self-tracks (`metrics.py`)

Each model service (`model_a` :8001, `model_b` :8002) keeps **lightweight in-process rolling
counters** in `metrics.py`. There is no external metrics database inside the service — just memory:

- **Ring buffers / counters** updated on every `/predict` call: request count, error count, and a
  bounded buffer of recent per-request latencies (for percentiles) and recent confidences.
- A process start timestamp for `uptime_seconds`.
- It computes the **system-signal** metrics it can see locally (`request_count`, `error_count`,
  `error_rate`, `avg/p50/p95/p99 latency`, `uptime`) and serves them as JSON at **`GET /metrics`**.
- **`GET /health`** returns a cheap liveness/readiness check (process up, model loaded).
- **`POST /predict`** returns the `prediction` and a `confidence` for each input.

The service **cannot** compute accuracy/F1/RMSE (it has no labels) nor drift/data-quality (it does
not retain the reference distribution and only sees one request at a time). Those belong to the
agent.

### 3.2 What the agent computes (`agent_core/monitoring/`)

The OBSERVE phase modules:

- **`model_probe.py`** — calls `GET /health` (→ `service_up`) and `GET /metrics` (→ ingests all the
  system signals the service self-tracked). Also independently times responses to cross-check
  latency and to compute agent-observed `inference_failure_rate`.
- **`prediction_probe.py`** — calls `POST /predict` with input rows, records each outcome
  `(prediction, confidence, latency, success/fail, input_id, tick_ts)` into the agent's own rolling
  store. From this it derives the **confidence** family and `pred_class_distribution` immediately
  (no labels needed).
- **`data_loader.py`** — loads CSV inputs, computes **data-quality** metrics (missing/out-of-range/
  schema) and **PSI drift** against the stored reference distribution, and measures
  `data_arrival_delay`/`staleness`.
- **Delayed-label join** — when ground-truth labels for *earlier* predictions become available
  (per the delayed-label simulation), the agent joins them back to the stored outcomes by
  `input_id` and computes/updates `rolling_accuracy`, `precision`, `recall`, `f1_score`, `rmse`.

The agent then assembles a single **`MetricSnapshot`** (Section 5), timestamps it, and **`POST`s it
to Django `/api/metrics`** for durable storage. The Django backend stores it (`monitoring_app`) and
the `dashboard_app` reads it back for visualization.

### 3.3 End-to-end flow diagram

```mermaid
sequenceDiagram
    autonumber
    participant CSV as Input CSV (data_loader)
    participant MS as Model Service (8001/8002)
    participant AG as Agent OBSERVE (monitoring/)
    participant DET as DETECT (detection/)
    participant DJ as Django backend (8000)
    participant DASH as Dashboard (dashboard_app)

    Note over AG: One tick begins
    AG->>MS: GET /health
    MS-->>AG: 200 {status} (service_up)
    AG->>MS: GET /metrics
    MS-->>AG: {request_count, error_rate, p50/p95/p99, avg_latency, uptime}
    AG->>CSV: load input batch
    CSV-->>AG: rows (+ expected timestamps)
    AG->>MS: POST /predict (batch)
    MS-->>AG: [{prediction, confidence}, ...] (+ measured latency)
    Note over AG: compute data-quality + PSI drift (vs reference)
    Note over AG: compute confidence/class-dist (no labels yet)
    AG->>AG: join newly-arrived DELAYED labels by input_id
    Note over AG: compute rolling_accuracy / F1 / RMSE on labelled subset
    AG->>AG: assemble MetricSnapshot, set health_status, timestamp
    AG->>DET: feed MetricSnapshot (Observe → Detect)
    AG->>DJ: POST /api/metrics (MetricSnapshot)
    DJ-->>AG: 201 Created
    DASH->>DJ: GET /api/metrics (poll)
    DJ-->>DASH: snapshots (render charts/gauges)
    Note over AG: tick ends; sleep until next interval
```

---

## 4. Windowing & aggregation

### 4.1 Rolling (sliding) windows vs fixed batches

- **Sliding windows** are used for all noisy/streaming signals (latency, error rate, confidence,
  accuracy). A sliding window of size `W` always holds the **most recent `W` observations**; each
  new observation evicts the oldest. This smooths transient spikes and produces a stable signal that
  detectors can threshold against without flapping.
- **Fixed (tumbling) batches** are used for the per-tick input batch that the agent scores: each
  tick reads a fresh batch of `N` rows from the CSV, scores them, and folds the per-request results
  into the sliding windows. The batch is the *unit of ingestion*; the window is the *unit of
  aggregation*.

Two distinct windows exist and should not be conflated:

| Window | Where | Default size | Used for |
|---|---|---|---|
| Service request window | model service `metrics.py` | last **200** requests | latency percentiles, in-process error_rate |
| Agent observation window | agent monitoring store | last **10** ticks (`ROLLING_WINDOW_SIZE`, ≈5 min; or last **500** predictions, whichever the config sets) | rolling accuracy/F1, confidence trend, drift trend |

### 4.2 Window sizing rationale

- Too small → noisy, the detectors flap (false positives).
- Too large → sluggish, a real degradation is diluted and detected late.
- Latency uses a **request-count window** (200) so percentiles are statistically meaningful.
- Accuracy/F1 uses a window of **labelled** predictions; because labels are delayed, the *effective*
  window grows only as labels arrive. The metric stays `null` until `labelled_count >=
  min_labels_for_accuracy` (default `30`) to avoid reporting accuracy off a handful of samples.

### 4.3 Snapshot timestamping

Each `MetricSnapshot` carries:

- `tick_id` — monotonically increasing integer per agent run.
- `timestamp` — UTC ISO-8601 instant at which the snapshot was assembled (`captured_at`).
- `window_start` / `window_end` — the time bounds of the observation window the metrics summarize.

Timestamps are assigned by the agent at assembly time (single clock), so all metrics within one
snapshot share a consistent time reference even though their underlying samples were collected at
slightly different moments within the tick.

### 4.4 Handling delayed labels when computing accuracy

Because labels arrive with a lag (see [`data_simulation.md`](./data_simulation.md)):

1. Every prediction is stored with its `input_id`, `tick_id`, `prediction`, `confidence`, and
   `label = null`.
2. On later ticks, as labels surface, the agent **joins them back by `input_id`** and fills in the
   `label`.
3. `rolling_accuracy`/`f1_score`/`rmse` are computed **only over predictions that now have a
   non-null label**, within the observation window.
4. The snapshot records both `labelled_count` (denominator) and `prediction_count` so consumers can
   see how mature the correctness estimate is.
5. Confidence-family and data-quality/drift metrics are **never** delayed — they are computed from
   inputs/outputs alone and so act as the early-warning signal while accuracy "catches up".

This decoupling is essential: the agent **must not block** waiting for labels; it acts on leading
indicators (drift, confidence drop, system signals) and uses the delayed accuracy to **confirm**
during the VERIFY phase.

---

## 5. The `MetricSnapshot` schema

The single object the agent emits each tick. It is the payload of `POST /api/metrics` (see
[`api_contracts.md`](./api_contracts.md)), the stored row family in `monitoring_app` (see
[`data_model.md`](./data_model.md)), and the input to all detectors (see
[`detection_methods.md`](./detection_methods.md)). All three **must** stay aligned with this table.

> **Note:** Canonical schema/enums in `conventions.md` (authoritative). The core
> wire fields and enum casing below conform to `conventions.md §2`; the additional
> rows here (e.g. `pred_class_distribution`, `feature_drift_psi`, percentile
> latencies) are documentation extensions carried in the snapshot's `raw` JSON.

| Field | Type | Nullable | Description |
|---|---|---|---|
| `snapshot_id` | string (uuid) | no | Unique id for this snapshot. |
| `tick_id` | int | no | Monotonic tick counter for the agent run. |
| `timestamp` / `captured_at` | string (ISO-8601 UTC) | no | When the snapshot was assembled. |
| `window_start` | string (ISO-8601 UTC) | no | Start of the observation window. |
| `window_end` | string (ISO-8601 UTC) | no | End of the observation window. |
| `window_seconds` | int | no | Length of the rolling window in seconds (default `300`; see `conventions.md §2`). |
| `model_name` | string | no | Logical model being observed (e.g. `model_a`). |
| `model_version` | string | yes | Active model version, if known. |
| `endpoint` | string | no | Base URL of the probed service (e.g. `http://model_a:8001`). |
| **System signals** | | | |
| `service_up` | bool | no | Health check passed. |
| `request_count` | int | no | Requests in window. |
| `error_count` | int | no | Errors in window. |
| `error_rate` | float `[0,1]` | no | `error_count/request_count`. |
| `inference_failure_rate` | float `[0,1]` | no | Agent-observed predict failure rate. |
| `avg_latency_ms` | float | no | Mean latency. |
| `p50_latency_ms` | float | no | Median latency. |
| `p95_latency_ms` | float | no | 95th percentile latency. |
| `p99_latency_ms` | float | no | 99th percentile latency. |
| `uptime_seconds` | float | no | Service uptime. |
| `data_arrival_delay_ms` | float | yes | Input arrival delay. |
| `data_staleness_seconds` | float | yes | Age of freshest input. |
| **Performance** | | | |
| `rolling_accuracy` | float `[0,1]` | yes | Null until enough labels. |
| `precision` | float `[0,1]` | yes | |
| `recall` | float `[0,1]` | yes | |
| `f1_score` | float `[0,1]` | yes | Primary correctness KPI (classification). |
| `rmse` | float | yes | Regression case only; else null. |
| `avg_confidence` | float `[0,1]` | no | |
| `min_confidence` | float `[0,1]` | no | |
| `confidence_distribution` | list[int] (10 bins) | no | Histogram counts. |
| `confidence_drop` | float | yes | Relative drop vs baseline. |
| `pred_class_distribution` | object{class:float} | no | Fractions per class. |
| `prediction_count` | int | no | Predictions in window. |
| `labelled_count` | int | no | Of those, how many are labelled. |
| **Data quality** | | | |
| `missing_value_rate` | float `[0,1]` | no | |
| `out_of_range_rate` | float `[0,1]` | no | |
| `schema_violation_count` | int | no | |
| `feature_drift_psi` | object{feature:float} | no | Per-feature PSI map. |
| `max_feature_drift_psi` | float | no | |
| `drifted_feature_share` | float `[0,1]` | no | |
| `data_quality_score` | float `[0,1]` | no | Composite gauge value. |
| **Derived health** | | | |
| `health_status` | enum | no | `HEALTHY` / `DEGRADED` / `CRITICAL` / `UNKNOWN` (Section 6.2). |
| `baseline_ref` | string | yes | Id of the baseline this snapshot is compared against. |

Example payload:

```json
{
  "snapshot_id": "b1f2…",
  "tick_id": 142,
  "timestamp": "2026-05-30T10:15:03Z",
  "window_start": "2026-05-30T10:10:00Z",
  "window_end": "2026-05-30T10:15:00Z",
  "window_seconds": 300,
  "model_name": "model_a",
  "model_version": "1.3.0",
  "endpoint": "http://model_a:8001",
  "service_up": true,
  "request_count": 200, "error_count": 3, "error_rate": 0.015,
  "inference_failure_rate": 0.01,
  "avg_latency_ms": 42.7, "p50_latency_ms": 38.0,
  "p95_latency_ms": 88.0, "p99_latency_ms": 130.0,
  "uptime_seconds": 86400, "data_arrival_delay_ms": 120, "data_staleness_seconds": 4,
  "rolling_accuracy": 0.81, "precision": 0.79, "recall": 0.83, "f1_score": 0.81,
  "rmse": null, "avg_confidence": 0.74, "min_confidence": 0.51,
  "confidence_distribution": [1,0,2,3,5,12,40,80,45,12],
  "confidence_drop": 0.12,
  "pred_class_distribution": {"0": 0.46, "1": 0.54},
  "prediction_count": 500, "labelled_count": 320,
  "missing_value_rate": 0.004, "out_of_range_rate": 0.0,
  "schema_violation_count": 0,
  "feature_drift_psi": {"f1": 0.31, "f2": 0.08, "f3": 0.12},
  "max_feature_drift_psi": 0.31, "drifted_feature_share": 0.33,
  "data_quality_score": 0.66,
  "health_status": "DEGRADED",
  "baseline_ref": "baseline-2026-05-01"
}
```

---

## 6. Baseline capture & health status

### 6.1 Baseline capture (for the VERIFY phase)

A **baseline** is a frozen reference of "what healthy looks like", captured **before** any failure or
recovery action. It is what the VERIFY phase (see [`agent_logic.md`](./agent_logic.md) and
`verification/health_check.py`) compares post-action metrics against to decide whether a recovery
(e.g. switching from `model_a` to `model_b`) actually helped.

A baseline contains:

- The **reference feature distribution** (per-feature decile bin edges + proportions, and
  `[min,max]` bounds) used for PSI and out-of-range checks.
- **Baseline performance**: `baseline_accuracy`, `baseline_f1`, `baseline_avg_confidence`
  (and `baseline_rmse` for regression).
- **Baseline system signals**: `baseline_p95_latency_ms`, `baseline_error_rate`.
- Metadata: `baseline_id`, `model_name`, `model_version`, `captured_at`.

**When captured:** at first healthy startup, and re-captured (on demand) after a deployment that has
been confirmed healthy. It is **persisted via Django** so it survives agent restarts and is available
to the VERIFY phase. `MetricSnapshot.baseline_ref` records which baseline each snapshot is judged
against. `confidence_drop` and the VERIFY deltas are all computed relative to this baseline.

### 6.2 Deriving overall `health_status`

Each snapshot carries a single rolled-up `health_status`. It is a **summary for humans and the
dashboard**; the authoritative trigger logic lives in the detectors/decision engine. The status is
the **worst** category implied by any family (fail-stop priority):

```
UNKNOWN   if service_up == false, OR insufficient data (e.g. request_count == 0)
CRITICAL  if any "critical" condition holds:
            error_rate >= ERR_CRIT (e.g. 0.20)
            OR p95_latency_ms >= LAT_CRIT (e.g. 1000 ms)
            OR (rolling_accuracy is not null AND rolling_accuracy <= ACC_CRIT, e.g. 0.60)
            OR max_feature_drift_psi >= PSI_CRIT (e.g. 0.5)
DEGRADED  else if any "warning" condition holds:
            error_rate >= ERR_WARN (e.g. 0.05)
            OR p95_latency_ms >= LAT_WARN (e.g. 500 ms)
            OR (rolling_accuracy not null AND rolling_accuracy <= ACC_WARN, e.g. 0.75)
            OR confidence_drop >= CONF_DROP_WARN (e.g. 0.15)
            OR drifted_feature_share >= DRIFT_SHARE_WARN (e.g. 0.25)
            OR missing_value_rate >= MISSING_WARN (e.g. 0.02)
HEALTHY   otherwise
```

The exact thresholds are configuration, owned by [`detection_methods.md`](./detection_methods.md) /
`config.py`; the values above are illustrative defaults. `UNKNOWN` is important: it prevents the
agent from treating "I cannot measure" as "everything is fine".

---

## 7. Retention, sampling & storage

Kept deliberately simple — no time-series database is introduced.

- **Storage**: snapshots are persisted by Django (`monitoring_app`) into the configured relational
  DB — **SQLite** in local/dev, **Postgres** in compose/prod. Each `POST /api/metrics` writes one
  snapshot row (plus child rows for the PSI/confidence maps as defined in
  [`data_model.md`](./data_model.md)).
- **Sampling**: the agent stores **every** snapshot (one per tick); there is no probabilistic
  sampling. Within the model service, latency percentiles are computed over a **bounded ring buffer**
  (last 200 requests) so memory is constant regardless of total traffic.
- **Retention**: snapshots are retained for a rolling window (default **14 days**) by a periodic
  cleanup task; baselines are retained **indefinitely** (they are small and needed for VERIFY).
  Recovery **actions** are stored separately and indefinitely in `actions_app` for audit.
- **Cardinality**: per-feature PSI is stored as a small JSON map keyed by feature name; with a
  modest tabular feature count this stays well-bounded.
- **In-memory vs durable**: model-service counters are purely in-memory and reset on restart (which
  is fine — they are operational, not historical); only the agent-assembled snapshots are durable.

---

## 8. How metrics power the closed loop

Metrics are not collected for their own sake — every metric exists to drive one of the loop phases.

- **Detection** ([`detection_methods.md`](./detection_methods.md)): the `MetricSnapshot` is fed to
  `threshold_detector` (static thresholds on latency/error_rate/accuracy/missing/out-of-range),
  `anomaly_detector` (statistical deviation of latency/error/accuracy/confidence from rolling
  norms), and `drift_detector` (PSI / `drifted_feature_share` / `pred_class_distribution` shift).
  The "Consumed by" column in [Section 2](#2-metric-catalogue) is the explicit wiring.
- **Decision** ([`agent_logic.md`](./agent_logic.md)): detector outputs are classified into a
  severity (`severity_classifier.py`) and mapped to an action by `policy_rules.py` — e.g. a
  CRITICAL accuracy/drift combination → `switch_to_backup`; a DEGRADED system signal → `alert`; nothing
  actionable → `no_op`.
- **Verification** ([`agent_logic.md`](./agent_logic.md), `verification/`): after an action, fresh
  snapshots are compared against the **baseline** (Section 6) to confirm recovery; if metrics do not
  recover, `rollback_guard.py` reverses the action.
- **Dashboard** ([`dashboard.md`](./dashboard.md)): `dashboard_app` reads stored snapshots from
  `/api/metrics` to render latency/error/accuracy/confidence time-series, the PSI drift heatmap, the
  `data_quality_score` gauge, and the colour-coded `health_status` banner.

---

## 9. Defaults reference table

All values are configuration (see `agent_core/config.py` and the model-service `metrics.py`); these
are the shipped defaults. Detection thresholds are owned by
[`detection_methods.md`](./detection_methods.md) and listed here only for orientation.

| Setting | Default | Notes |
|---|---|---|
| Agent tick interval (`AGENT_TICK_INTERVAL_SECONDS`) | **30 s** | One snapshot per tick (`conventions.md §3`). |
| `METRIC_WINDOW_SECONDS` | **300** | `MetricSnapshot.window_seconds` (`conventions.md §3`). |
| `ROLLING_WINDOW_SIZE` (ticks) | **10** (≈5 min) | Sliding window for trends (`conventions.md §3`). |
| Predict batch size per tick (`N`) | **50 rows** | Tumbling input batch. |
| Service latency window | **200 requests** | Ring buffer for percentiles. |
| `min_labels_for_accuracy` | **30** | Below this, accuracy/F1 = `null`. |
| Percentile method | **nearest-rank**, `ceil(q·n)-1` | p50 / p95 / p99. |
| Confidence histogram bins | **10** (width 0.1) | `confidence_distribution`. |
| PSI bins | **10 (reference deciles)** | Per-feature PSI. |
| PSI drift threshold `τ` | **0.25** | Feature counted as drifted. |
| Health timeout | **2 s** | `GET /health`; failure ⇒ `service_up=false`. |
| `error_rate` warn / crit | **0.05 / 0.20** | Illustrative; see detection doc. |
| `p95_latency_ms` warn / crit | **500 / 1000 ms** | Illustrative. |
| `rolling_accuracy` warn / crit | **0.75 / 0.60** | Illustrative. |
| `confidence_drop` warn | **0.15** | Relative to baseline. |
| `drifted_feature_share` warn | **0.25** | Illustrative. |
| Snapshot retention | **14 days** | Periodic cleanup. |
| Baseline retention | **indefinite** | Needed by VERIFY. |
| Storage backend | **SQLite (dev) / Postgres (prod)** | Via Django ORM. |

---

*End of `monitoring_and_metrics.md`. For detection logic see `detection_methods.md`; for the loop
and recovery policy see `agent_logic.md`; for HTTP shapes see `api_contracts.md`; for DB DDL see
`data_model.md`; for the UI see `dashboard.md`; for delayed labels see `data_simulation.md`.*
