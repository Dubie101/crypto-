#!/usr/bin/env python3
"""Cross-asset rotation-flow analysis.
Tests Mike's hypothesis: capital rotates between coins in a detectable pattern
(pulled from A -> flows into B), across the full CEX watch set.

Two independent tests:
  A. Lead-lag correlation graph: for each ordered pair (A,B), max correlation
     of A's return at t with B's return at t+k (k=1..12 = 15s..3min).
     Asymmetry required: A must lead B more than B leads A.
  B. Event-based rotation: after a sharp drop in A (>=30bps in 1min),
     which assets rise most in the next 1-5 min vs their own baseline?

Null baselines via block-shuffling (preserves each asset's autocorrelation,
destroys cross-asset timing). Family-wise threshold = max over shuffled pairs.
"""
import sqlite3, json, datetime
import numpy as np

DB = '/home/hatch/workspace/ts-spaces/paper-trading-simulator/app.db'
OUT = '/home/hatch/workspace/goals/paper-trading-simulator/hidden_files/rotation_flow_2026-09-30.json'
GRID_MS = 300000        # 5-minute native cadence (one mark per asset per cycle)
LAG_MAX = 6             # 6 x 5min = 30 minutes
DROP_BPS = 50           # event threshold for test B (wider grid -> wider moves)
POST_MIN = [5, 10, 30]  # post-event windows for test B (minutes)
N_SHUFFLE = 3
BLOCK = 12              # ~1h blocks for shuffling

rng = np.random.default_rng(7)

# ---------- load ----------
c = sqlite3.connect(DB)
rows = c.execute(
    "select product_id, price_usd, observed_at from quote_history "
    "where track='CEX' and price_usd > 0 order by observed_at").fetchall()
