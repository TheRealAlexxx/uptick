import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

from helpers import LocalServer

from uptick.cli import main


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


class CliTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.config_path = os.path.join(self.dir.name, "uptick.json")
        code, _, _ = run_cli("--config", self.config_path, "init")
        assert code == 0
        # point the config's db into the temp dir too, and keep tests
        # offline: the example monitor points at a port nothing listens on
        with open(self.config_path) as handle:
            config = json.load(handle)
        config["data_path"] = os.path.join(self.dir.name, "uptick.db")
        config["monitors"][0]["url"] = "http://127.0.0.1:9/"
        with open(self.config_path, "w") as handle:
            json.dump(config, handle)

    def set_monitors(self, monitors):
        with open(self.config_path) as handle:
            config = json.load(handle)
        config["monitors"] = monitors
        with open(self.config_path, "w") as handle:
            json.dump(config, handle)

    def test_init_writes_example(self):
        self.assertTrue(os.path.exists(self.config_path))
        code, _, err = run_cli("--config", self.config_path, "init")
        self.assertEqual(code, 2)
        self.assertIn("already exists", err)

    def test_missing_config_exit_2(self):
        code, _, err = run_cli("--config", "/no/such/file.json", "list")
        self.assertEqual(code, 2)
        self.assertIn("not found", err)

    def test_add_and_list(self):
        code, out, _ = run_cli(
            "--config", self.config_path, "add", "shop", "https://shop.example.com",
            "--interval", "30",
        )
        self.assertEqual(code, 0)
        code, out, _ = run_cli("--config", self.config_path, "list")
        self.assertEqual(code, 0)
        self.assertIn("shop", out)
        self.assertIn("https://shop.example.com", out)
        self.assertIn("never checked", out)

    def test_add_rejects_invalid_values(self):
        # regression: cmd_add used to skip config validation entirely
        code, _, err = run_cli(
            "--config", self.config_path, "add", "bad", "https://x.example.com",
            "--interval", "-5",
        )
        self.assertEqual(code, 2)
        self.assertIn("interval_seconds", err)
        code, _, err = run_cli(
            "--config", self.config_path, "add", "bad", "https://x.example.com",
            "--expected-status", "999",
        )
        self.assertEqual(code, 2)
        self.assertIn("expected_status", err)
        code, _, err = run_cli(
            "--config", self.config_path, "add", "bad", "ftp://x.example.com",
        )
        self.assertEqual(code, 2)
        # and the bad monitor never landed in storage
        code, out, _ = run_cli("--config", self.config_path, "list")
        self.assertNotIn("bad", out)

    def test_remove(self):
        run_cli("--config", self.config_path, "add", "shop", "https://shop.example.com")
        code, out, _ = run_cli("--config", self.config_path, "remove", "shop")
        self.assertEqual(code, 0)
        code, out, _ = run_cli("--config", self.config_path, "remove", "shop")
        self.assertEqual(code, 1)

    def test_run_ok_exit_0(self):
        with LocalServer({"/": (200, "up")}) as server:
            self.set_monitors([{"name": "local", "url": server.url("/")}])
            code, out, _ = run_cli("--config", self.config_path, "run")
        self.assertEqual(code, 0)
        self.assertIn("ok    local", out)

    def test_run_failure_exit_1(self):
        with LocalServer({"/": (503, "down")}) as server:
            self.set_monitors([{"name": "local", "url": server.url("/")}])
            code, out, _ = run_cli("--config", self.config_path, "run")
        self.assertEqual(code, 1)
        self.assertIn("FAIL  local", out)

    def test_run_skips_inactive(self):
        with LocalServer({"/": (200, "up")}) as server:
            self.set_monitors([{"name": "local", "url": server.url("/")}])
            run_cli("--config", self.config_path, "run")  # syncs it in
            code, out, _ = run_cli("--config", self.config_path, "remove", "local")
            self.assertEqual(code, 0)
            code, out, _ = run_cli("--config", self.config_path, "run")
        self.assertEqual(code, 0)
        self.assertNotIn("ok    local", out)

    def test_history(self):
        with LocalServer({"/": (200, "up")}) as server:
            self.set_monitors([{"name": "local", "url": server.url("/")}])
            run_cli("--config", self.config_path, "run")
            code, out, _ = run_cli("--config", self.config_path, "history", "local")
        self.assertEqual(code, 0)
        self.assertIn("ok", out)
        self.assertIn("200", out)

    def test_history_unknown_monitor(self):
        code, out, _ = run_cli("--config", self.config_path, "history", "ghost")
        self.assertEqual(code, 1)

    def test_incidents_after_failures(self):
        with LocalServer({"/": (500, "bad")}) as server:
            self.set_monitors(
                [{"name": "local", "url": server.url("/"), "failure_threshold": 1}]
            )
            run_cli("--config", self.config_path, "run")
            code, out, _ = run_cli("--config", self.config_path, "incidents")
        self.assertEqual(code, 0)
        self.assertIn("local", out)
        self.assertIn("ongoing", out)

    def test_export_is_json(self):
        with LocalServer({"/": (200, "up")}) as server:
            self.set_monitors([{"name": "local", "url": server.url("/")}])
            run_cli("--config", self.config_path, "run")
            code, out, _ = run_cli("--config", self.config_path, "export")
        self.assertEqual(code, 0)
        data = json.loads(out)
        self.assertEqual([m["name"] for m in data["monitors"]], ["local"])

    def test_example_monitor_in_config_is_synced_on_run(self):
        # the example config ships one monitor pointing at example.com;
        # replace it with our local server so the test stays offline
        with LocalServer({"/": (200, "up")}) as server:
            with open(self.config_path) as handle:
                config = json.load(handle)
            config["monitors"][0]["url"] = server.url("/")
            with open(self.config_path, "w") as handle:
                json.dump(config, handle)
            code, out, _ = run_cli("--config", self.config_path, "run")
        self.assertEqual(code, 0)
        self.assertIn("example", out)


if __name__ == "__main__":
    unittest.main()
