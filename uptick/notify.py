"""Webhook notifications for incident transitions.

One generic JSON POST that works with Slack incoming webhooks, Discord
webhooks (which accept the same {"text": ...} shape), and anything else
that takes a JSON body. Failures to notify are logged and swallowed: a
monitor that goes down because the notifier is down would be a bad joke.
"""

import json
import urllib.request


def _post_json(url, payload, timeout=10):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "uptick/0.1"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.getcode()


def _format_text(monitor, event, incident, result):
    if event == "opened":
        return "DOWN: %s - %s" % (monitor["name"], result.error or "check failed")
    if event == "closed" and incident:
        duration = max(0, int((incident.get("ended_at") or 0) - incident["started_at"]))
        minutes, seconds = divmod(duration, 60)
        return "UP: %s recovered after %dm%02ds" % (monitor["name"], minutes, seconds)
    return None


class WebhookNotifier:
    """Callable with the scheduler's on_event signature."""

    def __init__(self, url, timeout=10, on_error=None):
        self.url = url
        self.timeout = timeout
        self.on_error = on_error or (lambda exc: None)

    def __call__(self, monitor, event, incident, result):
        if event not in ("opened", "closed"):
            return
        text = _format_text(monitor, event, incident, result)
        if text is None:
            return
        payload = {
            "text": text,
            "event": event,
            "monitor": monitor["name"],
            "url": monitor["url"],
        }
        try:
            _post_json(self.url, payload, timeout=self.timeout)
        except Exception as exc:  # notify must never break monitoring
            self.on_error(exc)
