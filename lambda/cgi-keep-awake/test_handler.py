import datetime as dt
import os
import unittest
from unittest import mock

os.environ["API_URL"] = "https://api.example"
import handler as H  # noqa: E402


class T(unittest.TestCase):
    def test_healthy_run_does_not_warm_outside_the_hourly_slot(self):
        with mock.patch.object(H, "_get", return_value=("200", 0.2)) as g, mock.patch.object(H, "should_warm", return_value=False):
            r = H.lambda_handler({}, None)
        self.assertEqual((r["api"], r["warmed"], g.call_count), ("200", 0, 1))

    def test_hourly_run_warms_every_page_one_by_one(self):
        with mock.patch.object(H, "_get", return_value=("200", 0.2)) as g, mock.patch.object(H, "should_warm", return_value=True), mock.patch.object(H.time, "sleep") as sl:
            r = H.lambda_handler({}, None)
        self.assertEqual((r["warmed"], g.call_count, sl.call_count), (len(H.WARM_PATHS), 1 + len(H.WARM_PATHS), len(H.WARM_PATHS)))

    def test_api_down_raises_so_cloudwatch_counts_an_error(self):
        with mock.patch.object(H, "_get", return_value=("no response (URLError)", 90.0)), mock.patch.object(H, "should_warm", return_value=False):
            with self.assertRaises(RuntimeError):
                H.lambda_handler({}, None)

    def test_a_503_raises(self):
        with mock.patch.object(H, "_get", return_value=("503", 1.0)), mock.patch.object(H, "should_warm", return_value=False):
            with self.assertRaises(RuntimeError):
                H.lambda_handler({}, None)

    def test_a_failed_warmup_never_fails_the_run(self):
        calls = iter([("200", 0.2)] + [("504", 70.0)] * len(H.WARM_PATHS))
        with mock.patch.object(H, "_get", side_effect=lambda *a, **k: next(calls)), mock.patch.object(H, "should_warm", return_value=True), mock.patch.object(H.time, "sleep"):
            self.assertEqual(H.lambda_handler({}, None)["api"], "200")

    def test_warm_slot_is_the_first_ten_minutes(self):
        self.assertEqual([H.should_warm(dt.datetime(2026, 11, 14, 3, m, tzinfo=dt.timezone.utc)) for m in (0, 9, 10, 50)], [True, True, False, False])

    def test_a_real_http_error_status_is_returned_not_raised(self):
        import urllib.error
        err = urllib.error.HTTPError("u", 502, "bad", {}, None)
        with mock.patch.object(H.urllib.request, "urlopen", side_effect=err):
            self.assertEqual(H._get("https://x", 5)[0], "502")


if __name__ == "__main__":
    unittest.main()
