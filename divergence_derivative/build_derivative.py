#!/usr/bin/env python3
"""Divergence-derivative branch prediction.

Follow-up to rotation_episodes. Hypothesis: at divergence onset (T0), the
DERIVATIVE of divergence (expanding vs exhausted/contracting) predicts whether
an episode RESOLVEs (violent convergence) or CONTINUES (violent continuation).

Reuses the 11 independent episodes from rotation_episodes_results.json
(same onsets, same frozen onset leader/laggard). Captures 12 observables at
T-60m / T-30m / T0 / T+30m from the 1s tick lane.

SIGNED FLOW CAVEAT (documented 2026-10-03): Coinbase Advanced Trade
market_trades `side` labels the MAKER side, not the aggressive side
(verified: "buy"-labeled flow coincided with price down ~-3.3bps).
Tick-lane buy_n/sell_n inherit this inversion. Reported signed flow is
labeled `signrep_*` and treated as SIGN-UNCERTAIN. Unsigned flow is safe.
"""
import json, glob, os, math, random
from datetime import datetime, timezone
import numpy as np

BASE = os.path.expanduser('~/workspace/goals/paper-trading-simulator/hidden_files')
TICKDIR = os.path.join(BASE, 'book_watcher', 'ticks')
OUTDIR = os.path.join(BASE, 'divergence_derivative')
EPJSON = os.path.join(BASE, 'rotation_episodes', 'rotation_episodes_results.json')
ASSETS = ['XPL-USD', 'VET-USD', 'KITE-USD', '2Z-USD']
BAR = 300
W = 288
MINW = 200
GAP_TOL = 6
# F1 loadings from latent_heartbeat step2 (sign arbitrary; residual magnitude is sign-invariant)
F1W = np.array([-0.6104, -0.6097, -0.3704, -0.3392])  # XPL, VET, KITE, 2Z
F1W = F1W / np.linalg.norm(F1W)

FIELDS = ['mid', 'spr', 'imb5', 'bb_px', 'bb_sz', 'ba_px', 'ba_sz', 'trn', 'buyn', 'selln']

# ---------------- load 1s rows (all fields, 4 assets present) ----------------
files = sorted(glob.glob(os.path.join(TICKDIR, 'ticks_2026-*.jsonl')))
secs_t = []
sec_dat = {a: [] for a in ASSETS}  # a -> list of 10-field lists
for f in files:
    with open(f) as fh:
        for line in fh:
            d = json.loads(line)
            t = datetime.fromisoformat(d['t'].replace('Z', '+00:00')).timestamp()
            row = d['d']
            ok = True
            vals = {}
            for a in ASSETS:
                v = row.get(a)
                if v is None or not v[0] or len(v) < 10:
                    ok = False
                    break
                vals[a] = v
            if not ok:
                continue
            secs_t.append(t)
            for a in ASSETS:
                sec_dat[a].append(vals[a])
order = np.argsort(secs_t)
T = np.array(secs_t)[order]
D = {}
for j, a in enumerate(ASSETS):
    arr = np.array(sec_dat[a])[order]
    D[a] = {f: arr[:, k].astype(float) for k, f in enumerate(FIELDS)}
N = len(T)
print(f'1s rows (all 4 assets): {N}, span {(T[-1]-T[0])/3600:.1f}h')

# ---------------- 5-min bar grid (identical construction to build_episodes) ----------------
bar_t0 = []   # first-second timestamp per bar
bar_slice = []  # (sec_i0, sec_i1) inclusive slice
cur0 = None
s0 = 0
for s in range(N):
    t = T[s]
    if cur0 is None or t - cur0 >= BAR:
        if cur0 is not None:
            bar_t0.append(cur0)
            bar_slice.append((s0, s - 1))
        cur0 = t
        s0 = s
bar_t0.append(cur0)
bar_slice.append((s0, N - 1))
ts = np.array(bar_t0)
nb = len(ts)
mid = np.full((nb, 4), np.nan)
medspr = np.full((nb, 4), np.nan)
trsum = np.full((nb, 4), np.nan)
for i, (a0, a1) in enumerate(bar_slice):
    for j, a in enumerate(ASSETS):
        m = D[a]['mid'][a0:a1 + 1]
        mid[i, j] = m[-1]
        medspr[i, j] = np.median(D[a]['spr'][a0:a1 + 1])
        trsum[i, j] = np.sum(D[a]['trn'][a0:a1 + 1])
