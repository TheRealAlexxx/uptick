"""The read-only status page.

Served by http.server, rendered as one self-contained HTML page (inline
CSS, no JavaScript, no external assets) so the page works on any connection
and never phones home. A JSON view of the same data lives at /api/status.
"""

import html
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_DAY = 86400
_MONTH = 30 * _DAY

_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="refresh" content="60">
<title>{title}</title>
<style>
:root {{ color-scheme: dark; }}
body {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  background: #0d1117; color: #c9d1d9; max-width: 720px; margin: 2rem auto;
  padding: 0 1rem; }}
h1 {{ font-size: 1.3rem; }}
.banner {{ padding: .6rem .9rem; border-radius: 6px; font-weight: bold;
  margin-bottom: 1.2rem; }}
.ok {{ background: #12351f; color: #3fb950; }}
.down {{ background: #4a1720; color: #f85149; }}
table {{ border-collapse: collapse; width: 100%; }}
td, th {{ text-align: left; padding: .35rem .5rem; border-bottom: 1px solid #21262d; }}
.state-up {{ color: #3fb950; font-weight: bold; }}
.state-down {{ color: #f85149; font-weight: bold; }}
.state-unknown {{ color: #8b949e; }}
.muted {{ color: #8b949e; font-size: .85rem; }}
h2 {{ font-size: 1rem; margin-top: 1.6rem; }}
</style>
</head>
<body>
<h1>{title}</h1>
{banner}
<table>
<tr><th>monitor</th><th>state</th><th>24h</th><th>30d</th><th>last check</th><th>latency</th></tr>
{rows}
</table>
{incidents}
<p class="muted">checked by uptick; page refreshes every 60s; generated {generated}</p>
</body>
</html>
"""


def _fmt_time(ts):
    if ts is None:
        return "never"
    return time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime(ts))


def _fmt_pct(fraction):
    if fraction is None:
        return "-"
    return "%.2f%%" % (fraction * 100)


def _fmt_duration(seconds):
    seconds = int(seconds)
    if seconds < 60:
        return "%ds" % seconds
    if seconds < 3600:
        return "%dm%02ds" % divmod(seconds, 60)
    hours, rest = divmod(seconds, 3600)
    return "%dh%02dm" % (hours, rest // 60)


def build_status(storage):
    """Collect everything the page and the JSON API show."""
    now = time.time()
    monitors = []
    any_down = False
    for monitor in storage.list_monitors():
        latest = storage.latest_result(monitor["id"])
        if latest is None:
            state = "unknown"
            last_ts, latency = None, None
        else:
            state = "up" if latest[1] else "down"
            last_ts, latency = latest[0], latest[3]
        open_incident = storage.open_incident(monitor["id"])
        if state == "down" and open_incident:
            any_down = True
        monitors.append(
            {
                "name": monitor["name"],
                "url": monitor["url"],
                "state": state,
                "uptime_24h": storage.uptime_fraction(monitor["id"], now - _DAY),
                "uptime_30d": storage.uptime_fraction(monitor["id"], now - _MONTH),
                "last_check": last_ts,
                "latency_ms": latency,
                "incident_open_since": open_incident["started_at"] if open_incident else None,
            }
        )
    incidents = storage.list_incidents(limit=20)
    names = {m["id"]: m["name"] for m in storage.list_monitors()}
    for incident in incidents:
        incident["monitor"] = names.get(incident["monitor_id"], "?")
    return {
        "overall": "down" if any_down else "operational",
        "generated_at": now,
        "monitors": monitors,
        "incidents": incidents,
    }


def render_html(status, title="uptick status"):
    banner_class = "down" if status["overall"] == "down" else "ok"
    banner_text = (
        "Something is down" if status["overall"] == "down" else "All systems operational"
    )
    banner = '<div class="banner %s">%s</div>' % (banner_class, banner_text)
    rows = []
    for m in status["monitors"]:
        state = html.escape(m["state"])
        cells = (
            "<td>%s</td>" % html.escape(m["name"]),
            '<td class="state-%s">%s</td>' % (state, state),
            "<td>%s</td>" % _fmt_pct(m["uptime_24h"]),
            "<td>%s</td>" % _fmt_pct(m["uptime_30d"]),
            "<td>%s</td>" % _fmt_time(m["last_check"]),
            "<td>%s</td>" % ("%.0fms" % m["latency_ms"] if m["latency_ms"] else "-"),
        )
        rows.append("<tr>%s</tr>" % "".join(cells))
    if not rows:
        rows.append('<tr><td colspan="6" class="muted">no monitors yet</td></tr>')

    if status["incidents"]:
        items = []
        for inc in status["incidents"]:
            if inc["ended_at"] is None:
                length = "ongoing (%s)" % _fmt_duration(time.time() - inc["started_at"])
            else:
                length = _fmt_duration(inc["ended_at"] - inc["started_at"])
            items.append(
                "<li><b>%s</b> %s - %s, %s <span class=\"muted\">(%s)</span></li>"
                % (
                    html.escape(inc["monitor"]),
                    _fmt_time(inc["started_at"]),
                    "recovered " + _fmt_time(inc["ended_at"]) if inc["ended_at"] else "ongoing",
                    length,
                    html.escape(inc["reason"] or "check failed"),
                )
            )
        incidents_html = "<h2>incidents</h2><ul>%s</ul>" % "".join(items)
    else:
        incidents_html = '<h2>incidents</h2><p class="muted">none recorded</p>'

    return _PAGE.format(
        title=html.escape(title),
        banner=banner,
        rows="\n".join(rows),
        incidents=incidents_html,
        generated=_fmt_time(status["generated_at"]),
    )


class StatusHandler(BaseHTTPRequestHandler):
    storage = None  # set by serve_status
    title = "uptick status"

    def _send(self, code, body, content_type):
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path in ("/", "/index.html"):
            self._send(200, render_html(build_status(self.storage), self.title), "text/html; charset=utf-8")
        elif self.path == "/api/status":
            self._send(200, json.dumps(build_status(self.storage), indent=2), "application/json")
        else:
            self._send(404, "not found\n", "text/plain; charset=utf-8")

    def log_message(self, *args):
        pass  # the monitor's job is watching others, not logging itself


class StatusServer:
    """Thread wrapper so the status page can run next to the scheduler."""

    def __init__(self, storage, host, port, title="uptick status"):
        handler = type("BoundStatusHandler", (StatusHandler,), {})
        handler.storage = storage
        handler.title = title
        self.httpd = ThreadingHTTPServer((host, port), handler)
        self.thread = None

    @property
    def port(self):
        return self.httpd.server_address[1]

    def start(self):
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        if self.thread:
            self.thread.join(timeout=5)
