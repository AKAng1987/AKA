"""python3 -m unittest test_handler -- no network, no AWS."""
import datetime as dt
import json
import unittest
from unittest import mock

import handler as h

FLIP = {"kind": "regime_flip_grid", "title": "INFLATION flipped on CPI",
        "detail": "grid_US 2 -> 1; pre-registered P(flip) was 31%, market said 25%",
        "when": "2026-10-14", "rarity": 1 / 8.9}
ONSET = {"kind": "theme_onset", "title": "copper started a run", "detail": "RS above 200d from 2026-10-14",
         "when": "2026-10-14", "rarity": 1 / 36}
NOISE = {"kind": "cot_extreme", "title": "Lean Hogs at an extreme", "detail": "x", "when": "2026-09-29", "rarity": 0.0037}
TICKS = ["COPX", "XME", "SLX", "GDXJ", "SEA", "SILVER", "INDA", "IHI", "CORN", "XAUUSD", "GLD", "EWG",
         "SLV", "EWQ", "FAN", "WOOD", "RICE", "HACK", "MLPX", "IYT"]


def wl(regime="C3G1"):
    row = lambda t: {"ticker": t}
    return {"current": {"compass": 3, "grid": 1},
            "watchlists": [{"name": "CGI · now", "regime": regime,
                            "best": [row(t) for t in TICKS], "worst": [row(t + "W") for t in TICKS]}]}


def brief(changes, compass=3, grid=1):
    return {"changes": changes, "sections": [{"body": {"current": {"compass": compass, "grid": grid}}}]}


class Pure(unittest.TestCase):
    def test_only_alert_kinds_and_dedup(self):
        got = h.select_events([FLIP, ONSET, NOISE, dict(FLIP)], set())
        self.assertEqual([c["kind"] for c in got], ["regime_flip_grid", "theme_onset"])  # rarest first, no cot, no dup

    def test_already_posted_is_skipped(self):
        self.assertEqual(h.select_events([FLIP], {h.event_key(FLIP)}), [])

    def test_regime_post_fits_cap_and_keeps_both_lists(self):
        idea = h.compose_regime(FLIP, wl()["watchlists"][0], "2026-10-14")
        self.assertLessEqual(len(idea["text"]), h.SOFT_CAP)
        self.assertIn("Best 20", idea["text"]); self.assertIn("Worst 20", idea["text"])
        self.assertIn("C3G1", idea["text"])
        self.assertEqual(idea["tickers"], [])

    def test_full_lists_kept_when_they_fit(self):
        idea = h.compose_regime(FLIP, wl()["watchlists"][0], "d")
        self.assertNotRegex(idea["text"], r"\(\+\d+\)")          # nothing trimmed, nothing claimed trimmed

    def test_trim_is_stated_not_hidden(self):
        long_ = {"name": "CGI · now", "regime": "C3G1",
                 "best": [{"ticker": f"LONGTICKER{i:02d}"} for i in range(20)],
                 "worst": [{"ticker": f"WORSTTICKR{i:02d}"} for i in range(20)]}
        idea = h.compose_regime(FLIP, long_, "d")
        self.assertLessEqual(len(idea["text"]), h.SOFT_CAP)
        self.assertRegex(idea["text"], r"\(\+\d+\)")               # says how many were left off
        self.assertIn("Best 20", idea["text"])                       # and still states the true list size

    def test_model_names_become_quadrants(self):
        self.assertEqual(h._plain("grid_US 2 -> 1; x"), "G2 → G1; x")
        self.assertEqual(h._plain("compass_US 3 -> 4"), "C3 → C4")
        idea = h.compose_regime(FLIP, wl()["watchlists"][0], "d")
        self.assertIn("G2 → G1", idea["text"]); self.assertNotIn("grid_US", idea["text"])

    def test_unreadable_state_raises_instead_of_reseeding(self):
        from botocore.exceptions import ClientError
        denied = ClientError({"Error": {"Code": "AccessDenied", "Message": "x"}}, "GetObject")
        missing = ClientError({"Error": {"Code": "NoSuchKey", "Message": "x"}}, "GetObject")
        fake = mock.Mock(); fake.get_object.side_effect = denied
        with mock.patch.object(h, "_s3", return_value=fake):
            with self.assertRaises(h.StateUnreadable):
                h.load_state()
        fake.get_object.side_effect = missing
        with mock.patch.object(h, "_s3", return_value=fake):
            self.assertIsNone(h.load_state())

    def test_the_real_brief_detail_shape_becomes_quadrants(self):
        # copied from the live weekly brief on 2026-10-05, not written from an assumption
        real = "grid 3 -> 2; pre-registered P(flip) was 40%"
        self.assertEqual(h._plain(real), "G3 → G2; pre-registered P(flip) was 40%")
        idea = h.compose_regime(dict(FLIP, detail=real, title="GROWTH flipped on GDP", when="2026-09-30"),
                                wl()["watchlists"][0], "2026-10-05")
        self.assertIn("(2026-09-30)", idea["text"]); self.assertIn("G3 → G2", idea["text"])
        self.assertNotIn("grid 3", idea["text"])

    def test_regimes_agree(self):
        self.assertTrue(h.regimes_agree(brief([]), wl()))
        self.assertFalse(h.regimes_agree(brief([], grid=2), wl()))
        self.assertFalse(h.regimes_agree({}, wl()))