print(f'5-min bars: {nb}')

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

pos = positions(mid)
valid = ~np.isnan(pos).any(axis=1)
div = np.full(nb, np.nan)
for i in range(nb):
    if valid[i]:
        div[i] = pos[i].max() - pos[i].min()

# ---------------- F1 score + betas (1s) ----------------
logmid = {}
for a in ASSETS:
    m = D[a]['mid']
    with np.errstate(divide='ignore', invalid='ignore'):
        logmid[a] = np.log(m)
R = np.full((N, 4), np.nan)
for j, a in enumerate(ASSETS):
    r = np.diff(logmid[a], prepend=np.nan)
    R[:, j] = r * 1e4  # bps per second
f1r = R @ F1W  # bps/sec, NaN-aware? diff gives NaN only at 0
f1r[0] = np.nan
betas = {}
for j, a in enumerate(ASSETS):
    x = f1r[1:]
    y = R[1:, j]
    msk = ~(np.isnan(x) | np.isnan(y))
    if msk.sum() > 1000:
        betas[a] = float(np.cov(y[msk], x[msk])[0, 1] / np.var(x[msk]))
    else:
        betas[a] = float('nan')
print('betas:', {a: round(b, 3) for a, b in betas.items()})
RES = np.full((N, 4), np.nan)  # idiosyncratic residual, bps/sec
for j, a in enumerate(ASSETS):
    RES[:, j] = R[:, j] - betas[a] * f1r

# ---------------- episodes (reuse, do not redefine) ----------------
epdata = json.load(open(EPJSON))
episodes = epdata['episodes']
print(f'episodes reused: {len(episodes)}')
for e in episodes:
    e['_li'] = ASSETS.index(e['onset']['leader'])
    e['_gi'] = ASSETS.index(e['onset']['laggard'])

def sec_window(tau, half=150):
    """boolean mask of 1s rows within [tau-half, tau+half]."""
    return (T >= tau - half) & (T <= tau + half)

def med_mid(a, tau, half=60):
    m = sec_window(tau, half)
    v = D[a]['mid'][m]
    v = v[v > 0]
    return float(np.median(v)) if len(v) else float('nan')

def bar_at(tau):
    """index of bar containing tau (largest i with ts[i] <= tau)."""
    i = int(np.searchsorted(ts, tau, side='right')) - 1
    return i if 0 <= i < nb else None

def snap(ep, tau):
    """All 12 observables at snapshot time tau. Returns dict (NaN where unavailable)."""
    li, gi = ep['_li'], ep['_gi']
    L, G = ASSETS[li], ASSETS[gi]
    out = {}
    m = sec_window(tau, 150)
    nsec = int(m.sum())
    out['nsec'] = nsec
    if nsec < 30:
        return None
    b = bar_at(tau)
    out['div'] = float(div[b]) if b is not None and not np.isnan(div[b]) else float('nan')
    for tag, a, j in (('L', L, li), ('G', G, gi)):
        spr = D[a]['spr'][m]
        out[f'spr_{tag}'] = float(np.median(spr))
        tr = D[a]['trn'][m]
        out[f'trrate_{tag}'] = float(np.sum(tr) / 300.0)
        out[f'unsigned_{tag}'] = float(np.sum(tr))
        out[f'signrep_{tag}'] = float(np.sum(D[a]['buyn'][m] - D[a]['selln'][m]))  # SIGN-UNCERTAIN
        md = D[a]['mid'][m]
        chg = np.zeros(len(md), dtype=bool)
        chg[1:] = md[1:] != md[:-1]
        out[f'qint_{tag}'] = float(chg.mean())  # proxy: frac of seconds with mid change
        dep = D[a]['bb_sz'][m] * D[a]['bb_px'][m] + D[a]['ba_sz'][m] * D[a]['ba_px'][m]
        out[f'depth_{tag}'] = float(np.median(dep[dep > 0])) if (dep > 0).any() else float('nan')
        out[f'imb_{tag}'] = float(np.median(D[a]['imb5'][m]))
        rs = np.abs(RES[m, j])
        out[f'resid_{tag}'] = float(np.nanmean(rs))
    # 4-asset mean quote intensity
    qis = []
    for a in ASSETS:
        md = D[a]['mid'][m]
        chg = np.zeros(len(md), dtype=bool)
        chg[1:] = md[1:] != md[:-1]
        qis.append(chg.mean())
    out['qint_mean'] = float(np.mean(qis))
    return out

