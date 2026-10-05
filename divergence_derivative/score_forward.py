#!/usr/bin/env python3
"""Forward scoring for the frozen divergence branch-prediction rule.

PROTOCOL.md is the specification. This script:
  1. Builds 5-min bars from the tick lane (verbatim construction from
     rotation_episodes/build_episodes.py).
  2. SELF-TEST: re-detects episodes on the full history and asserts the 11
     historical onsets reproduce exactly. Aborts without writing on mismatch.
  3. For NEW episodes (onset after FREEZE_TS, not in ledger): records the
     frozen prediction (slope_in < 0 AND leader_ret_30m < 0 -> RESOLVE).
  4. For ledger episodes past onset+12h without a realized branch: fills it
     in with the frozen classification rule.
  5. Writes only forward_ledger.json. Prints a JSON summary for the cron.

Frozen rule (2026-10-03 ~10:35 UTC, Mike):
  PREDICT_RESOLVE iff slope_in < 0 AND leader_ret_30m < 0 else PREDICT_CONTINUE
Realized (frozen): CONTINUE if fwd12 < -100bps or pair gap widened at +12h;
  else RESOLVE if fwd6 > 0 and fwd12 >= -100bps; else AMBIGUOUS.
"""
import json, glob, os, sys
from datetime import datetime, timezone
import numpy as np

BASE = os.path.expanduser('~/workspace/goals/paper-trading-simulator/hidden_files')
TICKDIR = os.path.join(BASE, 'book_watcher', 'ticks')
OUTDIR = os.path.join(BASE, 'divergence_derivative')
LEDGER = os.path.join(OUTDIR, 'forward_ledger.json')
EPJSON = os.path.join(BASE, 'rotation_episodes', 'rotation_episodes_results.json')
ASSETS = ['XPL-USD', 'VET-USD', 'KITE-USD', '2Z-USD']
BAR = 300
W = 288
MINW = 200
GAP_TOL = 6
FREEZE_TS = datetime(2026, 10, 3, 10, 30, tzinfo=timezone.utc).timestamp()

# ---------------- bar building (verbatim from build_episodes.py) ----------------
files = sorted(glob.glob(os.path.join(TICKDIR, 'ticks_2026-*.jsonl')))
secs = []
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
bars = []
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
for i, b in enumerate(bars):
    for j, a in enumerate(ASSETS):
        mid[i, j] = b[1][a]

def positions(mid):
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

def detect_episodes(mid, ts, gap_tol=GAP_TOL):
    pos = positions(mid)
    n = len(mid)
    valid = ~np.isnan(pos).any(axis=1)
    state = np.zeros(n, dtype=bool)
    for i in range(n):
        if valid[i]:
            p = pos[i]
            state[i] = (p.max() >= 0.70) and (p.min() <= 0.30)
    runs = []
    i = 0
    while i < n:
        if state[i]:
            j = i
            while j + 1 < n and state[j + 1]:
                j += 1
            runs.append((i, j))
            i = j + 1
        else:
            i += 1
    merged = []
    for (i0, i1) in runs:
        if merged and i0 - merged[-1][1] <= gap_tol:
            merged[-1] = (merged[-1][0], i1)
        else:
            merged.append((i0, i1))
    episodes = []
    for (i0, i1) in merged:
        p0 = pos[i0]
        li = int(np.argmax(p0)); gi = int(np.argmin(p0))
        gap0 = float(p0[li] - p0[gi])
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
        ie12 = i0 + 144
        gap12 = float(pos[ie12, li] - pos[ie12, gi]) if ie12 < n and valid[ie12] else None
        episodes.append({'i0': i0, 'ts0': float(ts[i0]), 'li': li, 'gi': gi,
                         'gap0': gap0, 'gap12': gap12, 'fwd': fwd, 'pos': pos})
    return episodes

def div_at(pos, i):
    return float(pos[i].max() - pos[i].min())

def classify(fwd, gap0, gap12):
    f6, f12 = fwd[6], fwd[12]
    if f12 is None:
        return 'UNCLASSIFIED'
    if f12 < -100 or (gap12 is not None and gap12 > gap0):
        return 'CONTINUE'
    if f6 is not None and f6 > 0 and f12 >= -100:
        return 'RESOLVE'
    return 'AMBIGUOUS'

