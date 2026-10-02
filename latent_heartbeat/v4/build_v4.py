#!/usr/bin/env python3
"""
Latent market-state experiment v4 — Mike's approved spec.

Conceptual shift from v3 (observations -> common factor, PCA covariance) to
past observations -> estimated state -> future observations (state dynamics).
Key measurement: decay of predictive information R^2(h) over horizons
h in {30, 60, 120, 300} s, at 30-second bar resolution.

Design (all frozen before OOS):
- 4 observable panels from the same 1s tick files, aggregated to 30s bars:
    (a) 30s log-mid returns
    (b) 30s spread-bps changes
    (c) 30s top-of-book imbalance (imb5) changes
    (d) 30s trade-flow imbalance = (buy_n - sell_n)/(buy_n + sell_n)
- Split: train = Sep 29..Oct 1, test = Oct 2. Train-only z-score normalization.
- k (factors) chosen on TRAIN scree before any OOS evaluation.
- Leave-one-out factor scores everywhere in predictive steps (v3 leakage lesson).
- Dynamics: F_{t+1} = phi * F_t + e  (state persistence, phi reported).
- Per asset, per horizon: r_i(t+h) = a + b*F_t + g*r_i(t-lags) + e, params frozen from train.
- Controls on frozen test: AR own-past, factor-only, AR+factor, SHUFFLED-factor,
  TIME-SHIFTED-factor (large circular shift). Shifted control preserves factor
  autocorrelation but breaks synchronization -> discriminates "synchronized state"
  from "persistent covariance".
- Cross-panel: factor computed separately per panel; compare scores across
  observables, test trade-flow -> imbalance -> mid temporal ordering.
- FALSIFICATION BOUNDARY: F1 in mid-price only (absent from flow panels) =>
  QUOTE COORDINATION, not latent market state. Same component across panels
  with flow->imbalance->mid ordering => state interpretation strengthens.
- ws_leaders.json is read ONLY in the final blind-comparison step.

Outputs: v4_results.json, v4_findings.md in this directory.
"""
import json, glob, os
from datetime import datetime, timezone
import numpy as np

rng = np.random.default_rng(20261002)

BASE = os.path.expanduser('~/workspace/goals/paper-trading-simulator/hidden_files/book_watcher')
TICKDIR = os.path.join(BASE, 'ticks')
OUTDIR = os.path.dirname(os.path.abspath(__file__))
PRODS = ['AMP-USD','XPL-USD','KITE-USD','2Z-USD','MOG-USD','VET-USD','XYO-USD']
P = len(PRODS)
BAR = 30                      # seconds per bar
TRAIN_END = datetime(2026,10,2,0,0,0,tzinfo=timezone.utc).timestamp()
HORIZONS = [1, 2, 4, 10]       # bars = 30,60,120,300 s
AR_ORDER = 5
SHIFT_BARS = 720              # 6 h circular shift for time-shifted control

res = {'version': 4, 'design': 'past obs -> state -> future obs; R2(h) decay; controls incl. shuffled & time-shifted factor',
       'panels': ['log-mid returns', 'spread-bps changes', 'imb5 changes', 'trade-flow imbalance']}

# ---------------- 0. load 1s data ----------------
files = sorted(glob.glob(os.path.join(TICKDIR, 'ticks_2026-*.jsonl')))
T1 = 0
ts_list, mid_list, spr_list, imb_list, buy_list, sell_list = [], [], [], [], [], []
for f in files:
    with open(f) as fh:
        for line in fh:
            r = json.loads(line)
            t = datetime.fromisoformat(r['t'].replace('Z','+00:00')).timestamp()
            ts_list.append(t)
            d = r['d']
            m, s, im, b, sl = [], [], [], [], []
            for p in PRODS:
                v = d[p]
                if v is None:
                    m.append(np.nan); s.append(np.nan); im.append(np.nan); b.append(np.nan); sl.append(np.nan)
                else:
                    m.append(v[0]); s.append(v[1]); im.append(v[2]); b.append(v[8]); sl.append(v[9])
            mid_list.append(m); spr_list.append(s); imb_list.append(im); buy_list.append(b); sell_list.append(sl)
ts = np.array(ts_list)
mid = np.array(mid_list); spr = np.array(spr_list); imb = np.array(imb_list)
buy = np.array(buy_list); sell = np.array(sell_list)
T1 = len(ts)
res['coverage'] = {'rows_1s': T1,
                   'span_from': datetime.fromtimestamp(ts[0], timezone.utc).isoformat(),
                   'span_to': datetime.fromtimestamp(ts[-1], timezone.utc).isoformat()}

