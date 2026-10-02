#!/usr/bin/env python3
"""Matched-size rotation hunt (Mike's formulation):
$X net-sold in coin A within one minute, ~$X net-bought in coin B within
minutes t-1..t+2 -> candidate 'money moved A -> B' event.

Counts per ordered pair (A->B), vs block-shuffled null (same thresholds,
timing destroyed). Reports pairs whose match count beats the null.
"""
import json, glob, datetime
import numpy as np

SIZE = 400.0        # Mike's number: $400
TOL = 0.25          # buy size within +/-25% of sell size
BAR = 60            # 1-minute bars
WIN = (-1, 0, 1, 2) # buy can print 1 min before to 2 min after the sell minute
N_SHUFFLE = 20
BLOCK = 60          # 1h blocks
OUT = '/home/hatch/workspace/goals/paper-trading-simulator/hidden_files/rotation_matched_2026-09-30.json'
rng = np.random.default_rng(21)

raw = {}
for f in sorted(glob.glob('/home/hatch/workspace/goals/paper-trading-simulator/hidden_files/book_watcher/ticks/ticks_*.jsonl')):
    for line in open(f):
        d = json.loads(line)
        t = int(datetime.datetime.fromisoformat(d['t'].replace('Z', '+00:00')).timestamp())
        for coin, v in d['d'].items():
            if v and len(v) >= 10:
                raw.setdefault(coin, {})[t] = (v[8], v[9])
coins = sorted(raw)
t0 = min(t for c in raw.values() for t in c) // BAR * BAR
T = (max(t for c in raw.values() for t in c) - t0) // BAR + 1
N = len(coins)
F = np.zeros((T, N))
for j, c in enumerate(coins):
    for t, (b, s) in raw[c].items():
        F[(t - t0) // BAR, j] += b - s
print(f'{N} coins, {T} 1-min bars', flush=True)

def hunt(X):
    """returns (N,N) match counts: sells in i matched by buys in j."""
    C = np.zeros((N, N), dtype=int)
    tot_sells = np.zeros(N, dtype=int)
    for i in range(N):
        ev = np.flatnonzero(X[:, i] <= -SIZE)
        tot_sells[i] = len(ev)
        for e in ev:
            S = -X[e, i]
            lo, hi = S * (1 - TOL), S * (1 + TOL)
            for j in range(N):
                if j == i: continue
                for w in WIN:
                    ee = e + w
                    if 0 <= ee < T and lo <= X[ee, j] <= hi:
                        C[i, j] += 1
                        break
    return C, tot_sells

C, sells = hunt(F)
print('sell events per coin:', dict(zip(coins, sells.tolist())), flush=True)

null_max = np.zeros((N, N), dtype=int)
null_tot = np.zeros((N, N))
for s in range(N_SHUFFLE):
    Xs = F.copy(); nb = T // BLOCK
    perm = rng.permutation(nb)
    for j in range(N):
        col = Xs[:, j].copy(); out = np.empty(T)
        for nb2, ob in enumerate(perm):
            out[nb2*BLOCK:(nb2+1)*BLOCK] = col[ob*BLOCK:(ob+1)*BLOCK]
        out[nb*BLOCK:] = col[nb*BLOCK:]
        Xs[:, j] = out
    Cn, _ = hunt(Xs)
    null_max = np.maximum(null_max, Cn)
    null_tot += Cn
null_mean = null_tot / N_SHUFFLE

res = []
for i in range(N):
    for j in range(N):
        if i == j or sells[i] == 0: continue
        obs, nm, mx = int(C[i, j]), float(null_mean[i, j]), int(null_max[i, j])
        if obs > mx and obs >= 3:
            res.append({'from': coins[i], 'to': coins[j],
                        'matches': obs, 'null_mean': round(nm, 1), 'null_max': mx,
                        'sell_events': int(sells[i]),
                        'hit_rate': round(obs / sells[i], 3)})
res.sort(key=lambda x: -x['matches'])

json.dump({'ran_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
           'size_usd': SIZE, 'tol': TOL, 'pairs': res,
           'sell_events': {c: int(s) for c, s in zip(coins, sells)}}, open(OUT, 'w'), indent=1)
print(f'wrote {OUT}')
print(f'SIGNIFICANT PAIRS: {len(res)}')
for r in res:
    print(f"  {r['from']} -> {r['to']}: {r['matches']} matches (null mean {r['null_mean']}, max {r['null_max']}) from {r['sell_events']} sells, hit rate {r['hit_rate']}")
# also show top raw counts regardless of significance
print('top raw match counts:')
flat = sorted([(C[i, j], coins[i], coins[j]) for i in range(N) for j in range(N) if i != j], reverse=True)[:8]
for c_, a, b in flat:
    print(f'  {a} -> {b}: {c_} (null max {null_max[coins.index(a), coins.index(b)]})')
