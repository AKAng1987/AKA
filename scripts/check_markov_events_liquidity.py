"""Offline checks for the stored FOMC flip figure (lambda/regime_signal_updater/markov_events.py) and the smaller-hike
note (cgi-vercel api/macro_data.py, api/markov_data.py).
Run: ~/cgi-vercel/api/.venv/bin/python scripts/check_markov_events_liquidity.py"""
from __future__ import annotations

import os
import sys
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lambda", "regime_signal_updater"))
API = os.path.expanduser("~/cgi-vercel/api")
sys.path.insert(1, API)

# markov_events builds boto3 clients at import; a stub keeps this offline.
_real_boto3 = sys.modules.get("boto3")
sys.modules["boto3"] = types.SimpleNamespace(client=lambda *a, **k: None, resource=lambda *a, **k: None)
import markov_events as ME  # noqa: E402
if _real_boto3 is not None:
    sys.modules["boto3"] = _real_boto3
else:
    del sys.modules["boto3"]

fails = 0


def check(name, got, want):
    global fails
    ok = got == want
    fails += 0 if ok else 1
    print(("ok  " if ok else "FAIL"), name, "" if ok else f"got {got!r} want {want!r}")


# The 28 Oct 2026 meeting: liquidity tight (C3), a hold priced at about 82%.
oct28 = {"p_hike": 0.0, "p_hold": 0.82, "p_cut": 0.18}
check("tight: 1 - P(hike), not P(cut)", round(ME.liquidity_flip_p(oct28, 0), 3), 1.0)
p = {"p_hike": 0.18, "p_hold": 0.70, "p_cut": 0.12}
check("tight: hold + cut", round(ME.liquidity_flip_p(p, 0), 3), 0.82)
check("easing: P(hike)", round(ME.liquidity_flip_p(p, 1), 3), 0.18)

import markov_data as MD  # noqa: E402
import macro_data as MAC  # noqa: E402

for state in (0, 1):
    for case in (oct28, p, {"p_hike": 0.5, "p_hold": 0.25, "p_cut": 0.25}):
        check(f"Lambda copy equals cgi-vercel (state {state}, {case})",
              round(ME.liquidity_flip_p(case, state), 6), round(MD.liquidity_flip_p(case, state), 6))

# _market_liquidity reads the S3 json: feed it one through a stub client.
import json  # noqa: E402


class _Body:
    def __init__(self, b):
        self.b = b

    def read(self):
        return self.b


def _s3_with(doc):
    return types.SimpleNamespace(get_object=lambda **k: {"Body": _Body(json.dumps(doc).encode())})


ME._s3 = _s3_with({"last_move": {"date": "2026-09-18", "bp": 25},
                   "probabilities": [{"date": "2026-10-28", **oct28}]})
m = ME._market_liquidity("2026-10-28", 0)
check("stored figure for the 28 Oct hold", m["p_flip"], 1.0)
check("no smaller-hike text after a 25bp hike", "smaller" in m["detail"], False)

ME._s3 = _s3_with({"last_move": {"date": "2022-11-03", "bp": 75},
                   "probabilities": [{"date": "2022-12-14", "p_hike": 1.0, "p_hold": 0.0, "p_cut": 0.0, "p_smaller_hike": 0.92}]})
m = ME._market_liquidity("2022-12-14", 0)
check("smaller-hike odds carried in the stored detail", m["detail"].endswith("smaller hike than the last 75bp 92%"), True)
check("a 50bp hike after 75 still stores P(flip) 0 when tight", m["p_flip"], 0.0)

# Size helpers (cgi-vercel macro_data)
ff = [{"date": "2022-06-16", "upper": 1.75}, {"date": "2022-07-28", "upper": 2.50}, {"date": "2022-09-22", "upper": 3.25},
      {"date": "2022-11-03", "upper": 4.00}, {"date": "2022-12-15", "upper": 4.50}, {"date": "2023-02-02", "upper": 4.75},
      {"date": "2023-03-23", "upper": 5.00}, {"date": "2024-09-19", "upper": 5.00}, {"date": "2024-09-19", "upper": 5.00}]
mv = MAC.policy_moves([{"date": "2022-05-05", "upper": 1.00}] + ff)
check("moves in bp", [x["bp"] for x in mv], [75, 75, 75, 75, 50, 25, 25])
check("75 -> 50 is a smaller hike", MAC.is_smaller_hike(50, 75), True)
check("50 -> 25 is a smaller hike", MAC.is_smaller_hike(25, 50), True)
check("25 -> 25 is not", MAC.is_smaller_hike(25, 25), False)
check("a hike after a cut is not", MAC.is_smaller_hike(25, -25), False)
check("a cut is not", MAC.is_smaller_hike(-25, 50), False)
check("hold (no move) after a hike is not a move at all", MAC.is_smaller_hike(0, 50), False)

buckets = [{"steps": 0, "prob": 0.05}, {"steps": 1, "prob": 0.15}, {"steps": 2, "prob": 0.70}, {"steps": 3, "prob": 0.10}]
check("P(smaller hike) after 75: 25 or 50", MAC.p_smaller_hike(buckets, 75), 0.85)
check("P(smaller hike) after 50: 25 only", MAC.p_smaller_hike(buckets, 50), 0.15)
check("none after 25bp", MAC.p_smaller_hike(buckets, 25), None)
check("none after a cut", MAC.p_smaller_hike(buckets, -25), None)

# Scorecard notes (cgi-vercel markov_data)
check("size note on 14 Dec 2022 (effective 15 Dec)", MD.fomc_size_note("2022-12-14", mv), "smaller hike (75 -> 50bp): still Tightening")
check("size note on 1 Feb 2023", MD.fomc_size_note("2023-02-01", mv), "smaller hike (50 -> 25bp): still Tightening")
check("no size note on a 75 after 75", MD.fomc_size_note("2022-11-02", mv), None)
check("no size note on a hold", MD.fomc_size_note("2024-01-31", mv), None)
check("old-formula note on the 28 Oct row",
      MD.old_formula_note(0, 0.0, "hike 0 / hold 82 / cut 18"),
      "stored with the old formula (P(cut) only); 1 - P(hike) from the same odds was 100%")
check("no note once the figure is right", MD.old_formula_note(0, 1.0, "hike 0 / hold 82 / cut 18"), None)
check("no note when easing", MD.old_formula_note(1, 0.0, "hike 0 / hold 82 / cut 18"), None)

print("FAILS:", fails)
sys.exit(1 if fails else 0)
