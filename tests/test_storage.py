import os
import tempfile
import unittest

from helpers import make_monitor

from uptick.checker import CheckResult
from uptick.storage import Storage


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "test.db")
        self.storage = Storage(self.path)
        self.monitor = make_monitor("http://example.test/")
        self.monitor["id"] = self.storage.upsert_monitor(self.monitor)

    def tearDown(self):
        self.storage.close()
        self.dir.cleanup()

    def ok(self):
        return CheckResult(ok=True, status_code=200, latency_ms=12.0)

    def fail(self, error="boom"):
        return CheckResult(ok=False, status_code=500, latency_ms=9.0, error=error)

    def test_upsert_updates_existing_name(self):
        first = self.monitor["id"]
        changed = dict(self.monitor, url="http://other.test/", interval_seconds=30)
        second = self.storage.upsert_monitor(changed)
        self.assertEqual(first, second)
        stored = self.storage.get_monitor("test")
        self.assertEqual(stored["url"], "http://other.test/")
        self.assertEqual(stored["interval_seconds"], 30)
        self.assertEqual(len(self.storage.list_monitors()), 1)

    def test_deactivate_keeps_history(self):
        self.storage.record_result(self.monitor["id"], self.ok())
        self.assertTrue(self.storage.deactivate_monitor("test"))
        self.assertEqual(self.storage.list_monitors(active_only=True), [])
        self.assertEqual(len(self.storage.list_monitors()), 1)
        self.assertEqual(len(self.storage.recent_results(self.monitor["id"])), 1)

    def test_consecutive_failures_counts_from_the_top(self):
        mid = self.monitor["id"]
        self.storage.record_result(mid, self.fail())
        self.storage.record_result(mid, self.fail())
        self.storage.record_result(mid, self.ok())
        self.storage.record_result(mid, self.fail())
        self.assertEqual(self.storage.consecutive_failures(mid), 1)

    def test_uptime_fraction(self):
        mid = self.monitor["id"]
        self.storage.record_result(mid, self.ok(), ts=1000)
        self.storage.record_result(mid, self.fail(), ts=1001)
        self.assertEqual(self.storage.uptime_fraction(mid, since=0), 0.5)
        self.assertIsNone(self.storage.uptime_fraction(mid, since=5000))

    def test_prune_results(self):
        mid = self.monitor["id"]
        self.storage.record_result(mid, self.ok(), ts=100)
        self.storage.record_result(mid, self.ok(), ts=200)
        self.assertEqual(self.storage.prune_results(older_than=150), 1)
        self.assertEqual(len(self.storage.recent_results(mid)), 1)

    def test_incident_opens_at_threshold_not_before(self):
        event, _ = self.storage.apply_result(self.monitor, self.fail())
        self.assertIsNone(event)  # 1 failure, threshold is 2
        event, incident = self.storage.apply_result(self.monitor, self.fail())
        self.assertEqual(event, "opened")
        self.assertEqual(incident["reason"], "boom")

    def test_incident_does_not_double_open(self):
        self.storage.apply_result(self.monitor, self.fail())
        self.storage.apply_result(self.monitor, self.fail())
        event, _ = self.storage.apply_result(self.monitor, self.fail())
        self.assertIsNone(event)
        self.assertEqual(len(self.storage.list_incidents()), 1)

    def test_incident_closes_on_first_success(self):
        self.storage.apply_result(self.monitor, self.fail())
        self.storage.apply_result(self.monitor, self.fail())
        event, incident = self.storage.apply_result(self.monitor, self.ok())
        self.assertEqual(event, "closed")
        self.assertIsNotNone(incident)
        self.assertIsNone(self.storage.open_incident(self.monitor["id"]))

    def test_success_streak_resets_failure_count(self):
        self.storage.apply_result(self.monitor, self.fail())
        self.storage.apply_result(self.monitor, self.ok())
        event, _ = self.storage.apply_result(self.monitor, self.fail())
        self.assertIsNone(event)  # back to 1 consecutive failure
        self.assertEqual(len(self.storage.list_incidents()), 0)

    def test_incidents_listed_newest_first(self):
        mid = self.monitor["id"]
        self.storage.apply_result(self.monitor, self.fail(), ts=1000)
        self.storage.apply_result(self.monitor, self.fail(), ts=1001)
        self.storage.apply_result(self.monitor, self.ok(), ts=1002)
        self.storage.apply_result(self.monitor, self.fail(), ts=2000)
        self.storage.apply_result(self.monitor, self.fail(), ts=2001)
        incidents = self.storage.list_incidents(mid)
        self.assertEqual(len(incidents), 2)
        self.assertGreater(incidents[0]["started_at"], incidents[1]["started_at"])
        self.assertIsNone(incidents[0]["ended_at"])
        self.assertIsNotNone(incidents[1]["ended_at"])


if __name__ == "__main__":
    unittest.main()
