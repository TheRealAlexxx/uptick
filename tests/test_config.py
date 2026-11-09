import json
import os
import tempfile
import unittest

from uptick.config import ConfigError, load_config, write_example_config


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.dir.name, "uptick.json")

    def tearDown(self):
        self.dir.cleanup()

    def write(self, data):
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(data, handle)

    def test_example_config_round_trips(self):
        write_example_config(self.path)
        config = load_config(self.path)
        self.assertEqual(len(config["monitors"]), 1)
        self.assertEqual(config["server"]["port"], 8080)
        self.assertEqual(config["data_path"], "uptick.db")

    def test_missing_file_is_a_config_error(self):
        with self.assertRaises(ConfigError):
            load_config(self.path)

    def test_invalid_json_is_named(self):
        with open(self.path, "w") as handle:
            handle.write("{nope")
        with self.assertRaises(ConfigError) as ctx:
            load_config(self.path)
        self.assertIn("not valid JSON", str(ctx.exception))

    def test_empty_monitors_rejected(self):
        self.write({"monitors": []})
        with self.assertRaises(ConfigError):
            load_config(self.path)

    def test_url_scheme_enforced(self):
        self.write({"monitors": [{"name": "x", "url": "ftp://example.com"}]})
        with self.assertRaises(ConfigError) as ctx:
            load_config(self.path)
        self.assertIn("http://", str(ctx.exception))

    def test_defaults_applied(self):
        self.write({"monitors": [{"name": "x", "url": "https://example.com"}]})
        monitor = load_config(self.path)["monitors"][0]
        self.assertEqual(monitor["interval_seconds"], 60)
        self.assertEqual(monitor["timeout_seconds"], 10)
        self.assertEqual(monitor["expected_status"], 200)
        self.assertEqual(monitor["failure_threshold"], 2)
        self.assertIsNone(monitor["keyword"])

    def test_duplicate_names_rejected(self):
        self.write(
            {
                "monitors": [
                    {"name": "x", "url": "https://a.example.com"},
                    {"name": "x", "url": "https://b.example.com"},
                ]
            }
        )
        with self.assertRaises(ConfigError) as ctx:
            load_config(self.path)
        self.assertIn("unique", str(ctx.exception))

    def test_bad_interval_rejected(self):
        self.write(
            {"monitors": [{"name": "x", "url": "https://example.com", "interval_seconds": 0}]}
        )
        with self.assertRaises(ConfigError):
            load_config(self.path)

    def test_bool_is_not_an_int(self):
        self.write(
            {
                "monitors": [
                    {"name": "x", "url": "https://example.com", "timeout_seconds": True}
                ]
            }
        )
        with self.assertRaises(ConfigError):
            load_config(self.path)

    def test_bad_expected_status_rejected(self):
        self.write(
            {
                "monitors": [
                    {"name": "x", "url": "https://example.com", "expected_status": 99}
                ]
            }
        )
        with self.assertRaises(ConfigError):
            load_config(self.path)

    def test_webhook_url_validated(self):
        self.write(
            {
                "monitors": [{"name": "x", "url": "https://example.com"}],
                "webhook_url": "gopher://evil",
            }
        )
        with self.assertRaises(ConfigError):
            load_config(self.path)

    def test_refuses_to_overwrite_example(self):
        write_example_config(self.path)
        with self.assertRaises(ConfigError):
            write_example_config(self.path)


if __name__ == "__main__":
    unittest.main()
