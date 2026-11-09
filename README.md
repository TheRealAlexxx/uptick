# uptick

Self-hosted uptime monitoring in one Python package: periodic HTTP checks, incident tracking in sqlite, and a built-in status page. Standard library only, Python 3.9+.

No dependencies, no Docker, no build step. If you have Python, you have a monitor.

```
$ uptick init                          # writes uptick.json
$ uptick add shop https://shop.example.com --interval 30
$ uptick serve                         # checks + status page on :8080
uptick serving 2 monitor(s), status page on http://127.0.0.1:8080
```

```
$ uptick list
example              https://example.com            up at 2026-11-09 21:20:12 UTC
shop                 https://shop.example.com       never checked
$ uptick run                           # one pass, exit 1 on failure (cron mode)
ok    example           200 112ms
ok    shop              200 98ms
```

The status page shows per-monitor state, 24h and 30d uptime, last latency, and the incident log:

```
All systems operational

monitor   state   24h      30d      last check             latency
example   up      100.00%  99.87%   2026-11-09 21:20:12    112ms
shop      up      100.00%  100.00%  2026-11-09 21:19:48    98ms

incidents
shop 2026-11-02 03:14:00 UTC - recovered 2026-11-02 03:19:30 UTC, 5m30s (expected status 200, got 503)
```

The same data is at `/api/status` as JSON, and `uptick export` prints it.

## Why another uptime monitor

Full monitoring stacks assume you want a container, a database service, and a dashboard framework. Sometimes you want to watch three endpoints from a cron job or a spare VPS and get a page you can paste into a chat. uptick is that: one file of config, one sqlite file of history, one process when you want it live.

## What it checks

Each monitor is an HTTP(S) URL plus expectations:

- **status code** (default 200, configurable)
- **keyword** in the response body (optional: "the page loads" and "the page says what it should" are different things)
- **latency** is recorded on every check and shown on the status page

A monitor goes through states on **consecutive failures**, not single blips: one failed check is a failed check, `failure_threshold` (default 2) in a row opens an incident, and the first success after that closes it. Incidents - not raw checks - are what the status page leads with, and what notifications fire on.

## Notifications

Set `webhook_url` in the config and uptick POSTs a small JSON body on every incident open and close:

```json
{"text": "DOWN: shop - expected status 200, got 503", "event": "opened", "monitor": "shop", "url": "https://shop.example.com"}
```

The `text` field matches what Slack incoming webhooks and Discord webhooks accept, so those work with no adapter. Anything else that takes a JSON POST works too.

## Config

`uptick init` writes this shape; every monitor field except `name` and `url` has a default:

```json
{
  "data_path": "uptick.db",
  "server": {"host": "127.0.0.1", "port": 8080},
  "webhook_url": null,
  "monitors": [
    {
      "name": "shop",
      "url": "https://shop.example.com",
      "interval_seconds": 60,
      "timeout_seconds": 10,
      "expected_status": 200,
      "keyword": "Add to cart",
      "failure_threshold": 2
    }
  ]
}
```

The config file is the source of truth for what *should* be monitored; `uptick add` and `uptick remove` adjust the live database. `remove` deactivates (history is kept) and a later config sync never silently reactivates it.

## Design notes

- **Standard library only.** Checks use `urllib`, storage uses `sqlite3`, the page uses `http.server`, the CLI uses `argparse`. The whole thing installs anywhere Python 3.9+ runs and cannot break on a dependency update, because there are none.
- **sqlite, WAL mode, one file.** Results are append-only rows with an index on `(monitor_id, ts)`. Uptime percentages are `SUM(ok)/COUNT(*)` over a time window, computed at render time - no aggregation tables to drift out of sync. Raw results can be pruned (`storage.prune_results`); incidents are kept forever, because they are the history people actually read.
- **One scheduler thread, a small pool for checks.** A single loop decides what is due; a `ThreadPoolExecutor` runs the HTTP checks so one hanging endpoint (bounded by its own timeout) cannot stall the others.
- **The status page is read-only and self-contained.** Inline CSS, no JavaScript, no external assets, a 60-second meta refresh. It never writes to the database and never loads anything from the internet, so it is safe to expose. JSON consumers get the same data at `/api/status`.
- **Notification failures never break monitoring.** A webhook that is down writes one line to stderr and moves on; the monitor's job is watching other things, not falling over with them.

## Honest limits

- HTTP(S) only. No TCP/ping/DNS checks yet.
- The scheduler reads the monitor set when `serve` starts; config edits need a restart to take effect (the `run` cron mode always reads fresh).
- No authentication on the status page. Binding beyond localhost exposes your monitor names, URLs and incident history to anyone who can reach the port; keep the default localhost bind or put it behind something that auths.
- The request timeout bounds each socket operation, not the whole exchange; a slow redirect chain can take longer than `timeout_seconds` in wall-clock time.
- Keyword checks read at most the first 512KB of a response body.
- The status table scrolls horizontally on narrow screens rather than reflowing.
- No distributed anything. One process, one database file.

## Development

```
PYTHONPATH=. python -m unittest discover -s tests    # 58 tests, stdlib only
```

CI runs the suite on Python 3.9, 3.10, 3.11 and 3.12 (3.9 on the ubuntu-22.04 image, which still ships a 3.9 build).

## License

MIT
