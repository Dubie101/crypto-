#!/usr/bin/env python3
"""Real-return validation of the peak-dislocation edge.

Refetches 1-min candles, recomputes deviations EXACTLY like peak_watcher
(trailing-120min min-max norm, composite, bps of own price), finds >=200bps
events, and scores each LONG-side event (dev <= -200) on the REAL
direction-adjusted buy->sell return over 30 min -- not the deviation proxy.
"""
import json, urllib.request, time, datetime

MAJORS = ["BTC-USD","ETH-USD","SOL-USD","XRP-USD","BNB-USD","DOGE-USD","ZEC-USD","SUI-USD"]
CANDLE = "https://api.exchange.coinbase.com/products/%s/candles?granularity=60&start=%s&end=%s"
DAYS = 10
THRESH = 200.0
HOLD_MIN = 30
SKIP_MIN = 60

def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "nova-peak/1"})
    with urllib.request.urlopen(req, timeout=25) as r:
        return json.loads(r.read())

def fetch(pid, days):
    out = {}
    end = datetime.datetime.now(datetime.timezone.utc).replace(second=0, microsecond=0)
    start = end - datetime.timedelta(days=days)
    cur_end = end
    while cur_end > start:
        cur_start = max(start, cur_end - datetime.timedelta(minutes=300))
        try:
            rows = get(CANDLE % (pid, cur_start.isoformat(), cur_end.isoformat()))
            for c in rows:
                out[int(c[0]) // 60] = c[4]
        except Exception as e:
            print(pid, "chunk err", e, flush=True)
        cur_end = cur_start
        time.sleep(0.45)
    return out

print("fetching...", flush=True)
series = {m: fetch(m, DAYS) for m in MAJORS}
keys = sorted(set.intersection(*[set(s) for s in series.values()]))
print("aligned minutes:", len(keys), flush=True)
P = {m: [series[m][k] for k in keys] for m in MAJORS}
T = len(keys)

def dev_at(i):
    norms, rng = {}, {}
    for m in MAJORS:
        w = P[m][max(0, i-119):i+1]
        lo, hi, last = min(w), max(w), w[-1]
        norms[m] = (last-lo)/(hi-lo) if hi > lo else 0.5
        rng[m] = (hi-lo)/last*1e4 if last else 0.0
    comp = sum(norms.values())/len(norms)
    return {m: (norms[m]-comp)*rng[m] for m in MAJORS}

events = []
i = 120
while i < T - HOLD_MIN:
    d = dev_at(i)
    for m in MAJORS:
        if abs(d[m]) >= THRESH:
            entry, exitp = P[m][i], P[m][i+HOLD_MIN]
            real = (exitp-entry)/entry*1e4  # signed; long-side wants positive
            proxy_cap = abs(d[m]) - abs(dev_at(i+HOLD_MIN)[m])
            events.append({"m": m, "t": keys[i], "dev": d[m],
                           "real_bps": real, "proxy_bps": proxy_cap})
            i += SKIP_MIN
            break
    else:
        i += 1
        continue

longs = [e for e in events if e["dev"] <= -THRESH]
print("\n>=200bps events:", len(events), " long-side:", len(longs))
for e in longs:
    ts = datetime.datetime.fromtimestamp(e["t"]*60, datetime.timezone.utc).strftime("%m-%d %H:%M")
    print(f'{ts} {e["m"]:8s} dev={e["dev"]:+7.1f} real={e["real_bps"]:+8.1f} proxy={e["proxy_bps"]:+8.1f}')
if longs:
    rs = sorted(e["real_bps"] for e in longs)
    med = rs[len(rs)//2]
    import statistics
    print(f"\nreal-return median {med:+.1f}bps mean {statistics.mean(rs):+.1f}bps")
    for cost, name in ((100,"2-leg taker"), (200,"4-leg taker"), (100,"4-leg maker")):
        n = sum(1 for r in rs if r > cost)
        print(f"  > {name} ({cost}bps): {n}/{len(rs)}")
json.dump(events, open("real_events_10d.json","w"), indent=1)
print("saved real_events_10d.json")
