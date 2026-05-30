# Failure Scenarios Catalogue

> **Autonomous ML Monitoring & Auto-Recovery Agent**
> This document is simultaneously (a) the **demo script**, (b) the **test matrix**, and (c) the **behavioural specification** for the agent. Every scenario below is a self-contained, reproducible story that walks the agent through one full turn of its closed control loop:
>
> ```
> OBSERVE → DETECT → DECIDE → ACT → VERIFY → (repeat)
> ```

---

## 1. Introduction

> **Note:** Action/severity/outcome/health values follow `conventions.md` (authoritative).

### 1.1 Why catalogue failures?

A monitoring-and-recovery agent is only trustworthy if its behaviour is **predictable and verifiable** under every kind of degradation it claims to handle. We catalogue failures for four reasons:

1. **Specification** — Each scenario pins down the exact expected behaviour (which detector fires, which severity is assigned, which action runs). This removes ambiguity from "the agent should recover."
2. **Test matrix** — Each scenario is a black-box integration test. The "Pass/fail criteria" block at the end of every scenario is directly executable as a test assertion.
3. **Demo script** — Scenarios are ordered and written so they can be replayed live (or in batch) to demonstrate the system end-to-end.
4. **Safety review** — Negative tests (Section 4) and escalation paths (Section 5) prove the agent is **safe-by-default**: it does nothing when nothing is wrong, and it escalates to a human when automated recovery is exhausted.

### 1.2 The agent loop, and where each scenario touches it

| Loop stage | Module(s) responsible | What it does | Data sources |
|---|---|---|---|
| **Observe** | `agent_core/monitoring/` (`model_probe.py`, `prediction_probe.py`, `data_loader.py`) | Poll `/health`, `/metrics`, send `/predict` batches, load reference + live feature data | Model A (`:8001`), Model B (`:8002`), CSV / simulated stream |
| **Detect** | `agent_core/detection/` (`threshold_detector.py`, `anomaly_detector.py`, `drift_detector.py`) | Compare observed signals to thresholds, statistical baselines, and reference distributions | Observed metrics + reference window |
| **Decide** | `agent_core/decision_engine/` (`severity_classifier.py`, `policy_rules.py`, `decision.py`) | Classify severity (LOW/MEDIUM/HIGH), map (signal, severity) → action | Detector outputs |
| **Act** | `agent_core/actions/` (`no_op.py`, `alert.py`, `switch_model.py`) → `clients/jenkins_client.py`, `clients/django_client.py` | Execute the chosen recovery and record it | Jenkins jobs, Django APIs |
| **Verify** | `agent_core/verification/` (`health_check.py`, `rollback_guard.py`) | Confirm the action fixed the problem; roll back if it did not | Post-action metrics |

Every scenario in this document is described against these five stages plus the **audit trail** that lands in `actions_app` (fields: `action`, `severity`, `outcome`, plus timestamp/target/correlation id).

### 1.3 Reference thresholds & baselines used throughout

These are the canonical values the scenarios assume. They live in `agent_core/config.py` and are the single source of truth; the numbers below are the defaults used for the demo.

| Signal | Symbol | Reference / baseline | LOW band | MEDIUM band | HIGH band | Detector |
|---|---|---|---|---|---|---|
| Error rate (5xx + invalid predictions) | `err` | 0.5 % | 1–3 % | 3–10 % | > 10 % | threshold + anomaly |
| p95 inference latency | `p95` | 80 ms | 150–300 ms | 300–800 ms | > 800 ms | threshold |
| Health check (`/health`) | `up` | `200 OK` | n/a | 1 failed poll | ≥ 2 consecutive fails | threshold (health) |
| Data drift — PSI (per feature) | `psi` | 0.0 | 0.1–0.2 | 0.2–0.3 | > 0.3 | drift |
| Data drift — KS p-value | `ks_p` | 1.0 | 0.05–0.01 | 0.01–0.001 | < 0.001 | drift |
| Concept drift — accuracy | `acc` | 0.92 | 0.88–0.90 | 0.80–0.88 | < 0.80 | drift (perf) |
| Concept drift — F1 | `f1` | 0.90 | 0.86–0.88 | 0.78–0.86 | < 0.78 | drift (perf) |
| Mean prediction confidence | `conf` | 0.86 | 0.70–0.78 | 0.55–0.70 | < 0.55 | anomaly |
| Invalid-prediction ratio (NaN/out-of-domain output) | `inv` | 0 % | 1–3 % | 3–10 % | > 10 % | anomaly |
| Missing-value ratio in input batch | `miss` | 0 % | 1–5 % | 5–20 % | > 20 % | threshold (data quality) |
| Data freshness (age of newest batch) | `age` | < 1 batch | 1–2 batches late | 2–4 batches late | > 4 batches late | threshold (freshness) |

**Anti-flap defaults (also in `config.py`):**

- `CONFIRM_N = 2` — a non-LOW condition must persist for **2 consecutive observation cycles** before a non-trivial action is taken (rolling confirmation).
- `COOLDOWN = 3 cycles` — after any ACT (other than no-op/alert), the agent does not issue another switch/rollback for 3 cycles.
- `EWMA_ALPHA = 0.3` — smoothing applied to noisy metrics before thresholding, to ignore single-sample spikes.
- `MAX_RECOVERY_ATTEMPTS = 1` per incident before escalation to a human.

### 1.4 The recovery actions catalogue

