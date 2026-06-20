"""Django settings for the control plane (data_model.md §1.1).

SQLite by default (zero-config, ships in the repo); switch to Postgres via the
DJANGO_DB_* env vars. The agent reaches this service only over /api/* — no shared DB.
"""
from __future__ import annotations

import os
import warnings
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Secure by default: DEBUG is OFF unless DJANGO_DEBUG explicitly enables it. Run the
# local dev server with `DJANGO_DEBUG=1`.
DEBUG = os.environ.get("DJANGO_DEBUG", "0") in ("1", "true", "True")

_DEV_SECRET = "dev-insecure-change-me"
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", _DEV_SECRET)
if SECRET_KEY == _DEV_SECRET and not DEBUG:
    # Loud, non-fatal warning so a forgotten secret is obvious in prod logs without
    # breaking the zero-config local/test path.
    warnings.warn("DJANGO_SECRET_KEY is unset; using the insecure dev key with "
                  "DEBUG=False. Set a real secret before deploying.", stacklevel=2)

ALLOWED_HOSTS = os.environ.get(
    "DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1,backend").split(",")

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "rest_framework",
    "registry_app",
    "monitoring_app",
    "actions_app",
    "dashboard_app",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
X_FRAME_OPTIONS = "DENY"

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
TEMPLATES = [{
    "BACKEND": "django.template.backends.django.DjangoTemplates",
    "DIRS": [BASE_DIR / "templates"],
    "APP_DIRS": True,
    "OPTIONS": {"context_processors": []},
}]

DATABASES = {
    "default": {
        "ENGINE": os.environ.get("DJANGO_DB_ENGINE", "django.db.backends.sqlite3"),
        "NAME": os.environ.get("DJANGO_DB_NAME", str(BASE_DIR / "db.sqlite3")),
        "USER": os.environ.get("DJANGO_DB_USER", ""),
        "PASSWORD": os.environ.get("DJANGO_DB_PASSWORD", ""),
        "HOST": os.environ.get("DJANGO_DB_HOST", ""),
        "PORT": os.environ.get("DJANGO_DB_PORT", ""),
    }
}

# Secure by default: the mutating + read API requires a token. The agent already
# sends `Authorization: Token …` (config.DJANGO_API_TOKEN). Set DJANGO_REQUIRE_AUTH=0
# to fall back to open access for a throwaway local demo. (The /api/health/ probe and
# the read-only dashboard are plain Django views and stay public by design.)
_REQUIRE_AUTH = os.environ.get("DJANGO_REQUIRE_AUTH", "1") in ("1", "true", "True")

# Data-privacy hardening (opt-in). When False, the read serializers drop internal
# service topology (endpoint_url/port) and raw operational metric blobs
# (before_metrics/after_metrics) from their output. Defaults to True to preserve
# the current local demo/test behavior; set DJANGO_EXPOSE_INTERNAL=0 to harden.
EXPOSE_INTERNAL_TOPOLOGY = os.environ.get(
    "DJANGO_EXPOSE_INTERNAL", "1") not in ("0", "false", "False")
REST_FRAMEWORK = {
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated" if _REQUIRE_AUTH
        else "rest_framework.permissions.AllowAny"
    ],
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.TokenAuthentication"
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
    # Cheap DoS protection on the unauthenticated edge and per-agent fairness.
    # Generous so a normal agent cadence (one sweep per poll interval) never trips.
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": os.environ.get("DJANGO_THROTTLE_ANON", "60/min"),
        "user": os.environ.get("DJANGO_THROTTLE_USER", "2000/min"),
    },
}
if _REQUIRE_AUTH:
    INSTALLED_APPS.append("rest_framework.authtoken")

# Transport hardening — only meaningful behind TLS. Gated on DEBUG off, and the
# HTTPS-redirect is itself opt-in (DJANGO_SSL_REDIRECT=1) so a plain-HTTP local run
# or the test suite is not 301-redirected. The cookie/nosniff flags are harmless
# over HTTP (the API uses tokens, not cookies).
if not DEBUG:
    SECURE_SSL_REDIRECT = os.environ.get("DJANGO_SSL_REDIRECT", "0") in ("1", "true", "True")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = int(os.environ.get("DJANGO_HSTS_SECONDS", "0") or 0)
    SECURE_CONTENT_TYPE_NOSNIFF = True

USE_TZ = True
TIME_ZONE = "UTC"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
