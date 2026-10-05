#!/usr/bin/env python3
"""Lane 6: independent raw-data audit of leadership + divergence pipelines.

Blind-first: all samples drawn (seed 7) BEFORE any reconstruction.
Independent implementation from documented definitions; no pipeline imports.
Batches:
  1. 30 leadership events from causal_tracks/events.json
  2. all 18 unique divergence episodes (11 rotation_episodes + 7 forward_ledger;
     population < 30, noted)
  3. 30 quiet controls (>=6h from any event/episode)
Plus: (a) laggard=argmin check, (c) UTC timestamp consistency.
"""
import json, glob, os, math, random
from datetime import datetime, timezone
import numpy as np

BASE = os.path.expanduser('~/workspace/goals/paper-trading-simulator/hidden_files')
TICKDIR = os.path.join(BASE, 'book_watcher', 'ticks')
OUTDIR = os.path.join(BASE, 'raw_audit')
os.makedirs(OUTDIR, exist_ok=True)
PRODS7 = ["AMP-USD", "XPL-USD", "KITE-USD", "2Z-USD", "MOG-USD", "VET-USD", "XYO-USD"]
CLUSTER = ["XPL-USD", "VET-USD", "KITE-USD", "2Z-USD"]
SEED = 7
rng = random.Random(SEED)

# ================= SAMPLE FIRST (blind) =================
events = json.load(open(BASE + '/causal_tracks/events.json'))
lead_sample = rng.sample(events, 30)
ep_hist = json.load(open(BASE + '/rotation_episodes/rotation_episodes_results.json'))['episodes']
ledger = json.load(open(BASE + '/divergence_derivative/forward_ledger.json'))
episodes = [('hist', e) for e in ep_hist] + [('ledger', e) for e in ledger]

busy = [e['t0'] for e in events]
for e in ep_hist:
    busy.append(datetime.strptime(e['onset_utc'], '%Y-%m-%d %H:%M').replace(tzinfo=timezone.utc).timestamp())
for e in ledger:
    busy.append(e['onset_ts'])
busy = np.array(busy)

results = {'seed': SEED, 'batches': {}, 'checks': {}}

# ================= raw loaders =================
day_cache = {}
def get_day(date):
    if date not in day_cache:
        rows = []
        with open(os.path.join(TICKDIR, f'ticks_{date}.jsonl')) as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
        ep = np.array([datetime.fromisoformat(r['t'].replace('Z', '+00:00')).timestamp() for r in rows])
        e0 = int(ep[0]); n = int(ep[-1] - ep[0]) + 1
        S = {p: np.full(n, np.nan) for p in PRODS7}
        for r, e in zip(rows, ep):
            i = int(e - ep[0])
            if 0 <= i < n:
                d = r.get('d', {})
                for p in PRODS7:
                    v = d.get(p)
                    if v is None:
                        continue
                    if v[0] and v[0] > 0:
                        S[p][i] = v[0]  # last write wins on dup seconds
        day_cache[date] = (e0, n, S)
    return day_cache[date]

def bars30(e0, n, S):
    nb = n // 30
    last, chg = {}, {}
    for p in PRODS7:
        m = S[p][:nb * 30].reshape(nb, 30)
        valid = np.sum(~np.isnan(m), axis=1)
        L = np.array([row[~np.isnan(row)][-1] if np.any(~np.isnan(row)) else np.nan for row in m])
        L[valid < 20] = np.nan
        last[p] = L
    for p in PRODS7:
        md = last[p]
        c = np.full(nb, np.nan)
        ok = ~np.isnan(md[1:]) & ~np.isnan(md[:-1]) & (md[:-1] > 0)
        c[1:][ok] = (md[1:][ok] / md[:-1][ok] - 1) * 1e4
        chg[p] = c
    return nb, last, chg