| Action | Implemented by | Reversible? | Default? | Typical trigger |
|---|---|---|---|---|
| **no-op** (monitor more) | `actions/no_op.py` | N/A | ✅ safe default | transient noise, LOW severity |
| **alert-only** | `actions/alert.py` | N/A (informational) | — | LOW/MEDIUM that does not yet warrant traffic change |
| **switch traffic to backup** | `actions/switch_model.py` → Jenkins `switch_active_model` | ✅ (switch back) | — | HIGH on active model when backup is healthy |
| **rollback to previous version** | `clients/jenkins_client.py` → Jenkins `rollback_model` | ✅ (redeploy) | — | bad deploy / HIGH perf regression tied to a version |
| **retrain with recent data** (simulated) | Jenkins `deploy_model` (retrain stub) | ✅ | — | sustained concept/data drift |
| **disable predictions temporarily** | `switch_model.py` (degrade mode) | ✅ | — | both models unhealthy; safer to fail closed |

---

## 2. Scenario Catalogue

### 2.0 Scenario template

Every scenario below uses this exact structure:

```
ID & Name
Category
Description & real-world trigger
Simulation / injection (concrete)
OBSERVE  — signals that move, and where measured
DETECT   — detector that fires + expected metric values
SEVERITY — LOW/MEDIUM/HIGH + rationale
DECIDE   — policy rule matched → chosen action
ACT      — exact recovery (Jenkins job / API call)
VERIFY   — confirmation method + expected post-recovery metrics
Audit    — what is written to actions_app
Pass/fail — demo/test acceptance criteria
```

---

### D1 — Gradual Data Drift (feature mean slowly shifts)

| Field | Value |
|---|---|
| **ID & Name** | D1 — Gradual data drift |
| **Category** | Data Drift |
| **Description & trigger** | A real-world input feature (e.g. `sensor_temp`) slowly trends away from its training distribution — sensor aging, seasonal change, gradual user-behaviour shift. Inputs are still valid; the distribution is just sliding. |
| **Simulation / injection** | In `data_loader.py`, apply a per-cycle additive ramp to one feature: `x['f3'] += 0.05 * cycle_index` so the mean moves from `0.0` toward `+1.5` over ~30 cycles. Variance unchanged. |
| **OBSERVE** | `drift_detector.py` compares the live window of `f3` to the stored reference window. PSI and KS computed per feature. Error rate and latency stay flat. |
| **DETECT** | `drift_detector` fires gradually: PSI on `f3` climbs `0.04 → 0.12 → 0.22`. KS p-value falls `0.4 → 0.03 → 0.008`. Threshold/anomaly detectors stay silent. |
| **SEVERITY** | **LOW → MEDIUM** as PSI crosses `0.1` then `0.2`. Gradual, single feature, no performance impact yet → escalates slowly. |
| **DECIDE** | `policy_rules`: `DATA_DRIFT + LOW → alert-only`; once `DATA_DRIFT + MEDIUM` confirmed for `CONFIRM_N=2` cycles → **retrain with recent data**. |
| **ACT** | LOW: `alert.py` posts an advisory. MEDIUM (confirmed): `jenkins_client` triggers `deploy_model` in **retrain mode** (simulated retrain on recent window), producing model_a v(N+1). |
| **VERIFY** | After retrain+deploy, `drift_detector` recomputes PSI against the **new** reference → PSI back under `0.1`; `health_check` confirms model_a healthy. |
| **Audit** | Rows: `(action=alert, severity=LOW, outcome=success)`, then `(action=retrain, severity=MEDIUM, outcome=success, target=model_a)`. |
| **Pass/fail** | **PASS** if no traffic switch happens (gradual drift must not flap to backup), an alert is logged at LOW, and a retrain is triggered exactly once after MEDIUM is confirmed, with post-retrain PSI < 0.1. **FAIL** if it switches to model_b or retrains on the first MEDIUM reading without confirmation. |

---

### D2 — Sudden Data Drift (distribution jumps after upstream change)

| Field | Value |
|---|---|
| **ID & Name** | D2 — Sudden data drift |
| **Category** | Data Drift |
| **Description & trigger** | An upstream ETL/feature-pipeline change flips a feature's scale or encoding overnight (e.g. units change Celsius→Fahrenheit, or a categorical re-mapping). The shift is large and instantaneous. |
| **Simulation / injection** | In `data_loader.py`, from cycle `k` onward multiply `f3` by `1.8` and add `+3.0` (mean `0.0 → ~3.0`, variance inflated). Step change, not a ramp. |
| **OBSERVE** | `drift_detector` sees a one-cycle jump in the live window vs reference; downstream `acc`/`f1` may begin to slip a cycle later. |
| **DETECT** | `drift_detector` fires immediately: PSI on `f3` jumps `0.03 → 0.45`; KS p-value `< 0.001`. Multiple features may co-move. |
| **SEVERITY** | **HIGH** — PSI > 0.3 and KS p < 0.001 in a single step → HIGH. |
| **DECIDE** | `policy_rules`: `DATA_DRIFT + HIGH` → **switch traffic to backup** (if backup healthy) as immediate mitigation, **and** schedule **retrain** of the active model. |
| **ACT** | `switch_model.py` → Jenkins `switch_active_model` (active → model_b). `registry_app` `active_flag` updated to model_b via `/api/active-model`. Follow-up `deploy_model` retrain queued for model_a. |
| **VERIFY** | `health_check` confirms model_b serving and healthy; error rate stays ≤ baseline. Rollback guard armed in case the switch does not improve outcomes. |
| **Audit** | `(action=switch_to_backup, severity=HIGH, outcome=success, from=model_a, to=model_b)`, `(action=retrain, severity=HIGH, outcome=pending, target=model_a)`. |
| **Pass/fail** | **PASS** if a switch to model_b happens within 1–2 cycles of the jump, audit shows the switch + retrain, and post-switch error rate ≤ 1 %. **FAIL** if the agent waits the full gradual-confirmation window (a HIGH single-step should act fast) or disables predictions while a healthy backup exists. |

