#!/usr/bin/env python3
"""Scan 1 rerun with corrected nulls (spec from Mike, 2026-10-01).

OBSERVED TEST - FROZEN, identical to rotation_matched_all.py:
  * 7 days of trades_*.jsonl per-second prints -> 1-min net-flow bars (buy - sell USD)
  * 402 products x T bars; first 600s of each daily file trimmed
  * Event: minute where product A is net sold >= $400
  * Match: ordered pair A->B; B has a minute bar within -1..+2 min whose net buy
    is within +-25% of A's sell size. Max one match per sell event.

NULLS - independent per product (the original used one shared permutation,
which preserves within-block A->B timing and had no power):
  1. blockperm:   60-min blocks permuted independently per product (tail fixed)
  2. circshift:   whole series circular-shifted by independent uniform offset
  3. timeshuffle: timestamps permuted independently per product

R = 1000 realizations per null method.

Per A->B pair: observed_matches, null_mean/std/max, empirical_p, effect_size,
A_event_count, match_rate, median_lag, lag_distribution, size_ratio_distribution.
BH-FDR (q=0.05) per null method across all tested ordered pairs.

Usage: rotation_scan1_rerun.py [--smoke]   # smoke: R=5 + equivalence asserts
"""
import json, glob, os, sys, time, datetime
import numpy as np

BASE = os.path.dirname(os.path.abspath(__file__))
TDIR = os.path.join(BASE, 'trades_all')
PRODS = json.load(open(os.path.join(BASE, 'all_products.json')))
N = len(PRODS)

# ---- frozen observed-data parameters ----
DAYS = 7
SIZE = 400.0
TOL = 0.25
TRIM_S = 600
BAR = 60
WIN = (-1, 0, 1, 2)
BLOCK_MIN = 60
SEED = 20261001
FDR_Q = 0.05
OUT = os.path.join(os.path.dirname(BASE), 'scan1_rerun.json')
LOG = os.path.join(os.path.dirname(BASE), 'scan1_rerun.log')

SMOKE = '--smoke' in sys.argv
R = 5 if SMOKE else 1000
METHODS = ['blockperm', 'circshift', 'timeshuffle']

logf = open(LOG, 'a')
def log(*a):
    line = ' '.join(str(x) for x in a)
    print(line, flush=True)
    logf.write(line + '\n'); logf.flush()

# ---- observed 1-min net-flow matrix (identical construction to original) ----
flows = {}
tmin = None
for f in sorted(glob.glob(os.path.join(TDIR, 'trades_*.jsonl')))[-DAYS:]:
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
tmax = max(m for d in flows.values() for m in d)
T = tmax - tmin + 1
F = np.zeros((T, N))
for i, d in flows.items():
    for m, v in d.items():
        F[m - tmin, i] = v
log(f'observed matrix: {N} products x {T} bars ({T/60:.1f} h)')

def hunt(X, details=False):
    """Vectorized hunt. Returns (C, evc, det). det: {(i,j): ([lags],[ratios])}."""
    Tn, Nn = X.shape
    Xp = np.empty((Tn + 3, Nn))
    Xp[:] = np.nan
    Xp[1:Tn + 1, :] = X
    C = np.zeros((Nn, Nn), dtype=np.int64)
    evc = np.zeros(Nn, dtype=np.int64)
    det = {} if details else None
    ar = np.arange(Nn)
    for i in range(Nn):
        col = X[:, i]
        ev = np.flatnonzero(col <= -SIZE)
        evc[i] = len(ev)
        if len(ev) == 0:
            continue
        S = -col[ev]
        lo = S * (1 - TOL)
        hi = S * (1 + TOL)
        for k in range(len(ev)):
            e = int(ev[k])
            W = Xp[e:e + 4, :]          # original minutes e-1..e+2 (NaN-padded)
            m = (W >= lo[k]) & (W <= hi[k])
            hit = m.any(axis=0)
            if not hit.any():
                continue
            first = np.argmax(m, axis=0)  # first matching lag position 0..3
            js = ar[hit]
            js = js[js != i]
            if len(js) == 0:
                continue
            C[i, js] += 1
            if details:
                fl = first[js]
                for jj, ff in zip(js.tolist(), fl.tolist()):
                    key = (i, int(jj))
                    if key not in det:
                        det[key] = ([], [])
                    det[key][0].append(int(ff) - 1)          # lag in -1..2
                    det[key][1].append(float(W[ff, jj] / S[k]))  # buy_B / sell_A
    return C, evc, det

