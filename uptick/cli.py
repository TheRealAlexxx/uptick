"""Command line interface.

Every command takes --config (default ./uptick.json). `run` is the cron
mode: one pass over all monitors, exit code 1 if anything is down, so cron
mails you only on failure. `serve` is the always-on mode: scheduler plus
status page in one process.
"""

import argparse
import json
import sys

from . import __version__
from .config import ConfigError, load_config, write_example_config
from .notify import WebhookNotifier
from .scheduler import Scheduler
from .server import StatusServer, _fmt_time
from .storage import Storage


def _print(message=""):
    sys.stdout.write(message + "\n")


def _open_storage(config):
    return Storage(config["data_path"])


def cmd_init(args):
    path = write_example_config(args.config)
    _print("wrote %s - edit the monitors list, then run 'uptick serve'" % path)
    return 0


def cmd_add(args):
    config = load_config(args.config)
    monitor = {
        "name": args.name,
        "url": args.url,
        "interval_seconds": args.interval,
        "timeout_seconds": args.timeout,
        "expected_status": args.expected_status,
        "keyword": args.keyword,
        "failure_threshold": args.failure_threshold,
    }
    with _open_storage(config) as storage:
        storage.upsert_monitor(monitor)
    _print("monitor %r saved (every %ds, expect %d)" % (args.name, args.interval, args.expected_status))
    return 0


def cmd_remove(args):
    config = load_config(args.config)
    with _open_storage(config) as storage:
        if storage.deactivate_monitor(args.name):
            _print("monitor %r deactivated (history kept)" % args.name)
            return 0
    _print("no monitor named %r" % args.name)
    return 1


def cmd_list(args):
    config = load_config(args.config)
    with _open_storage(config) as storage:
        monitors = storage.list_monitors()
        if not monitors:
            _print("no monitors yet - add one with 'uptick add'")
            return 0
        for monitor in monitors:
            latest = storage.latest_result(monitor["id"])
            if latest is None:
                state = "never checked"
            else:
                state = ("up" if latest[1] else "DOWN") + " at " + _fmt_time(latest[0])
            flag = "" if monitor["active"] else " (inactive)"
            _print("%-20s %-30s %s%s" % (monitor["name"], monitor["url"], state, flag))
    return 0


def _sync_config_monitors(storage, config):
    """Config file is the source of truth for what should be monitored.
    Syncing never reactivates a monitor the user removed with 'uptick remove'."""
    for monitor in config["monitors"]:
        storage.upsert_monitor(monitor, reactivate=False)
    return storage.list_monitors(active_only=True)


def cmd_run(args):
    config = load_config(args.config)
    failures = 0
    with _open_storage(config) as storage:
        monitors = _sync_config_monitors(storage, config)
        notifier = (
            WebhookNotifier(config["webhook_url"]) if config.get("webhook_url") else None
        )
        scheduler = Scheduler(storage, monitors, on_event=notifier)
        for monitor in monitors:
            result = scheduler.run_check(monitor)
            if result.ok:
                _print("ok    %-20s %d %.0fms" % (monitor["name"], result.status_code, result.latency_ms))
            else:
                failures += 1
                _print("FAIL  %-20s %s" % (monitor["name"], result.error))
    return 1 if failures else 0


def cmd_serve(args):
    config = load_config(args.config)
    with _open_storage(config) as storage:
        monitors = _sync_config_monitors(storage, config)
        notifier = (
            WebhookNotifier(config["webhook_url"]) if config.get("webhook_url") else None
        )
        scheduler = Scheduler(storage, monitors, on_event=notifier)
        status = StatusServer(
            storage, config["server"]["host"], config["server"]["port"]
        )
        status.start()
        _print(
            "uptick serving %d monitor(s), status page on http://%s:%d"
            % (len(monitors), config["server"]["host"], status.port)
        )
        try:
            scheduler.run_forever()
        except KeyboardInterrupt:
            _print("stopping")
        finally:
            scheduler.stop()
            status.stop()
    return 0


def cmd_history(args):
    config = load_config(args.config)
    with _open_storage(config) as storage:
        monitor = storage.get_monitor(args.name)
        if monitor is None:
            _print("no monitor named %r" % args.name)
            return 1
        for ts, ok, status_code, latency_ms, error in storage.recent_results(
            monitor["id"], limit=args.limit
        ):
            mark = "ok  " if ok else "FAIL"
            detail = (
                "%d %.0fms" % (status_code, latency_ms)
                if ok
                else (error or "failed")
            )
            _print("%s %s %s" % (mark, _fmt_time(ts), detail))
    return 0


def cmd_incidents(args):
    config = load_config(args.config)
    with _open_storage(config) as storage:
        incidents = storage.list_incidents(limit=args.limit)
        if not incidents:
            _print("no incidents recorded")
            return 0
        names = {m["id"]: m["name"] for m in storage.list_monitors()}
        for inc in incidents:
            end = "ongoing" if inc["ended_at"] is None else "until " + _fmt_time(inc["ended_at"])
            _print(
                "%s %s - %s (%s)"
                % (names.get(inc["monitor_id"], "?"), _fmt_time(inc["started_at"]), end, inc["reason"] or "check failed")
            )
    return 0


def cmd_export(args):
    config = load_config(args.config)
    with _open_storage(config) as storage:
        from .server import build_status

        _print(json.dumps(build_status(storage), indent=2))
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="uptick",
        description="Self-hosted uptime monitoring: checks, incidents, status page. Standard library only.",
    )
    parser.add_argument("--version", action="version", version="uptick " + __version__)
    parser.add_argument("--config", default="uptick.json", help="path to the JSON config (default: ./uptick.json)")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="write an example config file").set_defaults(func=cmd_init)

    add = sub.add_parser("add", help="add or update a monitor")
    add.add_argument("name")
    add.add_argument("url")
    add.add_argument("--interval", type=int, default=60, help="seconds between checks (default 60)")
    add.add_argument("--timeout", type=int, default=10, help="request timeout in seconds (default 10)")
    add.add_argument("--expected-status", type=int, default=200)
    add.add_argument("--keyword", default=None, help="fail unless the body contains this text")
    add.add_argument("--failure-threshold", type=int, default=2, help="consecutive failures before an incident opens (default 2)")
    add.set_defaults(func=cmd_add)

    remove = sub.add_parser("remove", help="deactivate a monitor (history is kept)")
    remove.add_argument("name")
    remove.set_defaults(func=cmd_remove)

    sub.add_parser("list", help="list monitors and their last state").set_defaults(func=cmd_list)
    sub.add_parser("run", help="check everything once; exit 1 if anything is down (cron mode)").set_defaults(func=cmd_run)
    sub.add_parser("serve", help="run the scheduler and the status page").set_defaults(func=cmd_serve)

    history = sub.add_parser("history", help="show recent check results for one monitor")
    history.add_argument("name")
    history.add_argument("--limit", type=int, default=20)
    history.set_defaults(func=cmd_history)

    incidents = sub.add_parser("incidents", help="show recent incidents")
    incidents.add_argument("--limit", type=int, default=20)
    incidents.set_defaults(func=cmd_incidents)

    sub.add_parser("export", help="dump current status as JSON").set_defaults(func=cmd_export)
    return parser


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except ConfigError as exc:
        sys.stderr.write("uptick: %s\n" % exc)
        return 2


if __name__ == "__main__":
    sys.exit(main())