---

### D3 — Missing / Corrupted Values (NaNs, nulls) in incoming features

| Field | Value |
|---|---|
| **ID & Name** | D3 — Missing/corrupted input values |
| **Category** | Data Drift (data quality) / borders System |
| **Description & trigger** | Upstream feed drops fields or emits nulls/NaNs (broken sensor, partial join, schema change). Predictions made on garbage inputs are unreliable. |
| **Simulation / injection** | In `data_loader.py`, randomly set `30 %` of `f2` values to `NaN`/`None` from cycle `k`. |
| **OBSERVE** | `data_loader` computes the missing-value ratio per batch (`miss`); `prediction_probe` may surface model-side errors when NaNs reach `/predict`. |
| **DETECT** | `threshold_detector` (data-quality rule) fires: `miss = 30 %` (> 20 % HIGH band). Possibly `anomaly_detector` also flags an `inv` (invalid prediction) uptick if NaNs propagate to outputs. |
| **SEVERITY** | **HIGH** — `miss = 30 %` > 20 %. |
| **DECIDE** | `policy_rules`: `DATA_QUALITY + HIGH` → **disable predictions temporarily** (fail closed) — switching models will not help a bad input feed; serving any model on NaNs is unsafe. Alert raised. |
| **ACT** | `switch_model.py` degrade-mode call sets serving to "disabled"; `/api/active-model` reflects degraded state. `alert.py` raises a HIGH alert to the human owner (the input pipeline must be fixed upstream). |
| **VERIFY** | `health_check` confirms the service is intentionally in degrade mode (not crashed). On a subsequent cycle where `miss` returns under `1 %`, the agent re-enables predictions and verifies error rate ≤ baseline. |
| **Audit** | `(action=disable_predictions, severity=HIGH, outcome=success, reason=missing_values_30pct)`, later `(action=enable_predictions, severity=LOW, outcome=success)`. |
| **Pass/fail** | **PASS** if the agent disables (does not switch to backup), alerts a human, and only re-enables once `miss` recovers. **FAIL** if it switches to model_b (which would also receive NaNs) or keeps serving on corrupted data. |

---

### D4 — Out-of-range / Invalid Inputs

| Field | Value |
|---|---|
| **ID & Name** | D4 — Out-of-range / invalid inputs |
| **Category** | Data Drift (data quality) |
| **Description & trigger** | Inputs are present (not NaN) but violate domain constraints — negative ages, probabilities > 1, impossible categoricals — typically a calibration or client-side bug. |
| **Simulation / injection** | In `data_loader.py`, clamp-break `f1` into `[-9999, 9999]` for `15 %` of rows from cycle `k` (schema expects `[0,1]`). |
| **OBSERVE** | `data_loader` validates against the feature schema in `schemas.py`; out-of-range ratio recorded. `prediction_probe` may see the model returning extreme/garbage outputs. |
| **DETECT** | `threshold_detector` (range/validation rule) fires: out-of-range ratio `= 15 %` (MEDIUM band 5–20 %). `anomaly_detector` may flag prediction-confidence dips. |
| **SEVERITY** | **MEDIUM** — 15 % invalid inputs; harmful but not catastrophic, and isolated to one feature. |
| **DECIDE** | `policy_rules`: `DATA_QUALITY + MEDIUM` → **alert-only + drop/clip invalid rows** for this batch (do not switch models). Escalates to disable if it persists/worsens. |
| **ACT** | `alert.py` raises a MEDIUM alert; the agent records that invalid rows were excluded from the served batch. No Jenkins job triggered. |
| **VERIFY** | Next-cycle out-of-range ratio is re-measured. If it falls < 1 %, incident closes; if it rises into HIGH (>20 %), D4 escalates into the D3 disable path. |
| **Audit** | `(action=alert, severity=MEDIUM, outcome=success, reason=out_of_range_15pct)`. |
| **Pass/fail** | **PASS** if it alerts + filters without a traffic switch at MEDIUM, and escalates only when sustained/worsening. **FAIL** if a single MEDIUM batch triggers a model switch. |

---

### C1 — Concept Drift (accuracy/F1 degrades though inputs look stable)

