"""sqlite storage: monitors, check results, incidents.

One database file, three tables. Every write goes through this module so
the schema and the incident state machine live in exactly one place.
Timestamps are UTC epoch seconds; rendering code converts for display.
"""

import sqlite3
import threading
import time

_SCHEMA = """
CREATE TABLE IF NOT EXISTS monitors (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    url TEXT NOT NULL,
    interval_seconds INTEGER NOT NULL,
    timeout_seconds INTEGER NOT NULL,
    expected_status INTEGER NOT NULL,
    keyword TEXT,
    failure_threshold INTEGER NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS results (
    id INTEGER PRIMARY KEY,
    monitor_id INTEGER NOT NULL REFERENCES monitors(id),
    ts REAL NOT NULL,
    ok INTEGER NOT NULL,
    status_code INTEGER,
    latency_ms REAL,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_results_monitor_ts ON results (monitor_id, ts);
CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY,
    monitor_id INTEGER NOT NULL REFERENCES monitors(id),
    started_at REAL NOT NULL,
    ended_at REAL,
    reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_incidents_monitor ON incidents (monitor_id, started_at);
"""


def _monitor_row_to_dict(row):
    return {
        "id": row[0],
        "name": row[1],
        "url": row[2],
        "interval_seconds": row[3],
        "timeout_seconds": row[4],
        "expected_status": row[5],
        "keyword": row[6],
        "failure_threshold": row[7],
        "active": bool(row[8]),
        "created_at": row[9],
    }


_MONITOR_COLUMNS = (
    "id, name, url, interval_seconds, timeout_seconds, expected_status,"
    " keyword, failure_threshold, active, created_at"
)