# ---------------- 1. aggregate to 30s bars ----------------
# bar index anchored at epoch
bidx = np.floor(ts / BAR).astype(np.int64)
ubars = np.unique(bidx)
B = len(ubars)
bmap = {b: k for k, b in enumerate(ubars)}
bts = ubars * BAR  # bar start epoch

bar_mid = np.full((B, P), np.nan)   # last non-NaN mid in bar
bar_spr = np.full((B, P), np.nan)   # mean spread bps in bar
bar_imb = np.full((B, P), np.nan)   # mean imb5 in bar
bar_buy = np.zeros((B, P)); bar_sell = np.zeros((B, P)); bar_has = np.zeros((B, P), dtype=bool)
for i in range(T1):
    k = bmap[bidx[i]]
    for j in range(P):
        if not np.isnan(mid[i, j]):
            bar_mid[k, j] = mid[i, j]          # last wins
        if not np.isnan(spr[i, j]):
            # accumulate mean via running sum
            pass
# second pass for means (simpler and correct)
for k, b in enumerate(ubars):
    sel = (bidx == b)
    for j in range(P):
        v = spr[sel, j]; v = v[~np.isnan(v)]
        if len(v): bar_spr[k, j] = v.mean()
        v = imb[sel, j]; v = v[~np.isnan(v)]
        if len(v): bar_imb[k, j] = v.mean()
        bv = buy[sel, j]; sv = sell[sel, j]
        ok = ~(np.isnan(bv) | np.isnan(sv))
        if ok.any():
            bar_buy[k, j] = np.nansum(bv); bar_sell[k, j] = np.nansum(sv); bar_has[k, j] = True

# bar-to-bar changes (NaN-aware, no lookahead: bar k uses only bars <= k)
ret = np.full((B, P), np.nan)   # log-mid returns
dsp = np.full((B, P), np.nan)   # spread changes
dim = np.full((B, P), np.nan)   # imbalance changes
tfi = np.full((B, P), np.nan)   # trade-flow imbalance
for j in range(P):
    m = bar_mid[:, j]
    ok = ~(np.isnan(m[:-1]) | np.isnan(m[1:]))
    ret[1:, j][ok] = np.log(m[1:][ok] / m[:-1][ok])
    s = bar_spr[:, j]
    ok = ~(np.isnan(s[:-1]) | np.isnan(s[1:]))
    dsp[1:, j][ok] = s[1:][ok] - s[:-1][ok]
    im = bar_imb[:, j]
    ok = ~(np.isnan(im[:-1]) | np.isnan(im[1:]))
    dim[1:, j][ok] = im[1:][ok] - im[:-1][ok]
    tot = bar_buy[:, j] + bar_sell[:, j]
    ok = bar_has[:, j] & (tot > 0)
    tfi[ok, j] = (bar_buy[ok, j] - bar_sell[ok, j]) / tot[ok]

panels = {'ret': ret, 'spr': dsp, 'imb': dim, 'tfi': tfi}
res['bar_coverage'] = {name: {'bars': B, 'nan_frac': round(float(np.isnan(X).mean()), 4)}
                       for name, X in panels.items()}
res['bar_coverage']['n_bars'] = B
res['bar_coverage']['bar_span_from'] = datetime.fromtimestamp(float(bts[0]), timezone.utc).isoformat()
res['bar_coverage']['bar_span_to'] = datetime.fromtimestamp(float(bts[-1]), timezone.utc).isoformat()

train_mask = bts < TRAIN_END
n_train = int(train_mask.sum()); n_test = B - n_train
res['split'] = {'train_bars': n_train, 'test_bars': n_test,
                'train_end_utc': '2026-10-02T00:00:00+00:00'}

# ---------------- 2. train-only normalization ----------------
znorm = {}
for name, X in panels.items():
    mu = np.nanmean(X[train_mask], axis=0)
    sd = np.nanstd(X[train_mask], axis=0)
    sd[sd == 0] = np.nan
    znorm[name] = ((X - mu) / sd, mu, sd)

# ---------------- 3. k selection on TRAIN scree (before any OOS) ----------------
def pca_fit(Z):
    """Z: (n,p) complete rows. Returns evals desc, evecs (p,k)."""
    C = np.cov(Z, rowvar=False)
    ev, V = np.linalg.eigh(C)
    ix = np.argsort(ev)[::-1]
    return ev[ix], V[:, ix]