def subret(a, t1, t2):
    m1, m2 = med_mid(a, t1), med_mid(a, t2)
    if m1 != m1 or m2 != m2 or m1 <= 0 or m2 <= 0:
        return float('nan')
    return float((math.log(m2) - math.log(m1)) * 1e4)

def f1move(t1, t2):
    m = (T >= t1) & (T <= t2)
    v = f1r[m]
    v = v[~np.isnan(v)]
    return float(np.nansum(v)) if len(v) else float('nan')

def volsec(a, t1, t2):
    m = (T >= t1) & (T <= t2)
    j = ASSETS.index(a)
    v = R[m, j]
    v = v[~np.isnan(v)]
    return float(np.nanstd(v)) if len(v) >= 60 else float('nan')

for e in episodes:
    T0 = ts[e['onset_idx']]
    e['T0'] = T0
    anchors = {'Tm60': T0 - 3600, 'Tm30': T0 - 1800, 'T0': T0, 'Tp30': T0 + 1800}
    e['snap'] = {}
    for k, tau in anchors.items():
        e['snap'][k] = snap(e, tau)
    L = e['onset']['leader']
    G = e['onset']['laggard']
    e['sub'] = {}
    for tag, a in (('L', L), ('G', G)):
        e['sub'][f'ret_{tag}_m60_m30'] = subret(a, T0 - 3600, T0 - 1800)
        e['sub'][f'ret_{tag}_m30_0'] = subret(a, T0 - 1800, T0)
        e['sub'][f'ret_{tag}_0_p30'] = subret(a, T0, T0 + 1800)
        e['sub'][f'vol_{tag}_m60_m30'] = volsec(a, T0 - 3600, T0 - 1800)
        e['sub'][f'vol_{tag}_m30_0'] = volsec(a, T0 - 1800, T0)
        e['sub'][f'vol_{tag}_0_p30'] = volsec(a, T0, T0 + 1800)
    e['sub']['f1_m60_m30'] = f1move(T0 - 3600, T0 - 1800)
    e['sub']['f1_m30_0'] = f1move(T0 - 1800, T0)
    e['sub']['f1_0_p30'] = f1move(T0, T0 + 1800)
    # divergence slopes (bar-level, per 30m)
    def div_at(tau):
        b = bar_at(tau)
        return div[b] if b is not None and not np.isnan(div[b]) else float('nan')
    d_m60, d_m30, d_0, d_p30 = div_at(T0 - 3600), div_at(T0 - 1800), div_at(T0), div_at(T0 + 1800)
    e['slope'] = {
        'div_m60': d_m60, 'div_m30': d_m30, 'div_0': d_0, 'div_p30': d_p30,
        'slope_pre': d_m30 - d_m60,      # [T-60,T-30]
        'slope_in': d_0 - d_m30,         # [T-30,T0]  <- the onset derivative
        'slope_post': d_p30 - d_0,       # [T0,T+30]
        'slope_accel': (d_0 - d_m30) - (d_m30 - d_m60),
        'sign_in': int(np.sign(d_0 - d_m30)) if (d_0 - d_m30) == (d_0 - d_m30) else None,
    }

print('snapshots done')

# ---------------- class comparison ----------------
def med(xs):
    xs = [x for x in xs if x is not None and x == x]
    return float(np.median(xs)) if xs else float('nan')

def mad(xs):
    xs = [x for x in xs if x is not None and x == x]
    if not xs:
        return float('nan')
    m = np.median(xs)
    return float(np.median([abs(x - m) for x in xs]))

RES = [e for e in episodes if e['class'] == 'RESOLVE']
CON = [e for e in episodes if e['class'] == 'CONTINUE']
LIVE = [e for e in episodes if e['class'] == 'UNCLASSIFIED']
CLS = RES + CON

# observable registry: (key, getter(ep))
OBS = []
for stamp in ['Tm60', 'Tm30', 'T0', 'Tp30']:
    OBS.append((f'div@{stamp}', lambda e, s=stamp: e['snap'][s]['div'] if e['snap'][s] else float('nan')))
