"""Load and validate the uptick JSON config.

The config is plain JSON so the standard library can read it on every
supported Python version. Validation happens here, once, with errors that
name the exact field that is wrong, so a bad config fails fast instead of
misbehaving at 3am.
"""

import json
import os


class ConfigError(Exception):
    """Raised when the config file is missing, unreadable, or invalid."""


DEFAULT_CONFIG_PATH = "uptick.json"

_DEFAULTS = {
    "interval_seconds": 60,
    "timeout_seconds": 10,
    "expected_status": 200,
    "keyword": None,
    "failure_threshold": 2,
}

_SERVER_DEFAULTS = {"host": "127.0.0.1", "port": 8080}


def _check_monitor(raw, index):
    if not isinstance(raw, dict):
        raise ConfigError("monitor %d: must be an object" % index)
    name = raw.get("name")
    if not name or not isinstance(name, str):
        raise ConfigError("monitor %d: 'name' is required and must be a string" % index)
    url = raw.get("url")
    if not url or not isinstance(url, str):
        raise ConfigError("monitor %r: 'url' is required and must be a string" % name)
    if not url.startswith(("http://", "https://")):
        raise ConfigError("monitor %r: url must start with http:// or https://" % name)

    monitor = {"name": name, "url": url}
    for key, default in _DEFAULTS.items():
        value = raw.get(key, default)
        if key in ("interval_seconds", "timeout_seconds", "failure_threshold"):
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ConfigError("monitor %r: %s must be a positive integer" % (name, key))
        elif key == "expected_status":
            if not isinstance(value, int) or isinstance(value, bool) or not 100 <= value <= 599:
                raise ConfigError("monitor %r: expected_status must be an HTTP status code" % name)
        elif key == "keyword":
            if value is not None and not isinstance(value, str):
                raise ConfigError("monitor %r: keyword must be a string or null" % name)
        monitor[key] = value
    return monitor


def load_config(path=DEFAULT_CONFIG_PATH):
    """Read and validate the config file. Returns a dict with 'monitors',
    'server', 'data_path' and optional 'webhook_url'."""
    if not os.path.exists(path):
        raise ConfigError("config file not found: %s (run 'uptick init' to create one)" % path)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = json.load(handle)
    except json.JSONDecodeError as exc:
        raise ConfigError("config file %s is not valid JSON: %s" % (path, exc))
    if not isinstance(raw, dict):
        raise ConfigError("config file %s: top level must be an object" % path)

    monitors_raw = raw.get("monitors")
    if not isinstance(monitors_raw, list) or not monitors_raw:
        raise ConfigError("config file %s: 'monitors' must be a non-empty list" % path)
    monitors = [_check_monitor(item, i) for i, item in enumerate(monitors_raw)]
    names = [m["name"] for m in monitors]
    if len(set(names)) != len(names):
        raise ConfigError("config file %s: monitor names must be unique" % path)

    server = dict(_SERVER_DEFAULTS)
    server_raw = raw.get("server", {})
    if not isinstance(server_raw, dict):
        raise ConfigError("config file %s: 'server' must be an object" % path)
    server.update({k: v for k, v in server_raw.items() if k in server})
    if not isinstance(server["port"], int) or not 1 <= server["port"] <= 65535:
        raise ConfigError("config file %s: server.port must be 1-65535" % path)

    webhook_url = raw.get("webhook_url")
    if webhook_url is not None:
        if not isinstance(webhook_url, str) or not webhook_url.startswith(("http://", "https://")):
            raise ConfigError("config file %s: webhook_url must be an http(s) URL" % path)

    data_path = raw.get("data_path", "uptick.db")
    if not isinstance(data_path, str) or not data_path:
        raise ConfigError("config file %s: data_path must be a non-empty string" % path)

    return {
        "monitors": monitors,
        "server": server,
        "webhook_url": webhook_url,
        "data_path": data_path,
    }


EXAMPLE_CONFIG = {
    "data_path": "uptick.db",
    "server": {"host": "127.0.0.1", "port": 8080},
    "monitors": [
        {
            "name": "example",
            "url": "https://example.com",
            "interval_seconds": 60,
            "timeout_seconds": 10,
            "expected_status": 200,
            "failure_threshold": 2,
        }
    ],
}


def write_example_config(path=DEFAULT_CONFIG_PATH):
    if os.path.exists(path):
        raise ConfigError("config file already exists: %s" % path)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(EXAMPLE_CONFIG, handle, indent=2)
        handle.write("\n")
    return path