| Field | Value |
|---|---|
| **ID & Name** | C1 — Concept drift (performance regression, stable inputs) |
| **Category** | Concept Drift |
| **Description & trigger** | The input distribution is unchanged, but the relationship between inputs and the target has shifted (fraud patterns evolve, user intent changes). Accuracy/F1 fall while PSI/KS stay flat. |
| **Simulation / injection** | Keep feature distributions fixed in `data_loader.py`; instead flip the *labels* of `~20 %` of the held-out evaluation batch (`label = 1 - label`) so the same model now scores worse. PSI ≈ 0; accuracy drops. |
| **OBSERVE** | `drift_detector` (performance mode) compares live `acc`/`f1` (computed on the labelled eval batch via `prediction_probe`) to baseline. Input-drift PSI checked and found **low** — this is the signature of concept drift. |
| **DETECT** | `drift_detector` (perf) fires while input drift is silent: `acc 0.92 → 0.83`, `f1 0.90 → 0.81`. PSI on all features < 0.1 → confirms concept (not data) drift. |
| **SEVERITY** | **MEDIUM** — accuracy in 0.80–0.88 band; performance degraded but above the HIGH floor (0.80). |
| **DECIDE** | `policy_rules`: `CONCEPT_DRIFT + MEDIUM` (confirmed `CONFIRM_N=2`) → **retrain with recent data**; if a recent good version exists and the regression is version-tied, prefer **rollback** first. |
| **ACT** | `jenkins_client` → `deploy_model` (retrain mode) using the recent labelled window; produces model_a v(N+1) and deploys it. |
| **VERIFY** | Re-evaluate `acc`/`f1` on a fresh labelled batch after deploy → expect `acc ≥ 0.90`, `f1 ≥ 0.88`. `rollback_guard`: if the retrained model does **not** beat the incumbent, roll back the deploy (see R1). |
| **Audit** | `(action=retrain, severity=MEDIUM, outcome=success, acc_before=0.83, acc_after=0.91)`. |
| **Pass/fail** | **PASS** if it correctly distinguishes concept drift from data drift (acts on perf, not PSI), retrains, and verifies improved accuracy. **FAIL** if it reports data drift, or switches to model_b without checking whether model_b suffers the same concept shift. |

---

### C2 — Slow Concept Drift  vs  C3 — Abrupt Concept Drift

These two share the C1 signature (perf down, inputs stable) but differ in **rate**, which changes severity timing and action.

| Field | C2 — Slow concept drift | C3 — Abrupt concept drift |
|---|---|---|
| **ID & Name** | C2 — Slow concept drift | C3 — Abrupt concept drift |
| **Category** | Concept Drift | Concept Drift |
| **Trigger** | Behaviour evolves over weeks; small steady erosion of accuracy. | A sudden regime change (policy change, market shock) flips the input→target mapping at once. |
| **Simulation** | `data_loader`: flip `+1 %` of labels per cycle (cumulative). | `data_loader`: flip `35 %` of labels at cycle `k` in a single step. |
| **OBSERVE** | `acc` erodes `0.92 → 0.905 → 0.89 → 0.875 …`; PSI flat. | `acc` drops `0.92 → 0.74` in one cycle; PSI flat. |
| **DETECT** | `drift_detector` (perf) trips LOW at `acc<0.90`, MEDIUM at `acc<0.88`. EWMA smoothing prevents reacting to single dips. | `drift_detector` (perf) trips **HIGH** immediately (`acc < 0.80`). |
| **SEVERITY** | **LOW → MEDIUM** over many cycles. | **HIGH** at once. |
| **DECIDE** | `CONCEPT_DRIFT + LOW → alert`; `+ MEDIUM (confirmed) → retrain`. No urgency to switch. | `CONCEPT_DRIFT + HIGH → switch to backup` (immediate mitigation) **+** retrain active. |
| **ACT** | `alert.py` then `deploy_model` retrain. | `switch_model.py` → `switch_active_model` to model_b, then `deploy_model` retrain for model_a. |
| **VERIFY** | Post-retrain `acc ≥ 0.90`. | model_b serving + healthy; if model_b also degraded (shared concept shift) → escalate (S4/R1). |
| **Audit** | `alert` → `retrain`. | `switch_to_backup` (HIGH) → `retrain`. |
| **Pass/fail** | **PASS**: no switch for slow drift; retrain after MEDIUM confirmation. **FAIL**: switching on slow drift. | **PASS**: switch within 1–2 cycles for HIGH. **FAIL**: waiting the full confirmation window on a HIGH abrupt drop. |

---

### A1 — Error-rate Spike (inference exceptions surge)

| Field | Value |
|---|---|
| **ID & Name** | A1 — Error-rate spike |
| **Category** | Sudden Anomaly |
| **Description & trigger** | The active model starts throwing exceptions / 5xx on `/predict` (memory pressure, dependency bug, malformed batch handling). |
| **Simulation / injection** | In model_a `app.py` test hook (or via `metrics.py`), force `/predict` to raise on `25 %` of requests from cycle `k`; `metrics.py` reports `err = 25 %`. |
| **OBSERVE** | `prediction_probe` batches to `/predict`; `model_probe` reads `/metrics`. Error rate spikes; latency may also rise. |
| **DETECT** | `threshold_detector` fires (`err 0.5 % → 25 %` > 10 % HIGH band) **and** `anomaly_detector` flags the spike as a deviation from the EWMA baseline. |
| **SEVERITY** | **HIGH** — `err` > 10 %. |
| **DECIDE** | `policy_rules`: `ERROR_SPIKE + HIGH` → **switch traffic to backup** (model_b), provided `health_check(model_b)` passes. |
| **ACT** | `switch_model.py` → Jenkins `switch_active_model` → model_b; `/api/active-model` `active_flag` → model_b. |
| **VERIFY** | `health_check` + `model_probe` on model_b: post-switch `err ≤ 1 %`. Cooldown started (3 cycles). |
| **Audit** | `(action=switch_to_backup, severity=HIGH, outcome=success, from=model_a, to=model_b, err_before=0.25, err_after=0.004)`. |
| **Pass/fail** | **PASS** if switch occurs within 1–2 cycles and post-switch error rate ≤ 1 %. **FAIL** if the agent retrains (slow) instead of switching, or switches to an unhealthy backup. |

---

### A2 — Prediction Confidence Collapse