for tag in ['L', 'G']:
    for w in ['m60_m30', 'm30_0', '0_p30']:
        OBS.append((f'ret_{tag}_{w}', lambda e, t=tag, x=w: e['sub'][f'ret_{t}_{x}']))
        OBS.append((f'vol_{tag}_{w}', lambda e, t=tag, x=w: e['sub'][f'vol_{t}_{x}']))
    for stamp in ['Tm60', 'Tm30', 'T0', 'Tp30']:
        for base in ['spr', 'trrate', 'unsigned', 'signrep', 'qint', 'depth', 'imb', 'resid']:
            OBS.append((f'{base}_{tag}@{stamp}',
                        lambda e, b=base, t=tag, s=stamp: e['snap'][s][f'{b}_{t}'] if e['snap'][s] else float('nan')))
OBS.append(('qint_mean@T0', lambda e: e['snap']['T0']['qint_mean'] if e['snap']['T0'] else float('nan')))
for w in ['m60_m30', 'm30_0', '0_p30']:
    OBS.append((f'f1_{w}', lambda e, x=w: e['sub'][f'f1_{x}']))
for k in ['slope_pre', 'slope_in', 'slope_post', 'slope_accel', 'div_0']:
    OBS.append((k, lambda e, x=k: e['slope'][x]))
# vol acceleration ratios
for tag in ['L', 'G']:
    OBS.append((f'volaccel_{tag}_in_vs_pre',
                lambda e, t=tag: (e['sub'][f'vol_{t}_m30_0'] / e['sub'][f'vol_{t}_m60_m30']
                                 if e['sub'][f'vol_{t}_m60_m30'] not in (None, 0)
                                 and e['sub'][f'vol_{t}_m60_m30'] == e['sub'][f'vol_{t}_m60_m30']
                                 else float('nan'))))
    OBS.append((f'volaccel_{tag}_post_vs_in',
                lambda e, t=tag: (e['sub'][f'vol_{t}_0_p30'] / e['sub'][f'vol_{t}_m30_0']
                                 if e['sub'][f'vol_{t}_m30_0'] not in (None, 0)
                                 and e['sub'][f'vol_{t}_m30_0'] == e['sub'][f'vol_{t}_m30_0']
                                 else float('nan'))))

comp = {}
for key, fn in OBS:
    rv = [fn(e) for e in RES]
    cv = [fn(e) for e in CON]
    allv = [fn(e) for e in CLS]
    m = mad(allv)
    mr, mc = med(rv), med(cv)
    sep = abs(mr - mc) / m if m and m == m and m > 0 else float('nan')
    comp[key] = {'resolve_median': mr, 'continue_median': mc,
                 'n_r': sum(1 for x in rv if x == x), 'n_c': sum(1 for x in cv if x == x),
                 'separation': float(sep) if sep == sep else float('nan'),
                 'resolve_values': [round(x, 4) if x == x else None for x in rv],
                 'continue_values': [round(x, 4) if x == x else None for x in cv]}
ranking = sorted([(k, v['separation']) for k, v in comp.items() if v['separation'] == v['separation']],
                 key=lambda x: -x[1])

print('\n=== CORE: onset slope_in (div(T0)-div(T-30m)) ===')
print('RESOLVE slope_in values:', comp['slope_in']['resolve_values'])
print('CONTINUE slope_in values:', comp['slope_in']['continue_values'])
print('median R vs C:', comp['slope_in']['resolve_median'], comp['slope_in']['continue_median'])
print('\n=== top-15 separation ranking ===')
for k, s in ranking[:15]:
    print(f'{s:5.2f}  {k:28s} R={comp[k]["resolve_median"]:.4g}  C={comp[k]["continue_median"]:.4g}')

# ---------------- consistency vs the qualitative pattern ----------------
# pattern: exhausted/contracting + quiet tape -> RESOLVE; expanding + active tape -> CONTINUE
def direction_ok(key, expect_R_lower):
    v = comp[key]
    mr, mc = v['resolve_median'], v['continue_median']
    if mr != mr or mc != mc:
        return 'missing'
    if abs(mr - mc) < 1e-12:
        return 'tie'
    r_lower = mr < mc
    return 'supports' if r_lower == expect_R_lower else 'contradicts'

