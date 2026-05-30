# API Contracts Reference

> **Autonomous ML Monitoring & Auto-Recovery Agent**
> Integration specification — *"One repo, many services, many environments, HTTP everywhere."*

This document is the **authoritative HTTP contract** between every service in the
platform. Each service can be built, mocked, and tested **independently** against
the schemas below. If an implementation and this document disagree, **this document
wins** until it is explicitly amended.

The agent runs a closed control loop:

```
Observe → Detect → Decide → Act → Verify → (loop)
```

The HTTP surfaces involved in that loop are:

| # | Surface | Tech | Port | Role | Direction |
|---|---------|------|------|------|-----------|
| A | Model Service `model_a` | FastAPI | `8001` | **ACTIVE** inference model | server (agent → model) |
| A | Model Service `model_b` | FastAPI | `8002` | **BACKUP** inference model | server (agent → model) |
| B | Control-Plane Backend | Django + DRF | `8000` | metrics store, registry, audit, dashboard | server (agent → django) |
| C | Jenkins | Jenkins core | `8080` | executes recovery jobs | server (agent → jenkins) |
| D | Agent Core | Python (pydantic) | — | the brain; **HTTP client only**, no server | client |

> The **Agent Core** never listens on a socket. It is purely an outbound HTTP client
> that talks to A, B, and C through `agent_core/clients/django_client.py` and
> `agent_core/clients/jenkins_client.py`.

---

## Table of Contents

