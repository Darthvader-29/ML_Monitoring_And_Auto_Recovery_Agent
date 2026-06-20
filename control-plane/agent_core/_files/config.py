# control-plane/agent_core/config.py
#
# The agent's SINGLE configuration surface. Every client and engine module imports
# its settings from here (deployment_and_devops.md §4.2) — the agent never reads
# os.environ anywhere else.
#
# Values are sourced, in increasing precedence, from:
#   1. the code defaults below,
#   2. a repo-root .env file (loaded here if present, stdlib-only — no python-dotenv),
#   3. the real process environment (set by docker-compose `env_file`, or the shell).
#
# Env-var names follow deployment_and_devops.md §4.1; where api_contracts.md §Conventions
# uses a different name for the same thing, BOTH are accepted (see `_first_env`).
# Threshold / anti-flap defaults are the canonical values from failure_scenarios.md §1.3.
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from feature_schema import FEATURE_COLS


# ---- .env loading (stdlib only) -----------------------------------------

def _load_dotenv() -> None:
    """Populate os.environ from a repo-root .env, WITHOUT overriding values that
    are already set in the real environment (so compose/shell always win)."""
    # config.py lives at control-plane/agent_core/_files/config.py → repo root is 3 up.
    for base in (Path(__file__).resolve().parents[3], Path.cwd()):
        env_path = base / ".env"
        if not env_path.is_file():
            continue
        for raw in env_path.read_text().splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            # strip surrounding quotes and inline comments on unquoted values
            value = value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)
        break


def _first_env(*names: str, default: str = "") -> str:
    """Return the first env var that is set among `names` (supports doc aliases)."""
    for name in names:
        val = os.environ.get(name)
        if val is not None:
            return val
    return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ[name])
    except (KeyError, ValueError):
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


def _env_bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


# ---- Settings -----------------------------------------------------------

@dataclass(frozen=True)
class Settings:
    # --- Service base URLs (never hardcode elsewhere; api_contracts.md §Conventions)
    model_a_url: str
    model_b_url: str
    backend_url: str          # the Django control plane (a.k.a. DJANGO_BASE_URL)
    jenkins_url: str

    # --- Auth
    django_api_token: str
    jenkins_user: str
    jenkins_api_token: str

    # --- Jenkins job names (deployment_and_devops.md §4.1)
    jenkins_job_deploy: str
    jenkins_job_switch: str
    jenkins_job_rollback: str

    # --- ACT executor strategy: "direct" (Phase 2 MVP) | "jenkins" (Phase 5)
    executor_type: str

    # --- Agent loop tuning
    poll_interval_seconds: int
    http_connect_timeout_seconds: float
    http_read_timeout_seconds: float
    verify_retries: int
    verify_backoff_seconds: float

    # --- Anti-flap controls (failure_scenarios.md §1.3)
    confirm_n: int            # consecutive non-LOW cycles before a non-trivial action
    cooldown_cycles: int      # cycles to wait after an ACT before another switch/rollback
    ewma_alpha: float         # smoothing applied to noisy metrics
    max_recovery_attempts: int  # attempts per incident before escalating to a human

    # --- Detection thresholds: HIGH-band edges that trip action
    #     (full LOW/MED/HIGH bands live in failure_scenarios.md §1.3).
    error_rate_threshold: float          # > 10% => HIGH
    p95_latency_threshold_ms: float      # > 800ms => HIGH
    consecutive_failures_to_switch: int  # >= 2 failed /health polls => HIGH (service down)
    confidence_floor: float              # mean confidence < 0.55 => HIGH (anomaly)
    invalid_ratio_threshold: float       # invalid-output ratio > 10% => HIGH
    missing_ratio_threshold: float       # missing-value ratio > 20% => HIGH
    drift_psi_threshold: float           # PSI > 0.3 => HIGH
    drift_ks_p_threshold: float          # KS p-value < 0.001 => HIGH
    accuracy_floor: float                # accuracy < 0.80 => HIGH (concept drift)

    # --- Confidence band edges (used by threshold_detector + severity_classifier).
    #     Mean confidence at/under `notable` is worth a LOW signal; under `med_floor`
    #     is MEDIUM; under `confidence_floor` (above) is HIGH. Kept here so all the
    #     confidence cut-points live in one tunable place.
    confidence_notable_floor: float      # mean confidence <= 0.78 => notable (LOW)
    confidence_med_floor: float          # mean confidence <  0.70 => MEDIUM

    # --- Confidence-based action thresholds (Phase 8 bonus, OFF by default).
    #     A leading signal the mean hides: the SHARE of predictions whose confidence
    #     lands in the "uncertain" zone (below `low_confidence_cutoff`). A model
    #     degrading toward the decision boundary grows this tail while its mean still
    #     looks healthy. When enabled the agent emits a `low_confidence_ratio` signal.
    confidence_gating_enabled: bool      # master flag for the bonus
    low_confidence_cutoff: float         # a single prediction < this is "uncertain"
    low_confidence_ratio_med: float      # uncertain share >= this => MEDIUM
    low_confidence_ratio_high: float     # uncertain share >= this => HIGH

    # --- Feature schema (data_simulation.md §2.1) — sourced from feature_schema,
    #     the single source of truth shared with data_loader / drift_detector.
    feature_names: tuple[str, ...] = field(
        default_factory=lambda: tuple(FEATURE_COLS)
    )

    def http_timeout(self, read: float | None = None) -> tuple[float, float]:
        """The requests `(connect, read)` timeout tuple — the one place it is built
        from the configured connect/read seconds. Pass `read` to override the read
        leg for short health probes / long Jenkins polls instead of hardcoding it."""
        return (self.http_connect_timeout_seconds,
                self.http_read_timeout_seconds if read is None else read)


