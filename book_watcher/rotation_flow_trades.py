#!/usr/bin/env python3
"""Rotation-flow analysis on ACTUAL FILLS (not quotes).
Tick lane: 7 coins, per-second buy/sell notionals from market_trades feed.
Tests Mike's hypothesis: net selling in A -> net buying in B (capital rotation).

Two tests on 30-second net-flow bars (z-scored per coin):
  A. Lead-lag: max over lags 1..20 (30s..10min) of corr(flow_A[t], flow_B[t+k])
     and the rotation version corr(-flow_A[t], flow_B[t+k]) = sells in A lead buys in B.
  B. Event: after a heavy net-sell bar in A (bottom 1% of A's flow),
     mean net flow of each other coin over next 1/2/5/10 min vs its baseline.

Null: block-shuffle each coin's flow (preserves autocorrelation, kills cross timing).
Family-wise threshold = max over shuffled pairs.
"""
import json, glob, datetime
import numpy as np

BAR = 30            # seconds per bar
LAG_MAX = 20        # 20 x 30s = 10 min
N_SHUFFLE = 5
BLOCK = 120         # 1h blocks (in bars)
OUT = '/home/hatch/workspace/goals/paper-trading-simulator/hidden_files/rotation_flow_trades_2026-09-30.json'
rng = np.random.default_rng(11)

files = sorted(glob.glob('/home/hatch/workspace/goals/paper-trading-simulator/hidden_files/book_watcher/ticks/ticks_*.jsonl'))
# per-coin dict of second -> (buy, sell)
raw = {}
for f in files:
    with open(f) as fh:
        for line in fh:
            d = json.loads(line)
            t = int(datetime.datetime.fromisoformat(d['t'].replace('Z', '+00:00')).timestamp())
            for coin, v in d['d'].items():
                if v and len(v) >= 10:
                    raw.setdefault(coin, {})[t] = (v[8], v[9])