k_choice, scree = {}, {}
for name in panels:
    Z, _, _ = znorm[name]
    Ztr = Z[train_mask]
    ok = ~np.isnan(Ztr).any(axis=1)
    n_ok = int(ok.sum())
    if n_ok < 500:
        scree[name] = {'complete_train_rows': n_ok, 'note': 'too sparse for PCA; panel analyzed via pairwise correlations only'}
        k_choice[name] = 0
        continue
    ev, _ = pca_fit(Ztr[ok])
    var_exp = ev / ev.sum()
    # keep factors above the 1/P white-noise baseline
    k = int((var_exp > 1.0 / P + 1e-9).sum())
    k = max(k, 1)
    k_choice[name] = k
    scree[name] = {'complete_train_rows': n_ok,
                   'var_explained': [round(float(v), 4) for v in var_exp],
                   'k_chosen': k,
                   'rule': 'factors above 1/P white-noise baseline, train only'}
res['scree'] = scree
res['k_choice'] = k_choice

# ---------------- 4. LOO factor scores for primary panel (ret) ----------------
# For asset i: PCA on train rows of the OTHER 6 assets; scores over full period.
NAME = 'ret'
Z, mu, sd = znorm[NAME]
k = k_choice[NAME]
Ztr = Z[train_mask]

def loo_scores(i, kk):
    others = [j for j in range(P) if j != i]
    Ztro = Ztr[:, others]
    ok = ~np.isnan(Ztro).any(axis=1)
    ev, V = pca_fit(Ztro[ok])
    L = V[:, :kk]                       # (6, k) loadings, train only
    Xo = Z[:, others]                   # full period, train-fit normalization
    S = Xo @ L                          # (B, k); NaN rows stay NaN
    return S, L

loo = {}   # i -> (B,k) score matrix
loadings_loo = {}
for i in range(P):
    S, L = loo_scores(i, k)
    loo[i] = S
    loadings_loo[PRODS[i]] = [[round(float(x), 4) for x in row] for row in L]
res['loo_loadings_ret'] = loadings_loo

# Full-sample (all 7) loadings per panel for cross-panel comparison (no per-asset prediction here)
full_loadings, full_scores = {}, {}
for name in panels:
    if k_choice[name] == 0:
        continue
    Zp, _, _ = znorm[name]
    Ztrp = Zp[train_mask]
    ok = ~np.isnan(Ztrp).any(axis=1)
    ev, V = pca_fit(Ztrp[ok])
    kk = k_choice[name]
    L = V[:, :kk]
    full_loadings[name] = {'var_explained': [round(float(v), 4) for v in (ev / ev.sum())[:kk]],
                           'loadings': [[round(float(x), 4) for x in row] for row in L],
                           'complete_train_rows': int(ok.sum())}
    full_scores[name] = Zp @ L   # (B, k)
res['full_panel_factors'] = full_loadings

# ---------------- 5. state persistence: F_{t+1} = phi F_t + e (train, LOO) ----------------
phi = {}
for i in range(P):
    S = loo[i]
    ph = []
    for f in range(k):
        s = S[train_mask, f]
        ok = ~(np.isnan(s[:-1]) | np.isnan(s[1:]))
        x, y = s[:-1][ok], s[1:][ok]
        ph.append(round(float(np.dot(x, y) / np.dot(x, x)), 4) if len(x) > 10 else None)
    phi[PRODS[i]] = ph
res['phi_state_persistence'] = {'per_asset_LOO': phi,
    'note': 'phi from OLS F(t+1) on F(t), train segment, LOO scores'}
# pooled phi across assets (median)
ph_all = [v for lst in phi.values() for v in lst if v is not None]
res['phi_state_persistence']['median_phi'] = round(float(np.median(ph_all)), 4) if ph_all else None

# ---------------- 6. predictive horse race, frozen, horizons ----------------
def fit_ols(Xd, y):
    ok = ~(np.isnan(Xd).any(axis=1) | np.isnan(y))
    Xc, yc = Xd[ok], y[ok]
    if len(yc) < 50:
        return None, 0
    Xc1 = np.column_stack([np.ones(len(yc)), Xc])
    beta, *_ = np.linalg.lstsq(Xc1, yc, rcond=None)
    return beta, len(yc)

def predict(beta, Xd):
    if beta is None:
        return np.full(Xd.shape[0], np.nan)
    Xd1 = np.column_stack([np.ones(Xd.shape[0]), Xd])
    return Xd1 @ beta

