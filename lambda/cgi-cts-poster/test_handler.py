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
            "watchlists": [{"name": "CGI · now", "watchlist_id": "347463015", "regime": regime,
                            "best": [row(t) for t in TICKS], "worst": [row(t + "W") for t in TICKS]}]}


def brief(changes, compass=3, grid=1):
    return {"changes": changes, "sections": [{"body": {"current": {"compass": compass, "grid": grid}}}]}


class Pure(unittest.TestCase):
    def test_only_alert_kinds_and_dedup(self):
        got = h.select_events([FLIP, ONSET, NOISE, dict(FLIP)], set())
        self.assertEqual([c["kind"] for c in got], ["regime_flip_grid", "theme_onset"])  # rarest first, no cot, no dup

    def test_already_posted_is_skipped(self):
        self.assertEqual(h.select_events([FLIP], {h.event_key(FLIP)}), [])

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

    def test_regime_post_is_short_and_links_to_the_list_and_the_app(self):
        tv = "https://www.tradingview.com/watchlists/347463015/"
        idea = h.compose_regime(FLIP, wl()["watchlists"][0], "2026-10-05", tv)
        self.assertLessEqual(len(idea["text"]), h.SOFT_CAP)
        self.assertIn(tv, idea["text"]); self.assertIn(h.APP_URL, idea["text"])
        self.assertNotIn("Best 20", idea["text"])                      # the lists live in the TradingView list now
        self.assertEqual(idea["images"][0]["url"], f"{h.APP_URL}/share/regime.png?v=2026-10-05")
        self.assertEqual(len(idea["images"]), 1)                       # ONE picture: the regime card

    def test_the_tradingview_link_comes_from_the_served_id(self):
        self.assertEqual(h.tv_list_url(wl()), "https://www.tradingview.com/watchlists/347463015/")
        self.assertIsNone(h.tv_list_url({"watchlists": []}))
        idea = h.compose_regime(FLIP, wl()["watchlists"][0], "d", None)   # no list served -> app link only
        self.assertNotIn("tradingview.com", idea["text"]); self.assertIn(h.APP_URL, idea["text"])

    def test_theme_posts_carry_the_links_but_no_picture(self):
        idea = h.compose_theme(ONSET, "d", "https://www.tradingview.com/watchlists/1/")
        self.assertIn("tradingview.com/watchlists/1/", idea["text"]); self.assertIn(h.APP_URL, idea["text"])
        self.assertNotIn("images", idea)

    def test_verify_image_accepts_a_real_png_and_rejects_everything_else(self):
        png = b"\x89PNG\r\n\x1a\n" + b"x" * 100
        class R:
            def __init__(self, status=200, ctype="image/png", body=png):
                self.status, self.headers, self._b = status, {"Content-Type": ctype}, body
                self.headers = type("H", (), {"get": lambda _s, k, d=None: {"Content-Type": ctype}.get(k, d)})()
            def read(self, n=-1): return self._b[:n]
            def __enter__(self): return self
            def __exit__(self, *a): return False
        def fake(r):
            return mock.patch.object(h.urllib.request, "urlopen", return_value=r)
        img = {"url": "https://x/share/regime.png"}
        with fake(R()):                                self.assertTrue(h.verify_image(img))
        with fake(R(ctype="text/html")):               self.assertFalse(h.verify_image(img))
        with fake(R(body=b"not a png at all")):        self.assertFalse(h.verify_image(img))
        with fake(R(body=png + b"x" * h.IMAGE_MAX_BYTES)): self.assertFalse(h.verify_image(img))   # over 5MB
        with mock.patch.object(h.urllib.request, "urlopen", side_effect=OSError("down")):
            self.assertFalse(h.verify_image(img))

    def test_attach_images_drops_the_key_when_none_verify(self):
        idea = {"text": "t", "images": [{"url": "u"}]}
        with mock.patch.object(h, "verify_image", return_value=False):
            self.assertNotIn("images", h.attach_images(dict(idea)))
        with mock.patch.object(h, "verify_image", return_value=True):
            self.assertIn("images", h.attach_images(dict(idea)))

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
            mock.patch.object(h, "verify_image", return_value=True),
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

    def test_a_regime_post_goes_out_with_its_picture_and_links(self):
        self.state_ret = {}
        h.run()
        regime = next(p for p in self.posts if "regime" in p["tags"])
        self.assertEqual(len(regime["images"]), 1); self.assertIn("tradingview.com/watchlists/347463015", regime["text"])
        self.assertNotIn("images", next(p for p in self.posts if "themes" in p["tags"]))

    def test_a_failed_picture_does_not_stop_the_post(self):
        self.state_ret = {}
        with mock.patch.object(h, "verify_image", return_value=False):
            h.run()
        regime = next(p for p in self.posts if "regime" in p["tags"])
        self.assertNotIn("images", regime); self.assertIn(h.APP_URL, regime["text"])   # still posted, links intact

    def test_replay_preview_shows_the_picture_url_to_open_before_posting(self):
        self.state_ret = {}
        out = h.replay({"kind": "regime_flip_grid", "when": "2026-10-14"}, preview=True)
        self.assertTrue(out["idea"]["images"][0]["url"].endswith("/share/regime.png?v=" + h.dt.datetime.now(h.dt.timezone.utc).date().isoformat()))
        self.assertEqual(self.posts, [])

    def test_repost_posts_again_while_the_default_still_refuses(self):
        self.state_ret = {h.event_key(FLIP): "2026-10-05"}
        spec = {"kind": "regime_flip_grid", "when": "2026-10-14"}
        self.assertTrue(h.replay(spec, preview=False)["already_posted"]); self.assertEqual(self.posts, [])
        out = h.replay(spec, preview=False, repost=True)
        self.assertTrue(out["posted"]); self.assertEqual(len(self.posts), 1)
        self.assertIn(h.event_key(FLIP), self.saved[-1])                              # re-recorded

    def test_inspect_returns_the_stored_fields_and_posts_nothing(self):
        calls = []
        def rpc(token, payload, session=None):
            calls.append((payload.get("params") or {}).get("name") or payload.get("method"))
            if payload.get("method") == "tools/call":
                body = json.dumps({"postId": "p1", "text": "t", "imageCount": 1, "tags": ["regime"], "secret": "no"})
                return {"result": {"content": [{"text": body}]}}, session
            return {"result": {}}, "sid"
        with mock.patch.object(h, "_rpc", side_effect=rpc), mock.patch.dict(h.os.environ, {"CTS_AGENT_TOKEN": "x"}):
            out = h.lambda_handler({"inspect": "p1"}, None)
        self.assertTrue(out["ok"]); self.assertEqual(out["post"]["imageCount"], 1)
        self.assertNotIn("secret", out["post"])                        # only the whitelisted fields come back
        self.assertIn("get_post", calls); self.assertNotIn("post_idea", calls)

    def test_old_state_is_pruned(self):
        old = (dt.date.today() - dt.timedelta(days=90)).isoformat()
        self.assertEqual(h.prune({"a": old, "b": dt.date.today().isoformat()}, dt.date.today()),
                         {"b": dt.date.today().isoformat()})


if __name__ == "__main__":
    unittest.main()
