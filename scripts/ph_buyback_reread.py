"""One-off: re-read stored buy-back (9-1) reports for the named companies to pick up the program size the parser used to miss
(a range such as "PHP5 to 10 Billion", or a program stated in shares in the note). Merges only the new keys back, by filing id.

    api/.venv/bin/python ~/market-dashboard/scripts/ph_buyback_reread.py ICT SMPH --out /tmp/edge_bb.json         # local file only
    api/.venv/bin/python ~/market-dashboard/scripts/ph_buyback_reread.py --from /tmp/edge_bb.json --upload         # merge onto the current stored snapshot and store
"""
import argparse, json, os, sys
sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.expanduser("~/cgi-vercel/api"))
import ph_edge as E  # noqa: E402
import ph_data  # noqa: E402

KEYS = ("appropriated", "appropriated_low", "appropriated_high", "program_shares")
ap = argparse.ArgumentParser()
ap.add_argument("symbols", nargs="*"); ap.add_argument("--out"); ap.add_argument("--upload", action="store_true"); ap.add_argument("--from", dest="src")
a = ap.parse_args()
if a.src:
    new = {r["edge_no"]: r for r in json.load(open(a.src))["buybacks"]}
else:
    snap = ph_data.read_blob("edge"); new = {}
    todo = [b for b in snap["buybacks"] if b["symbol"] in set(a.symbols)]
    print(f"{len(todo)} reports to re-read", flush=True)
    for i, b in enumerate(todo):
        try:
            p = E.parse_buyback(E.fetch_filing(b["edge_no"]))
            if p:
                for k in KEYS:
                    if k in p: b[k] = p[k]
                    elif k in b and k.startswith("appropriated_"): b.pop(k)
                new[b["edge_no"]] = b
        except Exception as e:  # noqa: BLE001
            print("  failed", b["edge_no"][:8], b["symbol"], str(e)[:80], flush=True)
        if i % 10 == 9: print(f"  {i+1}/{len(todo)}", flush=True)
    if a.out:
        json.dump({"buybacks": list(new.values())}, open(a.out, "w")); print("wrote", a.out, len(new))
if a.upload:
    cur = ph_data.read_blob("edge"); n = 0
    for b in cur["buybacks"]:
        r = new.get(b["edge_no"])
        if r:
            for k in KEYS:
                if k in r: b[k] = r[k]
            n += 1
    print("merged into", n, "stored reports"); ph_data.write_blob("edge", cur); print("stored ph/edge.json")