- [Conventions (read this first)](#conventions-read-this-first)
- [A. Model Service API (`model_a` :8001 / `model_b` :8002)](#a-model-service-api)
  - [`POST /predict`](#a1-post-predict)
  - [`GET /health`](#a2-get-health)
  - [`GET /metrics`](#a3-get-metrics)
- [B. Django Control-Plane API (:8000)](#b-django-control-plane-api)
  - [`monitoring_app`](#b1-monitoring_app)
  - [`registry_app`](#b2-registry_app)
  - [`actions_app`](#b3-actions_app)
- [C. Jenkins Execution API (:8080)](#c-jenkins-execution-api)
- [D. Internal Pydantic Schemas (`agent_core/schemas.py`)](#d-internal-pydantic-schemas)
- [Cross-surface consistency matrix](#cross-surface-consistency-matrix)

---

## Conventions (read this first)

### Base URLs / environment

All hostnames and ports are injected from the repo-root `.env` file and read by
`agent_core/config.py`. **Never hardcode URLs.** Inside the Docker network the
service DNS names are the compose service names; from the host they are
`localhost`.

| `.env` variable | Default (docker network) | Default (host) | Used by |
|-----------------|--------------------------|----------------|---------|
| `MODEL_A_URL`   | `http://model_a:8001`    | `http://localhost:8001` | agent → model_a |
| `MODEL_B_URL`   | `http://model_b:8002`    | `http://localhost:8002` | agent → model_b |
| `DJANGO_BASE_URL` | `http://backend:8000`  | `http://localhost:8000` | agent → django |
| `JENKINS_BASE_URL` | `http://jenkins:8080` | `http://localhost:8080` | agent → jenkins |
| `DJANGO_API_TOKEN` | _(secret)_            | _(secret)_     | agent → django auth |
| `JENKINS_USER`     | `agent`              | `agent`        | jenkins basic-auth user |
| `JENKINS_API_TOKEN`| _(secret)_           | _(secret)_     | jenkins basic-auth token |

All Django REST paths are mounted under the `/api/` prefix. All Model Service
paths sit at the root.

### Content types

- **Requests with a body:** `Content-Type: application/json` (Jenkins
  `buildWithParameters` is the one exception — see [Section C](#c-jenkins-execution-api)).
- **Responses:** `application/json` for every endpoint **except** the dashboard
  HTML pages (`text/html`) and `model GET /metrics` which may *also* be served as
  Prometheus text if `Accept: text/plain` is sent (JSON is the contract default).
- Encoding is UTF-8 everywhere. Timestamps are **ISO-8601 with timezone** in UTC,
  e.g. `2026-05-30T14:21:07.512Z`.

### Authentication model

| Surface | Scheme | Header |
|---------|--------|--------|
| Model services | **none** (trusted intra-network) | — |
| Django control plane | **Token auth** (DRF `TokenAuthentication`) | `Authorization: Token <DJANGO_API_TOKEN>` |
| Jenkins | **HTTP Basic** with user + API token | `Authorization: Basic base64(JENKINS_USER:JENKINS_API_TOKEN)` |

> The model services are deliberately unauthenticated: they are only reachable on
> the internal Docker network and must stay cheap to probe.

### Timeouts, retries, idempotency

| Concern | Policy |
|---------|--------|
| Connect timeout | `2s` for all clients |
| Read timeout | `5s` for `/predict` & `/metrics`; `1.5s` for `/health`; `10s` for Jenkins trigger; `30s` for Jenkins poll loop |
| Retries | Idempotent **GET**s: up to **3** attempts with exponential backoff (`0.25s, 0.5s, 1s`). **POST/PUT/PATCH**: **no automatic retry** unless an idempotency key is supplied |
| Idempotency key | For state-changing POSTs the agent MAY send `Idempotency-Key: <uuid4>`. Django stores it on `ActionLog`/`MetricSnapshot` and returns the original result (HTTP `200`) for a duplicate key instead of creating a second row |
| Probe failure | A timeout/connection error on a model probe is itself an **observation signal** (treated as `error` / unreachable), not a hard crash of the agent loop |

### Error envelope

Every non-2xx JSON response from the **Django** and **Model** services uses this
single envelope shape:

```json
{
  "error": {
    "code": "string_machine_code",
    "message": "Human readable explanation.",
    "details": { "field": "optional per-field info" },
    "request_id": "req_8f3c9a2b",
    "timestamp": "2026-05-30T14:21:07.512Z"
  }
}
```

| Field | Type | Notes |
|-------|------|-------|
| `error.code` | string | Stable, machine-parseable (e.g. `validation_error`, `not_found`, `model_not_loaded`) |
| `error.message` | string | Safe to log/surface to the dashboard |
| `error.details` | object \| null | DRF field errors keyed by field name, or extra context |
| `error.request_id` | string | Correlation id; echoed from `X-Request-ID` if sent, else generated |
| `error.timestamp` | string | ISO-8601 UTC |

> Jenkins does **not** use this envelope — it returns its own HTTP statuses and a
> `Location` header. See [Section C](#c-jenkins-execution-api).

### Standard status codes

| Code | Meaning in this platform |
|------|--------------------------|
| `200 OK` | Successful read or update |
| `201 Created` | New resource created (metric snapshot, action log, registry entry) |
| `202 Accepted` | Jenkins accepted a build trigger (async) |
| `204 No Content` | Successful delete (rare; soft-delete preferred) |
| `400 Bad Request` | Malformed JSON / schema validation failure |
| `401 Unauthorized` | Missing/invalid token |
| `403 Forbidden` | Token valid but action not permitted |
| `404 Not Found` | Unknown resource id or route |
| `409 Conflict` | Idempotency / state conflict (e.g. activating an already-active model) |
| `422 Unprocessable Entity` | Semantically invalid (e.g. feature vector wrong length) |
| `429 Too Many Requests` | Rate limited |
| `500 Internal Server Error` | Unhandled server fault |
| `503 Service Unavailable` | Model not loaded / dependency down |

### Versioning

The HTTP surface is versioned by a response header `X-API-Version: 1` on every
service. Breaking changes bump the major in this header and, for Django, may move
routes under `/api/v2/`. The current contract is **v1** and lives at the
unversioned `/api/` prefix.

### Pagination (Django list endpoints)

DRF page-number pagination, page size **50** (max **500** via `?limit=`):

```json
{
  "count": 184,
  "next": "http://backend:8000/api/metrics?page=3",
  "previous": "http://backend:8000/api/metrics?page=1",
  "results": [ /* array of resource objects */ ]
}
```

Common query params on list endpoints: `?page=`, `?limit=` (alias for page size),
`?ordering=` (e.g. `-timestamp`).

### Per-endpoint template

Every endpoint below is documented with: **Method & Path**, **Purpose**,
**Caller → Callee**, **Request headers**, **Request body (schema + example)**,
**Responses (per status, schema + example)**, and **Error cases**.

---

## A. Model Service API

> Identical contract for `model_a` (port `8001`, ACTIVE) and `model_b`
> (port `8002`, BACKUP). The only difference is the `model_version`/`model_name`
> values they report. FastAPI; root-mounted paths; no auth.

### Feature vector definition

`sample_input.csv` is treated as a **generic tabular feature vector**. The contract
fixes the column order and types so the request schema is stable regardless of the
underlying model. The canonical schema (six numeric features) is:

| Column | JSON key | Type | Example | Notes |
|--------|----------|------|---------|-------|
| `feature_1` | `feature_1` | float | `0.731` | continuous |
| `feature_2` | `feature_2` | float | `-1.204` | continuous |
| `feature_3` | `feature_3` | float | `12.0` | continuous |
| `feature_4` | `feature_4` | float | `3.5` | continuous |
| `feature_5` | `feature_5` | float | `0.0` | continuous |
| `feature_6` | `feature_6` | float | `8.9` | continuous |

A request may submit features either as a **named object** (preferred) or a
**positional array** (`"features": [f1..f6]`). Both forms map to the same internal
vector; the object form is validated by name, the array form by length and order.

---

### A.1 `POST /predict`

**Purpose:** Run inference on one feature vector (or a small batch) and return the
prediction together with the model's confidence, the model version, and the
measured server-side latency. This is the agent's `prediction_probe`.

**Caller → Callee:** `agent_core/monitoring/prediction_probe.py` → `model_a`/`model_b`
(`POST {MODEL_*_URL}/predict`). Also callable by the dashboard for manual testing.

**Request headers**

| Header | Value | Required |
|--------|-------|----------|
| `Content-Type` | `application/json` | yes |
| `X-Request-ID` | correlation id | optional |

**Request body schema**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `features` | object \| array | yes | Named feature object **or** ordered array of 6 floats |
| `request_id` | string | no | Client correlation id (mirrors `X-Request-ID`) |
| `return_proba` | bool | no (default `true`) | Include class probability vector |

**Request example (named object form)**

```json
{
  "features": {
    "feature_1": 0.731,
    "feature_2": -1.204,
    "feature_3": 12.0,
    "feature_4": 3.5,
    "feature_5": 0.0,
    "feature_6": 8.9
  },
  "return_proba": true,
  "request_id": "req_8f3c9a2b"
}
```

**Request example (positional array form)**

```json
{ "features": [0.731, -1.204, 12.0, 3.5, 0.0, 8.9] }
```

**Response `200 OK` schema**

| Field | Type | Description |
|-------|------|-------------|
| `prediction` | number \| string | Predicted class label or regression value |
| `confidence` | float `[0,1]` | Max class probability / model confidence |
| `probabilities` | array<float> \| null | Per-class probabilities (omitted if `return_proba=false`) |
| `model_name` | string | `"model_a"` or `"model_b"` |
| `model_version` | string | Semantic version of the loaded artifact, e.g. `"1.4.2"` |
| `latency_ms` | float | Server-measured inference latency in milliseconds |
| `request_id` | string | Echoed correlation id |
| `timestamp` | string | ISO-8601 UTC |

**Response `200 OK` example**

```json
{
  "prediction": 1,
  "confidence": 0.92,
  "probabilities": [0.08, 0.92],
  "model_name": "model_a",
  "model_version": "1.4.2",
  "latency_ms": 7.41,
  "request_id": "req_8f3c9a2b",
  "timestamp": "2026-05-30T14:21:07.512Z"
}
```

**Error responses**

| Status | `error.code` | When |
|--------|--------------|------|
| `400` | `invalid_json` | Body is not valid JSON |
| `422` | `feature_validation_error` | Missing feature, wrong type, or array length ≠ 6 |
| `503` | `model_not_loaded` | `model.pkl` not yet loaded / artifact missing |
| `500` | `inference_error` | Unhandled exception during `model.predict()` |

**`422` example**

```json
{
  "error": {
    "code": "feature_validation_error",
    "message": "Expected 6 features, received 5.",
    "details": { "features": "missing feature_6" },
    "request_id": "req_8f3c9a2b",
    "timestamp": "2026-05-30T14:21:07.512Z"
  }
}
```

---

### A.2 `GET /health`

**Purpose:** Liveness/readiness probe. Tells the agent whether the process is up
**and** whether the model artifact is loaded and servable.

**Caller → Callee:** `agent_core/monitoring/model_probe.py` and
`agent_core/verification/health_check.py` → model service (`GET {MODEL_*_URL}/health`).
Also used by Docker/compose health checks.

**Request headers:** none required.

**Request body:** none.

**Response `200 OK` schema**

| Field | Type | Description |
|-------|------|-------------|
| `status` | enum `healthy` \| `degraded` \| `unhealthy` | Overall readiness |
| `model_loaded` | bool | `true` once `model.pkl` is in memory |
| `model_name` | string | `"model_a"` / `"model_b"` |
| `version` | string | Loaded artifact version |
| `uptime_seconds` | float | Seconds since process start |
| `timestamp` | string | ISO-8601 UTC |

**Response `200 OK` example**

```json
{
  "status": "healthy",
  "model_loaded": true,
  "model_name": "model_a",
  "version": "1.4.2",
  "uptime_seconds": 38271.5,
  "timestamp": "2026-05-30T14:21:07.512Z"
}
```

**Response `503 Service Unavailable`** — returned when the process is up but the
model is not loaded (still readiness-failing):

```json
{
  "status": "unhealthy",
  "model_loaded": false,
  "model_name": "model_a",
  "version": null,
  "uptime_seconds": 2.1,
  "timestamp": "2026-05-30T14:21:07.512Z"
}
```

> Note: `/health` returns its status object even on `503` (it is the readiness
> signal), so it deliberately does **not** use the standard error envelope.

---

### A.3 `GET /metrics`

**Purpose:** Return the model service's **own rolling operational metrics** over a
sliding window. The agent's `Observe` phase pulls this and folds it into a
`MetricSnapshot` for Django.

**Caller → Callee:** `agent_core/monitoring/model_probe.py` → model service
(`GET {MODEL_*_URL}/metrics`).

**Request headers**

| Header | Value | Required |
|--------|-------|----------|
| `Accept` | `application/json` (default) or `text/plain` (Prometheus) | optional |

**Query params**

| Param | Type | Default | Description |
|-------|------|---------|-------------|
| `window` | string | `5m` | Rolling window: `1m`, `5m`, `15m`, or `all` |

**Response `200 OK` schema**

| Field | Type | Description |
|-------|------|-------------|
| `model_name` | string | `"model_a"` / `"model_b"` |
| `model_version` | string | Loaded artifact version |
| `window` | string | Echo of the active window, e.g. `"5m"` |
| `request_count` | int | Requests served in window |
| `error_count` | int | Failed requests in window |
| `error_rate` | float `[0,1]` | `error_count / max(request_count,1)` |
| `avg_latency_ms` | float | Mean `/predict` latency in window |
| `p95_latency_ms` | float | 95th-percentile latency in window |
| `avg_confidence` | float `[0,1]` | Mean prediction confidence in window |
| `timestamp` | string | ISO-8601 UTC, window end |

**Response `200 OK` example**

```json
{
  "model_name": "model_a",
  "model_version": "1.4.2",
  "window": "5m",
  "request_count": 1240,
  "error_count": 9,
  "error_rate": 0.0073,
  "avg_latency_ms": 8.6,
  "p95_latency_ms": 21.3,
  "avg_confidence": 0.88,
  "timestamp": "2026-05-30T14:21:07.512Z"
}
```

**Error responses**

| Status | `error.code` | When |
|--------|--------------|------|
| `400` | `invalid_window` | `window` not in allowed set |
| `503` | `model_not_loaded` | Metrics unavailable because model never loaded |

---

## B. Django Control-Plane API

> Django + Django REST Framework on port `8000`, all routes mounted under `/api/`.
> Auth: `Authorization: Token <DJANGO_API_TOKEN>` on **every** request.
> Standard error envelope and pagination apply.

### B.1 `monitoring_app`

Stores the observation snapshots the agent pushes each loop and lets the
dashboard/agent query history. The DRF serializer `MetricSnapshotSerializer`
exposes these fields (this is the **wire shape** that must match the pydantic
`MetricSnapshot` in [Section D](#d-internal-pydantic-schemas)):

| Field | Type | Read-only | Description |
|-------|------|-----------|-------------|
| `id` | int | yes | PK |
| `model_name` | string | no | Which model the snapshot is about |
| `model_version` | string | no | Artifact version observed |
| `request_count` | int | no | From model `/metrics` |
| `error_count` | int | no | From model `/metrics` |
| `error_rate` | float | no | From model `/metrics` |
| `avg_latency_ms` | float | no | From model `/metrics` |
| `p95_latency_ms` | float | no | From model `/metrics` |
| `avg_confidence` | float | no | From model `/metrics` |
| `accuracy` | float \| null | no | Optional evaluated accuracy/F1 |
| `status` | enum `healthy`\|`degraded`\|`unhealthy` | no | Health derived from `/health` |
| `window` | string | no | Window the metrics cover |
| `source` | enum `agent`\|`model`\|`manual` | no | Who created it (default `agent`) |
| `timestamp` | string (ISO-8601) | no | Observation time (client-supplied) |
| `created_at` | string (ISO-8601) | yes | Server insert time |

---

#### `POST /api/metrics`

**Purpose:** Agent pushes one observation snapshot per model per loop iteration.

**Caller → Callee:** `agent_core/clients/django_client.py` →
`POST {DJANGO_BASE_URL}/api/metrics`.

**Request headers**

| Header | Value | Required |
|--------|-------|----------|
| `Authorization` | `Token <DJANGO_API_TOKEN>` | yes |
| `Content-Type` | `application/json` | yes |
| `Idempotency-Key` | uuid4 | optional |

**Request body example** (exactly the JSON-serialized `MetricSnapshot`)

```json
{
  "model_name": "model_a",
  "model_version": "1.4.2",
  "request_count": 1240,
  "error_count": 9,
  "error_rate": 0.0073,
  "avg_latency_ms": 8.6,
  "p95_latency_ms": 21.3,
  "avg_confidence": 0.88,
  "accuracy": 0.91,
  "status": "healthy",
  "window": "5m",
  "source": "agent",
  "timestamp": "2026-05-30T14:21:07.512Z"
}
```

**Response `201 Created`** — the stored object including `id` and `created_at`:

```json
{
  "id": 5821,
  "model_name": "model_a",
  "model_version": "1.4.2",
  "request_count": 1240,
  "error_count": 9,
  "error_rate": 0.0073,
  "avg_latency_ms": 8.6,
  "p95_latency_ms": 21.3,
  "avg_confidence": 0.88,
  "accuracy": 0.91,
  "status": "healthy",
  "window": "5m",
  "source": "agent",
  "timestamp": "2026-05-30T14:21:07.512Z",
  "created_at": "2026-05-30T14:21:08.004Z"
}
```

**Error responses**

| Status | `error.code` | When |
|--------|--------------|------|
| `400` | `validation_error` | Field missing / wrong type (DRF errors in `details`) |
| `401` | `authentication_failed` | Missing/invalid token |
| `200` | — (duplicate key) | `Idempotency-Key` already seen → original row returned |

`400` example:

```json
{
  "error": {
    "code": "validation_error",
    "message": "Invalid metric snapshot.",
    "details": { "error_rate": ["A valid number is required."] },
    "request_id": "req_a91f",
    "timestamp": "2026-05-30T14:21:08.010Z"
  }
}
```

---

#### `GET /api/metrics`

**Purpose:** Query historical snapshots (dashboard charts, agent trend analysis).

**Caller → Callee:** dashboard & `agent_core` → `GET {DJANGO_BASE_URL}/api/metrics`.

**Query params**

| Param | Type | Description |
|-------|------|-------------|
| `model` | string | Filter by `model_name` (e.g. `model_a`) |
| `status` | string | Filter by health status |
| `since` | ISO-8601 | Lower bound on `timestamp` |
| `until` | ISO-8601 | Upper bound on `timestamp` |
| `limit` | int | Page size (max 500) |
| `page` | int | Page number |
| `ordering` | string | e.g. `-timestamp` (default) |

**Request example**

```
GET /api/metrics?model=model_a&since=2026-05-30T14:00:00Z&limit=2&ordering=-timestamp
Authorization: Token <DJANGO_API_TOKEN>
```

**Response `200 OK`** (paginated envelope)

```json
{
  "count": 184,
  "next": "http://backend:8000/api/metrics?model=model_a&page=2&limit=2",
  "previous": null,
  "results": [
    {
      "id": 5821,
      "model_name": "model_a",
      "model_version": "1.4.2",
      "request_count": 1240,
      "error_count": 9,
      "error_rate": 0.0073,
      "avg_latency_ms": 8.6,
      "p95_latency_ms": 21.3,
      "avg_confidence": 0.88,
      "accuracy": 0.91,
      "status": "healthy",
      "window": "5m",
      "source": "agent",
      "timestamp": "2026-05-30T14:21:07.512Z",
      "created_at": "2026-05-30T14:21:08.004Z"
    }
  ]
}
```

**Errors:** `401 authentication_failed`, `400 validation_error` (bad date/filter).

---

#### `GET /api/metrics/latest`

**Purpose:** Convenience endpoint returning the single most recent snapshot per
model — used by the dashboard "current status" tiles and the agent's quick check.

**Caller → Callee:** dashboard & `agent_core` → `GET {DJANGO_BASE_URL}/api/metrics/latest`.

**Query params:** `?model=model_a` (optional; omit to get latest for **each** model).

**Response `200 OK`** (object keyed by model when no filter):

```json
{
  "model_a": {
    "id": 5821, "model_name": "model_a", "model_version": "1.4.2",
    "error_rate": 0.0073, "avg_latency_ms": 8.6, "p95_latency_ms": 21.3,
    "avg_confidence": 0.88, "accuracy": 0.91, "status": "healthy",
    "window": "5m", "source": "agent",
    "timestamp": "2026-05-30T14:21:07.512Z", "created_at": "2026-05-30T14:21:08.004Z"
  },
  "model_b": {
    "id": 5810, "model_name": "model_b", "model_version": "1.3.0",
    "error_rate": 0.0, "avg_latency_ms": 9.9, "p95_latency_ms": 25.0,
    "avg_confidence": 0.90, "accuracy": 0.89, "status": "healthy",
    "window": "5m", "source": "agent",
    "timestamp": "2026-05-30T14:21:05.000Z", "created_at": "2026-05-30T14:21:05.300Z"
  }
}
```

When `?model=model_a` is supplied, a **single** snapshot object is returned (not
keyed). `404 not_found` if no snapshots exist for that model yet.

---

### B.2 `registry_app`

The model registry: which models exist, their versions, and which is **active**.
Serializer `ModelRegistrySerializer` fields:

| Field | Type | Read-only | Description |
|-------|------|-----------|-------------|
| `id` | int | yes | PK |
| `model_name` | string | no | `model_a` / `model_b` |
| `version` | string | no | Semantic version, e.g. `1.4.2` |
| `active_flag` | bool | no | `true` for exactly one row per service slot |
| `status` | enum `stable`\|`candidate`\|`deprecated`\|`disabled` | no | Lifecycle state |
| `endpoint_url` | string | no | e.g. `http://model_a:8001` |
| `notes` | string \| null | no | Free text (e.g. "rolled back from 1.5.0") |
| `created_at` | string (ISO-8601) | yes | Registration time |
| `updated_at` | string (ISO-8601) | yes | Last change |

> **Invariant:** at any moment **exactly one** registry row has `active_flag=true`
> for the active serving slot. Flipping a new row to active automatically clears the
> previous one inside one transaction.

---

#### `GET /api/active-model`

**Purpose:** Return the currently active model the agent should be observing /
routing traffic to.

**Caller → Callee:** `agent_core` (Observe + Verify phases), dashboard →
`GET {DJANGO_BASE_URL}/api/active-model`.

**Response `200 OK`**

```json
{
  "id": 12,
  "model_name": "model_a",
  "version": "1.4.2",
  "active_flag": true,
  "status": "stable",
  "endpoint_url": "http://model_a:8001",
  "notes": null,
  "created_at": "2026-05-01T09:00:00Z",
  "updated_at": "2026-05-30T14:00:00Z"
}
```

`404 not_found` if no active model is configured (cold start).

---

#### `POST /api/active-model` / `PUT /api/active-model`

**Purpose:** Set/flip the active model. `POST` records a new activation event;
`PUT` is the idempotent "make this the active model" upsert. Used by the agent
**after** a verified Jenkins switch/rollback succeeds (so Django metadata reflects
reality).

**Caller → Callee:** `agent_core/actions/switch_model.py` (post-verify metadata
update) → `POST {DJANGO_BASE_URL}/api/active-model`.

**Request headers:** `Authorization: Token ...`, `Content-Type: application/json`.

**Request body**

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `model_name` | string | yes | Model to activate |
| `version` | string | yes | Version to activate |
| `reason` | string | no | Audit note (e.g. `"auto-switch after HIGH severity"`) |

**Request example**

```json
{ "model_name": "model_b", "version": "1.3.0", "reason": "auto-switch after HIGH severity" }
```

**Response `200 OK`** — the now-active registry row (same shape as `GET /api/active-model`),
with `active_flag=true` and the previously active model demoted.

**Error responses**

| Status | `error.code` | When |
|--------|--------------|------|
| `400` | `validation_error` | Missing `model_name`/`version` |
| `404` | `model_not_found` | No registry entry for that name+version |
| `409` | `already_active` | That model+version is already active (no-op conflict) |
| `401` | `authentication_failed` | Bad token |

---

#### `GET /api/models`

**Purpose:** List the full registry (all models, all versions, flags) for the
dashboard registry panel and the agent's rollback target selection.

**Caller → Callee:** dashboard, `agent_core/decision_engine` →
`GET {DJANGO_BASE_URL}/api/models`.

**Query params:** `?model=`, `?status=`, `?active_flag=true|false`, `?ordering=-created_at`, pagination.

**Response `200 OK`** (paginated)

```json
{
  "count": 3,
  "next": null,
  "previous": null,
  "results": [
    {
      "id": 12, "model_name": "model_a", "version": "1.4.2", "active_flag": true,
      "status": "stable", "endpoint_url": "http://model_a:8001", "notes": null,
      "created_at": "2026-05-01T09:00:00Z", "updated_at": "2026-05-30T14:00:00Z"
    },
    {
      "id": 9, "model_name": "model_a", "version": "1.3.0", "active_flag": false,
      "status": "deprecated", "endpoint_url": "http://model_a:8001",
      "notes": "previous stable", "created_at": "2026-04-10T09:00:00Z",
      "updated_at": "2026-05-01T09:00:00Z"
    },
    {
      "id": 7, "model_name": "model_b", "version": "1.3.0", "active_flag": false,
      "status": "stable", "endpoint_url": "http://model_b:8002", "notes": "backup",
      "created_at": "2026-04-10T09:00:00Z", "updated_at": "2026-04-10T09:00:00Z"
    }
  ]
}
```

---

#### `GET /api/models/{model_name}/versions`

**Purpose:** Version history for one model (rollback candidate list).

**Caller → Callee:** `agent_core/verification/rollback_guard.py`, dashboard →
`GET {DJANGO_BASE_URL}/api/models/model_a/versions`.

**Response `200 OK`**

```json
{
  "model_name": "model_a",
  "active_version": "1.4.2",
  "versions": [
    { "version": "1.4.2", "status": "stable",     "active_flag": true,  "created_at": "2026-05-01T09:00:00Z" },
    { "version": "1.3.0", "status": "deprecated",  "active_flag": false, "created_at": "2026-04-10T09:00:00Z" },
    { "version": "1.2.0", "status": "deprecated",  "active_flag": false, "created_at": "2026-03-01T09:00:00Z" }
  ]
}
```

`404 model_not_found` if `model_name` is unknown.

---

### B.3 `actions_app`

The agent's decision/audit log: every Decide→Act→Verify cycle writes one row.
Serializer `ActionLogSerializer` fields:

| Field | Type | Read-only | Description |
|-------|------|-----------|-------------|
| `id` | int | yes | PK |
| `action` | enum | no | `no_op`\|`alert`\|`rollback`\|`switch_backup`\|`retrain`\|`disable_predictions` |
| `severity` | enum `LOW`\|`MEDIUM`\|`HIGH`\|`CRITICAL` | no | From `severity_classifier` |
| `target_model` | string | no | Model the action affects |
| `reason` | string | no | Human-readable justification (the detection that fired) |
| `triggered_by` | enum `agent`\|`manual` | no | Default `agent` |
| `detection_signal` | object \| null | no | Raw signal payload (the `DetectionResult`) |
| `jenkins_job` | string \| null | no | Job triggered (if any), e.g. `switch_active_model` |
| `jenkins_build_number` | int \| null | no | Build number returned by Jenkins |
| `outcome` | enum `pending`\|`success`\|`failed`\|`skipped` | no | Default `pending`; updated after Verify |
| `verification` | object \| null | no | The serialized `VerificationResult` |
| `timestamp` | string (ISO-8601) | no | When the decision was made |
| `created_at` | string (ISO-8601) | yes | Server insert time |
| `updated_at` | string (ISO-8601) | yes | Last update (set by PATCH) |

---

#### `POST /api/actions`

**Purpose:** Log a decision/action at the moment the agent acts (outcome typically
starts as `pending`).

**Caller → Callee:** `agent_core/clients/django_client.py` (from `actions/*.py`) →
`POST {DJANGO_BASE_URL}/api/actions`.

**Request headers:** `Authorization: Token ...`, `Content-Type: application/json`,
optional `Idempotency-Key`.

**Request body example** (serialized `Decision` + `ActionResult` context)

```json
{
  "action": "switch_backup",
  "severity": "HIGH",
  "target_model": "model_a",
  "reason": "error_rate 0.34 over 5m exceeded HIGH threshold 0.20",
  "triggered_by": "agent",
  "detection_signal": {
    "detector": "threshold_detector",
    "metric": "error_rate",
    "observed": 0.34,
    "threshold": 0.20,
    "window": "5m"
  },
  "jenkins_job": "switch_active_model",
  "jenkins_build_number": 142,
  "outcome": "pending",
  "timestamp": "2026-05-30T14:21:09.000Z"
}
```

**Response `201 Created`** — stored row including `id`, `outcome`, `created_at`:

```json
{
  "id": 908,
  "action": "switch_backup",
  "severity": "HIGH",
  "target_model": "model_a",
  "reason": "error_rate 0.34 over 5m exceeded HIGH threshold 0.20",
  "triggered_by": "agent",
  "detection_signal": {
    "detector": "threshold_detector", "metric": "error_rate",
    "observed": 0.34, "threshold": 0.20, "window": "5m"
  },
  "jenkins_job": "switch_active_model",
  "jenkins_build_number": 142,
  "outcome": "pending",
  "verification": null,
  "timestamp": "2026-05-30T14:21:09.000Z",
  "created_at": "2026-05-30T14:21:09.120Z",
  "updated_at": "2026-05-30T14:21:09.120Z"
}
```

**Errors:** `400 validation_error`, `401 authentication_failed`.

---

#### `GET /api/actions`

**Purpose:** Audit history for the dashboard and for the agent's
"have-I-recently-acted?" debounce logic.

**Caller → Callee:** dashboard, `agent_core/decision_engine` →
`GET {DJANGO_BASE_URL}/api/actions`.

**Query params**

| Param | Type | Description |
|-------|------|-------------|
| `action` | string | Filter by action type |
| `severity` | string | Filter by severity |
| `outcome` | string | `pending` / `success` / `failed` / `skipped` |
| `target_model` | string | Filter by affected model |
| `since` / `until` | ISO-8601 | Time bounds on `timestamp` |
| `limit` / `page` | int | Pagination |
| `ordering` | string | Default `-timestamp` |

**Response `200 OK`** — paginated envelope of `ActionLog` objects (same shape as the
`POST` response). Example abbreviated:

```json
{
  "count": 57,
  "next": "http://backend:8000/api/actions?page=2",
  "previous": null,
  "results": [
    { "id": 908, "action": "switch_backup", "severity": "HIGH",
      "target_model": "model_a", "outcome": "success", "...": "..." }
  ]
}
```

---

#### `PATCH /api/actions/{id}`

**Purpose:** Update an action **after the Verify phase** — flip `outcome` to
`success`/`failed` and attach the `VerificationResult`.

**Caller → Callee:** `agent_core/verification/*` →
`PATCH {DJANGO_BASE_URL}/api/actions/{id}`.

**Request headers:** `Authorization: Token ...`, `Content-Type: application/json`.

**Request body** (partial — only mutable fields)

| Field | Type | Description |
|-------|------|-------------|
| `outcome` | enum | New terminal outcome |
| `verification` | object | Serialized `VerificationResult` |
| `jenkins_build_number` | int | If discovered after async build |

**Request example**

```json
{
  "outcome": "success",
  "verification": {
    "verified": true,
    "model_checked": "model_b",
    "baseline_error_rate": 0.0073,
    "post_action_error_rate": 0.004,
    "post_action_health": "healthy",
    "recovered": true,
    "message": "Backup healthy; error_rate within baseline.",
    "checked_at": "2026-05-30T14:22:30.000Z"
  }
}
```

**Response `200 OK`** — the full updated `ActionLog` row with new `outcome`,
`verification`, and refreshed `updated_at`.

**Error responses**

| Status | `error.code` | When |
|--------|--------------|------|
| `400` | `validation_error` | Invalid `outcome` value / bad verification body |
| `404` | `not_found` | No action with that `id` |
| `409` | `already_finalized` | Action already terminal (not `pending`) and not re-openable |
| `401` | `authentication_failed` | Bad token |

---

## C. Jenkins Execution API

> Jenkins core on port `8080`. The agent triggers parameterized jobs over HTTP and
> then polls build status. **HTTP Basic auth** with `JENKINS_USER` + `JENKINS_API_TOKEN`.
> A **CSRF crumb** is required for POSTs unless crumb issuing is disabled.

Called exclusively by `agent_core/clients/jenkins_client.py` from
`agent_core/actions/switch_model.py` (and rollback / deploy flows).

### Jobs and their parameters

| Job (`{job_name}`) | Purpose | Parameters |
|--------------------|---------|------------|
| `switch_active_model` | Route active traffic to the backup model | `TARGET_MODEL` (e.g. `model_b`), `ACTION=switch`, `REASON` |
| `rollback_model` | Revert a model to a previous stable version | `TARGET_MODEL`, `VERSION` (target version), `ACTION=rollback`, `REASON` |
| `deploy_model` | Deploy/retrain-then-deploy a model artifact | `TARGET_MODEL`, `VERSION`, `ACTION=deploy`, `ARTIFACT_URL` (optional) |

### C.0 (Optional) Get CSRF crumb

```
GET {JENKINS_BASE_URL}/crumbIssuer/api/json
Authorization: Basic base64(JENKINS_USER:JENKINS_API_TOKEN)
```

`200 OK`:

```json
{ "crumb": "a1b2c3d4e5", "crumbRequestField": "Jenkins-Crumb" }
```

The crumb value is then sent as header `Jenkins-Crumb: <crumb>` on the trigger POST.

### C.1 Trigger a build — `POST /job/{job_name}/buildWithParameters`

**Purpose:** Start a parameterized recovery job asynchronously.

**Caller → Callee:** `jenkins_client.py` →
`POST {JENKINS_BASE_URL}/job/switch_active_model/buildWithParameters`.

**Request headers**

| Header | Value | Required |
|--------|-------|----------|
| `Authorization` | `Basic base64(user:token)` | yes |
| `Jenkins-Crumb` | crumb from C.0 | if CSRF enabled |
| `Content-Type` | `application/x-www-form-urlencoded` | yes |

> Jenkins `buildWithParameters` takes **form-encoded query/body params**, not JSON.
> Parameters may be sent as a query string or a form body.

**Request example**

```
POST /job/switch_active_model/buildWithParameters?TARGET_MODEL=model_b&ACTION=switch&REASON=HIGH_error_rate HTTP/1.1
Host: jenkins:8080
Authorization: Basic YWdlbnQ6dG9rZW4=
Jenkins-Crumb: a1b2c3d4e5
Content-Type: application/x-www-form-urlencoded
```

**Response `201 Created` / `202 Accepted`** — empty body; the build is **queued**.
The important data is in the `Location` header pointing at the queue item:

```
HTTP/1.1 201 Created
Location: http://jenkins:8080/queue/item/873/
```

The client stores this queue URL, polls it to resolve the queue item into a build,
and from there obtains the **build number**.

**Error responses (Jenkins-native, not the envelope)**

| Status | When |
|--------|------|
| `401` | Bad/missing basic-auth credentials |
| `403` | Missing/invalid CSRF crumb (`No valid crumb`) |
| `404` | Unknown `{job_name}` |
| `400` | Missing a required job parameter |
| `500` | Jenkins controller fault |

### C.2 Resolve queue item — `GET /queue/item/{id}/api/json`

`200 OK` once scheduled (the `executable` block carries the build number):

```json
{
  "id": 873,
  "blocked": false,
  "stuck": false,
  "executable": { "number": 142, "url": "http://jenkins:8080/job/switch_active_model/142/" }
}
```

If `executable` is `null`, the item is still queued — keep polling.

### C.3 Poll build status — `GET /job/{job_name}/lastBuild/api/json`

(or `GET /job/{job_name}/{build_number}/api/json` for a specific build)

**Purpose:** Determine whether the recovery job finished and whether it succeeded.

**Caller → Callee:** `jenkins_client.py` poll loop (until `building=false` or
read-timeout `30s`).

**Response `200 OK`**

```json
{
  "number": 142,
  "result": "SUCCESS",
  "building": false,
  "duration": 8421,
  "timestamp": 1748614869000,
  "url": "http://jenkins:8080/job/switch_active_model/142/",
  "actions": [
    { "parameters": [
        { "name": "TARGET_MODEL", "value": "model_b" },
        { "name": "ACTION", "value": "switch" },
        { "name": "REASON", "value": "HIGH_error_rate" }
    ] }
  ]
}
```

| Field | Type | Description |
|-------|------|-------------|
| `number` | int | Build number → stored on `ActionLog.jenkins_build_number` |
| `result` | enum `SUCCESS`\|`FAILURE`\|`UNSTABLE`\|`ABORTED`\|`null` | `null` while still `building` |
| `building` | bool | `true` until the build finishes |
| `duration` | int (ms) | Build duration |
| `timestamp` | int (epoch ms) | Build start |

**Mapping to `ActionLog.outcome`:** `SUCCESS → success`; `FAILURE`/`ABORTED`/
`UNSTABLE → failed`; `building=true → pending` (keep polling). The agent then
PATCHes the action (Section B.3) with the resolved outcome and verification.

---

## D. Internal Pydantic Schemas

> Defined in `control-plane/agent_core/schemas.py`. These are the typed objects the
> agent passes between loop phases **in memory**. The JSON the agent serializes for
> Django must match these field-for-field (see the
> [consistency matrix](#cross-surface-consistency-matrix)). Shown as Pydantic v2.

```python
# control-plane/agent_core/schemas.py
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional, Any

from pydantic import BaseModel, Field


# ---- Enumerations -------------------------------------------------------

class HealthStatus(str, Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


class Severity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ActionType(str, Enum):
    NO_OP = "no_op"
    ALERT = "alert"
    ROLLBACK = "rollback"
    SWITCH_BACKUP = "switch_backup"
    RETRAIN = "retrain"
    DISABLE_PREDICTIONS = "disable_predictions"


class Outcome(str, Enum):
    PENDING = "pending"
    SUCCESS = "success"
    FAILED = "failed"
    SKIPPED = "skipped"


# ---- 1. OBSERVE ---------------------------------------------------------

class MetricSnapshot(BaseModel):
    """Rolling operational metrics for one model.
    JSON-identical to the Django `MetricSnapshotSerializer` body of POST /api/metrics."""
    model_name: str
    model_version: str
    request_count: int = 0
    error_count: int = 0
    error_rate: float = 0.0
    avg_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    avg_confidence: float = 0.0
    accuracy: Optional[float] = None
    status: HealthStatus = HealthStatus.HEALTHY
    window: str = "5m"
    source: str = "agent"
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class Observation(BaseModel):
    """One full sweep of a single model: health + metrics + a sample prediction.
    Built in the Observe phase from /health, /metrics and /predict probes."""
    model_name: str
    endpoint_url: str
    reachable: bool                       # False => probe timed out / connection error
    health_status: HealthStatus
    model_loaded: bool
    uptime_seconds: Optional[float] = None
    metrics: MetricSnapshot
    sample_prediction: Optional[float] = None
    sample_confidence: Optional[float] = None
    observed_at: datetime = Field(default_factory=datetime.utcnow)


# ---- 2. DETECT ----------------------------------------------------------

class DetectionResult(BaseModel):
    """Output of a detector (threshold / anomaly / drift).
    Serialized into ActionLog.detection_signal."""
    detector: str                         # "threshold_detector" | "anomaly_detector" | "drift_detector"
    anomaly_detected: bool
    metric: Optional[str] = None          # e.g. "error_rate", "p95_latency_ms", "avg_confidence"
    observed: Optional[float] = None
    threshold: Optional[float] = None
    window: str = "5m"
    score: Optional[float] = None         # drift/anomaly score when applicable
    message: str = ""
    detected_at: datetime = Field(default_factory=datetime.utcnow)


# ---- 3. DECIDE ----------------------------------------------------------

class Decision(BaseModel):
    """Output of the decision engine: severity + chosen action.
    Maps directly onto the POST /api/actions body."""
    action: ActionType
    severity: Severity
    target_model: str
    reason: str
    triggered_by: str = "agent"
    detection_signal: Optional[DetectionResult] = None
    requires_jenkins: bool = False
    jenkins_job: Optional[str] = None     # "switch_active_model" | "rollback_model" | "deploy_model"
    jenkins_params: dict[str, Any] = Field(default_factory=dict)
    decided_at: datetime = Field(default_factory=datetime.utcnow)


# ---- 4. ACT -------------------------------------------------------------

class ActionResult(BaseModel):
    """Result of executing a Decision (e.g. triggering Jenkins + logging to Django)."""
    action: ActionType
    target_model: str
    executed: bool
    outcome: Outcome = Outcome.PENDING
    action_log_id: Optional[int] = None   # id returned by POST /api/actions
    jenkins_job: Optional[str] = None
    jenkins_build_number: Optional[int] = None
    jenkins_build_url: Optional[str] = None
    message: str = ""
    executed_at: datetime = Field(default_factory=datetime.utcnow)


# ---- 5. VERIFY ----------------------------------------------------------

class VerificationResult(BaseModel):
    """Post-recovery validation. Serialized into the PATCH /api/actions/{id} body."""
    verified: bool
    model_checked: str
    baseline_error_rate: Optional[float] = None
    post_action_error_rate: Optional[float] = None
    baseline_confidence: Optional[float] = None
    post_action_confidence: Optional[float] = None
    post_action_health: Optional[HealthStatus] = None
    recovered: bool = False
    escalate_to_human: bool = False
    message: str = ""
    checked_at: datetime = Field(default_factory=datetime.utcnow)
```

### How the schemas flow through the loop

```
Observe   →  Observation { metrics: MetricSnapshot }   ──POST /api/metrics──▶ Django
Detect    →  DetectionResult
Decide    →  Decision (severity + ActionType)
Act       →  ActionResult                              ──POST /api/actions──▶ Django (outcome=pending)
                 └─ if Decision.requires_jenkins:      ──buildWithParameters──▶ Jenkins
Verify    →  VerificationResult                        ──PATCH /api/actions/{id}──▶ Django (outcome=success/failed)
                 └─ if recovered: ──POST /api/active-model──▶ Django registry update
```

---

## Cross-surface consistency matrix

These mappings are **load-bearing** — the same field names/types must appear on
both sides or integration breaks.

| Concept | Model service field | Pydantic field | Django field |
|---------|---------------------|----------------|--------------|
| Error rate | `GET /metrics.error_rate` | `MetricSnapshot.error_rate` | `MetricSnapshot.error_rate` |
| p95 latency | `GET /metrics.p95_latency_ms` | `MetricSnapshot.p95_latency_ms` | `MetricSnapshot.p95_latency_ms` |
| Avg confidence | `GET /metrics.avg_confidence` | `MetricSnapshot.avg_confidence` | `MetricSnapshot.avg_confidence` |
| Health | `GET /health.status` | `Observation.health_status` / `MetricSnapshot.status` | `MetricSnapshot.status` |
| Version | `/predict.model_version`, `/health.version` | `MetricSnapshot.model_version` | `MetricSnapshot.model_version`, `ModelRegistry.version` |
| Action type | — | `Decision.action` (`ActionType`) | `ActionLog.action` |
| Severity | — | `Decision.severity` (`Severity`) | `ActionLog.severity` |
| Detection signal | — | `DetectionResult` | `ActionLog.detection_signal` |
| Verification | — | `VerificationResult` | `ActionLog.verification` |
| Outcome | Jenkins `result` (mapped) | `ActionResult.outcome` (`Outcome`) | `ActionLog.outcome` |
| Build number | Jenkins `lastBuild.number` | `ActionResult.jenkins_build_number` | `ActionLog.jenkins_build_number` |
| Active model | — | `Decision.target_model` | `ModelRegistry.model_name` + `active_flag` |

**Enum value alignment (must be byte-identical across services):**

- `status`: `healthy` / `degraded` / `unhealthy`
- `severity`: `LOW` / `MEDIUM` / `HIGH` / `CRITICAL`
- `action`: `no_op` / `alert` / `rollback` / `switch_backup` / `retrain` / `disable_predictions`
- `outcome`: `pending` / `success` / `failed` / `skipped`
- Jenkins `result → outcome`: `SUCCESS→success`, `FAILURE|ABORTED|UNSTABLE→failed`, `building→pending`

---

*End of API contracts (v1). Amendments to this document are themselves the
mechanism for evolving the cross-service contract; bump `X-API-Version` on a
breaking change.*
