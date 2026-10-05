#!/usr/bin/env python3
"""Lane 5: cross-asset transmission at event time (Mike's spec).

30s factor work shows the F1 cluster (XPL/VET/KITE/2Z) moves together but
cannot distinguish common forcing from directional transmission. This goes to
1s event time.

Events: >=5bps 30s mid moves (leader = argmax |30s move| at detection).
First moves: first >=3bps 5s move per asset within T0±60s (1s resolution).
Lead/lag matrix over ordered pairs + directional asymmetry tests.
Controls: time-shift (+/-120s), circular-shift null per asset, asset-label
permutations. Transmission requires real != ALL controls.
Classification per event: leader_first / follower_first / zero_lag / indeterminate.
Sub-second claims out of scope (1s lane).
"""
import json, glob, os, math, random
from datetime import datetime, timezone
import numpy as np

BASE = os.path.expanduser('~/workspace/goals/paper-trading-simulator/hidden_files')
TICKDIR = os.path.join(BASE, 'book_watcher', 'ticks')
OUTDIR = os.path.join(BASE, 'transmission')
os.makedirs(OUTDIR, exist_ok=True)
ASSETS = ['XPL-USD', 'VET-USD', 'KITE-USD', '2Z-USD']
EV_TH = 5.0      # bps, 30s move event threshold
FM_TH = 3.0      # bps, 5s first-move threshold
WIN = 60         # s, first-move search window T0±WIN
SEP = 120        # s, min separation between events
ZL = 2           # s, zero-lag bin half-width

# ---------------- load 1s mids (deterministic: last row wins per second) ----------------
# NOTE (Lane-6 audit finding): tick files contain ~22k duplicate seconds (4.4%),
# all within-file, with mid differences up to 164bps (overlapping writers).
# Earlier version resolved these arbitrarily via unstable argsort; now last-row-wins.
t_all, m_all = [], []
for f in sorted(glob.glob(os.path.join(TICKDIR, 'ticks_2026-*.jsonl'))):
    with open(f) as fh:
        for line in fh:
            d = json.loads(line)
            t = datetime.fromisoformat(d['t'].replace('Z', '+00:00')).timestamp()
            row = d['d']
            ms = []
            for a in ASSETS:
                v = row.get(a)
                ms.append(v[0] if (v is not None and v[0]) else np.nan)
            t_all.append(t); m_all.append(ms)
# deterministic last-write-wins per integer second
last = {}
for t, ms in zip(t_all, m_all):
    last[int(t)] = (t, ms)
items = sorted(last.values(), key=lambda x: x[0])
t_all = np.array([t for t, _ in items]); m_all = np.array([m for _, m in items])
# regular 1s grid
t0g = math.floor(t_all[0]); t1g = math.ceil(t_all[-1])
grid = np.arange(t0g, t1g + 1, dtype=float)
mid = np.full((len(grid), 4), np.nan)
idx = np.searchsorted(grid, t_all)
# keep last mid per second
for k, gi in enumerate(idx):
    if 0 <= gi < len(grid):
        mid[gi] = m_all[k]
# forward fill (quote data: last mid persists)
for j in range(4):
    m = mid[:, j]
    mask = np.isnan(m)
    fwd = np.maximum.accumulate(np.where(~mask, np.arange(len(m)), -1))
    valid = fwd >= 0
    m[valid] = m[fwd[valid]]
    mid[:, j] = m
N = len(grid)
print(f'grid seconds: {N} ({N/86400:.2f} days), assets: {ASSETS}')
n_valid = (~np.isnan(mid).any(axis=1)).sum()
print(f'rows with all 4 mids: {n_valid}')

def rbps(m, lag):
    out = np.full_like(m, np.nan)
    ok = ~np.isnan(m[lag:]) & ~np.isnan(m[:-lag])
    out[lag:][ok] = (m[lag:][ok] / m[:-lag][ok] - 1.0) * 1e4
    return out

def detect_events(mid):
    r30 = np.stack([rbps(mid[:, j], 30) for j in range(4)], axis=1)
    amax = np.nanmax(np.abs(r30), axis=1)
    cand = np.where(amax >= EV_TH)[0]
    cand = cand[(cand >= WIN + 30) & (cand <= N - WIN - 1)]
    peaks = []
    # greedy by strength with SEP separation
    order_c = cand[np.argsort(-amax[cand])]
    taken = np.zeros(N, dtype=bool)
    for i in order_c:
        if taken[max(0, i - SEP):i + SEP + 1].any():
            continue
        taken[max(0, i - SEP):i + SEP + 1] = True
        leader = int(np.nanargmax(np.abs(r30[i])))
        peaks.append((i, leader, float(r30[i, leader])))
    peaks.sort()
    return peaks, r30