consistency = {
    # R should be LOWER (more contracting / quieter) than C
    'slope_in': direction_ok('slope_in', True),
    'slope_pre': direction_ok('slope_pre', True),
    'slope_accel': direction_ok('slope_accel', True),
    'div_0': direction_ok('div_0', True),
    'qint_mean@T0': direction_ok('qint_mean@T0', True),
    'qint_L@T0': direction_ok('qint_L@T0', True),
    'qint_G@T0': direction_ok('qint_G@T0', True),
    'trrate_L@T0': direction_ok('trrate_L@T0', True),
    'trrate_G@T0': direction_ok('trrate_G@T0', True),
    'vol_L_m30_0': direction_ok('vol_L_m30_0', True),
    'vol_G_m30_0': direction_ok('vol_G_m30_0', True),
    'resid_L@T0': direction_ok('resid_L@T0', True),
    'resid_G@T0': direction_ok('resid_G@T0', True),
    # R should be HIGHER (leader already fading / laggard bouncing into onset)
    'ret_L_m30_0': direction_ok('ret_L_m30_0', True),   # leader return into onset: R lower (fading)
    'ret_G_m30_0': direction_ok('ret_G_m30_0', False),  # laggard return into onset: R higher (bouncing)
}
print('\n=== consistency ===')
for k, v in consistency.items():
    print(f'{v:11s} {k}')

# ---------------- null: does onset slope separate shuffled classes? ----------------
def detect_null(mid_s):
    # copied detection (bar-level only); positions + runs + classification
    pos_s = np.full_like(mid_s, np.nan)
    for j in range(4):
        mm = mid_s[:, j]
        for i in range(W - 1, len(mm)):
            w = mm[i - W + 1:i + 1]
            v = w[~np.isnan(w)]
            if len(v) >= MINW and not np.isnan(mm[i]):
                lo, hi = v.min(), v.max()
                pos_s[i, j] = (mm[i] - lo) / (hi - lo) if hi > lo else 0.5
    n = len(mid_s)
    valid_s = ~np.isnan(pos_s).any(axis=1)
    state = np.zeros(n, dtype=bool)
    for i in range(n):
        if valid_s[i]:
            p = pos_s[i]
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
        if merged and i0 - merged[-1][1] <= GAP_TOL:
            merged[-1] = (merged[-1][0], i1)
        else:
            merged.append((i0, i1))
    out = []
    for (i0, i1) in merged:
        p0 = pos_s[i0]
        li = int(np.argmax(p0)); gi = int(np.argmin(p0))
        gap0 = float(p0[li] - p0[gi])
        fwd = {}
        for h, bnn in [(3, 36), (6, 72), (12, 144)]:
            ie = i0 + bnn
            if ie < n and not np.isnan(mid_s[i0, li]) and not np.isnan(mid_s[ie, li]) \
               and not np.isnan(mid_s[i0, gi]) and not np.isnan(mid_s[ie, gi]):
                fwd[h] = float((mid_s[ie, gi] / mid_s[i0, gi] - 1) * 1e4 -
                               (mid_s[ie, li] / mid_s[i0, li] - 1) * 1e4)
            else:
                fwd[h] = None
        ie12 = i0 + 144
        gap12 = float(pos_s[ie12, li] - pos_s[ie12, gi]) if ie12 < n and valid_s[ie12] else None
        f6, f12 = fwd[6], fwd[12]
        if f12 is None:
            cls = 'UNCLASSIFIED'
        elif f12 < -100 or (gap12 is not None and gap12 > gap0):
            cls = 'CONTINUE'
        elif f6 is not None and f6 > 0 and f12 >= -100:
            cls = 'RESOLVE'
        else:
            cls = 'AMBIGUOUS'
        # onset slope: div(onset bar) - div(onset bar - 6)
        ib = i0 - 6
        slope_in = None
        if ib >= 0 and valid_s[ib] and valid_s[i0]:
            slope_in = float((pos_s[i0].max() - pos_s[i0].min()) - (pos_s[ib].max() - pos_s[ib].min()))
        out.append((cls, slope_in))
    return out