| Field | Value |
|---|---|
| **ID & Name** | A2 — Prediction confidence collapse |
| **Category** | Sudden Anomaly |
| **Description & trigger** | The model still returns 200s, but output probabilities cluster near `0.5` (uncertain) — often an early sign of drift or a corrupted model artifact. |
| **Simulation / injection** | Load a deliberately under-trained/blended artifact into model_a, or in `prediction_probe` simulate `conf` drawn from `N(0.52, 0.03)` from cycle `k` (baseline ≈ 0.86). |
| **OBSERVE** | `prediction_probe` records mean confidence per batch. Error rate and latency normal. |
| **DETECT** | `anomaly_detector` fires: `conf 0.86 → 0.52` (HIGH band < 0.55). Threshold detector silent (no errors). |
| **SEVERITY** | **HIGH** — `conf` < 0.55; predictions effectively unreliable. |
| **DECIDE** | `policy_rules`: `CONFIDENCE_COLLAPSE + HIGH` → **switch to backup** + investigate (alert). Confidence collapse without input drift suggests a bad active artifact, so backup is preferred over retrain. |
| **ACT** | `switch_model.py` → `switch_active_model` → model_b; `alert.py` raises HIGH alert for artifact review. |
| **VERIFY** | Post-switch mean `conf` from model_b back ≥ 0.78; `health_check` green. |
| **Audit** | `(action=switch_to_backup, severity=HIGH, outcome=success, conf_before=0.52, conf_after=0.83)`. |
| **Pass/fail** | **PASS** if low confidence (with no errors) still triggers a switch and recovery is verified by a confidence rebound. **FAIL** if the agent ignores confidence because error rate is fine. |

---

### A3 — Invalid / Garbage Predictions

| Field | Value |
|---|---|
| **ID & Name** | A3 — Invalid/garbage predictions |
| **Category** | Sudden Anomaly |
| **Description & trigger** | Model outputs are syntactically returned but semantically invalid — NaN probabilities, labels outside the class set, all-identical predictions (mode collapse). |
| **Simulation / injection** | In model_a `app.py` hook, emit `NaN` or out-of-class labels for `40 %` of predictions, or return a constant class for an entire batch. |
| **OBSERVE** | `prediction_probe` validates each prediction against the `schemas.py` output schema; `inv` (invalid-output ratio) computed. |
| **DETECT** | `anomaly_detector` (output-validity rule) fires: `inv 0 % → 40 %` (HIGH band > 10 %). |
| **SEVERITY** | **HIGH** — `inv` > 10 %; outputs unusable. |
| **DECIDE** | `policy_rules`: `INVALID_OUTPUT + HIGH` → **switch to backup**; if backup also invalid → **disable predictions** + escalate. |
| **ACT** | `switch_model.py` → `switch_active_model` → model_b. If model_b output-validity also fails → degrade mode + HIGH human alert. |
| **VERIFY** | Post-switch `inv ≤ 1 %`; `health_check` green. |
| **Audit** | `(action=switch_to_backup, severity=HIGH, outcome=success, inv_before=0.40, inv_after=0.0)`. |
| **Pass/fail** | **PASS** if invalid outputs trigger a switch and validity is restored; correct escalation if backup also invalid. **FAIL** if invalid outputs pass undetected because HTTP 200 was returned. |

---

### S1 — High Prediction Latency / p95 Breach

| Field | Value |
|---|---|
| **ID & Name** | S1 — High latency / p95 breach |
| **Category** | System / Operational |
| **Description & trigger** | Inference becomes slow (CPU contention, GC pauses, oversized batches, noisy neighbour). Correctness fine, but the SLA is breached. |
| **Simulation / injection** | In model_a `app.py`, add `time.sleep` to `/predict` so p95 rises to `~900 ms` from cycle `k` (baseline 80 ms). |
| **OBSERVE** | `model_probe` reads the `/metrics` latency histogram; p95 computed. |
| **DETECT** | `threshold_detector` (latency rule) fires: `p95 80 ms → 900 ms` (> 800 ms HIGH band). |
| **SEVERITY** | **MEDIUM → HIGH** — `p95` in 300–800 → MEDIUM, > 800 → HIGH. |
| **DECIDE** | `policy_rules`: `LATENCY + MEDIUM → alert` (watch); `LATENCY + HIGH (confirmed) → switch to backup` if model_b latency is healthy. |
| **ACT** | MEDIUM: `alert.py`. HIGH (confirmed `CONFIRM_N=2`): `switch_model.py` → `switch_active_model` → model_b. |
| **VERIFY** | Post-switch `p95 ≤ 150 ms` on model_b; `health_check` green. |
| **Audit** | `(action=alert, severity=MEDIUM, ...)` then `(action=switch_to_backup, severity=HIGH, outcome=success, p95_before=900, p95_after=110)`. |
| **Pass/fail** | **PASS** if a single latency blip is smoothed (no action), sustained HIGH p95 triggers a switch, and post-switch p95 < 150 ms. **FAIL** if a one-cycle spike causes a switch (flapping). |

---

### S2 — Model Service Down / Health Check Failing