def r2_zero(y, yhat):
    ok = ~(np.isnan(y) | np.isnan(yhat))
    y, yhat = y[ok], yhat[ok]
    if len(y) < 20 or np.sum(y ** 2) == 0:
        return None, 0
    return float(1 - np.sum((y - yhat) ** 2) / np.sum(y ** 2)), len(y)

Zy = znorm['ret'][0]          # target panel: 30s log-mid returns (z-scored)
race = {}
for i, p in enumerate(PRODS):
    y = Zy[:, i]
    S = loo[i][:, :k]          # LOO factor(s) for asset i
    race[p] = {}
    for h in HORIZONS:
        # design rows: predict y[t+h] from info at t
        n = B - h
        yt = y[h:]
        # AR(5) own past: y[t], y[t-1], ..., y[t-4]  (np.roll wraps; first AR_ORDER rows NaN-masked below)
        AR = np.column_stack([np.roll(y, l)[h:] for l in range(1, AR_ORDER+1)])
        FO = S[:n]
        AF = np.column_stack([AR, FO])
        # train / test masks on target time t+h
        tr = train_mask[h:]
        te = ~train_mask[h:]
        # invalidate AR rows that cross the train/test boundary or wrap
        # (roll wrap: first AR_ORDER rows of AR reference the end of the series)
        AR[:AR_ORDER, :] = np.nan
        AF[:, :AR_ORDER] = AR
        out = {}
        # (a) AR own past
        b, ntr = fit_ols(AR[tr], yt[tr]); out['AR'] = {'r2': r2_zero(yt[te], predict(b, AR[te]))[0], 'n_train': ntr}
        # (b) factor only
        b, ntr = fit_ols(FO[tr], yt[tr]); out['FO'] = {'r2': r2_zero(yt[te], predict(b, FO[te]))[0], 'n_train': ntr}
        # (c) AR + factor
        b, ntr = fit_ols(AF[tr], yt[tr]); out['AF'] = {'r2': r2_zero(yt[te], predict(b, AF[te]))[0], 'n_train': ntr}
        # (d) shuffled-factor control: same fitted AF params, factor columns replaced by shuffled test factor
        if b is not None:
            AFsh = AF.copy()
            fsh = FO[te].copy()
            for f in range(k):
                col = fsh[:, f]; okc = ~np.isnan(col)
                perm = rng.permutation(okc.sum())
                tmp = col[okc].copy(); col[okc] = tmp[perm]; fsh[:, f] = col
            AFsh[te, AR_ORDER:] = fsh
            out['SHUF'] = {'r2': r2_zero(yt[te], predict(b, AFsh[te]))[0], 'n_train': ntr}
        # (e) time-shifted factor control: circular shift by SHIFT_BARS
        AFts = AF.copy()
        AFts[:, AR_ORDER:] = np.roll(FO, SHIFT_BARS, axis=0)
        # rows where shifted factor wraps across the train/test boundary are contaminated -> NaN them
        # (conservative: NaN the first SHIFT_BARS rows of the shifted block)
        AFts[:SHIFT_BARS, AR_ORDER:] = np.nan
        b2, ntr2 = fit_ols(AF[tr], yt[tr])
        out['TSHIFT'] = {'r2': r2_zero(yt[te], predict(b2, AFts[te]))[0], 'n_train': ntr2}
        race[p][f'{h*BAR}s'] = {m: round(v['r2'], 5) if v['r2'] is not None else None
                                for m, v in out.items()}
        race[p][f'{h*BAR}s']['n_test'] = int(te.sum())
res['horse_race_R2h'] = race

# pooled means per horizon/model (mean over assets, ignoring None)
pooled = {}
for h in HORIZONS:
    key = f'{h*BAR}s'
    pooled[key] = {}
    for m in ['AR', 'FO', 'AF', 'SHUF', 'TSHIFT']:
        vals = [race[p][key][m] for p in PRODS if race[p][key][m] is not None]
        pooled[key][m] = round(float(np.mean(vals)), 5) if vals else None
res['horse_race_pooled'] = pooled

# ---------------- 7. cross-panel factor comparison ----------------
xpanel = {}
names = [n for n in panels if n in full_scores]
for a in names:
    for b_ in names:
        if a >= b_:
            continue
        fa = full_scores[a][:, 0]; fb = full_scores[b_][:, 0]
        ok = ~(np.isnan(fa) | np.isnan(fb))
        c = float(np.corrcoef(fa[ok], fb[ok])[0, 1]) if ok.sum() > 100 else None
        xpanel[f'{a}_vs_{b_}'] = {'corr_F1': round(c, 4) if c is not None else None,
                                  'n': int(ok.sum())}