# ================= BATCH 1: leadership =================
b1 = []
for e in lead_sample:
    date, t0, asset, lchg = e['date'], e['t0'], e['asset'], e['lchg']
    e0, n, S = get_day(date)
    rec = {'t0': t0, 'date': date, 'claimed_asset': asset, 'claimed_lchg': lchg}
    rec['align_ok'] = ((t0 - e0) % 30 == 0)
    k = (t0 - e0) // 30
    nb, last, chg = bars30(e0, n, S)
    rec['k_in_range'] = (1 <= k <= nb - 2)
    cs = {p: chg[p][k] for p in PRODS7}
    nvalid = sum(1 for v in cs.values() if not np.isnan(v))
    rec['nvalid'] = nvalid
    if nvalid >= 7:
        lead = max(cs, key=lambda p: abs(cs[p]))
        rec['recon_leader'] = lead
        rec['recon_lchg'] = float(cs[lead])
        rec['identity_match'] = (lead == asset)
        rec['lchg_diff_bps'] = float(abs(cs[lead] - lchg))
        rec['lchg_within_tol'] = rec['lchg_diff_bps'] <= 0.05
        rec['threshold_ok'] = bool(abs(cs[lead]) >= 5.0)
        # secondary: argmax over cluster-4 only
        cs4 = {p: cs[p] for p in CLUSTER if not np.isnan(cs[p])}
        lead4 = max(cs4, key=lambda p: abs(cs4[p])) if cs4 else None
        rec['leader_argmax4'] = lead4
        rec['argmax4_agrees'] = (lead4 == lead)
    else:
        rec['identity_match'] = False; rec['note'] = 'fewer than 7 valid products'
    b1.append(rec)
results['batches']['leadership'] = {
    'n': len(b1),
    'identity_match_rate': sum(r.get('identity_match', False) for r in b1) / len(b1),
    'lchg_tol_rate': sum(r.get('lchg_within_tol', False) for r in b1) / len(b1),
    'align_ok_rate': sum(r['align_ok'] for r in b1) / len(b1),
    'records': b1,
}

with open(os.path.join(OUTDIR, 'audit_part1.json'), 'w') as f:
    json.dump(results, f, indent=1)
print('batch1 done: identity match', results['batches']['leadership']['identity_match_rate'])

# ================= 5-min bars (independent build) =================
def load_bars5():
    secs = []
    for fp in sorted(glob.glob(os.path.join(TICKDIR, 'ticks_2026-*.jsonl'))):
        with open(fp) as fh:
            for line in fh:
                d = json.loads(line)
                t = datetime.fromisoformat(d['t'].replace('Z', '+00:00')).timestamp()
                row = {}
                for a in CLUSTER:
                    v = d['d'].get(a)
                    if v is not None and v[0]:
                        row[a] = v[0]
                if len(row) == 4:
                    secs.append((t, row))
    secs.sort(key=lambda x: x[0])  # stable: file order kept on dup seconds
    bars = []
    cur = None
    for t, row in secs:
        if cur is None or t - cur[0] >= 300:
            if cur:
                bars.append(cur)
            cur = [t, dict(row)]
        else:
            for a in CLUSTER:
                cur[1][a] = row[a]
    if cur:
        bars.append(cur)
    return bars

print('building 5-min bars...', flush=True)
bars = load_bars5()
ts = np.array([b[0] for b in bars])
mid = np.array([[b[1][a] for a in CLUSTER] for b in bars])
print('bars:', len(bars), 'span_h:', (ts[-1] - ts[0]) / 3600)

def positions(mid):
    pos = np.full_like(mid, np.nan)
    for j in range(4):
        m = mid[:, j]
        for i in range(287, len(m)):
            w = m[i - 287:i + 1]
            v = w[~np.isnan(w)]
            if len(v) >= 200 and not np.isnan(m[i]):
                lo, hi = v.min(), v.max()
                pos[i, j] = (m[i] - lo) / (hi - lo) if hi > lo else 0.5
    return pos

