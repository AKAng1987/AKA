"""python3 -m unittest test_cgi_refresh_calendar -- no network, no TradingView. The calendar rules in plan()."""
import contextlib
import datetime as dt
import importlib.util
import io
import json
import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import cgi_refresh as r

T = dt.date(2026, 10, 27)


def ev(series, date, kind="rate", title="Interest Rate Decision"):
    return {"series": series, "date": date, "kind": kind, "title": title}


class Say(unittest.TestCase):
    def test_passed_release_not_loaded_is_due_now(self):
        k, why = r.calendar_say("PH_POLICY_RATE", "2026-08-01", [ev("PH_POLICY_RATE", "2026-10-22")], T)
        self.assertEqual(k, "due"); self.assertIn("2026-10-22", why)

    def test_passed_release_already_loaded_is_not_due(self):
        self.assertIsNone(r.calendar_say("PH_POLICY_RATE", "2026-10-01", [ev("PH_POLICY_RATE", "2026-10-22")], T))

    def test_future_release_means_wait(self):
        k, _ = r.calendar_say("JP_POLICY_RATE", "2026-09-01", [ev("JP_POLICY_RATE", "2026-10-30")], T)
        self.assertEqual(k, "wait")

    def test_unlisted_series_is_left_to_the_old_rules(self):
        self.assertIsNone(r.calendar_say("PH_M2", "2026-08-01", [ev("PH_POLICY_RATE", "2026-10-22")], T))

    def test_a_print_the_source_never_posts_stops_being_forced_after_ten_days(self):
        old = [ev("KR_CPI_YOY", "2026-10-02", "cpi", "Inflation Rate")]
        self.assertIsNone(r.calendar_say("KR_CPI_YOY", "2026-08-01", old, T))

    def test_cpi_and_gdp_stamps(self):
        self.assertEqual(r._period_stamp("cpi", dt.date(2026, 11, 5)), "2026-10-01")
        self.assertEqual(r._period_stamp("gdp", dt.date(2026, 11, 9)), "2026-09-01")
        self.assertEqual(r._period_stamp("gdp", dt.date(2026, 3, 26)), "2025-12-01")
        self.assertEqual(r._period_stamp("rate", dt.date(2026, 10, 22)), "2026-10-01")

    def test_stamps_agree_with_the_api_module(self):
        p = pathlib.Path.home() / "cgi-vercel/api/country_calendar.py"
        spec = importlib.util.spec_from_file_location("cc", p); cc = importlib.util.module_from_spec(spec); spec.loader.exec_module(cc)
        for kind in ("rate", "cpi", "gdp"):
            for d in (dt.date(2026, m, day) for m in range(1, 13) for day in (5, 22)):
                self.assertEqual(r._period_stamp(kind, d), cc._period_stamp({"kind": kind}, d), (kind, d))


class UsConsensusDue(unittest.TestCase):
    def test_outstanding_us_forecast_forces_a_fetch_even_with_a_fresh_calendar(self):
        today = dt.date(2026, 11, 8)
        fresh = {"at": "2026-11-07T00:00:00+00:00", "rows": [], "us_forecast_missing": ["2026-11-10"]}
        self.assertTrue(r.calendar_due(fresh, today))

    def test_nothing_outstanding_keeps_the_six_day_cadence(self):
        today = dt.date(2026, 11, 8)
        self.assertFalse(r.calendar_due({"at": "2026-11-07T00:00:00+00:00", "rows": [], "us_forecast_missing": []}, today))

    def test_us_is_in_the_country_list_and_has_an_offset(self):
        self.assertIn("US", r.CAL_COUNTRIES.split(","))
        self.assertIn("US", r.CAL_UTC_OFFSET)
        self.assertTrue(r.CAL_ROW.rstrip("]").endswith("actual"))


class Localize(unittest.TestCase):
    def test_tokyo_release_after_15_utc_is_the_next_local_day(self):
        self.assertEqual(r.localize_calendar_row(["JP", "2026-11-15T23:50:00.000Z", "GDP"])[1], "2026-11-16")

    def test_manila_morning_release_keeps_its_day(self):
        self.assertEqual(r.localize_calendar_row(["PH", "2026-11-09T01:00:00.000Z", "GDP"])[1], "2026-11-09")

    def test_date_only_and_garbage_pass_through(self):
        self.assertEqual(r.localize_calendar_row(["PH", "2026-11-09", "x"]), ["PH", "2026-11-09", "x"])
        self.assertEqual(r.localize_calendar_row(["PH"]), ["PH"])

    def test_new_york_style_release_stays_on_its_day(self):
        self.assertEqual(r.localize_calendar_row(["CA", "2026-10-28T13:45:00.000Z", "BoC"])[1], "2026-10-28")


class Plan(unittest.TestCase):
    def run_plan(self, series, cal_rows, checked=None):
        fresh = {"manual": {"series": series}}
        buf = io.StringIO()
        with mock.patch.object(r, "_get", return_value=fresh), mock.patch.object(r, "_state", return_value=checked or {}), \
                mock.patch.object(r, "_calendar", return_value={"at": "2026-10-27", "rows": cal_rows}), \
                mock.patch.object(r.dt, "date", wraps=dt.date) as d, contextlib.redirect_stdout(buf):
            d.today.return_value = T
            r.plan()
        text = buf.getvalue()
        return json.loads(text[:text.rfind("}") + 1])

    def row(self, sym, ours, cad="monthly", src="ECONOMICS:X"):
        return {"symbol": sym, "ours_date": ours, "cadence": cad, "source_symbol": src, "action": "check_source"}

    def test_released_policy_rate_is_fetched_even_if_asked_recently(self):
        out = self.run_plan([self.row("PH_POLICY_RATE", "2026-08-01")], [ev("PH_POLICY_RATE", "2026-10-22")],
                            {"PH_POLICY_RATE": {"at": "2026-10-20"}})
        self.assertEqual([x["symbol"] for x in out["fetch"]], ["PH_POLICY_RATE"])
        self.assertIn("why", out["fetch"][0])

    def test_already_asked_today_is_not_asked_again(self):
        out = self.run_plan([self.row("PH_POLICY_RATE", "2026-08-01")], [ev("PH_POLICY_RATE", "2026-10-22")],
                            {"PH_POLICY_RATE": {"at": "2026-10-27"}})
        self.assertEqual(out["fetch"], [])

    def test_cpi_with_next_release_ahead_is_left_alone(self):
        out = self.run_plan([self.row("PH_CPI_YOY", "2026-09-01")], [ev("PH_CPI_YOY", "2026-11-05", "cpi", "Inflation Rate")],
                            {"PH_CPI_YOY": {"at": "2026-10-07"}})
        self.assertEqual(out["fetch"], [])

    def test_no_calendar_means_the_old_rules(self):
        out = self.run_plan([self.row("PH_M2", "2026-08-01")], [])
        self.assertEqual([x["symbol"] for x in out["fetch"]], ["PH_M2"])      # never asked -> ask


if __name__ == "__main__":
    unittest.main()