| Field | Value |
|---|---|
| **ID & Name** | S2 — Active model service down |
| **Category** | System / Operational |
| **Description & trigger** | The active model container crashes / hangs; `/health` returns non-200 or times out. |
| **Simulation / injection** | Stop the model_a container (`docker stop model_a`) or make `/health` return `503` from cycle `k`. |
| **OBSERVE** | `model_probe` → `health_check.py` polls `/health`. Consecutive failures counted. |
| **DETECT** | `threshold_detector` (health rule): 1 fail = MEDIUM, **≥ 2 consecutive fails = HIGH**. |
| **SEVERITY** | **HIGH** after 2 consecutive failed health polls. |
| **DECIDE** | `policy_rules`: `SERVICE_DOWN + HIGH` → **switch to backup** (model_b) immediately if healthy. |
| **ACT** | `switch_model.py` → `switch_active_model` → model_b; `/api/active-model` `active_flag` → model_b. Optionally trigger `deploy_model`/redeploy of model_a to recover it. |
| **VERIFY** | `health_check(model_b)` = `200 OK`; traffic served; `err`/`p95` at baseline. |
| **Audit** | `(action=switch_to_backup, severity=HIGH, outcome=success, reason=health_check_failed, from=model_a, to=model_b)`. |
| **Pass/fail** | **PASS** if 2 consecutive health failures trigger a switch to model_b and serving resumes. **FAIL** if a single transient health blip causes a switch, or the agent waits indefinitely. |

---

### S3 — Data Arrival Delay / Stale Data

| Field | Value |
|---|---|
| **ID & Name** | S3 — Data arrival delay / stale data |
| **Category** | System / Operational |
| **Description & trigger** | The upstream batch feed is late or stalled; the agent would otherwise be scoring/monitoring on stale data and could draw wrong conclusions. |
| **Simulation / injection** | In `data_loader.py`, stop advancing the batch timestamp / withhold new batches for several cycles so `age` grows. |
| **OBSERVE** | `data_loader` reports freshness/age of the newest batch each cycle. |
| **DETECT** | `threshold_detector` (freshness rule): `age` 1–2 late = LOW, 2–4 = MEDIUM, > 4 = HIGH. |
| **SEVERITY** | **LOW → MEDIUM → HIGH** as staleness grows. |
| **DECIDE** | `policy_rules`: `STALE_DATA → alert` (and importantly: **suppress drift/perf detectors** while data is stale to avoid false drift alarms). HIGH staleness → escalate to a human (pipeline issue), no model switch. |
| **ACT** | `alert.py` raises the staleness alert; agent enters "monitor-only / detectors gated" mode. No Jenkins job (switching models cannot fix a data feed). |
| **VERIFY** | When fresh data resumes (`age` < 1 batch), detectors re-enable; agent confirms metrics are computed on current data. |
| **Audit** | `(action=alert, severity=MEDIUM, outcome=success, reason=stale_data_age=3)`, later `(action=no_op, severity=LOW, outcome=success)`. |
| **Pass/fail** | **PASS** if stale data suppresses false drift detections and only alerts/escalates (no model switch). **FAIL** if staleness is misread as data/concept drift and triggers a retrain or switch. |

---

### S4 — Backup Model Also Unhealthy (escalation case)

| Field | Value |
|---|---|
| **ID & Name** | S4 — Backup unhealthy during failover |
| **Category** | System / Operational (escalation) |
| **Description & trigger** | The active model is HIGH-severity bad (any of A1/A2/A3/S1/S2/D2), the agent tries to fail over — but model_b is **also** unhealthy (down, high error, or invalid). No safe automated target remains. |
| **Simulation / injection** | Trigger A1 on model_a (force 25 % errors) **and** simultaneously make model_b `/health` return `503` (`docker stop model_b`). |
| **OBSERVE** | `health_check` probes both model_a and model_b before any switch; `model_probe` confirms both unhealthy. |
| **DETECT** | `threshold_detector` HIGH on model_a; pre-switch `health_check(model_b)` fails → backup is not a valid target. |
| **SEVERITY** | **HIGH (escalated)** — primary failure with no healthy fallback. |
| **DECIDE** | `policy_rules`: `HIGH on active + backup_unhealthy` → **disable predictions temporarily** (fail closed) **+ HIGH alert / page human**. The agent must NOT switch to a known-bad backup. |
| **ACT** | `switch_model.py` degrade-mode (serving disabled); `alert.py` raises a **page-level** HIGH alert with both models' health states. No `switch_active_model`. |
| **VERIFY** | `health_check` confirms degrade mode is active and intentional; agent stops auto-recovery and waits for human / for a model to recover. |
| **Audit** | `(action=disable_predictions, severity=HIGH, outcome=failed, escalate_to_human=true, reason=both_models_unhealthy, human_notified=true)`. |
| **Pass/fail** | **PASS** if the agent refuses to switch to the unhealthy backup, disables predictions, and escalates to a human. **FAIL** if it switches to model_b anyway (the worst outcome) or keeps serving the broken active model. |

---

### N1 — Transient Noise that should be IGNORED (no-op)  *(negative test)*

| Field | Value |
|---|---|
| **ID & Name** | N1 — Transient noise (anti-flap) |
| **Category** | Sudden Anomaly (negative case) |
| **Description & trigger** | A single noisy reading — one slow request, one 500, one batch with a slightly higher PSI — that is **not** a real degradation. The correct behaviour is to do nothing. |
| **Simulation / injection** | Inject a **single-cycle** spike then return to baseline: e.g. `err = 8 %` for exactly **one** cycle, or `p95 = 400 ms` for one cycle, then back to baseline. Or one batch with PSI 0.15 that reverts. |
| **OBSERVE** | `model_probe`/`prediction_probe` record the single elevated sample; EWMA smoothing (`alpha=0.3`) dampens it. |
| **DETECT** | Raw value momentarily enters MEDIUM band, but the **EWMA-smoothed** value stays under threshold, and the condition does **not** persist for `CONFIRM_N=2` cycles. Detector raises at most a tentative LOW. |
| **SEVERITY** | **LOW** (transient) — fails the persistence/confirmation requirement for any non-LOW action. |
| **DECIDE** | `policy_rules`: unconfirmed/transient → **no-op** (`monitor more`). |
| **ACT** | `no_op.py` — record an observation, take no recovery action. |
| **VERIFY** | Next cycle confirms metrics back at baseline; incident auto-closes with no intervention. |
| **Audit** | `(action=no_op, severity=LOW, outcome=skipped)` — note: even no-ops are auditable so reviewers can see the agent saw the blip and chose not to act. |
| **Pass/fail** | **PASS** if **zero** Jenkins jobs are triggered, `active_flag` is unchanged, and the only audit row is a `no_op`. **FAIL** if a single-cycle blip causes any switch/retrain/rollback (flapping). |

