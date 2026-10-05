#!/usr/bin/env python3
"""Rotation-episode regime classification: RESOLVE vs CONTINUE.

Divergence episodes in the F1 cluster (XPL/VET/KITE/2Z): contiguous runs of
5-min bars where max(24h position) >= 0.70 AND min(24h position) <= 0.30.
Leader/laggard frozen at onset (tradeable definition). Forward
laggard-minus-leader returns at 3h/6h/12h. Onset observables compared across
RESOLVE vs CONTINUE. Circular-shift null.
"""
import json, glob, os, math, random
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import numpy as np

BASE = os.path.expanduser('~/workspace/goals/paper-trading-simulator/hidden_files')
TICKDIR = os.path.join(BASE, 'book_watcher', 'ticks')
OUTDIR = os.path.join(BASE, 'rotation_episodes')
ASSETS = ['XPL-USD', 'VET-USD', 'KITE-USD', '2Z-USD']
BAR = 300
W = 288          # 24h window in 5-min bars
BASE6H = 72      # 6h baseline in bars
MINW = 200       # min valid bars for a position value

# ---------------- load 1s rows ----------------
files = sorted(glob.glob(os.path.join(TICKDIR, 'ticks_2026-*.jsonl')))
secs = []  # (t, {asset: (mid, spr_bps, tr_n)})
for f in files:
    with open(f) as fh:
        for line in fh:
            d = json.loads(line)
            t = datetime.fromisoformat(d['t'].replace('Z', '+00:00')).timestamp()
            row = {}
            for a in ASSETS:
                v = d['d'].get(a)
                if v is not None and v[0]:
                    row[a] = (v[0], v[1], v[7])
            if len(row) == len(ASSETS):
                secs.append((t, row))
secs.sort(key=lambda x: x[0])
print(f'seconds with all 4 assets: {len(secs)}')

# ---------------- 5-min bars ----------------
bars = []  # [t, {a: mid}, {a: med_spr}, {a: tr_sum}]
cur = None
for t, row in secs:
    if cur is None or t - cur[0] >= BAR:
        if cur:
            bars.append(cur)
        cur = [t, {a: row[a][0] for a in ASSETS},
               {a: [row[a][1]] for a in ASSETS},
               {a: row[a][2] for a in ASSETS}]
    else:
        for a in ASSETS:
            cur[1][a] = row[a][0]
            cur[2][a].append(row[a][1])
            cur[3][a] += row[a][2]
if cur:
    bars.append(cur)
nb = len(bars)
ts = np.array([b[0] for b in bars])
mid = np.full((nb, 4), np.nan)
medspr = np.full((nb, 4), np.nan)
trsum = np.full((nb, 4), np.nan)
for i, b in enumerate(bars):
    for j, a in enumerate(ASSETS):
        mid[i, j] = b[1][a]
        medspr[i, j] = float(np.median(b[2][a]))
        trsum[i, j] = b[3][a]
print(f'5-min bars: {nb}, span {(ts[-1]-ts[0])/3600:.1f}h')

def positions(mid):
    """24h rolling min-max normalized positions per asset."""
    pos = np.full_like(mid, np.nan)
    for j in range(4):
        m = mid[:, j]
        for i in range(W - 1, len(m)):
            w = m[i - W + 1:i + 1]
            v = w[~np.isnan(w)]
            if len(v) >= MINW and not np.isnan(m[i]):
                lo, hi = v.min(), v.max()
                pos[i, j] = (m[i] - lo) / (hi - lo) if hi > lo else 0.5
    return pos

def pctile(baseline, x):
    b = baseline[~np.isnan(baseline)]
    if len(b) == 0 or np.isnan(x):
        return float('nan')
    return float(np.mean(b < x))

GAP_TOL = 6  # runs separated by <=6 bars (30 min) merge into one episode

