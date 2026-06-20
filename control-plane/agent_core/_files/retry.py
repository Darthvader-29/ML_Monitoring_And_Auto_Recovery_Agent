"""Small retry/poll helpers shared by the agent's network clients.

Consolidates the hand-rolled "try N times with a delay" loops that lived in
django_client (an exponential-backoff GET) and jenkins_client (two fixed-delay
pollers). Pure stdlib; `sleep` is injectable so tests run instantly.
"""
from __future__ import annotations

import time
from typing import Callable, Optional, TypeVar

T = TypeVar("T")


def poll_until(fn: Callable[[], Optional[T]], *, attempts: int, delay: float = 1.0,
               exponential: bool = False,
               sleep: Callable[[float], None] = time.sleep) -> Optional[T]:
    """Call ``fn()`` up to ``attempts`` times and return its first truthy result
    (else the last falsy one — typically ``None``).

    ``fn`` should swallow its own transient errors and return a falsy value to mean
    "not ready, retry". Between attempts we wait ``delay`` seconds, or
    ``delay * 2**i`` when ``exponential`` (no wait after the final attempt).
    """
    result: Optional[T] = None
    for i in range(max(1, attempts)):
        result = fn()
        if result:
            return result
        if i < attempts - 1:
            sleep(delay * (2 ** i) if exponential else delay)
    return result