pos = positions(mid)
valid = ~np.isnan(pos).any(axis=1)
div = np.full(len(ts), np.nan)
div[valid] = pos[valid].max(axis=1) - pos[valid].min(axis=1)
# gap-tolerant merged diverged runs (for control exclusion zones)
_state = np.zeros(len(ts), bool)
for _i in range(len(ts)):
    if valid[_i]:
        _p = pos[_i]
        _state[_i] = (_p.max() >= 0.70 and _p.min() <= 0.30)
_runs, _i = [], 0
while _i < len(ts):
    if _state[_i]:
        _j = _i
        while _j + 1 < len(ts) and _state[_j + 1]:
            _j += 1
        _runs.append((_i, _j))
        _i = _j + 1
    else:
        _i += 1
merged_runs = []
for _a, _b in _runs:
    if merged_runs and _a - merged_runs[-1][1] <= 6:
        merged_runs[-1] = (merged_runs[-1][0], _b)
    else:
        merged_runs.append((_a, _b))

# ================= BATCH 2: divergence episodes (all 18) =================
b2 = []
for src, e in episodes:
    if src == 'hist':
        target = datetime.strptime(e['onset_utc'], '%Y-%m-%d %H:%M').replace(tzinfo=timezone.utc).timestamp()
        claimed = {'leader': e['onset']['leader'], 'laggard': e['onset']['laggard'],
                   'spread': e['onset']['spread'], 'leader_pos': e['onset']['leader_pos'],
                   'laggard_pos': e['onset']['laggard_pos'], 'prediction': None,
                   'slope_in': None, 'leader_ret': None}
        oid = e['onset_utc']
    else:
        target = e['onset_ts']
        claimed = {'leader': e['leader'], 'laggard': e['laggard'], 'spread': None,
                   'leader_pos': None, 'laggard_pos': None,
                   'prediction': 'PREDICT_' + ('RESOLVE' if (e['slope_in'] < 0 and e['leader_ret_30m_bps'] < 0) else 'CONTINUE'),
                   'slope_in': e['slope_in'], 'leader_ret': e['leader_ret_30m_bps']}
        claimed['prediction_claimed'] = e['prediction']
        oid = e['onset_utc']
    i0 = int(np.argmin(np.abs(ts - target)))
    rec = {'id': oid, 'src': src, 'bar_gap_s': float(abs(ts[i0] - target))}
    has_pre = (i0 >= 6 and valid[i0 - 6])
    ok = rec['bar_gap_s'] <= 300 and valid[i0]
    rec['reconstructable'] = bool(ok)
    rec['left_censored_no_history'] = bool(ok and not has_pre)
    if ok:
        p = pos[i0]
        li, gi = int(np.argmax(p)), int(np.argmin(p))
        rec['cond_70_30'] = bool(p.max() >= 0.70 and p.min() <= 0.30)
        rec['recon_leader'] = CLUSTER[li]
        rec['recon_laggard'] = CLUSTER[gi]
        rec['leader_match'] = (CLUSTER[li] == claimed['leader'])
        rec['laggard_match'] = (CLUSTER[gi] == claimed['laggard'])
        rec['recon_spread'] = float(p.max() - p.min())
        rec['recon_leader_pos'] = float(p[li])
        rec['recon_laggard_pos'] = float(p[gi])
        if claimed['spread'] is not None:
            rec['spread_diff'] = float(abs(rec['recon_spread'] - claimed['spread']))
            rec['spread_ok'] = rec['spread_diff'] <= 0.02
            rec['leader_pos_diff'] = float(abs(rec['recon_leader_pos'] - claimed['leader_pos']))
            rec['laggard_pos_diff'] = float(abs(rec['recon_laggard_pos'] - claimed['laggard_pos']))
        if has_pre:
            s_in = float(div[i0] - div[i0 - 6])
            lret = float((mid[i0, li] / mid[i0 - 6, li] - 1) * 1e4)
            rec['recon_slope_in'] = s_in
            rec['recon_leader_ret'] = lret
            if claimed['slope_in'] is not None:
                rec['slope_diff'] = float(abs(s_in - claimed['slope_in']))
                rec['slope_ok'] = rec['slope_diff'] <= 0.01
                rec['ret_diff_bps'] = float(abs(lret - claimed['leader_ret']))
                rec['ret_ok'] = rec['ret_diff_bps'] <= 2.0
                rpred = 'PREDICT_RESOLVE' if (s_in < 0 and lret < 0) else 'PREDICT_CONTINUE'
                rec['recon_prediction'] = rpred
                rec['prediction_match'] = (rpred == claimed['prediction_claimed'])
        else:
            rec['recon_slope_in'] = None
            rec['recon_leader_ret'] = None
            rec['note'] = 'slope/ret not computable at left-censored onset (pipeline also null)'
        # (a) laggard-labeling: old bug picked by CLUSTER order; check argmin rules
        rec['laggard_is_argmin'] = rec['laggard_match']
        laggards30 = [a for a in CLUSTER if p[CLUSTER.index(a)] <= 0.30]
        first_in_order = laggards30[0] if laggards30 else None
        rec['asset_order_would_give'] = first_in_order
        rec['bug_would_matter'] = (first_in_order != CLUSTER[gi])
    b2.append(rec)

