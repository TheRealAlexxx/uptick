"""Shared fixtures: a controllable local HTTP server for checker tests."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        routes = self.server.routes
        path = self.path.split("?", 1)[0]
        if path in routes:
            status, body = routes[path]
        else:
            status, body = 404, "not found"
        if status == "hang":
            return  # never answer: exercises the client timeout
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        self.server.posts.append(
            {"path": self.path, "headers": dict(self.headers), "body": body}
        )
        data = b"ok"
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class LocalServer:
    """Context manager: routes maps path -> (status, body)."""

    def __init__(self, routes=None):
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.httpd.routes = routes or {}
        self.httpd.posts = []
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    @property
    def port(self):
        return self.httpd.server_address[1]

    def url(self, path="/"):
        return "http://127.0.0.1:%d%s" % (self.port, path)

    @property
    def posts(self):
        return self.httpd.posts

    def set_route(self, path, status, body):
        self.httpd.routes[path] = (status, body)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)


def make_monitor(url, **overrides):
    monitor = {
        "id": 1,
        "name": "test",
        "url": url,
        "interval_seconds": 60,
        "timeout_seconds": 2,
        "expected_status": 200,
        "keyword": None,
        "failure_threshold": 2,
        "active": True,
    }
    monitor.update(overrides)
    return monitor


def read_json_body(post):
    return json.loads(post["body"].decode("utf-8"))
