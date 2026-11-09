"""Run a single HTTP check.

Kept deliberately small: one function in, one result object out, no global
state, so the scheduler, the CLI and the tests all drive the same code path.
"""

import time
import urllib.error
import urllib.request

_MAX_BODY_READ = 512 * 1024  # only read what a keyword check could need
_USER_AGENT = "uptick/0.1 (+https://github.com/TheRealAlexxx/uptick)"


class CheckResult:
    def __init__(self, ok, status_code=None, latency_ms=None, error=None):
        self.ok = ok
        self.status_code = status_code
        self.latency_ms = latency_ms
        self.error = error

    def __repr__(self):
        if self.ok:
            return "<CheckResult ok status=%s %.0fms>" % (self.status_code, self.latency_ms or 0)
        return "<CheckResult FAIL status=%s error=%r>" % (self.status_code, self.error)

    def __eq__(self, other):
        return (
            isinstance(other, CheckResult)
            and self.ok == other.ok
            and self.status_code == other.status_code
            and self.error == other.error
        )


def check(monitor):
    """Check one monitor dict (see config.load_config). Returns a CheckResult.

    A check fails when the request errors, the status code differs from
    expected_status, or the keyword is set and absent from the body.
    Redirects are followed; a redirect chain that lands on the expected
    status is a pass, because what matters is what a visitor gets.
    """
    url = monitor["url"]
    timeout = monitor.get("timeout_seconds", 10)
    expected = monitor.get("expected_status", 200)
    keyword = monitor.get("keyword")

    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    start = time.monotonic()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = response.getcode()
            body = response.read(_MAX_BODY_READ) if keyword else None
        latency_ms = (time.monotonic() - start) * 1000.0
    except urllib.error.HTTPError as exc:
        # The server answered with a 4xx/5xx status. That is still an answer:
        # compare it against expected_status like any other response, because
        # a monitor may legitimately expect a 404 or a 401.
        latency_ms = (time.monotonic() - start) * 1000.0
        status = exc.code
        body = exc.read(_MAX_BODY_READ) if keyword else None
    except (urllib.error.URLError, OSError, ValueError) as exc:
        latency_ms = (time.monotonic() - start) * 1000.0
        reason = getattr(exc, "reason", exc)
        return CheckResult(ok=False, latency_ms=latency_ms, error=str(reason))

    if status != expected:
        return CheckResult(
            ok=False,
            status_code=status,
            latency_ms=latency_ms,
            error="expected status %d, got %d" % (expected, status),
        )
    if keyword is not None:
        text = body.decode("utf-8", errors="replace")
        if keyword not in text:
            return CheckResult(
                ok=False,
                status_code=status,
                latency_ms=latency_ms,
                error="keyword %r not found in body" % keyword,
            )
    return CheckResult(ok=True, status_code=status, latency_ms=latency_ms)
