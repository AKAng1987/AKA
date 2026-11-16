"""One-off: read the insider numbers (net shares, value, side) for stored insider-chip filings that never got them.

ph_edge_build.py reads details only for filings under 14 days old, so older 13-1 / 23-B filings in the stored snapshot have no numbers.
This reads each one once from EDGE (2 s pacing, personal use, keeps only the derived figures) and merges them back by filing id.

    api/.venv/bin/python ~/market-dashboard/scripts/ph_insider_backfill.py --out /tmp/edge_insider.json      # local file only (default)
    api/.venv/bin/python ~/market-dashboard/scripts/ph_insider_backfill.py --from /tmp/edge_insider.json --upload   # store a file you checked
"""
import argparse, json, os, sys, time
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.expanduser("~/cgi-vercel/api"))
import ph_edge as E  # noqa: E402
import ph_data  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--out"); ap.add_argument("--upload", action="store_true"); ap.add_argument("--from", dest="src")
ap.add_argument("--limit", type=int, default=400)
a = ap.parse_args()
snap = json.load(open(a.src)) if a.src else ph_data.read_blob("edge")
if not a.src:
    need = [d for d in snap["disclosures"] if d.get("chip") == "insider" and not d.get("insider")]
    print(f"{len(need)} insider-chip filings without numbers", flush=True)
    ok = fail = 0
    for i, d in enumerate(need[:a.limit]):
        try:
            d.update(E.parse_detail(E.fetch_filing(d["edge_no"]), "insider")); ok += 1
        except Exception as e:  # noqa: BLE001
            fail += 1; print("  failed", d["edge_no"][:8], d["symbol"], str(e)[:80], flush=True)
        if i % 20 == 19: print(f"  {i+1}/{min(len(need), a.limit)} (ok {ok}, failed {fail})", flush=True)
    print(f"read {ok}, failed {fail}", flush=True)
n = sum(1 for d in snap["disclosures"] if d.get("insider"))
print("insider rows with numbers now:", n)
if a.out:
    json.dump(snap, open(a.out, "w"), separators=(",", ":")); print("wrote", a.out)
if a.upload:
    if not a.src: sys.exit("refusing to upload without --from <checked file>")
    ph_data.write_blob("edge", snap); print("stored ph/edge.json")
