import unittest

from helpers import LocalServer, make_monitor

from uptick.checker import check


class CheckerTests(unittest.TestCase):
    def test_success(self):
        with LocalServer({"/": (200, "hello")}) as server:
            result = check(make_monitor(server.url("/")))
        self.assertTrue(result.ok)
        self.assertEqual(result.status_code, 200)
        self.assertGreaterEqual(result.latency_ms, 0)
        self.assertIsNone(result.error)

    def test_wrong_status_fails(self):
        with LocalServer({"/": (500, "boom")}) as server:
            result = check(make_monitor(server.url("/")))
        self.assertFalse(result.ok)
        self.assertEqual(result.status_code, 500)
        self.assertIn("500", result.error)

    def test_expected_status_is_configurable(self):
        with LocalServer({"/": (204, "")}) as server:
            result = check(make_monitor(server.url("/"), expected_status=204))
        self.assertTrue(result.ok)

    def test_404_fails_by_default(self):
        with LocalServer({}) as server:
            result = check(make_monitor(server.url("/missing")))
        self.assertFalse(result.ok)
        self.assertEqual(result.status_code, 404)

    def test_keyword_present(self):
        with LocalServer({"/": (200, "the magic word is sesame")}) as server:
            result = check(make_monitor(server.url("/"), keyword="sesame"))
        self.assertTrue(result.ok)

    def test_keyword_missing_fails(self):
        with LocalServer({"/": (200, "nothing here")}) as server:
            result = check(make_monitor(server.url("/"), keyword="sesame"))
        self.assertFalse(result.ok)
        self.assertIn("keyword", result.error)

    def test_connection_refused_fails(self):
        with LocalServer({}) as server:
            url = server.url("/")
        # server closed: connection refused
        result = check(make_monitor(url))
        self.assertFalse(result.ok)
        self.assertIsNotNone(result.error)

    def test_timeout_fails(self):
        with LocalServer({"/slow": ("hang", "")}) as server:
            result = check(make_monitor(server.url("/slow"), timeout_seconds=1))
        self.assertFalse(result.ok)
        self.assertLess(result.latency_ms, 5000)

    def test_result_equality_ignores_latency(self):
        with LocalServer({"/": (200, "x")}) as server:
            a = check(make_monitor(server.url("/")))
            b = check(make_monitor(server.url("/")))
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
