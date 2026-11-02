"""Lambda entry for the Philippines refresh (replaces the launchd job on the Mac once verified). One invocation does what ph_daily.sh does:
PSE EDGE (disclosures, results, buy-backs, directory) -> PSE circular watcher -> prices, bars, breadth -> run check. Same code as the Mac scripts;
only the storage helper (cache.py) and the request pause (PH_PAUSE) differ. Event: {} (Saturday in Manila = full refresh) or {"full": true|false}.
A step that fails is logged and the later steps still run; the invocation raises at the end so CloudWatch counts an error."""
import datetime as dt
import json
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
os.environ.setdefault("PH_PAUSE", "0.7")        # EDGE answered in about 0.2 s from AWS; 0.7 s between requests keeps the load gentle

import ph_data  # noqa: E402
import ph_edge as E  # noqa: E402
import ph_edge_build as EB  # noqa: E402
import ph_index_watch as IW  # noqa: E402
import ph_prices_build as PB  # noqa: E402
import ph_run_check as RC  # noqa: E402

MANILA = dt.timezone(dt.timedelta(hours=8))


def step_edge(now):
    prev = ph_data.read_blob("edge")
    snap = EB.build(prev, 4, 600, False, 14)
    lr = snap.get("last_run", {})
    tried = lr.get("filings_tried", 0)
    if not snap["disclosures"]:
        raise RuntimeError("refusing to store: no disclosures at all")
    if tried >= 5 and lr.get("n_failed", 0) > 0.3 * tried:
        raise RuntimeError(f"refusing to store: {lr['n_failed']} of {tried} filings failed to parse")
    ph_data.write_blob("edge", snap)
    return {"disclosures": len(snap["disclosures"]), "results": len(snap["results"]), "buybacks": len(snap["buybacks"]), "failed": lr.get("n_failed")}


def step_index(now):
    state = IW.scan(ph_data.read_blob("index") or {}, now.date())
    ph_data.write_blob("index", state)
    return {"last_scanned": state.get("last_scanned"), "events": len(state.get("events", [])), "psei_members": len(state.get("psei_members", []))}


def step_prices(now, full):
    edge = ph_data.read_blob("edge")
    if not edge or not edge.get("directory"):
        raise RuntimeError("no EDGE company directory yet")
    directory = edge["directory"]
    if not all(d.get("security_id") for d in directory.values()):
        for d in E.fetch_directory():
            if d["symbol"] in directory:
                directory[d["symbol"]]["security_id"] = d["security_id"]
    prev = ph_data.read_blob("screener")
    members = (ph_data.read_blob("index") or {}).get("psei_members")
    snap, bars, breadth = PB.build(prev, directory, full, None, members)
    lr = snap["last_run"]
    if lr["kept"] < 40:
        raise RuntimeError(f"refusing to store: only {lr['kept']} names kept (expect 100+)")
    if lr["n_failed"] > 0.3 * max(lr["tried"], 1):
        raise RuntimeError(f"refusing to store: {lr['n_failed']} of {lr['tried']} reads failed")
    ph_data.write_blob("screener", snap)
    ph_data.write_blob("bars", bars)
    if breadth:
        ph_data.write_blob("breadth", breadth)
    return {"asof": snap["asof"], "kept": lr["kept"], "tried": lr["tried"], "failed": lr["n_failed"], "psei": snap["psei_index"].get("c"), "breadth": bool(breadth)}


def step_check(now):
    r = RC.assess(now, "daily", ph_data.read_blob("screener"), ph_data.read_blob("edge"), ph_data.read_blob("index"), ph_data.read_blob("bars"), ph_data.read_blob("breadth"))
    print("ph_run_check:", json.dumps(r))
    return {"ok": r["ok"], "messages": r["messages"]}


def lambda_handler(event, context):
    event = event or {}
    now = dt.datetime.now(MANILA)
    full = bool(event["full"]) if "full" in event else now.weekday() == 5
    out, failed = {"at": now.isoformat(timespec="seconds"), "full": full}, []
    for name, fn in (("edge", lambda: step_edge(now)), ("index", lambda: step_index(now)), ("prices", lambda: step_prices(now, full)), ("check", lambda: step_check(now))):
        try:
            out[name] = fn()
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            out[name] = {"error": f"{type(e).__name__}: {str(e)[:200]}"}
            failed.append(name)
        print(name, json.dumps(out[name], default=str))
    if failed:
        raise RuntimeError(f"steps failed: {', '.join(failed)} | {json.dumps(out, default=str)[:900]}")
    return out
