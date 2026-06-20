"""Client for triggering Jenkins recovery jobs (api_contracts.md §C).

Triggers a parameterized build via buildWithParameters (HTTP Basic auth, optional
CSRF crumb), resolves the queue item into a build number, and polls the build until
it finishes — mapping Jenkins' result to a success/failure verdict
(deployment_and_devops.md §6.6). Used by actions/switch_model.py only when the
executor is configured as "jenkins"; the direct executor remains the default and a
permanent fallback.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

import requests

import config
from retry import poll_until

log = logging.getLogger("agent.clients.jenkins")

# Jenkins result -> our outcome (api_contracts.md §C.3)
_RESULT_SUCCESS = "SUCCESS"


@dataclass
class JenkinsBuildResult:
    triggered: bool
    success: bool
    build_number: Optional[int] = None
    build_url: Optional[str] = None
    result: Optional[str] = None
    message: str = ""


class JenkinsClient:
    def __init__(self, base_url: Optional[str] = None, user: Optional[str] = None,
                 token: Optional[str] = None) -> None:
        s = config.settings
        self._base = (base_url or s.jenkins_url).rstrip("/")
        self._auth = (user or s.jenkins_user, token or s.jenkins_api_token)
        self._trigger_timeout = s.http_timeout(read=10.0)
        self._poll_timeout = s.http_timeout(read=30.0)

    # ---- low-level steps ------------------------------------------------

    def _crumb(self) -> dict:
        """Fetch a CSRF crumb header if the controller issues one (else {})."""
        try:
            r = requests.get(f"{self._base}/crumbIssuer/api/json",
                             auth=self._auth, timeout=self._trigger_timeout)
            if r.status_code == 200:
                body = r.json()
                return {body["crumbRequestField"]: body["crumb"]}
        except (requests.RequestException, ValueError, KeyError):
            pass
        return {}

    def trigger_job(self, job_name: str, params: dict) -> Optional[str]:
        """POST buildWithParameters. Returns the queue-item URL (Location header)."""
        headers = self._crumb()
        try:
            r = requests.post(f"{self._base}/job/{job_name}/buildWithParameters",
                              params=params, headers=headers, auth=self._auth,
                              timeout=self._trigger_timeout)
            if r.status_code in (200, 201, 202):
                return r.headers.get("Location")
            log.warning("trigger %s -> %s", job_name, r.status_code)
        except requests.RequestException as exc:
            log.warning("trigger %s failed: %s", job_name, exc)
        return None

    def resolve_queue(self, queue_url: str, attempts: int = 30,
                      backoff: float = 1.0) -> Optional[dict]:
        """Poll the queue item until it yields an executable (build number/url)."""
        def _try() -> Optional[dict]:
            try:
                r = requests.get(f"{queue_url.rstrip('/')}/api/json",
                                 auth=self._auth, timeout=self._poll_timeout)
                if r.status_code == 200:
                    ex = r.json().get("executable")
                    if ex:
                        return {"number": ex.get("number"), "url": ex.get("url")}
            except (requests.RequestException, ValueError):
                pass
            return None

        return poll_until(_try, attempts=attempts, delay=backoff)

    def poll_build(self, build_url: str, attempts: int = 60,
                   backoff: float = 1.0) -> Optional[dict]:
        """Poll a build until building=false; return {result, number, url}."""
        def _try() -> Optional[dict]:
            try:
                r = requests.get(f"{build_url.rstrip('/')}/api/json",
                                 auth=self._auth, timeout=self._poll_timeout)
                if r.status_code == 200:
                    b = r.json()
                    if not b.get("building", True) and b.get("result") is not None:
                        return {"result": b.get("result"), "number": b.get("number"),
                                "url": b.get("url")}
            except (requests.RequestException, ValueError):
                pass
            return None

        return poll_until(_try, attempts=attempts, delay=backoff)

    # ---- high-level orchestration --------------------------------------

    def run_job(self, job_name: str, params: dict) -> JenkinsBuildResult:
        """Trigger -> resolve queue -> poll build. Returns a verdict."""
        queue_url = self.trigger_job(job_name, params)
        if not queue_url:
            return JenkinsBuildResult(triggered=False, success=False,
                                      message="trigger failed / Jenkins unreachable")
        executable = self.resolve_queue(queue_url)
        if not executable:
            return JenkinsBuildResult(triggered=True, success=False,
                                      message="build never scheduled (queue timeout)")
        build = self.poll_build(executable["url"])
        if not build:
            return JenkinsBuildResult(triggered=True, success=False,
                                      build_number=executable["number"],
                                      build_url=executable["url"],
                                      message="build did not finish in time")
        success = build["result"] == _RESULT_SUCCESS
        return JenkinsBuildResult(
            triggered=True, success=success, build_number=build["number"],
            build_url=build["url"], result=build["result"],
            message=f"build #{build['number']} {build['result']}")