results['batches']['divergence'] = {
    'n': len(b2),
    'note': 'population is 18 unique episodes (< 30 requested); audited all',
    'cond_rate': sum(r.get('cond_70_30', False) for r in b2) / len(b2),
    'leader_match_rate': sum(r.get('leader_match', False) for r in b2) / len(b2),
    'laggard_match_rate': sum(r.get('laggard_match', False) for r in b2) / len(b2),
    'records': b2,
}
with open(os.path.join(OUTDIR, 'audit_part12.json'), 'w') as f:
    json.dump(results, f, indent=1)
print('batch2 done')

# ================= BATCH 3: controls (REDESIGNED 2026-10-05) =================
# v1 was flawed: 6h exclusion from 5317 leadership events (median gap 30s,
# max gap 34min) is impossible inside the pipeline window, forcing all
# controls into 10-03+, where the pipeline never ran. Two well-posed types:
#   3a. divergence-controls (15): random 5-min bars >=6h from any of the 18
#       episode onsets -> expect NO 70/30 divergence.
#   3b. leadership-controls (15): random 30s bars in pipeline window with no
#       event in events.json -> expect pipeline rule + cluster filter gives
#       no event: NOT (nvalid>=7 AND argmax|chg|>=5 AND argmax in CLUSTER).
ep_onsets = np.array(sorted(
    [datetime.strptime(e['onset_utc'], '%Y-%m-%d %H:%M').replace(tzinfo=timezone.utc).timestamp()
     for e in ep_hist] + [e['onset_ts'] for e in ledger]))
event_t0s = set(e['t0'] for e in events)

b3a, seen3a, tries = [], set(), 0
def in_episode_exclusion(t):
    # exclude [run_start - 6h, run_end + 6h] for every reconstructed merged run
    # (batch 2 verifies these runs match pipeline onsets; v1 wrongly excluded
    # only 6h from onsets, admitting controls inside long episode spans)
    for _a, _b in merged_runs:
        if ts[_a] - 6 * 3600 <= t <= ts[_b] + 6 * 3600:
            return True
    return False
while len(b3a) < 15 and tries < 20000:
    tries += 1
    i = rng.randrange(300, len(ts) - 13)
    t = float(ts[i])
    if t in seen3a:
        continue
    if in_episode_exclusion(t):
        continue
    seen3a.add(t)
    p = pos[i]
    divg = bool(p.max() >= 0.70 and p.min() <= 0.30)
    b3a.append({'bar_start_utc': datetime.fromtimestamp(t, timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),
                'diverged_70_30': divg, 'quiet': bool(not divg and valid[i])})

day30_cache = {}
def get_chg(date):
    if date not in day30_cache:
        e0, n, S = get_day(date)
        nb, last, chg = bars30(e0, n, S)
        day30_cache[date] = (e0, nb, chg)
    return day30_cache[date]