def first_move_idx(q_true_list, i0):
    """q_true_list[j] = sorted indices where asset j has |5s move|>=FM_TH."""
    out = []
    for j in range(4):
        ti = q_true_list[j]
        k = np.searchsorted(ti, i0 - WIN)
        if k < len(ti) and ti[k] <= i0 + WIN:
            out.append(int(ti[k]))
        else:
            out.append(None)
    return out

def build_q(mid):
    ql = []
    for j in range(4):
        r5 = rbps(mid[:, j], 5)
        ql.append(np.where(np.abs(r5) >= FM_TH)[0])
    return ql

def analyze(mid, peaks):
    ql = build_q(mid)
    ev = []
    for (i0, leader, lr) in peaks:
        fm = first_move_idx(ql, i0)
        ev.append({'i0': i0, 't0': float(grid[i0]), 'leader': leader,
                   'leader_r30': lr, 'fm': fm})
    return ev

def pair_stats(ev):
    """For ordered pairs (A,B): n_AB (# A first-move strictly earlier than B by >ZL),
    n_BA, n_zero (|dt|<=ZL). Only events where both have first moves."""
    pairs = {}
    for a in range(4):
        for b in range(4):
            if a == b: continue
            nab = nba = nz = 0
            for e in ev:
                ta, tb = e['fm'][a], e['fm'][b]
                if ta is None or tb is None: continue
                dt = tb - ta
                if dt > ZL: nab += 1
                elif dt < -ZL: nba += 1
                else: nz += 1
            n = nab + nba + nz
            asym = (nab - nba) / (nab + nba) if (nab + nba) > 0 else 0.0
            pairs[(a, b)] = {'n_AB': nab, 'n_BA': nba, 'n_zero': nz, 'n': n,
                             'asym': asym}
    return pairs

def classify(ev):
    counts = {'leader_first': 0, 'follower_first': 0, 'zero_lag': 0,
              'indeterminate': 0, 'follower_first_by': [0]*4}
    for e in ev:
        fm = e['fm']; L = e['leader']
        if any(x is None for x in fm):
            counts['indeterminate'] += 1; continue
        tL = fm[L]
        toth = [fm[j] for j in range(4) if j != L]
        if all(tL < t - ZL for t in toth):
            counts['leader_first'] += 1
        elif any(t < tL - ZL for t in toth):
            counts['follower_first'] += 1
            first = int(np.argmin(fm))
            counts['follower_first_by'][first] += 1
        elif max(fm) - min(fm) <= ZL:
            counts['zero_lag'] += 1
        else:
            counts['indeterminate'] += 1
    return counts

# ---------------- real run ----------------
peaks, r30 = detect_events(mid)
ev = analyze(mid, peaks)
ps = pair_stats(ev)
cls = classify(ev)
print(f'events: {len(ev)}')
print('classification:', {k: (v if not isinstance(v, list) else v) for k, v in cls.items()})

def asym_of(ps, a, b):
    return ps[(a, b)]['asym'], ps[(a, b)]['n_AB'] + ps[(a, b)]['n_BA']

obs_asym = {(a, b): asym_of(ps, a, b) for a in range(4) for b in range(4) if a != b}

# ---------------- control 1: time-shifted ----------------
shift_res = {}
# vectorized shift control: precompute ql once per shift
def pair_stat_shift(shift_b, a, b):
    mid_s = mid.copy(); mid_s[:, b] = np.roll(mid[:, b], shift_b)
    r5b = rbps(mid_s[:, b], 5)
    tb_idx = np.where(np.abs(r5b) >= FM_TH)[0]
    ta_idx = np.where(np.abs(rbps(mid[:, a], 5)) >= FM_TH)[0]
    nab = nba = nz = 0
    for e in ev:
        i0 = e['i0']
        ka = np.searchsorted(ta_idx, i0 - WIN)
        ta = int(ta_idx[ka]) if (ka < len(ta_idx) and ta_idx[ka] <= i0 + WIN) else None
        kb = np.searchsorted(tb_idx, i0 - WIN)
        tb = int(tb_idx[kb]) if (kb < len(tb_idx) and tb_idx[kb] <= i0 + WIN) else None
        if ta is None or tb is None: continue
        dt = tb - ta
        if dt > ZL: nab += 1
        elif dt < -ZL: nba += 1
        else: nz += 1
    asym = (nab - nba) / (nab + nba) if (nab + nba) else 0.0
    return {'n_AB': nab, 'n_BA': nba, 'n_zero': nz, 'asym': asym}