random.seed(20261003)
obs_sep = comp['slope_in']['continue_median'] - comp['slope_in']['resolve_median']
null_seps = []
null_empty = 0
N_NULL = 50
for rep in range(N_NULL):
    m2 = mid.copy()
    for j in range(4):
        m2[:, j] = np.roll(m2[:, j], random.randrange(nb))
    eps = detect_null(m2)
    rs = [s for c, s in eps if c == 'RESOLVE' and s is not None]
    cs = [s for c, s in eps if c == 'CONTINUE' and s is not None]
    if not rs or not cs:
        null_empty += 1
        continue
    null_seps.append(float(np.median(cs)) - float(np.median(rs)))
null_seps = [x for x in null_seps if x == x]
print(f'\nnull: {N_NULL} reps, {null_empty} with empty class, observed separation={obs_sep:.4f}')
if null_seps:
    print(f'null separation: mean {np.mean(null_seps):.4f} median {np.median(null_seps):.4f} '
          f'p5-p95 [{np.percentile(null_seps,5):.4f},{np.percentile(null_seps,95):.4f}]')
    print(f'fraction of null seps >= observed: {np.mean([x >= obs_sep for x in null_seps]):.3f}')

results = {
    'meta': {
        'assets': ASSETS,
        'episodes_reused_from': 'rotation_episodes/rotation_episodes_results.json',
        'n_episodes': len(episodes),
        'snapshot_times': ['T-60m', 'T-30m', 'T0', 'T+30m'],
        'window_half_s': 150,
        'f1': 'PCA loadings from latent_heartbeat (XPL -0.610, VET -0.610, KITE -0.370, 2Z -0.339), betas by full-sample regression',
        'betas': {a: round(b, 4) for a, b in betas.items()},
        'qint_proxy': 'fraction of seconds in +/-150s window with mid != previous second mid',
        'signed_flow_caveat': ('signrep_* = sum(buy_n - sell_n) as REPORTED; Coinbase Advanced Trade '
                               'market_trades side labels the MAKER side (verified 2026-10-03), so the '
                               'aggressive-side sign is INVERTED/uncertain. Unsigned flow is safe.'),
        'small_n': 'RESOLVE n=2, CONTINUE n=8: all class comparisons are descriptive; cannot establish a classifier.',
    },
    'episodes': [
        {'onset_utc': e['onset_utc'], 'class': e['class'],
         'leader': e['onset']['leader'], 'laggard': e['onset']['laggard'],
         'left_censored': e['left_censored'],
         'slope': {k: (round(v, 5) if isinstance(v, float) and v == v else v) for k, v in e['slope'].items()},
         'sub': {k: (round(v, 3) if isinstance(v, float) and v == v else None) for k, v in e['sub'].items()},
         'snap': {s: ({k: (round(v, 4) if isinstance(v, float) and v == v else None)
                            for k, v in snapd.items()} if snapd else None)
                  for s, snapd in e['snap'].items()}}
        for e in episodes
    ],
    'class_comparison': comp,
    'separation_ranking': [(k, round(s, 3)) for k, s in ranking],
    'consistency': consistency,
    'core_result': {
        'slope_in_resolve_values': comp['slope_in']['resolve_values'],
        'slope_in_continue_values': comp['slope_in']['continue_values'],
        'slope_in_resolve_median': comp['slope_in']['resolve_median'],
        'slope_in_continue_median': comp['slope_in']['continue_median'],
        'observed_separation_C_minus_R': float(obs_sep) if obs_sep == obs_sep else None,
    },
    'null': {
        'method': 'circular-shift each asset 1s mid series independently, rebuild bars, re-detect, 50 reps',
        'n_reps': N_NULL, 'n_empty_class': null_empty,
        'separation_mean': float(np.mean(null_seps)) if null_seps else None,
        'separation_median': float(np.median(null_seps)) if null_seps else None,
        'separation_p5_p95': [float(np.percentile(null_seps, 5)), float(np.percentile(null_seps, 95))] if null_seps else None,
        'frac_null_ge_observed': float(np.mean([x >= obs_sep for x in null_seps])) if null_seps and obs_sep == obs_sep else None,
    },
}
with open(os.path.join(OUTDIR, 'divergence_derivative_results.json'), 'w') as fh:
    json.dump(results, fh, indent=1)
print('wrote divergence_derivative_results.json')