assets = sorted({r[0] for r in rows})
ai = {a: i for i, a in enumerate(assets)}
t0 = min(r[2] for r in rows) // GRID_MS * GRID_MS
t1 = max(r[2] for r in rows)
T = int((t1 - t0) // GRID_MS) + 1
N = len(assets)
print(f'{N} assets, {T} grid points ({T*5/60:.1f} h)', flush=True)

P = np.full((T, N), np.nan)
for a, p, ts in rows:
    P[int((ts - t0) // GRID_MS), ai[a]] = p
# keep assets with decent coverage, ffill small gaps
cover = (~np.isnan(P)).mean(axis=0)
keep = cover > 0.7
P, assets, ai = P[:, keep], [a for i, a in enumerate(assets) if keep[i]], {a: i for i, a in enumerate([a for i, a in enumerate(assets) if keep[i]])}
N = len(assets)
for j in range(N):
    col = P[:, j]
    m = np.isnan(col)
    col[m] = np.interp(np.flatnonzero(m), np.flatnonzero(~m), col[~m])
R = np.diff(np.log(P), axis=0)          # log returns, (T-1, N)
T = R.shape[0]
Rs = (R - R.mean(0)) / (R.std(0) + 1e-12)
print(f'kept {N} assets, {T} return obs', flush=True)

def max_lag_corr(X):
    """For each ordered pair (i,j): max over lags 1..LAG_MAX of corr(X_i[t], X_j[t+k]).
    Returns (M, best_lag) N x N."""
    n, t = X.shape[1], X.shape[0]
    M = np.zeros((n, n)); BL = np.zeros((n, n), dtype=np.int16)
    for k in range(1, LAG_MAX + 1):
        C = (X[:-k].T @ X[k:]) / (t - k)
        better = C > M
        M[better] = C[better]; BL[better] = k
    np.fill_diagonal(M, 0)
    return M, BL

def block_shuffle(X):
    Xs = X.copy(); t = X.shape[0]
    nb = t // BLOCK
    perm = rng.permutation(nb)
    out = np.empty_like(X)
    for new_b, old_b in enumerate(perm):
        out[new_b*BLOCK:(new_b+1)*BLOCK] = X[old_b*BLOCK:(old_b+1)*BLOCK]
    out[nb*BLOCK:] = X[nb*BLOCK:]
    return out

# ---------- A. lead-lag ----------
print('A: lead-lag...', flush=True)
M, BL = max_lag_corr(Rs)
asym = M - M.T                      # >0 means i leads j more than j leads i
null_max = 0
for s in range(N_SHUFFLE):
    Ms, _ = max_lag_corr(block_shuffle(Rs))
    null_max = max(null_max, Ms.max())
    print(f'  shuffle {s+1}: null max={Ms.max():.4f}', flush=True)
print(f'  family-wise null threshold: {null_max:.4f}', flush=True)

pairs = []
for i in range(N):
    for j in range(N):
        if i != j and asym[i, j] > 0 and M[i, j] > null_max:
            pairs.append((assets[i], assets[j], float(M[i, j]), float(M[j, i]), int(BL[i, j])))
pairs.sort(key=lambda x: -x[2])

# market mode: mean contemporaneous correlation (ex-diagonal)
C0 = np.corrcoef(Rs.T)
np.fill_diagonal(C0, np.nan)
mkt_corr = float(np.nanmean(C0))

# ---------- B. event-based rotation ----------
print('B: event rotation...', flush=True)
# 5-min returns (one grid step)
R1 = P[1:] / P[:-1] - 1.0
T1 = R1.shape[0]
ev_mask = R1 < -DROP_BPS / 1e4
n_events = int(ev_mask.sum())
print(f'  {n_events} drop events (>={DROP_BPS}bps/5min)', flush=True)
# post-event windows in 5-min steps
post_steps = [m // 5 for m in POST_MIN]   # 1, 2, 6 steps
base = R1.mean(axis=0) * np.array(post_steps)[:, None]  # expected drift per window
flow = np.zeros((len(post_steps), N, N))  # flow[w, drop_asset, rise_asset]
cnt = np.zeros(N)
for w, s in enumerate(post_steps):
    # for each event time t (index into R1), post return of every asset over next s steps
    for j in range(N):
        ev_t = np.flatnonzero(ev_mask[:, j])
        ev_t = ev_t[ev_t + s < T1]
        if len(ev_t) == 0: continue
        cnt[j] += len(ev_t)
        post = P[1 + ev_t + s] / P[1 + ev_t] - 1.0   # (E, N)
        flow[w, j] = post.mean(axis=0) - base[w]
# null: same count of random times
null_flow_max = 0
for s_i in range(N_SHUFFLE):
    for w, s in enumerate(post_steps):
        fn = np.zeros((N, N))
        for j in range(N):
            e = int(cnt[j])
            if e == 0: continue
            rt = rng.integers(0, T1 - s, e)
            post = P[1 + rt + s] / P[1 + rt] - 1.0
            fn[j] = post.mean(axis=0) - base[w]
        null_flow_max = max(null_flow_max, np.abs(fn).max())
print(f'  null |flow| threshold: {null_flow_max*1e4:.1f} bps', flush=True)

flows = []
for w, m in enumerate(POST_MIN):
    F = flow[w]
    for i in range(N):
        for j in range(N):
            if i != j and abs(F[i, j]) > null_flow_max and cnt[i] >= 5:
                flows.append({'drop': assets[i], 'into': assets[j],
                              'window_min': m, 'excess_bps': float(F[i, j] * 1e4),
                              'events': int(cnt[i])})
flows.sort(key=lambda x: -abs(x['excess_bps']))

out = {
    'ran_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'n_assets': N, 'n_obs': T, 'grid_sec': 300,
    'mean_contemporaneous_corr': mkt_corr,
    'leadlag_null_threshold': float(null_max),
    'n_lead_pairs': len(pairs),
    'top_lead_pairs': [{'leader': a, 'follower': b, 'corr': cc, 'rev_corr': rc,
                        'lag_5min': l} for a, b, cc, rc, l in pairs[:40]],
    'drop_events': n_events,
    'flow_null_bps': float(null_flow_max * 1e4),
    'n_sig_flows': len(flows),
    'top_flows': flows[:40],
}
json.dump(out, open(OUT, 'w'), indent=1)
print(f'wrote {OUT}')
print(f"LEAD_PAIRS={len(pairs)} FLOWS={len(flows)} MKT_CORR={mkt_corr:.3f}")
for p in pairs[:10]:
    print(f'  lead {p[0]} -> {p[1]} corr={p[2]:.3f} rev={p[3]:.3f} lag={p[4]*5}min')
for f in flows[:10]:
    print(f"  flow {f['drop']} -> {f['into']} {f['excess_bps']:+.1f}bps/{f['window_min']}m n={f['events']}")
