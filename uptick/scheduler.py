"""The check loop.

A single thread scans for monitors that are due and runs their checks in a
small thread pool, so one slow endpoint cannot stall the others. The loop
re-reads nothing per tick: monitors are synced from storage, which the CLI
writes to, so 'uptick add' in another process is picked up on restart of
the serve command. (Live reload is a documented non-goal for v0.1.)
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import checker


class Scheduler:
    def __init__(self, storage, monitors, on_event=None, max_workers=4, sleep=0.5):
        self.storage = storage
        self.monitors = {m["name"]: m for m in monitors}
        self.on_event = on_event or (lambda monitor, event, incident, result: None)
        self.max_workers = max_workers
        self.sleep = sleep
        self._next_due = {}
        self._stop = threading.Event()

    def due_monitors(self, now=None):
        now = time.time() if now is None else now
        due = []
        for name, monitor in self.monitors.items():
            if not monitor.get("active", True):
                continue
            if now >= self._next_due.get(name, 0):
                due.append(monitor)
        return due

    def run_check(self, monitor):
        """Run one check, store it, fire the event hook. Returns the result.
        Safe to call directly from tests or the CLI."""
        result = checker.check(monitor)
        event, incident = self.storage.apply_result(monitor, result)
        self._next_due[monitor["name"]] = time.time() + monitor["interval_seconds"]
        self.on_event(monitor, event, incident, result)
        return result

    def tick(self, executor=None):
        """Run every monitor that is due. Returns the number of checks run."""
        due = self.due_monitors()
        if not due:
            return 0
        if executor is None:
            for monitor in due:
                self.run_check(monitor)
        else:
            list(executor.map(self.run_check, due))
        return len(due)

    def run_forever(self):
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            while not self._stop.is_set():
                self.tick(executor)
                self._stop.wait(self.sleep)

    def stop(self):
        self._stop.set()
