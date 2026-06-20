"""Shared test helpers.

The API now requires a token by default (config.settings._REQUIRE_AUTH), so tests
use an authenticated client. `authed_client()` mirrors how the agent authenticates
(`Authorization: Token …`)."""
from __future__ import annotations

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import Client

AUTH_ENABLED = "rest_framework.authtoken" in settings.INSTALLED_APPS


def authed_client() -> Client:
    """An API client that works in both modes: tokened when auth is required, a plain
    client when DJANGO_REQUIRE_AUTH=0 (open-access demo)."""
    if not AUTH_ENABLED:
        return Client()
    from rest_framework.authtoken.models import Token
    user, _ = get_user_model().objects.get_or_create(username="agent")
    token, _ = Token.objects.get_or_create(user=user)
    return Client(HTTP_AUTHORIZATION=f"Token {token.key}")