def hunt_original_loop(X):
    """Verbatim logic of rotation_matched_all.py hunt() for equivalence check."""
    Tn, Nn = X.shape
    C = np.zeros((Nn, Nn), dtype=np.int64)
    for i in range(Nn):
        ev = np.flatnonzero(X[:, i] <= -SIZE)
        for e in ev:
            S = -X[e, i]
            lo, hi = S * (1 - TOL), S * (1 + TOL)
            for j in range(Nn):
                if j == i:
                    continue
                col = X[:, j]
                for w in WIN:
                    ee = e + w
                    if 0 <= ee < Tn and lo <= col[ee] <= hi:
                        C[i, j] += 1
                        break
    return C

if SMOKE:
    # equivalence: vectorized hunt == original loop on synthetic data
    srng = np.random.default_rng(0)
    Xs = srng.normal(0, 500, size=(60, 8))
    Xs[srng.random((60, 8)) < 0.05] -= 900  # sprinkle sell events
    C1, _, _ = hunt(Xs)
    C2 = hunt_original_loop(Xs)
    assert np.array_equal(C1, C2), 'vectorized hunt != original loop'
    log('smoke: vectorized hunt matches original loop exactly')
    # spot-check 6 random pairs on real data with the slow loop on 2 products
    for (a, b) in [(3, 40), (40, 3), (100, 200), (0, 1), (300, 301), (150, 7)]:
        sub = F[:, [a, b]]
        Cs, _, _ = hunt(sub)
        Co = hunt_original_loop(sub)
        assert Cs[0, 1] == Co[0, 1] and Cs[1, 0] == Co[1, 0], f'mismatch pair {(a,b)}'
    log('smoke: 6 real-data pair spot-checks match')

def null_idx(method, rng):
    """(T, N) index array: Xs[r, j] = F[idx[r, j], j]."""
    if method == 'timeshuffle':
        return np.argsort(rng.random((N, T)), axis=1).T
    if method == 'circshift':
        s = rng.integers(0, T, size=N)
        return ((np.arange(T)[:, None] - s[None, :]) % T)
    if method == 'blockperm':
        nb = T // BLOCK_MIN
        tail = T - nb * BLOCK_MIN
        order = np.argsort(rng.random((N, nb)), axis=1)          # (N, nb)
        blocks = (order * BLOCK_MIN)[:, :, None] + np.arange(BLOCK_MIN)[None, None, :]
        idx = blocks.reshape(N, nb * BLOCK_MIN).T                # (nb*60, N)
        if tail:
            t = np.arange(nb * BLOCK_MIN, T)[:, None].repeat(N, axis=1)
            idx = np.vstack([idx, t])
        return idx
    raise ValueError(method)

t00 = time.time()
Cobs, evc_obs, det = hunt(F, details=True)
log(f'observed hunt done in {time.time()-t00:.1f}s; '
    f'sell events total={int(evc_obs.sum())}, '
    f'products with >=1 event={int((evc_obs>0).sum())}')

tested = [(i, j) for i in range(N) for j in range(N)
          if i != j and evc_obs[i] > 0]
M = len(tested)
log(f'tested ordered pairs: {M}')

acc = {m: {'n_ge': np.zeros((N, N), dtype=np.int64),
           'sum': np.zeros((N, N)),
           'sumsq': np.zeros((N, N)),
           'mx': np.zeros((N, N), dtype=np.int64)} for m in METHODS}
colidx = np.arange(N)[None, :]
for mi, method in enumerate(METHODS):
    mrng = np.random.default_rng(SEED + 1000 + mi)
    t0 = time.time()
    for r in range(R):
        Xs = F[null_idx(method, mrng), colidx]
        Cn, _, _ = hunt(Xs)
        A = acc[method]
        A['n_ge'] += (Cn >= Cobs)
        A['sum'] += Cn
        A['sumsq'] += Cn * Cn
        np.maximum(A['mx'], Cn, out=A['mx'])
        if (r + 1) % 100 == 0 or (SMOKE and r + 1 == R):
            log(f'{method}: {r+1}/{R} ({time.time()-t0:.0f}s elapsed)')
    log(f'{method}: {R} realizations done in {time.time()-t0:.1f}s')

