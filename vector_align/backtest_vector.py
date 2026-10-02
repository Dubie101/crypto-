#!/usr/bin/env python3
"""Vector-averaged variants of the peak-dislocation backtest.

Mike's critique: scoring one entry point / one exit point understates the
pattern. Test multi-point averaged executions on real prices:

  V0 baseline : 100% in at cross minute, 100% out at +30min
  V1 time-avg : scale IN in thirds at cross/+10/+20min while dev<=-150,
                scale OUT in thirds at +30/+40/+50min
  V2 basket   : at cross minute, equal-weight ALL majors with dev<=-200,
                exit all at +30min
"""
import json, urllib.request, time, datetime, statistics

MAJORS = ["BTC-USD","ETH-USD","SOL-USD","XRP-USD","BNB-USD","DOGE-USD","ZEC-USD","SUI-USD"]
CANDLE = "https://api.exchange.coinbase.com/products/%s/candles?granularity=60&start=%s&end=%s"
DAYS, THRESH, SKIP = 10, 200.0, 60

def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "nova-peak/1"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read())

def fetch(pid, days):
    out = {}
    end = datetime.datetime.now(datetime.timezone.utc).replace(second=0, microsecond=0)
    start = end - datetime.timedelta(days=days)
    cur = end
    while cur > start:
        s = max(start, cur - datetime.timedelta(minutes=300))
        try:
            for c in get(CANDLE % (pid, s.isoformat(), cur.isoformat())):
                out[int(c[0]) // 60] = c[4]
        except Exception as e:
            print(pid, "chunk err", e, flush=True)
        cur = s
        time.sleep(0.45)
    return out

print("fetching...", flush=True)
series = {m: fetch(m, DAYS) for m in MAJORS}
keys = sorted(set.intersection(*[set(s) for s in series.values()]))
P = {m: [series[m][k] for k in keys] for m in MAJORS}
T = len(keys)
print("aligned minutes:", T, flush=True)

def devs_at(i):
    n, r = {}, {}
    for m in MAJORS:
        w = P[m][max(0, i-119):i+1]
        lo, hi, last = min(w), max(w), w[-1]
        n[m] = (last-lo)/(hi-lo) if hi > lo else 0.5
        r[m] = (hi-lo)/last*1e4 if last else 0.0
    c = sum(n.values())/len(n)
    return {m: (n[m]-c)*r[m] for m in MAJORS}

def v0(m, i):
    return (P[m][i+30]-P[m][i])/P[m][i]*1e4

def v1(m, i):
    d0 = devs_at(i)
    entries = [(i, P[m][i])]
    for j in (i+10, i+20):
        if j+50 < T and devs_at(j)[m] <= -150:
            entries.append((j, P[m][j]))
    if not entries:
        return None
    exits = [P[m][i+30], P[m][i+40], P[m][i+50]]
    ae = sum(p for _, p in entries)/len(entries)
    ax = sum(exits)/len(exits)
    return (ax-ae)/ae*1e4

def v2(i):
    d = devs_at(i)
    ms = [m for m in MAJORS if d[m] <= -THRESH]
    if not ms or i+30 >= T:
        return None
    return sum((P[m][i+30]-P[m][i])/P[m][i] for m in ms)/len(ms)*1e4

res = {"V0": [], "V1": [], "V2": []}
i = 120
while i < T - 80:
    d = devs_at(i)
    sig = [m for m in MAJORS if d[m] <= -THRESH]
    if sig:
        m = min(sig, key=lambda x: d[x])
        r0 = v0(m, i); r1 = v1(m, i); r2 = v2(i)
        res["V0"].append(r0)
        if r1 is not None: res["V1"].append(r1)
        if r2 is not None: res["V2"].append(r2)
        i += SKIP
    else:
        i += 1

print("\n=== results (bps, real prices) ===")
for v, rs in res.items():
    if not rs:
        print(v, "no trades"); continue
    rs2 = sorted(rs); med = rs2[len(rs2)//2]
    beats = {c: sum(1 for r in rs if r > c) for c in (50, 100, 200)}
    print(f"{v}: n={len(rs)} median={med:+.1f} mean={statistics.mean(rs):+.1f} " +
          f"min={min(rs):+.1f} max={max(rs):+.1f} >50:{beats[50]} >100:{beats[100]} >200:{beats[200]}")
json.dump(res, open("vector_variants.json", "w"))
print("saved vector_variants.json")
