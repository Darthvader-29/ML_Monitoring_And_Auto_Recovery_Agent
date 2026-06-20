"""Unit tests for the shared retry/poll helper and its client use sites."""
from retry import poll_until


def test_poll_until_returns_first_truthy():
    seq = iter([None, None, "ready"])
    sleeps = []
    out = poll_until(lambda: next(seq), attempts=5, delay=2.0,
                     sleep=sleeps.append)
    assert out == "ready"
    assert sleeps == [2.0, 2.0]  # waited between the two misses, not after success


def test_poll_until_exhausts_and_returns_last():
    out = poll_until(lambda: None, attempts=3, delay=1.0, sleep=lambda _s: None)
    assert out is None


def test_poll_until_exponential_backoff():
    sleeps = []
    poll_until(lambda: None, attempts=3, delay=0.25, exponential=True,
               sleep=sleeps.append)
    assert sleeps == [0.25, 0.5]  # 0.25*2**0, 0.25*2**1 (no wait after last)


def test_jenkins_poll_build_returns_finished_build(monkeypatch):
    """poll_build keeps polling until building=false (first coverage for the
    Jenkins client, now that it routes through poll_until)."""
    import retry
    from clients import jenkins_client

    responses = iter([
        {"building": True, "result": None},
        {"building": False, "result": "SUCCESS", "number": 7, "url": "http://j/7/"},
    ])

    class _Resp:
        status_code = 200

        def __init__(self, body):
            self._b = body

        def json(self):
            return self._b

    monkeypatch.setattr(jenkins_client.requests, "get",
                        lambda *a, **k: _Resp(next(responses)))
    # make the poller's waits instant
    monkeypatch.setattr(jenkins_client, "poll_until",
                        lambda fn, *, attempts, delay=1.0: retry.poll_until(
                            fn, attempts=attempts, delay=0, sleep=lambda _s: None))

    build = jenkins_client.JenkinsClient().poll_build("http://j/7", attempts=5)
    assert build == {"result": "SUCCESS", "number": 7, "url": "http://j/7/"}
