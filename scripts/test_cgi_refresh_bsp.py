"""python3 -m unittest test_cgi_refresh_bsp -- no network. When a BSP decision note is due, and what apply accepts."""
import datetime as dt
import pathlib
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import cgi_refresh as r

D = dt.date.fromisoformat


class Due(unittest.TestCase):
    def test_meeting_one_day_old_no_note(self):
        self.assertEqual(r.bsp_note_due(D("2026-10-23"), []), {"meeting": "2026-10-22", "age_days": 1})

    def test_same_day_counts(self):
        self.assertEqual(r.bsp_note_due(D("2026-10-22"), [])["age_days"], 0)

    def test_note_present_means_nothing_due(self):
        self.assertIsNone(r.bsp_note_due(D("2026-10-23"), ["2026-10-22"]))
        self.assertIsNone(r.bsp_note_due(D("2026-10-23"), ["2026-10-23"]))     # a note dated a day off still counts

    def test_quiet_days_and_old_meetings(self):
        self.assertIsNone(r.bsp_note_due(D("2026-11-02"), []))                 # 11 days after 22 Oct: stops asking
        self.assertIsNone(r.bsp_note_due(D("2026-12-16"), []))                 # meeting not yet held
        self.assertIsNone(r.bsp_note_due(D("2026-10-10"), []))

    def test_17_dec_is_picked_up(self):
        self.assertEqual(r.bsp_note_due(D("2026-12-18"), ["2026-10-22"])["meeting"], "2026-12-17")

    def test_meetings_match_the_api_calendar(self):
        api = pathlib.Path.home() / "cgi-vercel" / "api"
        if not api.exists():
            self.skipTest("cgi-vercel not here")
        sys.path.insert(0, str(api))
        import release_calendar as rc
        self.assertEqual(sorted(d for ds in rc._BSP.values() for d in ds), sorted(r.BSP_MEETINGS))


class Clean(unittest.TestCase):
    ok = {"date": "2026-10-22", "decision": "hike", "rate": 5.5, "change_bp": 50, "lines": ["a" * 200, "b", "", "c", "d"],
          "source_url": "https://example.com/x"}

    def test_valid_row_is_trimmed(self):
        (row,) = r.clean_bsp_post([self.ok])
        self.assertEqual(len(row["lines"]), 3); self.assertEqual(len(row["lines"][0]), 160)

    def test_decision_and_sign_must_agree(self):
        for bad in ({"change_bp": -50}, {"decision": "hold"}, {"decision": "cut"}, {"decision": "raise"}, {"rate": 40}, {"change_bp": 500}):
            self.assertEqual(r.clean_bsp_post([{**self.ok, **bad}]), [], bad)
        self.assertEqual(len(r.clean_bsp_post([{**self.ok, "decision": "hold", "change_bp": 0}])), 1)

    def test_junk_and_non_https_source(self):
        self.assertEqual(r.clean_bsp_post("x"), []); self.assertEqual(r.clean_bsp_post([{"date": "no"}]), [])
        self.assertEqual(r.clean_bsp_post([{**self.ok, "source_url": "http://x.com"}])[0]["source_url"], "")


class PlanItem(unittest.TestCase):
    def test_item_only_when_due(self):
        with mock.patch.object(r, "_get", return_value={"bsp": {"notes": []}}):
            self.assertIn("send_in_apply_as", r.bsp_plan_item(D("2026-10-23")))
            self.assertIsNone(r.bsp_plan_item(D("2026-11-02")))
        with mock.patch.object(r, "_get", return_value={"bsp": {"notes": [{"date": "2026-10-22"}]}}):
            self.assertIsNone(r.bsp_plan_item(D("2026-10-23")))

    def test_api_down_does_not_break_the_plan(self):
        with mock.patch.object(r, "_get", side_effect=OSError("down")):
            self.assertIsNone(r.bsp_plan_item(D("2026-10-23")))


if __name__ == "__main__":
    unittest.main()