# lag offsets: cross-correlation of F1 series, lags -20..+20 bars
LAGS = list(range(-20, 21))
def xcorr_lag(x, y, lags):
    ok = ~(np.isnan(x) | np.isnan(y))
    x, y = x[ok], y[ok]
    if len(x) < 200:
        return None, None
    x = (x - x.mean()) / (x.std() or 1); y = (y - y.mean()) / (y.std() or 1)
    out = {}
    for L in lags:
        if L >= 0:
            out[L] = float(np.mean(x[:len(x)-L] * y[L:])) if L < len(x) else 0.0
        else:
            out[L] = float(np.mean(x[-L:] * y[:len(y)+L]))
    bl = max(out, key=lambda L: abs(out[L]))
    return bl, round(out[bl], 4)
order = {}
pairs = [('tfi', 'imb'), ('imb', 'ret'), ('tfi', 'ret'), ('spr', 'ret'), ('tfi', 'spr')]
for a, b_ in pairs:
    if a in full_scores and b_ in full_scores:
        # positive lag L means b_ lags a (a leads)
        bl, bv = xcorr_lag(full_scores[a][:, 0], full_scores[b_][:, 0], LAGS)
        order[f'{a}_leads_{b_}'] = {'best_lag_bars': bl, 'best_lag_s': bl * BAR if bl is not None else None,
                                    'xcorr': bv}
res['cross_panel'] = {'F1_correlations': xpanel, 'lag_offsets': order,
                      'note': 'positive best_lag means second panel lags first (first leads)'}

# ---------------- 8. BLIND leaders-board comparison (read only now) ----------------
try:
    with open(os.path.join(BASE, 'ws_leaders.json')) as fh:
        leaders = json.load(fh)
    # leaders board format unknown; extract per-product counts robustly
    counts = {}
    def walk(o):
        if isinstance(o, dict):
            for kk, vv in o.items():
                if kk in PRODS and isinstance(vv, (int, float)):
                    counts[kk] = counts.get(kk, 0) + vv
                else:
                    walk(vv)
        elif isinstance(o, list):
            for e in o:
                walk(e)
    walk(leaders)
    # beta magnitudes from AF model at h=30s, F1 coefficient
    betas = {}
    Zy = znorm['ret'][0]
    y = None
    for i, p in enumerate(PRODS):
        S = loo[i][:, :k]
        n = B - 1
        yt = Zy[1:, i]
        AR = np.column_stack([np.roll(Zy[:, i], l)[1:] for l in range(1, AR_ORDER+1)])
        AR[:AR_ORDER, :] = np.nan
        AF = np.column_stack([AR, S[:n]])
        tr = train_mask[1:]
        b, _ = fit_ols(AF[tr], yt[tr])
        betas[p] = abs(float(b[AR_ORDER])) if b is not None else None  # |beta_F1|
    xs = [betas[p] for p in PRODS]; ys = [counts.get(p, 0) for p in PRODS]
    ok = [x is not None for x in xs]
    xs = [x for x, o in zip(xs, ok) if o]; ys = [y for y, o in zip(ys, ok) if o]
    rx = np.argsort(np.argsort(xs)); ry = np.argsort(np.argsort(ys))
    n_ = len(xs)
    rho = float(1 - 6 * np.sum((rx - ry) ** 2) / (n_ * (n_ ** 2 - 1))) if n_ > 2 else None
    res['blind_leaders'] = {'spearman_rho_beta_vs_leadership': round(rho, 4) if rho is not None else None,
                            'beta_F1_ranking': sorted(PRODS, key=lambda p: -(betas[p] or -1)),
                            'leadership_ranking': sorted(PRODS, key=lambda p: -counts.get(p, 0)),
                            'note': 'ws_leaders.json read ONLY at this final step'}
except Exception as e:
    res['blind_leaders'] = {'error': str(e)}

with open(os.path.join(OUTDIR, 'v4_results.json'), 'w') as fh:
    json.dump(res, fh, indent=1)
print('wrote v4_results.json')
print(json.dumps({kk: res[kk] for kk in ['k_choice', 'horse_race_pooled', 'phi_state_persistence']}, indent=1)[:2000])
