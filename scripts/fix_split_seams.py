"""
fix_split_seams.py -- put every price-history bar of a split symbol on ONE scale.

Cause (found 2026-11-11): marketstack's stored `close` is NOT split-adjusted, and the Yahoo updater rewrites the last 5 days
with adjusted prices, so a symbol's history can hold two scales with a seam. Fix: for every seam, divide the OPEN/HIGH/LOW/CLOSE
of every bar BEFORE the seam by the split ratio (reverse splits multiply). Closes keep the vendor's own values otherwise
(no dividend adjustment), so nothing else in the history moves. Volume and adj_* fields are left alone.

Detection: price sources only (marketstack, yahoo_finance); a one-day close ratio within 6% of 1/k or k (k = 2..5) is a seam
candidate; it is accepted when the seam row carries marketstack's split_factor != 1 OR the symbol is in CONFIRMED (checked against
TradingView's split-adjusted bars). Everything else is only listed.

  DRY_RUN=True  (default)  prints the plan and writes nothing
  DRY_RUN=False            backs up the old items to ~/market-dashboard/data_manual/split_seams_backup_<date>.json, then writes
  ONLY=SOXX,ITA            limit to these symbols
Idempotent: after a fix no seam is found, so a second run changes nothing.
"""
import os, json, datetime as dt
from decimal import Decimal
import boto3

DRY_RUN = os.environ.get("DRY_RUN", "True").lower() != "false"
ONLY = {s for s in os.environ.get("ONLY", "").split(",") if s}
CONFIRMED = {"SOXX", "ITA"}          # Yahoo-written seams (no split_factor); confirmed against TradingView adjusted closes
REGION, TABLE, REG = "ap-southeast-1", "cmon-stage-backend-price-history", "cmon-stage-backend-metrics-source"
SPLIT_TOL = 0.06
ddb = boto3.client("dynamodb", region_name=REGION)


def split_of(ratio):
    for k in (2, 3, 4, 5):
        if abs(ratio * k - 1) < SPLIT_TOL: return Decimal(k)            # price fell to 1/k: divide the older bars by k
        if abs(ratio / k - 1) < SPLIT_TOL: return Decimal(1) / k        # price rose k times: reverse split, multiply older bars
    return None


def rows(sym):
    out, kw = [], dict(TableName=TABLE, KeyConditionExpression="#s=:s", ExpressionAttributeNames={"#s": "symbol"},
                       ExpressionAttributeValues={":s": {"S": sym}})
    while True:
        r = ddb.query(**kw); out += r["Items"]
        if "LastEvaluatedKey" not in r: break
        kw["ExclusiveStartKey"] = r["LastEvaluatedKey"]
    return sorted(out, key=lambda x: x["date"]["S"])


def universe():
    items, kw = [], dict(TableName=REG, ProjectionExpression="symbol,#s,frequency", ExpressionAttributeNames={"#s": "source"})
    while True:
        r = ddb.scan(**kw); items += r["Items"]
        if "LastEvaluatedKey" not in r: break
        kw["ExclusiveStartKey"] = r["LastEvaluatedKey"]
    return sorted({i["symbol"]["S"] for i in items if i.get("frequency", {}).get("S") == "1D"
                   and i["source"]["S"] in ("marketstack", "yahoo_finance")})


def plan(sym):
    rs = [r for r in rows(sym)]
    seams = []
    cl = [(r, float(r["close"]["N"])) for r in rs if "close" in r and "N" in r["close"]]
    for (a, ca), (b, cb) in zip(cl, cl[1:]):
        if ca > 0 and cb > 0:
            k = split_of(cb / ca)
            if k:
                sf = b.get("split_factor", {}).get("N")
                ok = (sf is not None and float(sf) != 1) or sym in CONFIRMED
                seams.append(dict(date=b["date"]["S"], k=k, ratio=round(cb / ca, 3), split_factor=sf, accepted=ok,
                                  before=ca, after=cb, prev=a["date"]["S"]))
    return rs, seams


def main():
    print(f"=== fix_split_seams.py -- {'DRY RUN' if DRY_RUN else 'LIVE WRITE'} ===")
    changes, backup = {}, {}
    for sym in universe():
        if ONLY and sym not in ONLY: continue
        rs, seams = plan(sym)
        if not seams: continue
        for s in seams:
            print(f"{sym:5} seam {s['prev']} -> {s['date']} x{s['ratio']} (split_factor {s['split_factor']}) "
                  f"{'ACCEPT divide older bars by ' + str(s['k']) if s['accepted'] else 'NOT ACCEPTED (no split evidence), left alone'}")
        acc = [s for s in seams if s["accepted"]]
        if not acc: continue
        new = []
        for r in rs:
            d, f = r["date"]["S"], Decimal(1)
            for s in acc:
                if d < s["date"]: f *= s["k"]
            if f != 1:
                n = json.loads(json.dumps(r))
                for fld in ("open", "high", "low", "close"):
                    if fld in n and "N" in n[fld]:
                        n[fld] = {"N": format((Decimal(n[fld]["N"]) / f).quantize(Decimal("0.0000001")).normalize(), "f")}
                new.append((r, n))
        changes[sym] = new
        for s in acc:
            a = next(n for o, n in new if o["date"]["S"] == s["prev"])
            print(f"      {sym} {s['prev']} close {s['before']:.4f} -> {float(a['close']['N']):.4f}; {s['date']} stays {s['after']:.4f}; "
                  f"{len(new)} bars change, first {new[0][0]['date']['S']}")
    print(f"\nsymbols to fix: {sorted(changes)}; bars: {sum(len(v) for v in changes.values())}")
    if DRY_RUN or not changes:
        print("nothing written" if DRY_RUN else "nothing to do"); return
    path = os.path.expanduser(f"~/market-dashboard/data_manual/split_seams_backup_{dt.date.today()}.json")
    json.dump({s: [o for o, _ in v] for s, v in changes.items()}, open(path, "w"))
    print("backup:", path)
    for sym, v in changes.items():
        for i in range(0, len(v), 25):
            ddb.batch_write_item(RequestItems={TABLE: [{"PutRequest": {"Item": n}} for _, n in v[i:i + 25]]})
        print("wrote", sym, len(v))


main()