class Storage:
    def __init__(self, path):
        self.path = path
        # check_same_thread=False: the scheduler writes from its own threads
        # and the status server reads from others; the lock serializes access.
        self._lock = threading.RLock()
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.row_factory = None
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(_SCHEMA)

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # -- monitors ---------------------------------------------------------

    def upsert_monitor(self, monitor, reactivate=True):
        """Insert a monitor or update the row with the same name. Returns id.

        reactivate=False is used when syncing from the config file: a monitor
        the user deactivated with 'uptick remove' stays inactive even though
        it is still listed in the config.
        """
        now = time.time()
        with self._lock:
            self.db.execute(
                "INSERT INTO monitors (name, url, interval_seconds, timeout_seconds,"
                " expected_status, keyword, failure_threshold, active, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)"
                " ON CONFLICT(name) DO UPDATE SET url=excluded.url,"
                " interval_seconds=excluded.interval_seconds,"
                " timeout_seconds=excluded.timeout_seconds,"
                " expected_status=excluded.expected_status,"
                " keyword=excluded.keyword,"
                " failure_threshold=excluded.failure_threshold"
                + (", active=1" if reactivate else ""),
                (
                    monitor["name"],
                    monitor["url"],
                    monitor["interval_seconds"],
                    monitor["timeout_seconds"],
                    monitor["expected_status"],
                    monitor["keyword"],
                    monitor["failure_threshold"],
                    now,
                ),
            )
            self.db.commit()
            row = self.db.execute(
                "SELECT id FROM monitors WHERE name = ?", (monitor["name"],)
            ).fetchone()
        return row[0]

    def get_monitor(self, name):
        row = self.db.execute(
            "SELECT %s FROM monitors WHERE name = ?" % _MONITOR_COLUMNS, (name,)
        ).fetchone()
        return _monitor_row_to_dict(row) if row else None

    def list_monitors(self, active_only=False):
        query = "SELECT %s FROM monitors" % _MONITOR_COLUMNS
        if active_only:
            query += " WHERE active = 1"
        query += " ORDER BY name"
        return [_monitor_row_to_dict(row) for row in self.db.execute(query)]

    def deactivate_monitor(self, name):
        with self._lock:
            cursor = self.db.execute(
                "UPDATE monitors SET active = 0 WHERE name = ? AND active = 1", (name,)
            )
            self.db.commit()
        return cursor.rowcount > 0

    # -- results ----------------------------------------------------------

    def record_result(self, monitor_id, result, ts=None):
        ts = time.time() if ts is None else ts
        with self._lock:
            self.db.execute(
                "INSERT INTO results (monitor_id, ts, ok, status_code, latency_ms, error)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    monitor_id,
                    ts,
                    1 if result.ok else 0,
                    result.status_code,
                    result.latency_ms,
                    result.error,
                ),
            )
            self.db.commit()
        return ts

    def recent_results(self, monitor_id, limit=50):
        return self.db.execute(
            "SELECT ts, ok, status_code, latency_ms, error FROM results"
            " WHERE monitor_id = ? ORDER BY ts DESC LIMIT ?",
            (monitor_id, limit),
        ).fetchall()

    def latest_result(self, monitor_id):
        rows = self.recent_results(monitor_id, limit=1)
        return rows[0] if rows else None

    def consecutive_failures(self, monitor_id):
        """How many of the most recent results in a row failed."""
        count = 0
        for (ok,) in self.db.execute(
            "SELECT ok FROM results WHERE monitor_id = ? ORDER BY ts DESC LIMIT 100",
            (monitor_id,),
        ):
            if ok:
                break
            count += 1
        return count

    def uptime_fraction(self, monitor_id, since):
        """Share of successful checks since the given epoch time, or None
        when there are no checks in the window."""
        row = self.db.execute(
            "SELECT COUNT(*), SUM(ok) FROM results WHERE monitor_id = ? AND ts >= ?",
            (monitor_id, since),
        ).fetchone()
        total, passed = row[0], row[1]
        if not total:
            return None
        return (passed or 0) / total

    def prune_results(self, older_than):
        """Drop raw results older than the given epoch time. Incidents are
        kept forever: they are the history people actually read."""
        with self._lock:
            cursor = self.db.execute("DELETE FROM results WHERE ts < ?", (older_than,))
            self.db.commit()
        return cursor.rowcount

    # -- incidents --------------------------------------------------------

    def open_incident(self, monitor_id):
        row = self.db.execute(
            "SELECT id, started_at, reason FROM incidents"
            " WHERE monitor_id = ? AND ended_at IS NULL"
            " ORDER BY started_at DESC LIMIT 1",
            (monitor_id,),
        ).fetchone()
        if row:
            return {"id": row[0], "started_at": row[1], "reason": row[2]}
        return None

    def list_incidents(self, monitor_id=None, limit=20):
        if monitor_id is None:
            rows = self.db.execute(
                "SELECT id, monitor_id, started_at, ended_at, reason FROM incidents"
                " ORDER BY started_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        else:
            rows = self.db.execute(
                "SELECT id, monitor_id, started_at, ended_at, reason FROM incidents"
                " WHERE monitor_id = ? ORDER BY started_at DESC LIMIT ?",
                (monitor_id, limit),
            ).fetchall()
        return [
            {
                "id": r[0],
                "monitor_id": r[1],
                "started_at": r[2],
                "ended_at": r[3],
                "reason": r[4],
            }
            for r in rows
        ]

    def apply_result(self, monitor, result, ts=None):
        """Record a result and advance the incident state machine.

        Opens an incident once failures reach the monitor's threshold in a
        row; closes the open incident on the first success after one.
        Returns (event, incident) where event is 'opened', 'closed' or None.
        """
        ts = self.record_result(monitor["id"], result, ts=ts)
        open_inc = self.open_incident(monitor["id"])
        if not result.ok:
            failures = self.consecutive_failures(monitor["id"])
            if open_inc is None and failures >= monitor["failure_threshold"]:
                with self._lock:
                    self.db.execute(
                        "INSERT INTO incidents (monitor_id, started_at, ended_at, reason)"
                        " VALUES (?, ?, NULL, ?)",
                        (monitor["id"], ts, result.error or "check failed"),
                    )
                    self.db.commit()
                return "opened", self.open_incident(monitor["id"])
            return None, open_inc
        if open_inc is not None:
            with self._lock:
                self.db.execute(
                    "UPDATE incidents SET ended_at = ? WHERE id = ?", (ts, open_inc["id"])
                )
                self.db.commit()
            # the row fetched above predates the UPDATE; stamp the close time
            # on the returned incident so callers can compute the duration
            open_inc = dict(open_inc, ended_at=ts)
            return "closed", open_inc
        return None, None