b3b, used3b, tries = [], set(), 0
while len(b3b) < 15 and tries < 40000:
    tries += 1
    date = rng.choice(['2026-09-29', '2026-09-30', '2026-10-01', '2026-10-02'])
    e0, nb, chg = get_chg(date)
    k = rng.randrange(1, nb - 1)
    t = e0 + k * 30
    if t in event_t0s or t in used3b:
        continue
    used3b.add(t)
    cs = {p: chg[p][k] for p in PRODS7}
    nvalid = sum(1 for v in cs.values() if not np.isnan(v))
    # full pipeline rule incl. downstream filter (build_tracks.py:197-207):
    # cluster leader AND edge trim (need 1800s baseline history; 30s headroom)
    e0d, nd, _ = get_day(date)
    iT = t - e0d
    edge_ok = (iT - 1800 >= 0) and (iT + 30 < nd)
    rec = {'t0_utc': datetime.fromtimestamp(t, timezone.utc).strftime('%Y-%m-%d %H:%M:%S'),
           'nvalid': nvalid, 'edge_ok': bool(edge_ok)}
    if nvalid >= 7:
        lead = max(cs, key=lambda p: abs(cs[p]))
        rec['recon_argmax'] = lead
        rec['recon_max_abs_chg'] = float(abs(cs[lead]))
        rec['rule_fires_cluster'] = bool(abs(cs[lead]) >= 5.0 and lead in CLUSTER and edge_ok)
    else:
        rec['recon_argmax'] = None
        rec['recon_max_abs_chg'] = None
        rec['rule_fires_cluster'] = False
    rec['match'] = bool(not rec['rule_fires_cluster'])
    if not rec['match']:
        rec['note'] = 'reconstruction says event should exist but none in events.json'
    b3b.append(rec)

results['batches']['controls'] = {
    'design': 'redesigned 2026-10-05 after v1 flaw (see findings)',
    'divergence_controls': {'n': len(b3a),
                            'quiet_rate': sum(r['quiet'] for r in b3a) / len(b3a) if b3a else 0,
                            'records': b3a},
    'leadership_controls': {'n': len(b3b),
                            'match_rate': sum(r['match'] for r in b3b) / len(b3b) if b3b else 0,
                            'records': b3b},
}

# ================= CHECK (c): UTC timestamp consistency =================
c_leader = {'n': len(events), 'align_ok': 0, 'misaligned': []}
for e in events:
    e0, _, _ = get_day(e['date'])
    if (e['t0'] - e0) % 30 == 0:
        c_leader['align_ok'] += 1
    elif len(c_leader['misaligned']) < 5:
        c_leader['misaligned'].append((e['date'], e['t0']))
c_ledger = {'n': len(ledger), 'ts_match': 0, 'mismatches': []}
for e in ledger:
    s = datetime.fromtimestamp(e['onset_ts'], timezone.utc).strftime('%Y-%m-%d %H:%M')
    if s == e['onset_utc']:
        c_ledger['ts_match'] += 1
    else:
        c_ledger['mismatches'].append((e['onset_utc'], s))
c_hist = {'n': len(ep_hist), 'parse_ok': 0}
for e in ep_hist:
    try:
        datetime.strptime(e['onset_utc'], '%Y-%m-%d %H:%M').replace(tzinfo=timezone.utc)
        c_hist['parse_ok'] += 1
    except Exception:
        pass
results['checks']['utc_consistency'] = {
    'leadership_30s_alignment': c_leader,
    'ledger_onset_ts_vs_string': c_ledger,
    'hist_onset_parse': c_hist,
}

with open(os.path.join(OUTDIR, 'audit_results.json'), 'w') as f:
    json.dump(results, f, indent=1)
print('batch3 done; div quiet', results['batches']['controls']['divergence_controls']['quiet_rate'],
      'lead match', results['batches']['controls']['leadership_controls']['match_rate'])
print('ALL DONE')