stats = {}
for method in METHODS:
    A = acc[method]
    mean = A['sum'] / R
    var = A['sumsq'] / R - mean ** 2
    std = np.sqrt(np.maximum(var, 0.0))
    p = (1.0 + A['n_ge']) / (1.0 + R)
    eff = np.where(std > 0, (Cobs - mean) / np.where(std > 0, std, 1.0),
                   Cobs - mean)
    stats[method] = {'mean': mean, 'std': std, 'p': p, 'eff': eff,
                      'mx': A['mx']}

def bh_fdr(pvals, q):
    order = np.argsort(pvals)
    sp = pvals[order]
    m = len(pvals)
    thresh = np.arange(1, m + 1) / m * q
    ok = np.flatnonzero(sp <= thresh)
    if len(ok) == 0:
        return set()
    kmax = ok.max()
    return set(order[:kmax + 1].tolist())

pair_index = {p: k for k, p in enumerate(tested)}
surv = {}
for method in METHODS:
    pv = np.array([stats[method]['p'][i, j] for (i, j) in tested])
    s = bh_fdr(pv, FDR_Q)
    surv[method] = s
    log(f'{method}: FDR q={FDR_Q} survivors: {len(s)} / {M}')
inter = set.intersection(*surv.values()) if surv else set()
log(f'intersection survivors: {len(inter)}')

def qtiles(a):
    a = np.asarray(a, dtype=float)
    qs = np.percentile(a, [0, 10, 25, 50, 75, 90, 100])
    return {'min': float(qs[0]), 'q10': float(qs[1]), 'q25': float(qs[2]),
            'median': float(qs[3]), 'q75': float(qs[4]), 'q90': float(qs[5]),
            'max': float(qs[6]), 'mean': float(a.mean()), 'n': int(len(a))}

records = []
for k, (i, j) in enumerate(tested):
    obs = int(Cobs[i, j])
    evc = int(evc_obs[i])
    if (i, j) in det:
        lags, ratios = det[(i, j)]
        lag_dist = {str(w): int(sum(1 for x in lags if x == w)) for w in (-1, 0, 1, 2)}
        med_lag = float(np.median(lags))
        ratio_dist = qtiles(ratios)
    else:
        lag_dist = {str(w): 0 for w in (-1, 0, 1, 2)}
        med_lag = None
        ratio_dist = None
    nulls = {}
    for mi, method in enumerate(METHODS):
        st = stats[method]
        nulls[method] = {
            'null_mean': float(st['mean'][i, j]),
            'null_std': float(st['std'][i, j]),
            'null_max': int(st['mx'][i, j]),
            'empirical_p': float(st['p'][i, j]),
            'effect_size': float(st['eff'][i, j]),
            'fdr_survivor': bool(k in surv[method]),
        }
    records.append({
        'from': PRODS[i], 'to': PRODS[j],
        'A_event_count': evc,
        'observed_matches': obs,
        'match_rate': (obs / evc) if evc else 0.0,
        'median_lag': med_lag,
        'lag_distribution': lag_dist,
        'size_ratio_distribution': ratio_dist,
        'nulls': nulls,
    })

def pname(k):
    i, j = tested[k]
    return f'{PRODS[i]} -> {PRODS[j]}'

out = {
    'spec': {
        'ran_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'days': DAYS, 'size_usd': SIZE, 'tol': TOL, 'trim_s': TRIM_S,
        'bar_s': BAR, 'window': list(WIN), 'block_min': BLOCK_MIN,
        'products': N, 'bars': T, 'null_methods': METHODS,
        'realizations_per_method': R, 'seed': SEED, 'fdr_q': FDR_Q,
        'effect_size_def': '(observed - null_mean)/null_std; raw difference when null_std==0',
        'empirical_p_def': '(1 + #{null >= observed}) / (1 + R)',
        'note': 'Observed test frozen identical to rotation_matched_all.py. '
                'Nulls are independent per product (corrected).',
    },
    'tested_pairs': M,
    'survivors': {m: [pname(k) for k in sorted(surv[m])] for m in METHODS},
    'survivor_intersection': [pname(k) for k in sorted(inter)],
    'pairs': records,
}
json.dump(out, open(OUT, 'w'))
log(f'wrote {OUT} ({os.path.getsize(OUT)/1e6:.1f} MB) in {time.time()-t00:.0f}s total')
logf.close()