def detect_episodes(mid, medspr, trsum, ts, gap_tol=GAP_TOL):
    pos = positions(mid)
    n = len(mid)
    valid = ~np.isnan(pos).any(axis=1)
    state = np.zeros(n, dtype=bool)
    for i in range(n):
        if valid[i]:
            p = pos[i]
            state[i] = (p.max() >= 0.70) and (p.min() <= 0.30)
    # contiguous runs
    runs = []
    i = 0
    first_valid = int(np.argmax(valid)) if valid.any() else 0
    while i < n:
        if state[i]:
            j = i
            while j + 1 < n and state[j + 1]:
                j += 1
            runs.append((i, j))
            i = j + 1
        else:
            i += 1
    # gap-tolerant merge: flicker in/out at the 0.70/0.30 boundary is one regime
    merged = []
    for (i0, i1) in runs:
        if merged and i0 - merged[-1][1] <= gap_tol:
            merged[-1] = (merged[-1][0], i1, merged[-1][2] + 1)
        else:
            merged.append((i0, i1, 1))
    ET = ZoneInfo('America/New_York')
    episodes = []
    for (i0, i1, nruns) in merged:
        p0 = pos[i0]
        li = int(np.argmax(p0)); gi = int(np.argmin(p0))
        gap0 = float(p0[li] - p0[gi])
        leaders70 = [ASSETS[j] for j in range(4) if p0[j] >= 0.70]
        laggs30 = [ASSETS[j] for j in range(4) if p0[j] <= 0.30]
        # max cross-sectional spread within episode span
        mx = gap0
        for i in range(i0, i1 + 1):
            if valid[i]:
                mx = max(mx, float(pos[i].max() - pos[i].min()))
        diverged_bars = sum(1 for i in range(i0, i1 + 1) if state[i])
        # forwards (need non-NaN mids)
        fwd = {}
        for h, bn_ in [(3, 36), (6, 72), (12, 144)]:
            ie = i0 + bn_
            if ie < n and not np.isnan(mid[i0, li]) and not np.isnan(mid[ie, li]) \
               and not np.isnan(mid[i0, gi]) and not np.isnan(mid[ie, gi]):
                rl = (mid[ie, li] / mid[i0, li] - 1) * 1e4
                rg = (mid[ie, gi] / mid[i0, gi] - 1) * 1e4
                fwd[h] = float(rg - rl)
            else:
                fwd[h] = None
        # frozen-pair position gap at +12h (expansion measured on the tradeable pair)
        ie12 = i0 + 144
        gap12 = None
        if ie12 < n and valid[ie12]:
            gap12 = float(pos[ie12, li] - pos[ie12, gi])
        # --- onset observables ---
        onset = {}
        onset['spread'] = gap0
        onset['leader_pos'] = float(p0[li]); onset['laggard_pos'] = float(p0[gi])
        onset['leader'] = ASSETS[li]; onset['laggard'] = ASSETS[gi]
        onset['n_leaders_ge70'] = len(leaders70); onset['n_laggards_le30'] = len(laggs30)
        onset['leaders_ge70'] = leaders70; onset['laggards_le30'] = laggs30
        onset['hour_et'] = datetime.fromtimestamp(ts[i0], tz=timezone.utc).astimezone(ET).hour + \
                           datetime.fromtimestamp(ts[i0], tz=timezone.utc).astimezone(ET).minute / 60.0
        # spread/trade-arrival percentiles vs trailing 6h
        b0 = max(0, i0 - BASE6H)
        for j, a in enumerate(ASSETS):
            onset[f'{a}_spr_pct'] = pctile(medspr[b0:i0, j], medspr[i0, j])
            onset[f'{a}_tr_pct'] = pctile(trsum[b0:i0, j], trsum[i0, j])
        # trailing-3h realized vol (bps per 5-min bar)
        for j, a in enumerate(ASSETS):
            r = np.diff(np.log(mid[max(0, i0 - 36):i0 + 1, j]))
            r = r[~np.isnan(r)]
            onset[f'{a}_vol3h'] = float(np.std(r) * 1e4) if len(r) >= 24 else None
        # F1 factor 3h move: equal-weight mean of 4-asset 3h returns (bps), signed
        f1moves = []
        for j in range(4):
            ib = i0 - 36
            if ib >= 0 and not np.isnan(mid[ib, j]) and not np.isnan(mid[i0, j]):
                f1moves.append((mid[i0, j] / mid[ib, j] - 1) * 1e4)
        onset['f1_3h_move'] = float(np.mean(f1moves)) if f1moves else None
        # fresh 24h high / low at onset
        onset['leader_fresh_high'] = None
        onset['laggard_fresh_low'] = None
        wb = max(0, i0 - W + 1)
        wl = mid[wb:i0 + 1, li]; wl = wl[~np.isnan(wl)]
        wg = mid[wb:i0 + 1, gi]; wg = wg[~np.isnan(wg)]
        if len(wl) and not np.isnan(mid[i0, li]):
            onset['leader_fresh_high'] = bool(mid[i0, li] >= wl.max())
        if len(wg) and not np.isnan(mid[i0, gi]):
            onset['laggard_fresh_low'] = bool(mid[i0, gi] <= wg.min())
        # divergence expansion rate: spread_now - spread_3h_ago
        onset['expansion_3h'] = None
        ib = i0 - 36
        if ib >= 0 and valid[ib]:
            onset['expansion_3h'] = float(gap0 - (pos[ib].max() - pos[ib].min()))
        # --- classification (frozen pair; expansion = pair gap widened in position space) ---
        f6, f12 = fwd[6], fwd[12]
        if f12 is None:
            cls = 'UNCLASSIFIED'
        elif f12 < -100 or (gap12 is not None and gap12 > gap0):
            cls = 'CONTINUE'
        elif f6 is not None and f6 > 0 and f12 >= -100:
            cls = 'RESOLVE'
        else:
            cls = 'AMBIGUOUS'
        episodes.append({
            'onset_utc': datetime.fromtimestamp(ts[i0], tz=timezone.utc).strftime('%Y-%m-%d %H:%M'),
            'onset_idx': i0,
            'span_bars': i1 - i0 + 1,
            'diverged_bars': diverged_bars,
            'runs_merged': nruns,
            'left_censored': bool(i0 == first_valid),
            'max_spread_in_episode': mx,
            'gap_onset': gap0,
            'gap_12h': gap12,
            'fwd_3h': fwd[3], 'fwd_6h': fwd[6], 'fwd_12h': fwd[12],
            'class': cls,
            'onset': onset,
        })
    return episodes

