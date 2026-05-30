# Dashboard UI Specification — Autonomous ML Monitoring & Auto-Recovery Agent

> **Guiding mantra (inherited from architecture):** *"One repo, many services, many
> environments, HTTP everywhere."* The dashboard is the **single pane of glass** an
> operator stares at to answer three questions at a glance: *Are my models healthy? Is
> the data drifting? What has the agent done about it?*

This document is the authoritative, implementation-ready specification for the
server-rendered observability UI provided by `control-plane/backend/dashboard_app/`. It
is intentionally long and concrete: it references real routes, real Django apps, real
field/metric names, real status enums, ASCII wireframes, and illustrative view/template
code so prose and code stay in lock-step.

It is consistent with, and cross-references:

- `docs/architecture.md` — system topology, planes, ports, Django app responsibilities.
- `docs/api_contracts.md` — endpoint request/response schemas (`/api/metrics`,
  `/api/active-model`, actions log).
- `docs/monitoring_and_metrics.md` — exact metric names, units, PSI drift definition.
- `docs/data_model.md` — Django model fields for `monitoring_app`, `registry_app`,
  `actions_app`.
- `docs/failure_scenarios.md` — runbooks the dashboard narrative cross-references in §10.

---

## Table of Contents

1. [Goals & Audience](#1-goals--audience)
2. [Tech Approach](#2-tech-approach)
3. [Information Architecture — Pages & Routes](#3-information-architecture--pages--routes)
4. [Page-by-Page Component Specification](#4-page-by-page-component-specification)
5. [Visual Design & Status Coding](#5-visual-design--status-coding)
6. [Wireframes (ASCII)](#6-wireframes-ascii)
7. [Data Refresh & State](#7-data-refresh--state)
8. [Example View & Template Structure](#8-example-view--template-structure)
9. [Accessibility & UX](#9-accessibility--ux)
10. [Tying the Loop Together — A Live Degradation+Recovery Walkthrough](#10-tying-the-loop-together)

---

## 1. Goals & Audience

### 1.1 Primary goal

Give an **on-call ML operator** (or a demo reviewer) a single, server-rendered surface
that makes the agent's closed loop — **Observe → Detect → Decide → Act → Verify** —
*visible and auditable*. The dashboard does not run the loop; the loop lives in
`control-plane/agent_core/agent.py`. The dashboard only **reads and renders** the durable
state that the agent and Jenkins write into Django.

The three deliverables mandated by the problem statement are first-class, top-level
concerns of the UI:

1. **MODEL HEALTH STATUS** — per-model `HEALTHY / DEGRADED / CRITICAL / UNKNOWN`.
2. **DRIFT INDICATORS** — per-feature PSI scores with severity bands and trend.
3. **ACTION HISTORY** — the complete, queryable agent audit trail
   (`NO_OP / ALERT / SWITCH / ROLLBACK / RETRAIN / DISABLE`).

Beyond those, the UI also surfaces the high-value extras: live metric time-series,
an incident timeline, and the current active model & version.

### 1.2 Audience

| Audience | What they need from the dashboard |
|----------|-----------------------------------|
| **On-call operator** | "Is anything on fire right now? Did the agent already fix it, or do I need to step in?" |
| **ML engineer / model owner** | Per-model metric trends and per-feature drift to diagnose *why* a model degraded. |
| **Reviewer / stakeholder (demo)** | A clear, screenshot-friendly story of autonomous detection and recovery. |
| **Auditor** | An immutable, filterable record of every decision and its outcome. |

### 1.3 Read-only by default

The dashboard is **read-only by default**, consistent with the architecture's statement
that *"the dashboard is a read-only observability UI, not a chat surface"* and that
**only `agent_core` decides** and **only Jenkins mutates the fleet**. The UI never makes
a decision and never talks to Jenkins or the model containers directly.

### 1.4 Safe manual controls (opt-in, guard-railed)

Two *optional* manual controls are permitted. They are **off by default** (gated behind
`DASHBOARD_ENABLE_MANUAL_CONTROLS = False` in settings) and, when enabled, never bypass
the safety boundaries:

| Control | What it does | Guardrails |
|---------|--------------|-----------|
| **Acknowledge alert / incident** | Sets an `acknowledged_by` + `acknowledged_at` on an incident/action row so the team knows a human has seen it. Pure metadata. | Does **not** change fleet state. Requires login (Django auth / staff). POST + CSRF token. Recorded as its own audit entry (`action = ALERT`, `outcome = ACKNOWLEDGED`). |
| **Manual rollback / switch trigger** | Submits the *same* parameterized Jenkins job the agent would (`switch_active_model` / `rollback_model`) **via Django → Jenkins**, never from the browser. | Login + `is_staff` required. POST + CSRF. Confirmation modal ("type the model name to confirm"). Passes through the **same `rollback_guard` precondition check** the agent uses (target must be `HEALTHY`). Idempotent: if the requested target is already active, it is a no-op. Every manual trigger is written to `actions_app` with `reason = "manual:<username>"` so it is indistinguishable in auditability from an agent action. Subject to a server-side rate limit (one destructive manual action per model per 60 s). |

> **Boundary reminder:** even a manual rollback goes *Browser → Django → Jenkins*. The
> browser never reaches Jenkins, the models, or the registry directly. This preserves the
> "Only Jenkins may change the running fleet" trust boundary from `architecture.md` §11.

---

## 2. Tech Approach

| Aspect | Decision |
|--------|----------|
| **Rendering** | **Server-rendered Django templates** served by `dashboard_app`. No SPA, no React/Vue/Angular. Consistent with architecture scope ("server-rendered observability UI"). |
| **Data access** | Views read state either by **direct Django ORM** queries against the sibling apps' models (`monitoring_app`, `registry_app`, `actions_app`) **or** by calling the existing **DRF JSON endpoints** (`/api/metrics`, `/api/active-model`, actions log). Pages prefer the ORM for the initial render (fast, no extra HTTP hop) and use the JSON endpoints for the JS auto-refresh / chart data feeds. |
| **Charts** | **Chart.js** loaded from CDN (`<script src="https://cdn.jsdelivr.net/npm/chart.js">`). Used for all time-series (line charts) and the drift bar charts. No build step. |
| **Interactivity** | A small amount of **vanilla JS** only: (a) `setInterval` polling to refresh data, (b) client-side table sort/filter, (c) confirmation modals for manual controls. No bundler, no npm. |
| **Styling** | A single hand-written `dashboard.css` (plus optional minimal classless base). Status colors are CSS classes/variables (see §5). Layout via CSS grid/flexbox. |
| **Refresh model** | **Polling** every `N` seconds (default 10 s, see §7). The system is explicitly batch/near-real-time, so polling is sufficient — no WebSockets/SSE required. |
| **Auth** | Read-only pages may be open in a local demo. Manual controls (§1.4) require Django login + `is_staff`. |
| **Templates location** | `control-plane/backend/dashboard_app/templates/dashboard/`. |
| **Static assets** | `control-plane/backend/dashboard_app/static/dashboard/` (`dashboard.css`, `dashboard.js`). |

The dashboard adds **no new persistence** of its own — it is a pure consumer of the three
state-owning apps, exactly mirroring how the agent is a pure consumer/producer over HTTP.

---

## 3. Information Architecture — Pages & Routes

All routes are mounted under `dashboard_app/urls.py`, included by `config/urls.py` at the
`/dashboard/` prefix. `/` redirects to `/dashboard/`.

| # | Page | Route (name) | Purpose | Primary data sources |
|---|------|--------------|---------|----------------------|
| 1 | **Overview / Home** | `/dashboard/` (`dashboard:overview`) | System-wide health summary, active-model card, KPI tiles, recent actions, active incidents. The default landing page. | `registry_app` (active model + model list), `monitoring_app` (latest metrics per model), `actions_app` (recent + open incidents) |
| 2 | **Model Detail** | `/dashboard/models/<str:model_name>/` (`dashboard:model_detail`) | Deep-dive on one model: metric time-series, drift panel, health, version history. `<model_name>` is `model_a` / `model_b`. | `monitoring_app` (time-series), `registry_app` (version/active_flag/status), drift records |
| 3 | **Drift Dashboard** | `/dashboard/drift/` (`dashboard:drift`) | Cross-model drift focus: per-feature PSI, drifted-feature count over time, reference-vs-current distribution comparison. | `monitoring_app` drift records (PSI per feature), reference distribution snapshot |
| 4 | **Incidents / Timeline** | `/dashboard/incidents/` (`dashboard:incidents`) | List of incidents with status + severity; chronological timeline. | `actions_app` (grouped into incidents) |
| 5 | **Incident Detail** | `/dashboard/incidents/<int:incident_id>/` (`dashboard:incident_detail`) | Drill-down: the full **action chain** for one incident (detect → decide → act → verify), with the metrics that justified each step. | `actions_app` (action chain), `monitoring_app` (justifying metrics) |
| 6 | **Action / Audit Log** | `/dashboard/actions/` (`dashboard:actions`) | The full sortable/filterable table of **every** agent action and its outcome. | `actions_app` (all rows) |

**JSON refresh endpoints** (served by `dashboard_app` for the JS poller; thin wrappers that
reuse the same querysets/serializers, returning JSON):

| Endpoint | Returns |
|----------|---------|
| `/dashboard/api/summary/` (`dashboard:api_summary`) | Overview KPIs + per-model health + open-incident count (one compact JSON blob for the poller). |
| `/dashboard/api/models/<model_name>/series/?metric=<m>&window=<w>` | Time-series points for one metric over a window (feeds Chart.js). |
| `/dashboard/api/drift/<model_name>/` | Per-feature PSI array + reference/current histograms. |

> These `dashboard/api/*` endpoints are *for the UI's own polling* and may simply re-call
> the canonical DRF endpoints (`/api/metrics`, `/api/active-model`) or query the ORM. The
> canonical contracts in `api_contracts.md` remain the source of truth for field shapes.

### 3.1 Canonical field names (consistency anchor)

To keep this spec aligned with `data_model.md` / `monitoring_and_metrics.md` /
`api_contracts.md`, the dashboard renders these exact fields:

**`monitoring_app` metric row** (per model, per cycle):
`model_name`, `latency_ms` (p95 latency in ms), `error_rate` (0–1 fraction),
`accuracy` (0–1), `confidence` (mean predicted-class probability, 0–1),
`drift_score` (overall PSI / max-feature PSI), `status` (`ok` / `degraded` / `error`),
`ts` (ISO-8601 UTC timestamp).

**Drift detail** (per feature, attached to a metric cycle): `feature_name`, `psi`
(Population Stability Index), `band` (`green` / `amber` / `red`), plus a reference
histogram and current histogram for distribution comparison.

**`registry_app` model row:** `model_name`, `version` (semver, e.g. `1.4.2`),
`active_flag` (bool), `status` (registry status), `role` (`ACTIVE` / `BACKUP`),
`port` (8001 / 8002).

**`actions_app` action/audit row:** `action`
(`NO_OP` / `ALERT` / `SWITCH` / `ROLLBACK` / `RETRAIN` / `DISABLE`),
`severity` (`LOW` / `MEDIUM` / `HIGH`),
`outcome` (`SUCCESS` / `FAILED` / `ROLLED_BACK` / `NO_OP` / `ACKNOWLEDGED` / `PENDING`),
`reason` (human/agent-readable justification string), `target_model`,
`from_model`, `to_model` (for switches), `incident_id`, `created_at`, `completed_at`.

Health status (`HEALTHY / DEGRADED / CRITICAL / UNKNOWN`) is **derived** by the dashboard
(or by the agent and stored) from the metric `status` + threshold breaches — see §5.5.

---

## 4. Page-by-Page Component Specification

For each component: **what it shows**, the **exact fields**, the **data source**, the
**chart type / rendering**, the **status coding**, and the **empty/error state**.

### 4.1 Overview / Home — `/dashboard/`

The landing page. Goal: in <5 seconds an operator knows the global posture.

#### 4.1.1 System Health Banner (top strip)
- **Shows:** one aggregate posture for the whole fleet, computed as the **worst** active-
  model health (`CRITICAL` > `DEGRADED` > `HEALTHY`; `UNKNOWN` if no recent metrics).
- **Fields:** aggregate label, count of models per health, "as of" timestamp.
- **Source:** `monitoring_app` latest row per model + `registry_app`.
- **Render:** full-width colored bar (green/amber/red, §5.1) with bold label, e.g.
  `SYSTEM: DEGRADED — 1 model degraded, 1 healthy`.
- **Empty/error:** grey bar `SYSTEM: UNKNOWN — no metrics received in last <staleness> s`.

#### 4.1.2 Active Model Card
- **Shows:** which model currently serves traffic.
- **Fields:** `model_name`, `version`, `role = ACTIVE`, `port`, current health badge,
  `latency_ms`, `error_rate`, `accuracy`, `confidence` (latest values), "active since" if
  derivable from the last `SWITCH`/`ROLLBACK` action.
- **Source:** `registry_app` (`active_flag = True`) joined with latest `monitoring_app` row.
- **Render:** prominent card with the active model name large, a health badge, and a 2×2
  mini-metric grid. A link "View detail →" to `/dashboard/models/<model_name>/`.
- **Status coding:** card left border colored by health (§5.1).
- **Empty/error:** if no active model resolvable, red card `NO ACTIVE MODEL` (a real
  incident condition — cross-ref `failure_scenarios.md`).

#### 4.1.3 KPI Tiles (row of 4–6 tiles)
- **Shows:** headline current numbers for the active model with sparkline trend.
- **Tiles & fields:**
  - **Accuracy** — `accuracy` (latest, %), tiny sparkline of last N cycles.
  - **Confidence** — `confidence` (latest, %), sparkline.
  - **Latency (p95)** — `latency_ms` (ms), sparkline.
  - **Error rate** — `error_rate` (%), sparkline.
  - **Drift** — `drift_score` (max feature PSI), banded color (§5.4).
  - **Open incidents** — count of incidents with status ≠ `RESOLVED`.
- **Source:** `monitoring_app` (latest + short window) and `actions_app`.
- **Render:** equal-width tiles; big number + unit + small Chart.js sparkline line.
- **Status coding:** each tile tinted/badged when the metric crosses its threshold
  (e.g., error rate tile turns amber/red) — thresholds from `monitoring_and_metrics.md`.
- **Empty/error:** tile shows `—` with caption `no data`.

#### 4.1.4 Models-at-a-Glance Strip
- **Shows:** every registered model (`model_a`, `model_b`) as a compact chip.
- **Fields per chip:** `model_name`, `role` (ACTIVE/BACKUP), health badge, `version`,
  `drift` band dot.
- **Source:** `registry_app` + latest `monitoring_app`.
- **Render:** horizontal chips, each linking to the model detail page.

#### 4.1.5 Recent Actions (mini audit feed)
- **Shows:** last ~10 agent actions (the ACTION HISTORY deliverable, summarized).
- **Fields:** `created_at`, `action` (icon+badge), `severity` badge, `target_model`,
  `outcome` badge, truncated `reason`.
- **Source:** `actions_app`, ordered by `created_at desc`, limit 10.
- **Render:** dense table/list; each row links to its incident detail. A "View full log →"
  link to `/dashboard/actions/`.
- **Status coding:** action-type badge (§5.3) + outcome color (§5.3).
- **Empty/error:** `No agent actions recorded yet.`

#### 4.1.6 Active Incidents Panel
- **Shows:** currently open incidents needing attention.
- **Fields:** `incident_id`, opened-at, severity badge, affected `target_model`, current
  status (`OPEN` / `RECOVERING` / `RESOLVED`), latest action in the chain.
- **Source:** `actions_app` grouped into incidents (open only).
- **Render:** cards or rows; each links to incident detail. If manual controls enabled
  (§1.4), an **Acknowledge** button appears per row.
- **Empty/error:** green pill `No active incidents — system nominal.`

### 4.2 Model Detail — `/dashboard/models/<model_name>/`

Deep-dive for one model. Goal: diagnose *why* a model is in its current health.

#### 4.2.1 Model Header
- **Shows:** identity + current posture.
- **Fields:** `model_name`, `role`, `version`, `active_flag`, `port`, `status`,
  derived **health badge**, "last metric at" timestamp.
- **Source:** `registry_app` + latest `monitoring_app`.
- **Render:** header bar with health-colored accent; toggle for time window
  (`15m / 1h / 6h / 24h`) that re-queries the series endpoints.
- **Empty/error:** `UNKNOWN` badge + banner `No metrics for <model_name> in selected window`.

#### 4.2.2 Metric Time-Series (4 line charts)
- **Shows:** the four core quality/perf metrics over the selected window.
- **Charts (Chart.js line):**
  1. **Accuracy** (`accuracy`, y-axis 0–1 / %) with a dashed **threshold line** at the
     configured accuracy floor.
  2. **Confidence** (`confidence`, 0–1 / %).
  3. **Latency p95** (`latency_ms`, ms) with dashed threshold line at the latency SLO.
  4. **Error rate** (`error_rate`, %) with dashed threshold line at the error budget.
- **Fields per point:** `ts` (x), metric value (y).
- **Source:** `/dashboard/api/models/<model_name>/series/?metric=<m>&window=<w>` →
  `monitoring_app`.
- **Status coding:** line colored neutral blue; threshold line red dashed; points/areas
  above (latency/error) or below (accuracy) the threshold shaded amber/red.
- **Empty/error:** chart canvas replaced by `No data in window` placeholder.

#### 4.2.3 Drift Panel (per-feature)
- **Shows:** the DRIFT INDICATORS deliverable for this model.
- **Fields:** per `feature_name`: `psi`, `band`.
- **Render:** horizontal **bar chart** of PSI per feature (one bar per feature), bars
  colored by band (§5.4); plus a numeric table beside it (`feature_name | psi | band`).
  Optional **heatmap** variant (features × recent cycles, cell colored by band).
- **Source:** `/dashboard/api/drift/<model_name>/`.
- **Status coding:** green `<0.1`, amber `0.1–0.25`, red `>0.25` (§5.4). A summary line:
  `3 features drifting (2 red, 1 amber)`.
- **Empty/error:** `No drift computed yet (need a reference + current window).`

#### 4.2.4 Drifted-Feature Count Trend
- **Shows:** how many features exceed the amber/red threshold over time.
- **Render:** Chart.js line/area, x = `ts`, y = count of features with `psi ≥ 0.1`.
- **Source:** drift records over the window.
- **Empty/error:** `No drift history yet.`

#### 4.2.5 Version & Activity Info
- **Shows:** registry facts + recent lifecycle events for this model.
- **Fields:** `version`, `active_flag`, `status`, list of recent `SWITCH/ROLLBACK/RETRAIN/
  DISABLE` actions where `target_model`/`from_model`/`to_model` is this model.
- **Source:** `registry_app` + `actions_app` filtered to this model.
- **Render:** small definition list + a compact action sub-table.

### 4.3 Drift Dashboard — `/dashboard/drift/`

Cross-cutting drift focus across all models.

#### 4.3.1 Per-Feature Drift Scores (per model)
- **Shows:** for each model, the per-feature PSI bar chart (as in §4.2.3) side by side.
- **Source:** `/dashboard/api/drift/<model_name>/` for each model.
- **Status coding:** banded (§5.4).
- **Empty/error:** per-model `No drift data`.

#### 4.3.2 Drifted-Feature Count Over Time (overlay)
- **Shows:** time-series of drifted-feature count, one line per model overlaid.
- **Render:** Chart.js multi-series line.
- **Source:** drift records over window.

#### 4.3.3 Reference-vs-Current Distribution Comparison
- **Shows:** for a selected `feature_name`, the **reference** (training) histogram vs the
  **current** (recent window) histogram, the source of the PSI computation.
- **Fields:** binned counts/frequencies for reference and current.
- **Render:** Chart.js grouped **bar chart** (two bars per bin: reference vs current) or
  overlaid step lines; the computed `psi` and `band` shown above the chart.
- **Source:** `/dashboard/api/drift/<model_name>/` (includes `reference_hist`,
  `current_hist`, `bins`).
- **Status coding:** PSI value badge banded (§5.4).
- **Empty/error:** `Select a feature to compare distributions.` / `No reference snapshot.`

### 4.4 Incidents / Timeline — `/dashboard/incidents/`

#### 4.4.1 Incident List
- **Shows:** incidents (a correlated cluster of actions sharing an `incident_id`).
- **Fields:** `incident_id`, opened-at (`created_at` of first action), severity (max in
  chain), affected `target_model`, status (`OPEN`/`RECOVERING`/`RESOLVED`), resolution
  action, duration, `acknowledged_by`.
- **Source:** `actions_app` grouped by `incident_id`.
- **Render:** sortable/filterable table (filter by status, severity, model). Rows link to
  incident detail.
- **Status coding:** severity badge (§5.2); status pill (open=amber, recovering=blue,
  resolved=green).
- **Empty/error:** `No incidents recorded.`

#### 4.4.2 Timeline View
- **Shows:** incidents arranged on a vertical/horizontal time axis.
- **Render:** a simple CSS timeline — each incident a marker colored by severity, hover/
  click for summary. Optional Chart.js scatter (x=time, y=severity).
- **Source:** same as list.

### 4.5 Incident Detail — `/dashboard/incidents/<int:incident_id>/`

#### 4.5.1 Incident Summary Header
- **Fields:** `incident_id`, severity, affected model, opened/closed timestamps, duration,
  final outcome, `acknowledged_by`/`acknowledged_at`.
- **Source:** `actions_app` aggregate over the chain.

#### 4.5.2 Action Chain (the loop, made visible)
- **Shows:** the ordered sequence of agent steps for this incident, mapped to the loop:
  **Detect → Decide → Act → Verify**.
- **Fields per step:** `created_at`, `action`, `severity`, `reason`, `target_model`,
  `from_model`/`to_model`, `outcome`, `completed_at`.
- **Render:** vertical stepper; each step a card with the action badge (§5.3) and outcome
  badge; arrows between steps. A `ROLLED_BACK` outcome renders a distinct red branch.
- **Source:** `actions_app` filtered by `incident_id`, ordered by `created_at`.

#### 4.5.3 Justifying Metrics
- **Shows:** the metric chart(s) for the affected model spanning the incident window, so a
  viewer sees the breach that triggered the chain and the recovery afterward.
- **Render:** Chart.js line with the incident window shaded; threshold lines drawn.
- **Source:** `monitoring_app` for the affected model over `[opened-δ, closed+δ]`.

### 4.6 Action / Audit Log — `/dashboard/actions/`

The full ACTION HISTORY deliverable.

#### 4.6.1 Filter Bar
- **Controls:** filter by `action` (multiselect of the 6 action types), `severity`,
  `outcome`, `target_model`, free-text `reason` search, and a date range.
- **Render:** GET query params (`?action=SWITCH&severity=HIGH&model=model_a`) so filtered
  views are bookmarkable/shareable; client-side narrowing for instant feel.

#### 4.6.2 Audit Table
- **Shows:** every row in `actions_app`.
- **Columns (sortable):** `created_at`, `action` (icon+badge), `severity` (badge),
  `target_model`, `from_model→to_model`, `outcome` (badge), `reason`, `incident_id`
  (link), `completed_at`, latency (completed−created).
- **Source:** `actions_app`, paginated (Django `Paginator`, 50/page); default order
  `created_at desc`.
- **Status coding:** §5.3 (action + outcome). Manual actions (`reason` starts `manual:`)
  carry a small "human" tag to distinguish from autonomous actions.
- **Empty/error:** `No actions match the current filters.`

---

## 5. Visual Design & Status Coding

A single, consistent palette is used everywhere. Colors are defined as CSS variables in
`dashboard.css` and applied via status classes. **Color is never the only signal** — every
state also carries text and/or an icon/shape (see §9).

### 5.1 Health status (model & system)

| Health | Color | CSS var | Hex | Icon/shape | Meaning |
|--------|-------|---------|-----|-----------|---------|
| **HEALTHY** | Green | `--c-healthy` | `#1e8e3e` | ● solid / ✓ | Within all thresholds. |
| **DEGRADED** | Amber | `--c-degraded` | `#f9a825` | ◐ / ! | One soft threshold breached; watch. |
| **CRITICAL** | Red | `--c-critical` | `#d93025` | ▲ / ✕ | Hard breach or model down; action expected. |
| **UNKNOWN** | Grey | `--c-unknown` | `#9aa0a6` | ○ / ? | No recent metrics / stale data. |

Applied to: system banner, model cards/headers (left border + badge), model chips.

### 5.2 Severity badges (incidents & actions)

| Severity | Color | Hex | Badge text |
|----------|-------|-----|-----------|
| **LOW** | Slate/blue-grey | `#5f6368` | `LOW` |
| **MEDIUM** | Amber | `#f9a825` | `MED` |
| **HIGH** | Red | `#d93025` | `HIGH` |

Rendered as rounded pill badges with white text.

### 5.3 Action-type badges & outcome coding

**Action types** (each gets an icon + colored badge; icons are unicode/inline SVG so no
icon-font dependency):

| Action | Icon | Badge color | Notes |
|--------|------|-------------|-------|
| **NO_OP** | ○ | grey | Safe default; low visual weight. |
| **ALERT** | 🔔 / ! | amber | Informational, no fleet change. |
| **SWITCH** | ⇄ | blue | Promote backup → active. |
| **ROLLBACK** | ↩ | purple | Restore previous version. |
| **RETRAIN** | ⟳ | teal | Retrain-and-deploy (simulated). |
| **DISABLE** | ⛔ / ▣ | red | Take model out of service. |

**Outcomes** (badge color):

| Outcome | Color |
|---------|-------|
| `SUCCESS` | green |
| `NO_OP` | grey |
| `ACKNOWLEDGED` | blue |
| `PENDING` | amber (animated dot) |
| `ROLLED_BACK` | purple |
| `FAILED` | red |

### 5.4 Drift severity bands (PSI thresholds)

Tied directly to the PSI thresholds in `monitoring_and_metrics.md`:

| Band | PSI range | Color | Hex | Interpretation |
|------|-----------|-------|-----|----------------|
| **green** | `PSI < 0.10` | green | `#1e8e3e` | No significant shift. |
| **amber** | `0.10 ≤ PSI ≤ 0.25` | amber | `#f9a825` | Moderate shift — monitor. |
| **red** | `PSI > 0.25` | red | `#d93025` | Significant shift — likely drift. |

Used for: drift bars, drift dots on chips/tiles, distribution-comparison PSI badge, and
the drift heatmap cells.

### 5.5 Health derivation rule (documented for consistency)

If health is not pre-computed by the agent, the dashboard derives it per model:

```
if no metric within STALENESS_SECONDS  -> UNKNOWN
elif status == "error" OR latency_ms > LATENCY_HARD OR error_rate > ERROR_HARD
     OR accuracy < ACCURACY_HARD OR drift_score > 0.25            -> CRITICAL
elif status == "degraded" OR latency_ms > LATENCY_SOFT OR error_rate > ERROR_SOFT
     OR accuracy < ACCURACY_SOFT OR drift_score >= 0.10           -> DEGRADED
else                                                              -> HEALTHY
```

Threshold constants come from `monitoring_and_metrics.md` (single source of truth) and are
read from Django settings / `.env`, never hard-coded in templates.

### 5.6 Layout & typography
- System font stack; base size 15–16px; tabular numbers for metric tiles.
- Card-based layout with subtle borders/shadows; generous whitespace for screenshots.
- Max content width ~1280px, centered; responsive down to tablet (§9).

---

## 6. Wireframes (ASCII)

### 6.1 Overview / Home — `/dashboard/`

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│  ML Monitoring & Auto-Recovery   [Overview] Models  Drift  Incidents  Actions  🔄  │
├──────────────────────────────────────────────────────────────────────────────────┤
│ ▌ SYSTEM: DEGRADED — 1 model degraded, 1 healthy        (as of 12:00:05 UTC) ░░░░ │  ← health banner (§4.1.1)
├──────────────────────────────────────────────────────────────────────────────────┤
│ ┌───────────────────────────┐  ┌──────┐┌──────┐┌──────┐┌──────┐┌──────┐┌───────┐  │
│ │ ACTIVE MODEL              ▌│  │ACCUR.││CONF. ││LAT.  ││ERROR ││DRIFT ││OPEN   │  │
│ │  model_a   v1.4.2  :8001  │  │ 91.2%││ 0.88 ││ 42ms ││0.10% ││ 0.07 ││INCID. │  │
│ │  ● DEGRADED               │  │ ╱╲_  ││ ‾╲_  ││ _╱‾  ││ __╱  ││green ││  1    │  │
│ │  acc 91.2  conf 0.88      │  └──────┘└──────┘└──────┘└──────┘└──────┘└───────┘  │  ← KPI tiles (§4.1.3)
│ │  lat 42ms  err 0.10%      │                                                      │
│ │  active since 11:32       │   Models: [model_a ACTIVE ●DEGRADED v1.4.2 •amber]   │  ← model chips (§4.1.4)
│ │  View detail →            │           [model_b BACKUP ●HEALTHY v1.4.1 •green]    │
│ └───────────────────────────┘                                                      │
├──────────────────────────────────────────────┬───────────────────────────────────┤
│ RECENT ACTIONS                  View full log→ │ ACTIVE INCIDENTS                  │
│ ┌────────┬────────┬──────┬───────┬──────────┐ │ ┌───────────────────────────────┐ │
│ │ 12:00  │⇄ SWITCH│ HIGH │SUCCESS│ acc<floor│ │ │ #42  HIGH  model_a            │ │
│ │ 11:58  │🔔 ALERT│ MED  │  —    │drift amber│ │ │ status: RECOVERING            │ │
│ │ 11:55  │○ NO_OP │ LOW  │ NO_OP │stable    │ │ │ last: SWITCH→model_b SUCCESS  │ │
│ │ 11:50  │○ NO_OP │ LOW  │ NO_OP │stable    │ │ │ [Acknowledge]  [Open detail→] │ │  ← (Ack only if enabled)
│ └────────┴────────┴──────┴───────┴──────────┘ │ └───────────────────────────────┘ │
└──────────────────────────────────────────────┴───────────────────────────────────┘
        Last updated 12:00:05 UTC · auto-refresh 10s · times shown in UTC
```

### 6.2 Model Detail — `/dashboard/models/model_a/`

```
┌──────────────────────────────────────────────────────────────────────────────────┐
│  ← Overview   MODEL: model_a  ●DEGRADED   v1.4.2  ACTIVE  :8001   last 12:00:05    │  ← header (§4.2.1)
│                                       Window: [15m] [1h] [6h] [24h]                 │
├───────────────────────────────────────────┬──────────────────────────────────────┤
│ ACCURACY (0–1)            floor ─ ─ ─ 0.90 │ CONFIDENCE (0–1)                      │
│  1.0┤‾‾‾‾‾╲___                              │  1.0┤‾‾‾‾╲__                          │
│  0.9┤- - - - -╲- - - - - - - - - - - - - - │  0.9┤- - - - ╲___                     │  ← metric time-series (§4.2.2)
│  0.8┤          ╲____                        │  0.8┤          ‾‾‾                    │
│     └────────────────── time →             │     └────────────────── time →        │
├───────────────────────────────────────────┼──────────────────────────────────────┤
│ LATENCY p95 (ms)          SLO ─ ─ ─ 200    │ ERROR RATE (%)        budget ─ ─ 1.0% │
│  200┤- - - - - - - - - - - - - - - - - - - │  1.0┤- - - - - - - - - - - - - - - - -│
│   42┤___╱‾‾╲__                              │  0.1┤_____╱‾╲__                        │
│     └────────────────── time →             │     └────────────────── time →        │
├──────────────────────────────────────────────────────────────────────────────────┤
│ DRIFT  — per-feature PSI                3 features drifting (2 red, 1 amber)        │
│  feat_0  ███░░░░░░░ 0.07  green          ┌──────────────────────────────────────┐ │
│  feat_1  ████████░░ 0.31  red  ▲         │ DRIFTED-FEATURE COUNT (PSI≥0.10)      │ │  ← drift panel (§4.2.3/4)
│  feat_2  █████░░░░░ 0.18  amber !        │   3┤        ___╱‾‾                     │ │
│  feat_3  █████████░ 0.42  red  ▲         │   0┤___╱‾‾‾                            │ │
│                                           │     └──────────────── time →          │ │
│                                           └──────────────────────────────────────┘ │
├──────────────────────────────────────────────────────────────────────────────────┤
│ VERSION & ACTIVITY   version 1.4.2 · active · recent: ⇄SWITCH(in) ↩ROLLBACK ...    │  ← version/activity (§4.2.5)
└──────────────────────────────────────────────────────────────────────────────────┘
        Last updated 12:00:05 UTC · auto-refresh 10s · UTC
```

---

## 7. Data Refresh & State

| Concern | Behavior |
|---------|----------|
| **Polling interval** | Default **10 s** (`DASHBOARD_REFRESH_SECONDS`, configurable per `.env`). Charts/tiles poll the `dashboard/api/*` endpoints; on success they patch in place (no full reload). |
| **Why polling is enough** | The agent runs on a fixed cadence (batch/near-real-time per architecture §1.1); new state appears at most once per agent cycle, so 10 s polling feels "live" without WebSockets. The refresh interval should be ≤ the agent cadence. |
| **"How live it feels"** | Each cycle, fresh metrics land in `monitoring_app` and any decision lands in `actions_app`; the poller surfaces them within one interval. A subtle pulse animation on updated tiles signals freshness. |
| **Last-updated display** | Every page footer shows `Last updated <HH:MM:SS> UTC` from the newest `ts`/`created_at` rendered, plus `auto-refresh Ns`. The 🔄 in the header toggles auto-refresh and offers a manual refresh. |
| **Staleness handling** | If the newest metric is older than `STALENESS_SECONDS` (default 60 s), health degrades to **UNKNOWN** and a banner warns `Data is stale — last metric <age> ago` (cross-ref `failure_scenarios.md`: Django/agent down). |
| **Timezone** | All timestamps are stored UTC (ISO-8601, matching `ts` in `api_contracts.md`). The UI displays **UTC by default** with an explicit `UTC` suffix; an optional toggle renders browser-local time via JS `toLocaleString()`. No server-side local conversion — avoids demo-machine ambiguity. |
| **Pause on hidden tab** | The poller checks `document.hidden` and pauses while the tab is backgrounded, resuming (and immediately fetching once) on focus. |
| **Error/backoff** | A failed poll keeps the last good values, shows a small amber dot next to "Last updated", and backs off (10s → 20s → 30s, capped) until success. |

---

## 8. Example View & Template Structure

Illustrative only — consistent with the field names in §3.1. Real serializers/models live
in the sibling apps.

### 8.1 `dashboard_app/urls.py`

```python
# control-plane/backend/dashboard_app/urls.py
from django.urls import path
from . import views

app_name = "dashboard"

urlpatterns = [
    path("", views.overview, name="overview"),
    path("models/<str:model_name>/", views.model_detail, name="model_detail"),
    path("drift/", views.drift_dashboard, name="drift"),
    path("incidents/", views.incidents, name="incidents"),
    path("incidents/<int:incident_id>/", views.incident_detail, name="incident_detail"),
    path("actions/", views.action_log, name="actions"),

    # JSON feeds for the JS poller / Chart.js
    path("api/summary/", views.api_summary, name="api_summary"),
    path("api/models/<str:model_name>/series/", views.api_model_series, name="api_model_series"),
    path("api/drift/<str:model_name>/", views.api_drift, name="api_drift"),
]
```

### 8.2 Representative view — `overview`

```python
# control-plane/backend/dashboard_app/views.py
from datetime import timedelta
from django.shortcuts import render
from django.utils import timezone

from monitoring_app.models import MetricSample          # latency_ms, error_rate, accuracy,
from registry_app.models import Model                    #   confidence, drift_score, status, ts
from actions_app.models import ActionLog                 # action, severity, outcome, reason, ...

STALENESS_SECONDS = 60
SOFT = {"accuracy": 0.93, "error_rate": 0.005, "latency_ms": 120}
HARD = {"accuracy": 0.90, "error_rate": 0.010, "latency_ms": 200}


def derive_health(sample):
    """Map the latest metric sample to HEALTHY/DEGRADED/CRITICAL/UNKNOWN (see spec §5.5)."""
    if sample is None or (timezone.now() - sample.ts).total_seconds() > STALENESS_SECONDS:
        return "UNKNOWN"
    if (sample.status == "error" or sample.latency_ms > HARD["latency_ms"]
            or sample.error_rate > HARD["error_rate"] or sample.accuracy < HARD["accuracy"]
            or (sample.drift_score or 0) > 0.25):
        return "CRITICAL"
    if (sample.status == "degraded" or sample.latency_ms > SOFT["latency_ms"]
            or sample.error_rate > SOFT["error_rate"] or sample.accuracy < SOFT["accuracy"]
            or (sample.drift_score or 0) >= 0.10):
        return "DEGRADED"
    return "HEALTHY"


def _latest_sample(model_name):
    return (MetricSample.objects
            .filter(model_name=model_name)
            .order_by("-ts")
            .first())


def overview(request):
    models = Model.objects.all().order_by("-active_flag", "model_name")
    cards = []
    for m in models:
        latest = _latest_sample(m.model_name)
        cards.append({"model": m, "latest": latest, "health": derive_health(latest)})

    active = next((c for c in cards if c["model"].active_flag), None)

    rank = {"HEALTHY": 0, "DEGRADED": 1, "CRITICAL": 2, "UNKNOWN": 1}
    system_health = max((c["health"] for c in cards),
                        key=lambda h: rank.get(h, 1), default="UNKNOWN")

    recent_actions = ActionLog.objects.order_by("-created_at")[:10]
    open_incidents = (ActionLog.objects
                      .exclude(outcome__in=["SUCCESS", "NO_OP"])
                      .filter(created_at__gte=timezone.now() - timedelta(hours=24))
                      .values("incident_id", "severity", "target_model")
                      .distinct())

    return render(request, "dashboard/overview.html", {
        "system_health": system_health,
        "cards": cards,
        "active": active,
        "recent_actions": recent_actions,
        "open_incidents": open_incidents,
        "refresh_seconds": 10,
        "last_updated": timezone.now(),
    })
```

### 8.3 JSON feed for Chart.js — `api_model_series`

```python
from django.http import JsonResponse

WINDOWS = {"15m": 15, "1h": 60, "6h": 360, "24h": 1440}

def api_model_series(request, model_name):
    metric = request.GET.get("metric", "accuracy")          # accuracy|confidence|latency_ms|error_rate
    minutes = WINDOWS.get(request.GET.get("window", "1h"), 60)
    since = timezone.now() - timedelta(minutes=minutes)
    qs = (MetricSample.objects
          .filter(model_name=model_name, ts__gte=since)
          .order_by("ts")
          .values_list("ts", metric))
    points = [{"t": ts.isoformat(), "y": val} for ts, val in qs]
    return JsonResponse({"model_name": model_name, "metric": metric, "points": points})
```

### 8.4 Template snippet — `templates/dashboard/model_detail.html` (Chart.js)

```html
{% extends "dashboard/base.html" %}
{% block content %}
<section class="model-header health-{{ health|lower }}">
  <h1>{{ model.model_name }}
      <span class="badge health-{{ health|lower }}">{{ health }}</span></h1>
  <p>v{{ model.version }} · {{ model.role }} · :{{ model.port }}
     · last {{ last_updated|date:"H:i:s" }} UTC</p>
</section>

<div class="chart-grid">
  <figure><figcaption>Accuracy</figcaption>
    <canvas id="chart-accuracy" height="160" aria-label="Accuracy time series"></canvas></figure>
  <figure><figcaption>Latency p95 (ms)</figcaption>
    <canvas id="chart-latency_ms" height="160" aria-label="Latency time series"></canvas></figure>
</div>

<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<script>
const MODEL = "{{ model.model_name|escapejs }}";
const REFRESH_MS = {{ refresh_seconds|default:10 }} * 1000;
const charts = {};

function makeChart(metric, threshold) {
  const ctx = document.getElementById("chart-" + metric);
  charts[metric] = new Chart(ctx, {
    type: "line",
    data: { datasets: [
      { label: metric, data: [], parsing: {xAxisKey: "t", yAxisKey: "y"},
        borderColor: "#1a73e8", tension: 0.2, pointRadius: 0 },
      { label: "threshold", data: [], borderColor: "#d93025",
        borderDash: [6, 4], pointRadius: 0 },
    ]},
    options: { responsive: true, animation: false,
      scales: { x: { type: "time" } } }
  });
  charts[metric]._threshold = threshold;
}

async function refresh(metric, window) {
  const r = await fetch(`/dashboard/api/models/${MODEL}/series/?metric=${metric}&window=${window}`);
  if (!r.ok) return;                                   // keep last good values on error
  const { points } = await r.json();
  const c = charts[metric];
  c.data.datasets[0].data = points;
  c.data.datasets[1].data = points.map(p => ({t: p.t, y: c._threshold}));
  c.update();
}

makeChart("accuracy", 0.90);
makeChart("latency_ms", 200);
function tick(){ if (document.hidden) return;
  refresh("accuracy", "1h"); refresh("latency_ms", "1h"); }
tick(); setInterval(tick, REFRESH_MS);
</script>
{% endblock %}
```

The `base.html` provides the nav bar, the auto-refresh toggle, the `Last updated ... UTC`
footer, and links `dashboard.css`.

---

## 9. Accessibility & UX

- **Tables:** sortable (click header, with `aria-sort`) and filterable (filter bar emits
  GET params so views are shareable/bookmarkable). Pagination via Django `Paginator`.
- **Color is never the sole signal:** every health/severity/band/outcome also carries
  **text and an icon/shape** (●/◐/▲/○, badges, ✓/!/✕), so the UI is usable with color
  vision deficiency and in greyscale screenshots. Target WCAG AA contrast.
- **Semantic HTML & ARIA:** `<table>`/`<th scope>` for data tables; charts carry
  `aria-label` and a visually-hidden data summary; status badges use `role="status"`;
  the live region announces "system health changed to …" politely.
- **Keyboard:** all controls (filters, sort headers, toggles, confirm modals) are tab-
  reachable with visible focus rings; modals trap focus and close on `Esc`.
- **Responsive:** CSS grid collapses 2-up chart grids and side-by-side panels to a single
  column on narrow viewports; the nav becomes a stacked menu. Tested down to ~768px.
- **Screenshot/demo friendly:** generous whitespace, large active-model card and KPI
  numbers, high-contrast badges, and a fixed max width so a single screenshot tells the
  whole story — useful for the demo deliverable.
- **No layout shift on refresh:** poller patches values/datasets in place; chart canvases
  keep fixed heights; empty states reserve space rather than collapsing.
- **Performance:** initial render is fully server-side (no blank flash); JS only enhances.

---

## 10. Tying the Loop Together

**What an operator sees during a live degradation + recovery** (read alongside
`failure_scenarios.md` — e.g. the *"active model accuracy degradation → SWITCH to backup"*
scenario):

1. **Steady state.** Overview shows `SYSTEM: HEALTHY`, active model **model_a v1.4.2**
   green, all KPI tiles within thresholds, `model_b` BACKUP green. Recent Actions are a
   quiet run of `NO_OP / LOW`. Active Incidents reads *"system nominal."*

2. **Drift creeps in (Observe + Detect).** Over a few cycles the **Drift** tile on Overview
   turns amber, and the active model's chip shows an amber drift dot. On **Model Detail →
   Drift Panel**, `feat_1` and `feat_3` cross into the **red band** (PSI > 0.25); the
   *drifted-feature count* trend ticks up. The accuracy time-series begins sliding toward
   its dashed `0.90` floor.

3. **Health flips (Detect).** The active-model card and system banner turn **DEGRADED**,
   then **CRITICAL** as `accuracy` breaches the hard floor. Recent Actions shows a
   `🔔 ALERT / MEDIUM` row appear (the agent recording evidence before acting). The
   "Last updated" stamp confirms this is fresh within one refresh interval.

4. **The agent decides and acts (Decide + Act).** A new row lands: `⇄ SWITCH / HIGH`,
   `from_model=model_a → to_model=model_b`, `reason="accuracy<floor; feat_1,feat_3 PSI>0.25"`,
   `outcome=PENDING` (animated). An **Active Incident #42** opens with status `OPEN →
   RECOVERING`. (Behind the scenes this is the agent → Jenkins `switch_active_model`
   pipeline; the dashboard only mirrors the resulting state.)

5. **Recovery verified (Verify).** Within a cycle the Active Model card now shows
   **model_b v1.4.1** as ACTIVE and **HEALTHY**; the SWITCH row's outcome flips to
   `SUCCESS`. The system banner returns to green. Incident #42 moves to `RESOLVED` with a
   recorded duration. (If `rollback_guard` had found model_b *worse*, the operator would
   instead see a `↩ ROLLBACK / ROLLED_BACK` branch in the incident's **action chain** —
   the dashboard renders that distinct red path.)

6. **The audit trail (transparency).** Opening **Incident #42 detail**, the operator reads
   the entire chain as a stepper — *Detect (drift/accuracy breach) → Decide (HIGH →
   SWITCH) → Act (Jenkins switch) → Verify (model_b healthy)* — each step time-stamped with
   its `reason` and `outcome`, the justifying metric chart shaded across the incident
   window. The full **Action Log** keeps the permanent, filterable record.

The net effect: the dashboard turns the agent's autonomous Observe→Detect→Decide→Act→Verify
loop into a **legible, auditable story**. The operator does not have to act — but can *see*
exactly what the agent saw, decided, did, and confirmed, and (if manual controls are
enabled) can acknowledge or, with guardrails, trigger a switch/rollback themselves.

---

*End of dashboard specification. See also: `docs/architecture.md` (topology & boundaries),
`docs/api_contracts.md` (endpoint schemas), `docs/monitoring_and_metrics.md` (metric & PSI
definitions), `docs/data_model.md` (Django model fields), `docs/failure_scenarios.md`
(recovery runbooks referenced in §10).*
