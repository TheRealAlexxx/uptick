import io
import unittest
from contextlib import redirect_stderr

from helpers import LocalServer, make_monitor, read_json_body

from uptick.checker import CheckResult
from uptick.notify import WebhookNotifier


class NotifyTests(unittest.TestCase):
    def test_open_and_close_post_text(self):
        with LocalServer({"/hook": (200, "ok")}) as server:
            notifier = WebhookNotifier(server.url("/hook"))
            monitor = make_monitor("http://example.test/", name="shop")
            down = CheckResult(ok=False, error="expected status 200, got 503")
            notifier(monitor, "opened", None, down)
            incident = {"started_at": 1000.0, "ended_at": 1130.0}
            up = CheckResult(ok=True, status_code=200, latency_ms=20.0)
            notifier(monitor, "closed", incident, up)

        self.assertEqual(len(server.posts), 2)
        first = read_json_body(server.posts[0])
        self.assertEqual(first["event"], "opened")
        self.assertIn("DOWN: shop", first["text"])
        self.assertIn("503", first["text"])
        second = read_json_body(server.posts[1])
        self.assertEqual(second["event"], "closed")
        self.assertIn("UP: shop recovered after 2m10s", second["text"])

    def test_no_event_no_post(self):
        with LocalServer({"/hook": (200, "ok")}) as server:
            notifier = WebhookNotifier(server.url("/hook"))
            notifier(make_monitor("http://x.test/"), None, None, CheckResult(ok=True))
        self.assertEqual(server.posts, [])

    def test_notify_failure_is_swallowed_to_on_error(self):
        errors = []
        # nothing listening on the port
        notifier = WebhookNotifier("http://127.0.0.1:1/hook", timeout=1, on_error=errors.append)
        notifier(make_monitor("http://x.test/"), "opened", None, CheckResult(ok=False, error="x"))
        self.assertEqual(len(errors), 1)

    def test_default_on_error_logs_to_stderr_and_never_raises(self):
        notifier = WebhookNotifier("http://127.0.0.1:1/hook", timeout=1)
        err = io.StringIO()
        with redirect_stderr(err):
            notifier(make_monitor("http://x.test/"), "opened", None, CheckResult(ok=False, error="x"))
        self.assertIn("webhook notification failed", err.getvalue())


if __name__ == "__main__":
    unittest.main()
