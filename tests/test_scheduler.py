import os
import tempfile
import unittest

from helpers import LocalServer, make_monitor

from uptick.scheduler import Scheduler
from uptick.storage import Storage


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.storage = Storage(os.path.join(self.dir.name, "test.db"))

    def tearDown(self):
        self.storage.close()
        self.dir.cleanup()

    def _stored_monitor(self, url, **overrides):
        monitor = make_monitor(url, **overrides)
        monitor["id"] = self.storage.upsert_monitor(monitor)
        return monitor

    def test_everything_is_due_at_first(self):
        with LocalServer({"/": (200, "ok")}) as server:
            monitor = self._stored_monitor(server.url("/"))
            scheduler = Scheduler(self.storage, [monitor])
            self.assertEqual(len(scheduler.due_monitors()), 1)

    def test_run_check_records_and_defers(self):
        with LocalServer({"/": (200, "ok")}) as server:
            monitor = self._stored_monitor(server.url("/"))
            scheduler = Scheduler(self.storage, [monitor])
            result = scheduler.run_check(monitor)
            self.assertTrue(result.ok)
            self.assertEqual(len(scheduler.due_monitors()), 0)
            self.assertIsNotNone(self.storage.latest_result(monitor["id"]))

    def test_tick_runs_only_due(self):
        with LocalServer({"/": (200, "ok")}) as server:
            monitor = self._stored_monitor(server.url("/"))
            scheduler = Scheduler(self.storage, [monitor])
            self.assertEqual(scheduler.tick(), 1)
            self.assertEqual(scheduler.tick(), 0)  # not due again yet

    def test_inactive_monitors_never_due(self):
        monitor = self._stored_monitor("http://127.0.0.1:1/", active=False)
        scheduler = Scheduler(self.storage, [monitor])
        self.assertEqual(scheduler.due_monitors(), [])

    def test_event_hook_fires_on_incident_open(self):
        events = []
        with LocalServer({"/": (500, "bad")}) as server:
            monitor = self._stored_monitor(server.url("/"), failure_threshold=1)
            scheduler = Scheduler(
                self.storage,
                [monitor],
                on_event=lambda m, event, inc, res: events.append(event),
            )
            scheduler.run_check(monitor)
        self.assertEqual(events, ["opened"])

    def test_failing_monitor_does_not_block_others(self):
        routes = {"/good": (200, "fine"), "/bad": (500, "nope")}
        with LocalServer(routes) as server:
            good = self._stored_monitor(server.url("/good"), name="good")
            bad = self._stored_monitor(server.url("/bad"), name="bad")
            scheduler = Scheduler(self.storage, [good, bad])
            self.assertEqual(scheduler.tick(), 2)
            self.assertTrue(self.storage.latest_result(good["id"])[1])
            self.assertFalse(self.storage.latest_result(bad["id"])[1])


if __name__ == "__main__":
    unittest.main()