def load_settings() -> Settings:
    """Build a Settings instance from defaults + .env + process environment."""
    _load_dotenv()

    return Settings(
        # Defaults are the host (non-docker) URLs so local venv dev works out of the
        # box; compose overrides these with in-cluster service names via .env.
        model_a_url=_first_env("MODEL_A_URL", default="http://localhost:8001"),
        model_b_url=_first_env("MODEL_B_URL", default="http://localhost:8002"),
        backend_url=_first_env("BACKEND_URL", "DJANGO_BASE_URL",
                               default="http://localhost:8000"),
        jenkins_url=_first_env("JENKINS_URL", "JENKINS_BASE_URL",
                               default="http://localhost:8080"),

        django_api_token=_first_env("DJANGO_API_TOKEN", default=""),
        jenkins_user=_first_env("JENKINS_USER", default="automation"),
        jenkins_api_token=_first_env("JENKINS_API_TOKEN", default=""),

        jenkins_job_deploy=_first_env("JENKINS_JOB_DEPLOY", default="deploy_model"),
        jenkins_job_switch=_first_env("JENKINS_JOB_SWITCH", default="switch_active_model"),
        jenkins_job_rollback=_first_env("JENKINS_JOB_ROLLBACK", default="rollback_model"),

        executor_type=_first_env("AGENT_EXECUTOR_TYPE", "EXECUTOR_TYPE", default="direct"),

        poll_interval_seconds=_env_int("AGENT_POLL_INTERVAL_SECONDS", 30),
        http_connect_timeout_seconds=_env_float("AGENT_HTTP_CONNECT_TIMEOUT_SECONDS", 2.0),
        http_read_timeout_seconds=_env_float("AGENT_HTTP_TIMEOUT_SECONDS", 5.0),
        verify_retries=_env_int("AGENT_VERIFY_RETRIES", 3),
        verify_backoff_seconds=_env_float("AGENT_VERIFY_BACKOFF_SECONDS", 10.0),

        confirm_n=_env_int("AGENT_CONFIRM_N", 2),
        cooldown_cycles=_env_int("AGENT_COOLDOWN_CYCLES", 3),
        ewma_alpha=_env_float("AGENT_EWMA_ALPHA", 0.3),
        max_recovery_attempts=_env_int("AGENT_MAX_RECOVERY_ATTEMPTS", 1),

        error_rate_threshold=_env_float("ERROR_RATE_THRESHOLD", 0.10),
        p95_latency_threshold_ms=_env_float("LATENCY_P95_THRESHOLD_MS", 800.0),
        consecutive_failures_to_switch=_env_int("CONSECUTIVE_FAILURES_TO_SWITCH", 2),
        confidence_floor=_env_float("CONFIDENCE_FLOOR", 0.55),
        invalid_ratio_threshold=_env_float("INVALID_RATIO_THRESHOLD", 0.10),
        missing_ratio_threshold=_env_float("MISSING_RATIO_THRESHOLD", 0.20),
        drift_psi_threshold=_env_float("DRIFT_PSI_THRESHOLD", 0.30),
        drift_ks_p_threshold=_env_float("DRIFT_KS_P_THRESHOLD", 0.001),
        accuracy_floor=_env_float("ACCURACY_FLOOR", 0.80),

        confidence_notable_floor=_env_float("CONFIDENCE_NOTABLE_FLOOR", 0.78),
        confidence_med_floor=_env_float("CONFIDENCE_MED_FLOOR", 0.70),

        confidence_gating_enabled=_env_bool("CONFIDENCE_GATING_ENABLED", False),
        low_confidence_cutoff=_env_float("LOW_CONFIDENCE_CUTOFF", 0.60),
        low_confidence_ratio_med=_env_float("LOW_CONFIDENCE_RATIO_MED", 0.20),
        low_confidence_ratio_high=_env_float("LOW_CONFIDENCE_RATIO_HIGH", 0.40),
    )


# Module-level singleton: `from config import settings`.
settings = load_settings()


def get_settings() -> Settings:
    """Accessor for code/tests that prefer a function over the module global."""
    return settings


if __name__ == "__main__":
    # `python config.py` prints the resolved configuration for quick inspection.
    from dataclasses import asdict
    import json

    print(json.dumps(asdict(settings), indent=2, default=str))