# ---------------- SELF-TEST: historical onsets must reproduce exactly ----------------
episodes = detect_episodes(mid, ts)
hist = json.load(open(EPJSON))['episodes']
hist_onsets = sorted(e['onset_utc'] for e in hist)
got_onsets = sorted(datetime.fromtimestamp(e['ts0'], tz=timezone.utc).strftime('%Y-%m-%d %H:%M')
                    for e in episodes if e['ts0'] <= FREEZE_TS)
if hist_onsets != got_onsets:
    print(json.dumps({'fatal': 'SELF-TEST FAILED: episode onsets do not reproduce the frozen 11',
                      'missing': sorted(set(hist_onsets) - set(got_onsets)),
                      'extra': sorted(set(got_onsets) - set(hist_onsets))}))
    sys.exit(1)

# ---------------- ledger ----------------
ledger = json.load(open(LEDGER)) if os.path.exists(LEDGER) else []
by_onset = {e['onset_utc']: e for e in ledger}
new_predictions, new_realizations = [], []
now = datetime.now(timezone.utc).timestamp()
for e in episodes:
    if e['ts0'] <= FREEZE_TS:
        continue  # pre-freeze: excluded from the forward test
    ou = datetime.fromtimestamp(e['ts0'], tz=timezone.utc).strftime('%Y-%m-%d %H:%M')
    rec = by_onset.get(ou)
    if rec is None:
        pos = e['pos']; i0 = e['i0']
        slope_in = div_at(pos, i0) - div_at(pos, i0 - 6) if i0 - 6 >= 0 else None
        li = e['li']
        lret = ((mid[i0, li] / mid[i0 - 6, li] - 1) * 1e4
                if i0 - 6 >= 0 and not np.isnan(mid[i0 - 6, li]) else None)
        if slope_in is None or lret is None or np.isnan(slope_in) or np.isnan(lret):
            continue  # not enough history yet; will be picked up next run
        pred = 'RESOLVE' if (slope_in < 0 and lret < 0) else 'CONTINUE'
        # Lane 1 record field (Mike 2026-10-05): F1 30m return at onset.
        # Record-only diagnostic; NOT part of the frozen rule.
        f1moves = []
        for j in range(4):
            ib = i0 - 6
            if ib >= 0 and not np.isnan(mid[ib, j]) and not np.isnan(mid[i0, j]):
                f1moves.append((mid[i0, j] / mid[ib, j] - 1) * 1e4)
        f1_30m = float(np.mean(f1moves)) if f1moves else None
        rec = {'onset_utc': ou, 'onset_ts': e['ts0'],
               'leader': ASSETS[li], 'laggard': ASSETS[e['gi']],
               'slope_in': float(slope_in), 'slope_in_neg': bool(slope_in < 0),
               'leader_ret_30m_bps': float(lret), 'leader_ret_neg': bool(lret < 0),
               'f1_30m_bps': f1_30m,
               'prediction': 'PREDICT_' + pred,
               'realized': None, 'fwd_3h': None, 'fwd_6h': None, 'fwd_12h': None}
        ledger.append(rec); by_onset[ou] = rec
        new_predictions.append(ou)
    if rec['realized'] is None and now >= rec['onset_ts'] + 12 * 3600:
        cls = classify(e['fwd'], e['gap0'], e['gap12'])
        if cls != 'UNCLASSIFIED':
            rec['realized'] = cls
            rec['fwd_3h'] = e['fwd'][3]; rec['fwd_6h'] = e['fwd'][6]; rec['fwd_12h'] = e['fwd'][12]
            new_realizations.append({'onset_utc': ou, 'prediction': rec['prediction'],
                                     'realized': cls})
ledger.sort(key=lambda r: r['onset_ts'])
with open(LEDGER, 'w') as fh:
    json.dump(ledger, fh, indent=1)
n_pred_res = sum(1 for r in ledger if r['prediction'] == 'PREDICT_RESOLVE' and r['realized'] == 'RESOLVE')
n_pred_con = sum(1 for r in ledger if r['prediction'] == 'PREDICT_CONTINUE' and r['realized'] == 'CONTINUE')
n_scored = sum(1 for r in ledger if r['realized'] in ('RESOLVE', 'CONTINUE'))
print(json.dumps({'self_test': 'pass', 'ledger_episodes': len(ledger),
                  'new_predictions': new_predictions, 'new_realizations': new_realizations,
                  'score': {'correct': n_pred_res + n_pred_con, 'scored': n_scored,
                            'target': 30}}))