coins = sorted(raw)
t0 = min(t for c in raw.values() for t in c) // BAR * BAR
t1 = max(t for c in raw.values() for t in c)
T = (t1 - t0) // BAR + 1
N = len(coins)
print(f'{N} coins, {T} bars of {BAR}s ({T*BAR/3600:.1f} h)', flush=True)
F = np.zeros((T, N))
for j, c in enumerate(coins):
    for t, (b, s) in raw[c].items():
        F[(t - t0) // BAR, j] += b - s          # net flow: + = net buying
Z = (F - F.mean(0)) / (F.std(0) + 1e-12)
print('coins:', coins, flush=True)
print('bars with any flow: %.1f%%' % (100 * (np.abs(F).sum(1) > 0).mean()), flush=True)

def max_lag_corr(X, Y=None):
    """max over lags 1..LAG_MAX of corr(X_i[t], Y_j[t+k]); Y=X if None."""
    if Y is None: Y = X
    n, t = X.shape[1], X.shape[0]
    M = np.full((n, n), -np.inf); BL = np.zeros((n, n), dtype=np.int16)
    for k in range(1, LAG_MAX + 1):
        C = (X[:-k].T @ Y[k:]) / (t - k)
        better = C > M
        M[better] = C[better]; BL[better] = k
    return M, BL

def block_shuffle(X):
    t = X.shape[0]; nb = t // BLOCK
    perm = rng.permutation(nb); out = np.empty_like(X)
    for nb2, ob in enumerate(perm):
        out[nb2*BLOCK:(nb2+1)*BLOCK] = X[ob*BLOCK:(ob+1)*BLOCK]
    out[nb*BLOCK:] = X[nb*BLOCK:]
    return out

# ---------- A1: buys follow buys ----------
print('A1: flow leads flow...', flush=True)
M1, BL1 = max_lag_corr(Z)
# ---------- A2: sells in A lead buys in B (rotation) ----------
print('A2: sells lead buys...', flush=True)
M2, BL2 = max_lag_corr(-Z, Z)
np.fill_diagonal(M1, -np.inf); np.fill_diagonal(M2, -np.inf)
null1 = null2 = 0
for s in range(N_SHUFFLE):
    Zs = block_shuffle(Z)
    a, _ = max_lag_corr(Zs); b, _ = max_lag_corr(-Zs, Zs)
    np.fill_diagonal(a, -np.inf); np.fill_diagonal(b, -np.inf)
    null1 = max(null1, a.max()); null2 = max(null2, b.max())
print(f'  null thr: buys->buys {null1:.3f}, sells->buys {null2:.3f}', flush=True)

def top_pairs(M, BL, thr, label):
    out = []
    for i in range(N):
        for j in range(N):
            if i != j and M[i, j] > thr:
                rev, _ = max_lag_corr(Z[:, [j]], Z[:, [i]])
                out.append({'from': coins[i], 'to': coins[j], 'corr': float(M[i, j]),
                            'rev_corr': float(rev[0, 0]), 'lag_s': int(BL[i, j]) * BAR,
                            'kind': label})
    return sorted(out, key=lambda x: -x['corr'])

pairs = top_pairs(M1, BL1, null1, 'buy_leads_buy') + top_pairs(M2, BL2, null2, 'sell_leads_buy')

# ---------- B: event-based ----------
print('B: sell-event rotation...', flush=True)
WINDOWS = [2, 4, 10, 20]   # bars: 1, 2, 5, 10 min
thr_q = np.quantile(F, 0.01, axis=0)   # heavy net-sell threshold per coin
base = F.mean(0)
flows, null_max = [], 0
for w in WINDOWS:
    Fw = np.zeros((N, N)); cnt = np.zeros(N)
    for j in range(N):
        ev = np.flatnonzero(F[:, j] < thr_q[j])
        ev = ev[ev + w < T]
        if len(ev) < 5: continue
        cnt[j] = len(ev)
        post = np.array([F[e+1:e+1+w, :].sum(0) for e in ev])
        Fw[j] = post.mean(0) - base * w
    # null with same event counts at random times
    Fn = np.zeros((N, N))
    for s in range(N_SHUFFLE):
        for j in range(N):
            e = int(cnt[j])
            if e == 0: continue
            rt = rng.integers(0, T - w, e)
            post = np.array([F[r+1:r+1+w, :].sum(0) for r in rt])
            Fn = np.maximum(Fn, np.abs(post.mean(0) - base * w))
    null_max = max(null_max, Fn.max())
    for i in range(N):
        for j in range(N):
            if i != j and cnt[i] >= 5 and abs(Fw[i, j]) > Fn[i, j] and abs(Fw[i, j]) > 0:
                flows.append({'sell_coin': coins[i], 'buy_coin': coins[j],
                              'window_min': w * BAR // 60,
                              'excess_notional': float(Fw[i, j]),
                              'events': int(cnt[i])})
flows.sort(key=lambda x: -abs(x['excess_notional']))
print(f'  null |flow| thr (notional): {null_max:.4f}', flush=True)

json.dump({'ran_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
           'coins': coins, 'bar_s': BAR, 'n_bars': T,
           'null_buy_leads_buy': float(null1), 'null_sell_leads_buy': float(null2),
           'lead_pairs': pairs, 'n_lead_pairs': len(pairs),
           'event_flows': flows[:40], 'n_event_flows': len(flows)},
          open(OUT, 'w'), indent=1)
print(f'wrote {OUT}\nLEAD_PAIRS={len(pairs)} EVENT_FLOWS={len(flows)}')
for p in pairs[:15]:
    print(f"  [{p['kind']}] {p['from']} -> {p['to']} corr={p['corr']:.3f} rev={p['rev_corr']:.3f} lag={p['lag_s']}s")
for f in flows[:15]:
    print(f"  [event] sell {f['sell_coin']} -> buy {f['buy_coin']} +{f['excess_notional']:.4f} notion/{f['window_min']}m n={f['events']}")
