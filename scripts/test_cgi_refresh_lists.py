"""python3 -m unittest test_cgi_refresh_lists -- no network, no TradingView."""
import contextlib
import io
import json
import os
import pathlib
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(pathlib.Path(__file__).parent))
import cgi_refresh as r

A = ["###BEST", "NASDAQ:MU", "NYSE:BE"]
B = ["###BEST", "NASDAQ:MU", "NYSE:COP"]          # B differs from A by one symbol


def api(*lists):
    return {"watchlists": [{"watchlist_id": i, "symbols": s} for i, s in lists]}


class Diff(unittest.TestCase):
    def test_no_record_means_read_first_never_a_blind_write(self):
        [p] = r.diff_lists({"1": A}, {})
        self.assertEqual(p["action"], "read_first")
        self.assertNotIn("remove", p)

    def test_unchanged_needs_no_tradingview_call(self):
        self.assertEqual(r.diff_lists({"1": A}, {"1": {"symbols": A}}), [{"id": "1", "action": "unchanged"}])

    def test_changed_removes_exactly_what_was_written_then_adds_desired(self):
        [p] = r.diff_lists({"1": B}, {"1": {"symbols": A}})
        self.assertEqual((p["action"], p["remove"], p["add"]), ("rewrite", A, B))
        self.assertEqual(p["hash"], r._hash(B))


class Flow(unittest.TestCase):
    def setUp(self):
        d = tempfile.mkdtemp()
        self.addCleanup(lambda: __import__("shutil").rmtree(d, ignore_errors=True))
        self.patches = [mock.patch.object(r, "STATE_PATH", pathlib.Path(d) / "s.json")]
        for p in self.patches:
            p.start(); self.addCleanup(p.stop)
        self.live = api((1, A), (2, B))

    def run_(self, fn, *a, stdin=""):
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(r, "_get", return_value=self.live), \
             mock.patch.object(sys, "stdin", io.StringIO(stdin)), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = fn(*a)
        return rc, out.getvalue(), err.getvalue()

    def test_full_cycle_writes_once_then_stays_quiet(self):
        # day 0: nothing recorded -> verify (the model read the lists back) records reality
        rc, out, _ = self.run_(r.lists_verify, stdin=json.dumps({"1": A, "2": B}))
        self.assertFalse(json.loads(out)["drift"])
        rc, out, err = self.run_(r.lists_plan)
        self.assertFalse(json.loads(out)["any_change"]); self.assertIn("no TradingView call", err)
        # the desired list 1 changes upstream -> plan says rewrite list 1 ONLY
        self.live = api((1, B), (2, B))
        plan = json.loads(self.run_(r.lists_plan)[1])
        self.assertEqual([(p["id"], p["action"]) for p in plan["lists"]], [("1", "rewrite"), ("2", "unchanged")])
        h = plan["lists"][0]["hash"]
        # the model does the writes, then records them with the hash it was given
        self.assertEqual(self.run_(r.lists_done, [f"1={h}"])[0], 0)
        self.assertFalse(json.loads(self.run_(r.lists_plan)[1])["any_change"])

    def test_done_refuses_when_the_api_moved_since_plan(self):
        self.run_(r.lists_verify, stdin=json.dumps({"1": A, "2": B}))
        self.live = api((1, B), (2, B))
        h = json.loads(self.run_(r.lists_plan)[1])["lists"][0]["hash"]
        self.live = api((1, A + ["NYSE:X"]), (2, B))                      # moved again
        rc, _, err = self.run_(r.lists_done, [f"1={h}"])
        self.assertEqual(rc, 1); self.assertIn("NOT recorded", err)
        self.assertTrue(json.loads(self.run_(r.lists_plan)[1])["any_change"])   # still pending

    def test_verify_catches_manual_drift_and_proposes_the_repair(self):
        self.run_(r.lists_verify, stdin=json.dumps({"1": A, "2": B}))
        drifted = A + ["NASDAQ:ADDED_BY_HAND"]
        rep = json.loads(self.run_(r.lists_verify, stdin=json.dumps({"1": drifted, "2": B}))[1])
        self.assertTrue(rep["drift"])
        row = next(x for x in rep["lists"] if x["id"] == "1")
        self.assertEqual((row["action"], row["remove"], row["add"]), ("rewrite", drifted, A))

    def test_a_bad_stdin_changes_nothing(self):
        self.assertEqual(self.run_(r.lists_verify, stdin="not json")[0], 2)
        self.assertFalse(r.STATE_PATH.exists())


class Defaults(unittest.TestCase):
    def test_the_routine_does_not_default_to_a_host_its_networks_sinkhole(self):
        # 2026-10-05: the first weekday run failed with "Network is unreachable" because the
        # machine's networks answer *.onrender.com with a private address. Everything the
        # routine calls goes through vercel.app, which stays reachable.
        saved = os.environ.pop("CGI_API_URL", None)
        try:
            import importlib
            importlib.reload(r)
            self.assertNotIn("onrender.com", r.API)
            self.assertIn("vercel.app", r.API)
        finally:
            if saved is not None:
                os.environ["CGI_API_URL"] = saved
            importlib.reload(r)


if __name__ == "__main__":
    unittest.main()