for a in range(4):
    for b in range(4):
        if a == b: continue
        shift_res[(a, b)] = {}

for (a, b) in list(shift_res.keys()):
    for sh in (-120, 120):
        shift_res[(a, b)][sh] = pair_stat_shift(sh, a, b)
    shift_res[(a, b)][0] = {'asym': obs_asym[(a, b)][0],
                           'n_AB': ps[(a, b)]['n_AB'], 'n_BA': ps[(a, b)]['n_BA'],
                           'n_zero': ps[(a, b)]['n_zero']}

# ---------------- control 2: circular-shift null ----------------
rng = random.Random(7)
R = 40
null_asym = {(a, b): [] for a in range(4) for b in range(4) if a != b}
for r in range(R):
    mid_s = mid.copy()
    for j in range(4):
        mid_s[:, j] = np.roll(mid[:, j], rng.randint(3600, N - 3600))
    peaks_s, _ = detect_events(mid_s)
    ev_s = analyze(mid_s, peaks_s)
    ps_s = pair_stats(ev_s)
    for k in null_asym:
        null_asym[k].append(ps_s[k]['asym'])
    if (r + 1) % 10 == 0:
        print(f'null rep {r+1}/{R}, events {len(ev_s)}')

# ---------------- control 3: asset-label permutations ----------------
import itertools
perm_max = []
for perm in itertools.permutations(range(4)):
    if perm == (0, 1, 2, 3): continue
    mid_p = mid[:, list(perm)]
    peaks_p, _ = detect_events(mid_p)
    ev_p = analyze(mid_p, peaks_p)
    ps_p = pair_stats(ev_p)
    mx = max(abs(v['asym']) for v in ps_p.values() if v['n_AB'] + v['n_BA'] >= 20)
    perm_max.append(mx)
obs_max = max(abs(v[0]) for k, v in obs_asym.items()
              if ps[k]['n_AB'] + ps[k]['n_BA'] >= 20)

def pct(xs, x):
    return float(np.mean(np.array(xs) <= x))

results = {
    'meta': {'assets': ASSETS, 'event_threshold_bps_30s': EV_TH,
             'first_move_threshold_bps_5s': FM_TH, 'window_s': WIN,
             'event_separation_s': SEP, 'zero_lag_s': ZL,
             'n_events': len(ev), 'grid_days': float(N / 86400),
             'null_reps': R},
    'classification': cls,
    'pairs': {(f'{ASSETS[a][:3]}->{ASSETS[b][:3]}'): {
        'n_AB': ps[(a, b)]['n_AB'], 'n_BA': ps[(a, b)]['n_BA'],
        'n_zero': ps[(a, b)]['n_zero'], 'asym': round(ps[(a, b)]['asym'], 4),
        'shift_m120_asym': round(shift_res[(a, b)][-120]['asym'], 4),
        'shift_p120_asym': round(shift_res[(a, b)][120]['asym'], 4),
        'null_median_asym': round(float(np.median(null_asym[(a, b)])), 4),
        'null_p5': round(float(np.percentile(null_asym[(a, b)], 5)), 4),
        'null_p95': round(float(np.percentile(null_asym[(a, b)], 95)), 4),
        'null_pct_of_obs': round(pct(null_asym[(a, b)], ps[(a, b)]['asym']), 4)}
        for a in range(4) for b in range(4) if a != b},
    'perm_control': {'obs_max_abs_asym': round(obs_max, 4),
                     'perm_max_median': round(float(np.median(perm_max)), 4),
                     'perm_max_p95': round(float(np.percentile(perm_max, 95)), 4),
                     'perm_pct_of_obs': round(pct(perm_max, obs_max), 4)},
    'leader_share': None,
}
# leader share of events
from collections import Counter
lc = Counter(e['leader'] for e in ev)
results['leader_share'] = {ASSETS[k]: v for k, v in lc.items()}

with open(os.path.join(OUTDIR, 'transmission_results.json'), 'w') as fh:
    json.dump(results, fh, indent=1)
print('wrote transmission_results.json')
print('obs_max_abs_asym', obs_max, 'perm p95', np.percentile(perm_max, 95))
for k, v in results['pairs'].items():
    print(k, 'asym', v['asym'], 'n', v['n_AB'] + v['n_BA'] + v['n_zero'],
          'shift', v['shift_m120_asym'], v['shift_p120_asym'],
          'null95', v['null_p95'], 'pct', v['null_pct_of_obs'])
