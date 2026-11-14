"""check_ph_index_watch.py -- the PSE circular watcher must not drop events posted while it was scanning."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.expanduser("~/cgi-vercel/api"))
import ph_index_watch as W  # noqa: E402

fails = 0
def check(n, got, want):
    global fails
    ok = got == want; fails += 0 if ok else 1
    print(("ok   " if ok else "FAIL ") + n + ("" if ok else f"\n     got {got!r}\n     want {want!r}"))

msci = {"provider": "MSCI", "date": "2026-11-11", "kind": "delete", "index": "MSCI Philippines Standard", "symbols": ["MER"], "effective": "2026-11-27", "source_url": "", "note": "x"}
pse = {"provider": "PSE", "date": "2026-11-12", "kind": "note", "index": "PSEi", "symbols": [], "effective": None, "source_url": "", "note": "y"}
# the scan started from a copy with no events, found one PSE event; meanwhile an MSCI event was posted
out = W.keep_posted_events({"events": [pse], "psei_members": ["A"]}, {"events": [msci]})
check("an event posted during the scan survives the write", sorted(e["provider"] for e in out["events"]), ["MSCI", "PSE"])
check("the scan's own fields are kept", out["psei_members"], ["A"])
check("an empty current copy changes nothing", W.keep_posted_events({"events": [pse]}, {})["events"], [pse])
check("the same event is not doubled", len(W.keep_posted_events({"events": [msci]}, {"events": [msci]})["events"]), 1)
print("FAILS:", fails); sys.exit(1 if fails else 0)
