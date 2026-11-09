import json
import os
import tempfile
import unittest
import urllib.request

from helpers import LocalServer, make_monitor

from uptick.checker import CheckResult
from uptick.server import StatusServer, build_status, render_html
from uptick.storage import Storage


class StatusBuildTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.storage = Storage(os.path.join(self.dir.name, "test.db"))

    def tearDown(self):
        self.storage.close()
        self.dir.cleanup()

    def _add(self, name="web", url="http://example.test/"):
        monitor = make_monitor(url, name=name)
        monitor["id"] = self.storage.upsert_monitor(monitor)
        return monitor

    def test_unknown_state_before_any_check(self):
        self._add()
        status = build_status(self.storage)
        self.assertEqual(status["monitors"][0]["state"], "unknown")
        self.assertEqual(status["overall"], "operational")

    def test_overall_down_only_with_open_incident(self):
        monitor = self._add()
        # one failure: state down but no incident yet (threshold 2)
        self.storage.apply_result(monitor, CheckResult(ok=False, error="boom"))
        status = build_status(self.storage)
        self.assertEqual(status["monitors"][0]["state"], "down")
        self.assertEqual(status["overall"], "operational")
        # second failure opens the incident
        self.storage.apply_result(monitor, CheckResult(ok=False, error="boom"))
        self.assertEqual(build_status(self.storage)["overall"], "down")

    def test_uptime_windows_present(self):
        monitor = self._add()
        self.storage.record_result(monitor["id"], CheckResult(ok=True))
        status = build_status(self.storage)
        self.assertEqual(status["monitors"][0]["uptime_24h"], 1.0)
        self.assertEqual(status["monitors"][0]["uptime_30d"], 1.0)

    def test_html_escapes_monitor_fields(self):
        self._add(name='<script>alert("x")</script>')
        page = render_html(build_status(self.storage))
        self.assertNotIn("<script>alert", page)
        self.assertIn("&lt;script&gt;", page)

    def test_html_shows_incident_history(self):
        monitor = self._add()
        self.storage.apply_result(monitor, CheckResult(ok=False, error="boom"), ts=1000)
        self.storage.apply_result(monitor, CheckResult(ok=False, error="boom"), ts=1001)
        self.storage.apply_result(monitor, CheckResult(ok=True), ts=1002)
        page = render_html(build_status(self.storage))
        self.assertIn("incidents", page)
        self.assertIn("boom", page)
        self.assertIn("recovered", page)
        self.assertIn("lasted", page)

    def test_open_incident_reads_once_with_duration(self):
        monitor = self._add()
        self.storage.apply_result(monitor, CheckResult(ok=False, error="boom"))
        self.storage.apply_result(monitor, CheckResult(ok=False, error="boom"))
        page = render_html(build_status(self.storage))
        self.assertIn("ongoing for", page)
        # the old wording printed "ongoing, ongoing (0s)" - must not regress
        self.assertNotIn("ongoing, ongoing", page)
        self.assertNotIn("recovered", page)


class StatusHttpTests(unittest.TestCase):
    def test_page_and_api_served(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = Storage(os.path.join(tmp, "test.db"))
            monitor = make_monitor("http://example.test/", name="web")
            monitor["id"] = storage.upsert_monitor(monitor)
            storage.record_result(monitor["id"], CheckResult(ok=True, status_code=200, latency_ms=5))
            server = StatusServer(storage, "127.0.0.1", 0)
            server.start()
            try:
                with urllib.request.urlopen(
                    "http://127.0.0.1:%d/" % server.port, timeout=5
                ) as response:
                    page = response.read().decode("utf-8")
                self.assertIn("All systems operational", page)
                self.assertIn("web", page)
                with urllib.request.urlopen(
                    "http://127.0.0.1:%d/api/status" % server.port, timeout=5
                ) as response:
                    data = json.loads(response.read().decode("utf-8"))
                self.assertEqual(data["overall"], "operational")
                self.assertEqual(data["monitors"][0]["state"], "up")
                try:
                    urllib.request.urlopen(
                        "http://127.0.0.1:%d/nope" % server.port, timeout=5
                    )
                    self.fail("expected 404")
                except urllib.error.HTTPError as exc:
                    self.assertEqual(exc.code, 404)
            finally:
                server.stop()
                storage.close()


if __name__ == "__main__":
    unittest.main()
