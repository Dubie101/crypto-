import json, math, urllib.request, datetime

BASE = "https://api.exchange.coinbase.com/products/{}/candles"
MAJORS = ["BTC-USD","ETH-USD","SOL-USD","XRP-USD","BNB-USD","DOGE-USD","ZEC-USD","SUI-USD"]
CTRLS  = ["ADA-USD","VET-USD","LINK-USD","AVAX-USD","DOT-USD","LTC-USD","ATOM-USD","UNI-USD"]

def candles(pid, gran=300, n=288):
    end = datetime.datetime.now(datetime.timezone.utc)
    start = end - datetime.timedelta(seconds=gran*n)
    url = BASE.format(pid) + f"?granularity={gran}&start={start.isoformat()}&end={end.isoformat()}"
    req = urllib.request.Request(url, headers={"User-Agent":"nova-align/1"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            rows = json.loads(r.read())
    except Exception as e:
        return None, str(e)
    rows = sorted(rows, key=lambda c: c[0])
    closes = [c[4] for c in rows]
    return closes, None

def norm(xs):
    lo, hi = min(xs), max(xs)
    if hi == lo: return [0.5]*len(xs)
    return [(x-lo)/(hi-lo) for x in xs]

def corr(a, b):
    n = len(a); ma = sum(a)/n; mb = sum(b)/n
    sa = math.sqrt(sum((x-ma)**2 for x in a)); sb = math.sqrt(sum((x-mb)**2 for x in b))
    if sa == 0 or sb == 0: return 0.0
    return sum((x-ma)*(y-mb) for x, y in zip(a, b))/(sa*sb)

def mean_pairwise(vecs):
    ks = list(vecs)
    cs = [corr(vecs[i], vecs[j]) for x in range(len(ks)) for j, y in enumerate(ks) if j > ks.index(x) for i, x in [0, ks][0:0]] if False else None
    vals = []
    for i in range(len(ks)):
        for j in range(i+1, len(ks)):
            vals.append(corr(vecs[ks[i]], vecs[ks[j]]))
    return sum(vals)/len(vals) if vals else float('nan'), len(vals)

series = {}
missing = []
for pid in MAJORS + CTRLS:
    closes, err = candles(pid)
    if closes is None or len(closes) < 200:
        missing.append((pid, err or f"only {0 if closes is None else len(closes)} candles"))
        continue
    series[pid] = norm(closes)

maj = {k: v for k, v in series.items() if k in MAJORS}
ctl = {k: v for k, v in series.items() if k in CTRLS}
# length-align (drop to shortest)
L = min(len(v) for v in series.values())
series = {k: v[-L:] for k, v in series.items()}
maj = {k: series[k] for k in maj}; ctl = {k: series[k] for k in ctl}

mm, nm = mean_pairwise(maj)
cc, nc = mean_pairwise(ctl)
cross = [corr(maj[a], ctl[b]) for a in maj for b in ctl]
cx = sum(cross)/len(cross)

print(json.dumps({
    "window": "last ~24h, 5-min candles",
    "points": L,
    "majors_used": sorted(maj), "controls_used": sorted(ctl),
    "missing": missing,
    "mean_pairwise_corr_majors": round(mm, 4), "pairs": nm,
    "mean_pairwise_corr_controls": round(cc, 4), "control_pairs": nc,
    "mean_cross_corr": round(cx, 4),
}, indent=1))

# per-major alignment to the majors' composite (the "pattern vector")
comp = [sum(maj[k][i] for k in maj)/len(maj) for i in range(L)]
print("alignment of each major to composite pattern:")
for k in sorted(maj):
    print(f"  {k}: {corr(maj[k], comp):+.4f}")
print("controls to composite:")
for k in sorted(ctl):
    print(f"  {k}: {corr(ctl[k], comp):+.4f}")

json.dump({"series": series, "composite": comp},
          open("normalized_series.json", "w"))
