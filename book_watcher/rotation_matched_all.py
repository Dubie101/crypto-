#!/usr/bin/env python3
"""Matched-size rotation hunt across ALL 402 assets (Mike's sold-to-buy map).

Reads trades_all/trades_YYYY-MM-DD.jsonl (per-second sparse: [idx,n,buy,sell]),
builds 1-minute net-flow bars per product, trims the first TRIM_S seconds of
each daily file (subscribe backlog replay), then hunts Mike's pattern:

  $X net-sold in A within a minute, ~$X net-bought in B within -1..+2 min
  -> 'money moved A -> B'

Counts per ordered pair vs block-shuffled null. Reports pairs whose match
count beats the null max AND hit rate >= 5% of A's sell events.

Usage: rotation_matched_all.py [--days N] [--size 400] [--tol 0.25]
"""
import json, glob, os, sys, datetime
import numpy as np

BASE = os.path.dirname(os.path.abspath(__file__))
TDIR = os.path.join(BASE, 'trades_all')
PRODS = json.load(open(os.path.join(BASE, 'all_products.json')))
N = len(PRODS)
TRIM_S = 600          # skip subscribe-backlog burst at each file start
BAR = 60
WIN = (-1, 0, 1, 2)
N_SHUFFLE = 20
BLOCK_MIN = 60        # 1h blocks
OUT = os.path.join(os.path.dirname(BASE), 'rotation_matched_all.json')

days = 7; SIZE = 400.0; TOL = 0.25
for i, a in enumerate(sys.argv[1:]):
    if a == '--days': days = int(sys.argv[i + 2])
    if a == '--size': SIZE = float(sys.argv[i + 2])
    if a == '--tol': TOL = float(sys.argv[i + 2])
rng = np.random.default_rng(7)

# per-product dict minute -> net flow
flows = {}
tmin = None
for f in sorted(glob.glob(os.path.join(TDIR, 'trades_*.jsonl')))[-days:]:
    fstart = None
    for line in open(f):
        d = json.loads(line)
        t = d['t']
        if fstart is None:
            fstart = t
        if t - fstart < TRIM_S:
            continue
        m = t // BAR
        tmin = m if tmin is None else min(tmin, m)
        for i, c, b, s in d['d']:
            flows.setdefault(i, {}).setdefault(m, 0.0)
            flows[i][m] += b - s
if tmin is None:
    print('no data'); sys.exit(1)
tmax = max(m for d in flows.values() for m in d)
T = tmax - tmin + 1
F = np.zeros((T, N))
for i, d in flows.items():
    for m, v in d.items():
        F[m - tmin, i] = v
print(f'{N} products, {T} 1-min bars ({T/60:.1f} h), trimmed {TRIM_S}s/file', flush=True)

def hunt(X):
    C = np.zeros((N, N), dtype=np.int32)
    sells = np.zeros(N, dtype=np.int32)
    for i in range(N):
        ev = np.flatnonzero(X[:, i] <= -SIZE)
        sells[i] = len(ev)
        if len(ev) == 0:
            continue
        for e in ev:
            S = -X[e, i]
            lo, hi = S * (1 - TOL), S * (1 + TOL)
            for j in range(N):
                if j == i:
                    continue
                col = X[:, j]
                for w in WIN:
                    ee = e + w
                    if 0 <= ee < T and lo <= col[ee] <= hi:
                        C[i, j] += 1
                        break
    return C, sells

C, sells = hunt(F)
nz = int((sells > 0).sum())
print(f'products with >=1 sell event (>=${SIZE:.0f}/min): {nz}', flush=True)

null_max = np.zeros((N, N), dtype=np.int32)
null_tot = np.zeros((N, N))
nb = T // BLOCK_MIN
for s in range(N_SHUFFLE):
    Xs = np.empty_like(F)
    perm = rng.permutation(nb)
    for j in range(N):
        col = F[:, j]
        for b2, ob in enumerate(perm):
            Xs[b2*BLOCK_MIN:(b2+1)*BLOCK_MIN, j] = col[ob*BLOCK_MIN:(ob+1)*BLOCK_MIN]
        Xs[nb*BLOCK_MIN:, j] = col[nb*BLOCK_MIN:]
    Cn, _ = hunt(Xs)
    np.maximum(null_max, Cn, out=null_max)
    null_tot += Cn
null_mean = null_tot / N_SHUFFLE

res = []
for i in range(N):
    if sells[i] < 5:
        continue
    for j in range(N):
        if i == j:
            continue
        obs = int(C[i, j])
        if obs >= 3 and obs > int(null_max[i, j]) and obs / sells[i] >= 0.05:
            res.append({'from': PRODS[i], 'to': PRODS[j], 'matches': obs,
                        'null_mean': round(float(null_mean[i, j]), 1),
                        'null_max': int(null_max[i, j]),
                        'sell_events': int(sells[i]),
                        'hit_rate': round(obs / sells[i], 3)})
res.sort(key=lambda x: (-x['matches'], -x['hit_rate']))

json.dump({'ran_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
           'days': days, 'size_usd': SIZE, 'tol': TOL, 'bars': T,
           'pairs': res}, open(OUT, 'w'), indent=1)
print(f'wrote {OUT}\nSIGNIFICANT PAIRS: {len(res)}')
for r in res[:20]:
    print(f"  {r['from']} -> {r['to']}: {r['matches']} matches "
          f"(null max {r['null_max']}) hit_rate {r['hit_rate']}")
if not res:
    top = []
    for i in range(N):
        for j in range(N):
            if i != j and C[i, j] > 0:
                top.append((int(C[i, j]), int(null_max[i, j]), PRODS[i], PRODS[j]))
    top.sort(reverse=True)
    print('top raw (none significant):')
    for c_, nm, a, b in top[:8]:
        print(f'  {a} -> {b}: {c_} (null max {nm})')