---

### R1 — Recovery FAILS Verification → Auto-rollback of the Recovery → Escalate

| Field | Value |
|---|---|
| **ID & Name** | R1 — Failed recovery, auto-rollback + escalate |
| **Category** | System / Operational (recovery integrity) |
| **Description & trigger** | The agent takes a recovery action (e.g. retrain+deploy from C1, or a switch), but **verification fails** — the new model/state is no better (or worse). The agent must undo its own action and call a human, not loop forever. |
| **Simulation / injection** | After a C1 retrain, force the redeployed model_a v(N+1) to still report `acc = 0.81` (no improvement) — e.g. retrain on the same corrupted window. Alternatively, after a switch, make model_b also breach error thresholds within the verify window. |
| **OBSERVE** | `verification/` re-measures post-action metrics (`acc`, `err`, `p95`, `health`) within the verify window. |
| **DETECT** | Verification gate fails: the post-recovery metric does not clear the recovery success threshold (e.g. `acc_after 0.81 < 0.90` target; or `err_after` still > 10 %). |
| **SEVERITY** | **HIGH (escalated)** — automated recovery exhausted. |
| **DECIDE** | `rollback_guard.py`: recovery did not verify → **roll back the recovery** (redeploy previous good version via `rollback_model`, or switch back). `MAX_RECOVERY_ATTEMPTS=1` reached → **escalate to human**; do not retry automatically. |
| **ACT** | `jenkins_client` → Jenkins `rollback_model` (restore last-known-good version / revert the switch). `alert.py` raises a HIGH page to a human with full incident context. Agent enters cooldown and stops auto-acting on this incident. |
| **VERIFY** | `health_check` confirms the system is back to the **previous known-good state** (the rollback itself succeeded). The original problem is now owned by a human. |
| **Audit** | `(action=retrain, severity=MEDIUM, outcome=failed, acc_after=0.81)`, then `(action=rollback, severity=HIGH, outcome=success, restored=model_a_vN)`, then `(action=alert, severity=HIGH, outcome=failed, escalate_to_human=true, human_notified=true)`. |
| **Pass/fail** | **PASS** if a non-improving recovery is detected, **auto-rolled-back to the prior good state**, capped at one attempt, and escalated — leaving the system in a safe known state. **FAIL** if the agent declares success without verifying, retries indefinitely, or leaves the system in the failed intermediate state. |

---

## 3. Summary Matrix

| ID | Scenario | Category | Detector(s) | Severity | Action | Verification outcome |
|---|---|---|---|---|---|---|
| D1 | Gradual data drift | Data Drift | drift (PSI/KS) | LOW→MEDIUM | alert → retrain (`deploy_model`) | PSI back < 0.1 post-retrain |
| D2 | Sudden data drift | Data Drift | drift (PSI/KS) | HIGH | switch_to_backup → model_b (`switch_active_model`) + retrain | model_b healthy, err ≤ 1 % |
| D3 | Missing/corrupted values | Data Quality | threshold (data-quality) + anomaly | HIGH | disable_predictions + alert | re-enable when `miss` < 1 % |
| D4 | Out-of-range inputs | Data Quality | threshold (range) | MEDIUM | alert + drop/clip rows | ratio < 1 % next cycle |
| C1 | Concept drift (stable inputs) | Concept Drift | drift (perf) | MEDIUM | retrain (`deploy_model`) | acc ≥ 0.90, f1 ≥ 0.88 |
| C2 | Slow concept drift | Concept Drift | drift (perf) | LOW→MEDIUM | alert → retrain | acc ≥ 0.90 |
| C3 | Abrupt concept drift | Concept Drift | drift (perf) | HIGH | switch_to_backup → model_b + retrain | model_b healthy (else escalate) |
| A1 | Error-rate spike | Sudden Anomaly | threshold + anomaly | HIGH | switch_to_backup → model_b | err ≤ 1 % post-switch |
| A2 | Confidence collapse | Sudden Anomaly | anomaly | HIGH | switch_to_backup → model_b + alert | conf ≥ 0.78 |
| A3 | Invalid predictions | Sudden Anomaly | anomaly (output-validity) | HIGH | switch_to_backup → model_b (else disable) | inv ≤ 1 % |
| S1 | High latency / p95 | System | threshold (latency) | MEDIUM→HIGH | alert → switch_to_backup → model_b | p95 < 150 ms |
| S2 | Service down | System | threshold (health) | HIGH | switch_to_backup → model_b + redeploy | model_b `200 OK` |
| S3 | Stale data | System | threshold (freshness) | LOW→HIGH | alert + gate detectors | resumes on fresh data, no switch |
| S4 | Backup also unhealthy | System (escalation) | threshold (health, both) | HIGH (esc.) | disable_predictions + page human | degrade mode, human owns it |
| N1 | Transient noise | Negative | (smoothed) — none confirmed | LOW | no-op | baseline next cycle, no jobs |
| R1 | Recovery fails verify | Recovery integrity | verification gate | HIGH (esc.) | rollback (`rollback_model`) + escalate | prior good state restored |