episodes = detect_episodes(mid, medspr, trsum, ts)
strict_episodes = detect_episodes(mid, medspr, trsum, ts, gap_tol=0)
print(f'episodes (gap-tol merged): {len(episodes)}; strict maximal runs: {len(strict_episodes)}')
for e in episodes:
    print(f"  {e['onset_utc']} {e['onset']['leader'][:3]}>={e['onset']['laggard'][:3]} span={e['span_bars']} div={e['diverged_bars']} runs={e['runs_merged']} "
          f"f3={e['fwd_3h'] and round(e['fwd_3h'],1)} f6={e['fwd_6h'] and round(e['fwd_6h'],1)} f12={e['fwd_12h'] and round(e['fwd_12h'],1)} {e['class']}{' LC' if e['left_censored'] else ''}")

# ---------------- class comparison ----------------
def med(xs):
    xs = [x for x in xs if x is not None and not (isinstance(x, float) and math.isnan(x))]
    return float(np.median(xs)) if xs else None

RES = [e for e in episodes if e['class'] == 'RESOLVE']
CON = [e for e in episodes if e['class'] == 'CONTINUE']
comp = {}
keys = ['spread', 'leader_pos', 'laggard_pos', 'hour_et', 'f1_3h_move', 'expansion_3h',
        'n_leaders_ge70', 'n_laggards_le30',
        'XPL-USD_spr_pct', 'VET-USD_spr_pct', 'KITE-USD_spr_pct', '2Z-USD_spr_pct',
        'XPL-USD_tr_pct', 'VET-USD_tr_pct', 'KITE-USD_tr_pct', '2Z-USD_tr_pct',
        'XPL-USD_vol3h', 'VET-USD_vol3h', 'KITE-USD_vol3h', '2Z-USD_vol3h']
for k in keys:
    comp[k] = {'resolve_median': med([e['onset'][k] for e in RES]),
               'continue_median': med([e['onset'][k] for e in CON]),
               'n_resolve': sum(1 for e in RES if e['onset'][k] is not None and not (isinstance(e['onset'][k], float) and math.isnan(e['onset'][k]))),
               'n_continue': sum(1 for e in CON if e['onset'][k] is not None and not (isinstance(e['onset'][k], float) and math.isnan(e['onset'][k])))}
# categorical
for k in ['leader_fresh_high', 'laggard_fresh_low']:
    comp[k] = {'resolve_true': sum(1 for e in RES if e['onset'][k] is True),
               'resolve_n': len(RES), 'continue_true': sum(1 for e in CON if e['onset'][k] is True),
               'continue_n': len(CON)}
comp['leader_identity'] = {
    'resolve': {a: sum(1 for e in RES if e['onset']['leader'] == a) for a in ASSETS},
    'continue': {a: sum(1 for e in CON if e['onset']['leader'] == a) for a in ASSETS}}