class Run(unittest.TestCase):
    def setUp(self):
        self.state = {}
        self.posts = []
        self.saved = []
        self.p = [
            mock.patch.object(h, "_get_json", side_effect=self._get),
            mock.patch.object(h, "load_state", side_effect=lambda: self.state_ret),
            mock.patch.object(h, "save_state", side_effect=lambda s: self.saved.append(dict(s))),
            mock.patch.object(h, "post_idea", side_effect=self._post),
            mock.patch.object(h, "_token", return_value="t"),
            mock.patch.object(h, "DRY_RUN", False),
            # Live mode needs a URL; post_idea is mocked, so this placeholder is never contacted.
            mock.patch.object(h, "CTS_MCP_URL", "https://cts.example.invalid/mcp"),
        ]
        for p in self.p: p.start()
        self.addCleanup(lambda: [p.stop() for p in self.p])
        self.brief, self.wl, self.state_ret, self.fail = brief([FLIP, ONSET]), wl(), {}, False

    def _get(self, url):
        return self.brief if "brief" in url else self.wl

    def _post(self, token, idea):
        if self.fail: raise h.CtsError("boom")
        self.posts.append(idea)
        return {}

    def test_first_run_posts_nothing_and_seeds(self):
        self.state_ret = None
        out = h.run()
        self.assertEqual(self.posts, []); self.assertEqual(out["seeded"], 2)
        self.assertEqual(len(self.saved[-1]), 2)

    def test_posts_once_then_second_run_is_silent(self):
        h.run()
        self.assertEqual(len(self.posts), 2)
        self.state_ret = self.saved[-1]          # what the first run persisted
        h.run()
        self.assertEqual(len(self.posts), 2)     # no new posts

    def test_failed_post_is_not_marked_posted(self):
        self.fail = True
        out = h.run()
        self.assertEqual(len(out["failed"]), 2); self.assertEqual(self.saved, [])

    def test_regime_deferred_when_watchlists_stale(self):
        self.wl = wl(); self.wl["current"] = {"compass": 3, "grid": 2}   # still the old regime
        out = h.run()
        kinds = [p["tags"][0] for p in self.posts]
        self.assertEqual(kinds, ["themes"])                               # theme went, regime waited
        self.assertEqual(len(out["deferred"]), 1)

    def test_per_run_cap(self):
        many = [dict(ONSET, title=f"t{i} started a run") for i in range(6)]
        self.brief = brief(many)
        h.run()
        self.assertEqual(len(self.posts), h.MAX_POSTS_PER_RUN)

    def test_dry_run_posts_and_saves_nothing(self):
        with mock.patch.object(h, "DRY_RUN", True):
            out = h.run()
        self.assertEqual(self.posts, []); self.assertEqual(self.saved, []); self.assertEqual(out["posted"], 2)

    def test_live_run_without_a_cts_url_fails_loudly_before_any_work(self):
        with mock.patch.object(h, "CTS_MCP_URL", ""):
            with self.assertRaises(RuntimeError) as cm:
                h.run()
        self.assertIn("CTS_MCP_URL", str(cm.exception))
        self.assertEqual(self.posts, []); self.assertEqual(self.saved, [])

    def test_dry_run_needs_no_cts_url(self):
        with mock.patch.object(h, "DRY_RUN", True), mock.patch.object(h, "CTS_MCP_URL", ""):
            out = h.run()
        self.assertTrue(out["dry_run"])

    def test_rpc_without_a_url_names_the_missing_setting(self):
        with mock.patch.object(h, "CTS_MCP_URL", ""):
            with self.assertRaises(RuntimeError) as cm:
                h._rpc("t", {})
        self.assertIn("CTS_MCP_URL", str(cm.exception))

    def test_selftest_reports_what_is_missing_without_failing(self):
        with mock.patch.object(h, "CTS_MCP_URL", ""), mock.patch.dict(h.os.environ, {}, clear=True):
            out = h.lambda_handler({"selftest": True}, None)
        self.assertFalse(out["ok"])
        self.assertIn("CTS_MCP_URL", out["missing"])

    def test_selftest_signs_in_calls_whoami_and_never_returns_the_token(self):
        secret = "FAKE-CREDENTIAL-FOR-TESTS-ONLY-0000"
        calls = []
        def rpc(token, payload, session=None):
            calls.append(payload.get("method") + ":" + str((payload.get("params") or {}).get("name")))
            if payload.get("method") == "tools/call":
                return {"result": {"content": [{"text": "bot grid-compass-bot"}]}}, session
            return {"result": {}}, "sid1"
        with mock.patch.object(h, "CTS_MCP_URL", "https://cts.example.invalid/mcp"), \
             mock.patch.dict(h.os.environ, {"CTS_AGENT_TOKEN": secret}), mock.patch.object(h, "_rpc", side_effect=rpc):
            out = h.lambda_handler({"selftest": True}, None)
        self.assertTrue(out["ok"])
        self.assertIn("tools/call:whoami", calls)
        self.assertNotIn("post_idea", " ".join(calls))             # read-only: never posts
        self.assertNotIn(secret, json.dumps(out))                  # and never echoes the credential

    def test_selftest_turns_an_http_401_into_a_hint_not_a_crash(self):
        import urllib.error
        err = urllib.error.HTTPError("u", 401, "unauthorized", {}, None)
        with mock.patch.object(h, "CTS_MCP_URL", "https://cts.example.invalid/mcp"), \
             mock.patch.dict(h.os.environ, {"CTS_AGENT_TOKEN": "x"}), mock.patch.object(h, "_rpc", side_effect=err):
            out = h.lambda_handler({"selftest": True}, None)
        self.assertFalse(out["ok"]); self.assertEqual(out["error"], "HTTP 401")

    def test_replay_posts_the_named_event_once_and_records_it(self):
        self.state_ret = {}
        out = h.replay({"kind": "regime_flip_grid", "when": "2026-10-14"}, preview=False)
        self.assertTrue(out["posted"]); self.assertEqual(len(self.posts), 1)
        self.assertIn("replay", self.posts[0]["tags"]); self.assertIn("(2026-10-14)", self.posts[0]["text"])
        self.assertIn(h.event_key(FLIP), self.saved[-1])
        self.state_ret = self.saved[-1]                                     # same record the daily run uses
        again = h.replay({"kind": "regime_flip_grid", "when": "2026-10-14"}, preview=False)
        self.assertTrue(again["already_posted"]); self.assertEqual(len(self.posts), 1)

    def test_replay_preview_posts_and_saves_nothing(self):
        self.state_ret = {}
        out = h.replay({"kind": "regime_flip_grid", "when": "2026-10-14"}, preview=True)
        self.assertTrue(out["previewed"]); self.assertEqual(self.posts, []); self.assertEqual(self.saved, [])
        self.assertLessEqual(out["chars"], h.SOFT_CAP)

    def test_replay_refuses_when_a_later_regime_flip_exists(self):
        later = dict(FLIP, title="CREDIT flipped on SLOOS", when="2026-10-20")
        self.brief = brief([FLIP, later])
        out = h.replay({"kind": "regime_flip_grid", "when": "2026-10-14"}, preview=False)
        self.assertFalse(out["ok"]); self.assertIn("later regime flip", out["error"]); self.assertEqual(self.posts, [])

    def test_replay_refuses_when_watchlists_show_another_regime(self):
        self.wl = wl(); self.wl["current"] = {"compass": 3, "grid": 2}
        out = h.replay({"kind": "regime_flip_grid", "when": "2026-10-14"}, preview=False)
        self.assertFalse(out["ok"]); self.assertEqual(self.posts, [])

    def test_replay_refuses_unknown_events_and_non_alert_kinds(self):
        self.assertFalse(h.replay({"kind": "regime_flip_grid", "when": "2026-01-01"}, preview=False)["ok"])
        self.assertFalse(h.replay({"kind": "cot_extreme", "when": "2026-10-14"}, preview=False)["ok"])
        self.assertFalse(h.replay({"kind": "regime_flip_grid"}, preview=False)["ok"])
        self.assertEqual(self.posts, [])

    def test_old_state_is_pruned(self):
        old = (dt.date.today() - dt.timedelta(days=90)).isoformat()
        self.assertEqual(h.prune({"a": old, "b": dt.date.today().isoformat()}, dt.date.today()),
                         {"b": dt.date.today().isoformat()})


if __name__ == "__main__":
    unittest.main()
