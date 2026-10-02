#!/usr/bin/env python3
"""Exact-amount bot fingerprint hunt (Mike: rule-based, predetermined amounts).

A bot with fixed rules sells $X of A / buys $Y of B on repeat. Humans and
discretionary algos produce varied sizes; a fixed-rule bot produces the SAME
notionals again and again. This hunts exact repetitions, not fuzzy matches:

  sell print (A, $X) -> buy print (B, $Y) within T_WIN seconds,
  notionals rounded to $1. Count identical ((A,X),(B,Y)) tuples.

A real predetermined-amount bot shows the same tuple dozens+ times with
tight timing. Null: shuffle buy timestamps, recount -- repetition of exact
tuples should collapse.

Usage: rotation_exact.py [--days N] [--win 60] [--min-rep 5]
"""
import json, glob, os, sys, datetime
import numpy as np
from collections import Counter, defaultdict

BASE = os.path.dirname(os.path.abspath(__file__))
TDIR = os.path.join(BASE, 'trades_all')
PRODS = json.load(open(os.path.join(BASE, 'all_products.json')))
TRIM_S = 600
OUT = os.path.join(os.path.dirname(BASE), 'rotation_exact.json')

days = 7; T_WIN = 60; MIN_REP = 5
for i, a in enumerate(sys.argv[1:]):
    if a == '--days': days = int(sys.argv[i + 2])
    if a == '--win': T_WIN = int(sys.argv[i + 2])
    if a == '--min-rep': MIN_REP = int(sys.argv[i + 2])
rng = np.random.default_rng(3)

sells = []  # (t_ms, prod, dollars)
buys = []
for f in sorted(glob.glob(os.path.join(TDIR, 'bigprints_*.jsonl')))[-days:]:
    fstart = None
    for line in open(f):
        d = json.loads(line)
        t = d['t']
        if fstart is None:
            fstart = t
        if t - fstart < TRIM_S * 1000:
            continue
        (sells if d['s'] == 0 else buys).append((t, d['p'], round(d['n'])))
print(f'sell prints: {len(sells)}, buy prints: {len(buys)}', flush=True)
if not sells or not buys:
    print('no data'); sys.exit(1)

sells.sort(); buys.sort()

# Memory-lean prefilter: an exact ((A,X),(B,Y)) tuple can only repeat if each
# leg's (product, dollars) repeats on its own. Drop one-off notionals before
# the O(N*M) window hunt -- this cuts the Counter from ~77M increments to
# the repeating-notional subspace.
from collections import Counter as _C
_sf = _C((p, n) for _, p, n in sells)
_bf = _C((p, n) for _, p, n in buys)
_keep_s = {k for k, c in _sf.items() if c >= 3}
_keep_b = {k for k, c in _bf.items() if c >= 3}
del _sf, _bf
sells = [(t, p, n) for t, p, n in sells if (p, n) in _keep_s]
buys = [(t, p, n) for t, p, n in buys if (p, n) in _keep_b]
del _keep_s, _keep_b
print(f'after prefilter: sell prints {len(sells)}, buy prints {len(buys)}', flush=True)
bt = np.array([b[0] for b in buys])

def hunt(buy_list, buy_times):
    cnt = Counter()
    for st, sp, sx in sells:
        lo = np.searchsorted(buy_times, st - T_WIN * 1000)
        hi = np.searchsorted(buy_times, st + T_WIN * 1000)
        for j in range(lo, hi):
            bt2, bp, bx = buy_list[j]
            if bp != sp:
                cnt[((sp, sx), (bp, bx))] += 1
    return cnt

C = hunt(buys, bt)
# null: shuffle buy times (keep products/amounts, kill timing)
null_max = 0
for _ in range(5):
    perm = rng.permutation(len(buys))
    sb = [buys[i] for i in perm]
    st_ = np.array(sorted(b[0] for b in sb))
    # hunt needs buys sorted by time with aligned list; rebuild
    order = np.argsort([b[0] for b in sb])
    sb_sorted = [sb[i] for i in order]
    st_sorted = np.array([b[0] for b in sb_sorted])
    Cn = hunt(sb_sorted, st_sorted)
    if Cn:
        null_max = max(null_max, max(Cn.values()))
print(f'null max exact-tuple repetitions: {null_max}', flush=True)

res = []
for ((sp, sx), (bp, bx)), c in C.most_common(50):
    if c >= MIN_REP and c > null_max:
        res.append({'sell': f'{PRODS[sp]} ${sx}', 'buy': f'{PRODS[bp]} ${bx}',
                    'repetitions': c, 'null_max': null_max})
json.dump({'ran_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
           'days': days, 'window_s': T_WIN, 'pairs': res}, open(OUT, 'w'), indent=1)
print(f'wrote {OUT}\nSIGNIFICANT EXACT PAIRS: {len(res)}')
for r in res[:15]:
    print(f"  sell {r['sell']} -> buy {r['buy']}: x{r['repetitions']} (null max {r['null_max']})")
if not res:
    print('top raw repetitions (none significant):')
    for ((sp, sx), (bp, bx)), c in C.most_common(8):
        print(f"  sell {PRODS[sp]} ${sx} -> buy {PRODS[bp]} ${bx}: x{c}")