comp['laggard_identity'] = {
    'resolve': {a: sum(1 for e in RES if e['onset']['laggard'] == a) for a in ASSETS},
    'continue': {a: sum(1 for e in CON if e['onset']['laggard'] == a) for a in ASSETS}}

# forward stats per class
fwdstat = {}
for cls, grp in [('RESOLVE', RES), ('CONTINUE', CON), ('ALL', [e for e in episodes if e['class'] in ('RESOLVE', 'CONTINUE')])]:
    d = {}
    for h, key in [(3, 'fwd_3h'), (6, 'fwd_6h'), (12, 'fwd_12h')]:
        xs = [e[key] for e in grp if e[key] is not None]
        d[f'{h}h'] = {'n': len(xs), 'mean': float(np.mean(xs)) if xs else None,
                      'median': float(np.median(xs)) if xs else None,
                      'frac_positive': float(np.mean([x > 0 for x in xs])) if xs else None}
    fwdstat[cls] = d

# ---------------- null: circular shift ----------------
random.seed(20261003)
null_rates = []
null_counts = []
N_NULL = 50
for rep in range(N_NULL):
    m2 = mid.copy()
    for j in range(4):
        m2[:, j] = np.roll(m2[:, j], random.randrange(nb))
    eps = detect_episodes(m2, medspr, trsum, ts)
    r = sum(1 for e in eps if e['class'] == 'RESOLVE')
    c = sum(1 for e in eps if e['class'] == 'CONTINUE')
    null_counts.append(len(eps))
    null_rates.append(r / (r + c) if (r + c) else float('nan'))
null_rates = [x for x in null_rates if not math.isnan(x)]
obs_r = sum(1 for e in episodes if e['class'] == 'RESOLVE')
obs_c = sum(1 for e in episodes if e['class'] == 'CONTINUE')
obs_rate = obs_r / (obs_r + obs_c) if (obs_r + obs_c) else None

results = {
    'meta': {
        'assets': ASSETS, 'bar_s': BAR, 'window_bars': W,
        'divergence_rule': 'max(24h pos) >= 0.70 AND min(24h pos) <= 0.30',
        'leader_laggard': 'frozen at onset (argmax/argmin position at onset bar)',
        'class_rule': ('CONTINUE if fwd12 < -100bps OR frozen-pair position gap(+12h) > gap(onset); '
                       'else RESOLVE if fwd6 > 0 AND fwd12 >= -100bps; else AMBIGUOUS; '
                       'UNCLASSIFIED if fwd12 missing. '
                       'Episodes = gap-tolerant merged runs (gap<=6 bars merges); strict maximal-run count kept as sensitivity.'),
        'f1_proxy': 'equal-weight mean of 4-asset 3h returns (bps), no fitted loadings',
        'n_bars': nb, 'span_h': round((ts[-1] - ts[0]) / 3600, 1),
    },
    'counts': {
        'episodes_merged': len(episodes),
        'episodes_strict_runs': len(strict_episodes),
        'resolve': obs_r, 'continue': obs_c,
        'ambiguous': sum(1 for e in episodes if e['class'] == 'AMBIGUOUS'),
        'unclassified': sum(1 for e in episodes if e['class'] == 'UNCLASSIFIED'),
        'left_censored': sum(1 for e in episodes if e['left_censored']),
        'observed_resolve_rate': obs_rate,
    },
    'episodes': episodes,
    'onset_comparison': comp,
    'forward_stats': fwdstat,
    'null': {
        'method': 'circular-shift each asset bar series independently, 50 realizations',
        'n_null': N_NULL,
        'null_resolve_rate_mean': float(np.mean(null_rates)) if null_rates else None,
        'null_resolve_rate_median': float(np.median(null_rates)) if null_rates else None,
        'null_resolve_rate_p5_p95': [float(np.percentile(null_rates, 5)), float(np.percentile(null_rates, 95))] if null_rates else None,
        'null_episode_count_mean': float(np.mean(null_counts)) if null_counts else None,
        'observed_resolve_rate': obs_rate,
    },
}
with open(os.path.join(OUTDIR, 'rotation_episodes_results.json'), 'w') as fh:
    json.dump(results, fh, indent=1)
print('counts:', results['counts'])
print('null resolve rate: mean %.3f median %.3f p5-p95 %s vs observed %s' % (
    results['null']['null_resolve_rate_mean'], results['null']['null_resolve_rate_median'],
    results['null']['null_resolve_rate_p5_p95'], obs_rate))
print('wrote rotation_episodes_results.json')