---

## 4. Negative Tests / Anti-flapping

The agent is **safe-by-default**: when there is no genuine, confirmed degradation, the correct action is **no action**. These tests prove the agent does not overreact, and are as important as the recovery tests.

| Test | Stimulus | Expected behaviour | Mechanism that enforces it |
|---|---|---|---|
| **N1 single-cycle spike** | One cycle at `err=8 %` / `p95=400 ms`, then baseline | no-op; no Jenkins job; `active_flag` unchanged | `CONFIRM_N=2` persistence requirement + EWMA smoothing |
| **N1b PSI blip** | One batch PSI 0.15, reverts next cycle | no-op (at most LOW alert), no retrain | drift confirmation window |
| **Cooldown respect** | A second HIGH appears 1 cycle after a switch | no second switch until `COOLDOWN=3` cycles elapse | `COOLDOWN` gate |
| **Stale-data false drift (S3)** | Detectors gated while data is stale | no drift/concept alarm, no retrain/switch | freshness gate disables drift/perf detectors |
| **Healthy steady state** | All metrics at baseline for N cycles | exactly zero actions (or periodic `no_op` heartbeats only) | thresholds not crossed |
| **Recovered-on-its-own** | MEDIUM condition for 1 cycle, then self-resolves before `CONFIRM_N` | incident auto-closes, no action | confirmation window |

**Demo PASS for this section:** Across a long healthy/noisy run, the only audit rows are `no_op` (and possibly LOW `alert`), `active_flag` never changes, and **no** `switch_active_model` / `deploy_model` / `rollback_model` job is triggered.

---

## 5. Edge Cases & Escalation Paths

The agent automates the common, safe recoveries and **escalates to a human** when automation is unsafe, exhausted, or ambiguous.

### 5.1 When the agent must involve a human

| Condition | Why automation stops | Agent behaviour |
|---|---|---|
| **Both models unhealthy (S4)** | No safe failover target | Disable predictions (fail closed) + HIGH page |
| **Recovery fails verification (R1)** | Automated fix did not work | Roll back to known-good + HIGH page; `MAX_RECOVERY_ATTEMPTS=1` |
| **Bad input feed (D3 sustained)** | Switching/retraining cannot fix upstream data | Disable + alert pipeline owner |
| **Repeated flapping detected** | Same incident toggling > 2 times | Freeze auto-actions, escalate (stability guard) |
| **Drift + stale data ambiguity (S3)** | Cannot trust metrics on stale data | Gate detectors, alert; no model change |
| **Jenkins job failure** | Recovery executor itself failed | Mark `outcome=failed`, do not assume success, escalate |
| **Unknown / unclassifiable signal** | No matching policy rule | Default to `no_op` + alert (never act blindly) |

### 5.2 Escalation ladder (per incident)

```
1. Detect degradation (confirmed over CONFIRM_N cycles)
2. Choose the least-invasive effective action:
      LOW    → alert / no-op
      MEDIUM → alert, then retrain/rollback if confirmed
      HIGH   → switch to healthy backup (if available)
3. VERIFY the action.
      success → close incident, enter COOLDOWN
      failure → rollback the action (R1)
4. If backup unhealthy (S4) OR rollback path exhausted OR
   MAX_RECOVERY_ATTEMPTS reached:
      → disable predictions (fail closed where serving is unsafe)
      → page a human with full audit context
      → STOP auto-acting on this incident
```

### 5.3 Safety invariants (must hold in every scenario)

1. **Never switch to a backup that fails its pre-switch `health_check`.**
2. **Every action is auditable** in `actions_app` — including `no_op` (so "did nothing" is an explicit, reviewable decision).
3. **Every action is reversible** — a switch can switch back; a deploy/retrain can roll back via `rollback_model`.
4. **Fail closed, not open** — when no safe model exists, disable predictions rather than serve a known-bad model.
5. **No flapping** — confirmation window, EWMA smoothing, cooldown, and a per-incident attempt cap bound how often the agent acts.
6. **Verification is mandatory** — an action is not "successful" until post-action metrics confirm it; otherwise it is rolled back and escalated.

### 5.4 Audit-trail field reference (`actions_app`)

| Field | Meaning | Example |
|---|---|---|
| `action` | Recovery taken | `no_op`, `alert`, `switch_to_backup`, `retrain`, `rollback`, `disable_predictions`, `enable_predictions` |
| `severity` | Classified severity | `LOW`, `MEDIUM`, `HIGH` |
| `outcome` | Result after verification | `pending`, `success`, `failed`, `reverted`, `skipped` (escalation carried via `escalate_to_human=true`) |
| `target` / `from` / `to` | Affected model / direction | `model_a`, `model_b` |
| `reason` | Triggering signal | `psi=0.45`, `err=0.25`, `health_check_failed`, `both_models_unhealthy` |
| `metrics_before` / `metrics_after` | Verification evidence | `acc_before=0.83, acc_after=0.91` |
| `human_notified` | Escalation flag | `true` / `false` |
| `correlation_id` | Links all rows of one incident | uuid |

---

*End of catalogue. To run this as the demo/test matrix, drive the simulations in `agent_core/monitoring/data_loader.py` and the model service `app.py` test hooks per scenario, then assert each scenario's Pass/fail criteria against the `actions_app` audit log and the `registry_app` `active_flag`.*
