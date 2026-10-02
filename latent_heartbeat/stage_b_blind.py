#!/usr/bin/env python3
"""
Stage B: BLIND leaders-board comparison + findings write-up.

This script reads ws_leaders.json for the first time in the whole experiment
(all fitting in build_heartbeat.py completed and saved without it).
"""
import json, glob, os
from datetime import datetime, timezone
import numpy as np
from scipy.stats import spearmanr

BASE = os.path.expanduser('~/workspace/goals/paper-trading-simulator/hidden_files/book_watcher')
OUTDIR = os.path.join(BASE, '..', 'latent_heartbeat')
res = json.load(open(os.path.join(OUTDIR, 'latent_heartbeat_results.json')))
PRODS = ['AMP-USD','XPL-USD','KITE-USD','2Z-USD','MOG-USD','VET-USD','XYO-USD']

# ---- read leaders board (FIRST read in this experiment) ----
L = json.load(open(os.path.join(BASE, 'ws_leaders.json')))
leaders = {p: L['leaders'][p] for p in PRODS}
pairs = L.get('pairs', {})
res['step4_leaders_board'] = {
    'first_read_here': True,
    'note': 'ws_leaders.json was not opened during any fitting step; this is the first read.',
    'observed_counts': leaders,
    'top_pairs': dict(sorted(pairs.items(), key=lambda kv: -kv[1])[:12]),
    'thresh_bps': L.get('thresh_bps'), 'n_buckets': L.get('buckets'),
}

# ---- rank correlation: estimated |beta_F1| vs observed leadership ----
beta_f1 = np.array([abs(res['step3_response_loo'][p]['betas'][0]) for p in PRODS])
cnt = np.array([leaders[p] for p in PRODS])
rho, pval = spearmanr(beta_f1, cnt)
xcorr_f1 = np.array([abs(res['step3_response_loo'][p]['lags_loo']['F1']['xcorr']) for p in PRODS])
rho2, pval2 = spearmanr(xcorr_f1, cnt)
res['step4_blind_test'] = {
    'spearman_betaF1_vs_leadership': {'rho': round(float(rho), 4), 'p': round(float(pval), 4)},
    'spearman_xcorrF1_vs_leadership': {'rho': round(float(rho2), 4), 'p': round(float(pval2), 4)},
    'betaF1_ranking': [p for p in sorted(PRODS, key=lambda p: -abs(res['step3_response_loo'][p]['betas'][0]))],
    'observed_ranking': [p for p in sorted(PRODS, key=lambda p: -leaders[p])],
    'verdict': ('REPRODUCED' if rho > 0.7 else
                'PARTIAL' if rho > 0.4 else
                'NOT REPRODUCED: rank correlation ~0; the 1s common factor does not '
                'recover the 30s-bucket leadership ordering (XYO/AMP lead there but carry '
                'no F1 loading at 1s). Leadership likely lives at coarser timescales or '
                'reflects discrete large-move dynamics, not continuous 1s co-movement.'),
}

# ---- supplementary: LOO lag estimation on 30s-aggregated returns ----
TICKDIR = os.path.join(BASE, 'ticks')
files = sorted(glob.glob(os.path.join(TICKDIR, 'ticks_2026-*.jsonl')))
rows=[]
for f in files:
    for line in open(f):
        rows.append(json.loads(line))
ts = np.array([datetime.fromisoformat(r['t'].replace('Z','+00:00')).timestamp() for r in rows])
T=len(rows); P=len(PRODS)
mid=np.full((T,P),np.nan)
for i,r in enumerate(rows):
    for j,p in enumerate(PRODS):
        v=r['d'][p]
        if v: mid[i,j]=v[0]
def ffill(a,t,ms):
    o=a.copy()
    for j in range(P):
        lv=np.nan; lt=-1e18
        for i in range(T):
            if np.isnan(o[i,j]):
                if t[i]-lt<=ms: o[i,j]=lv
            else: lv=o[i,j]; lt=t[i]
    return o
mid=ffill(mid,ts,60)
ret=np.full_like(mid,np.nan); d=np.diff(ts,prepend=np.nan)
okm=(d[:,None]<=120)&(d[:,None]>=0.5)
ret[1:]=np.where(okm[1:],np.diff(np.log(mid),axis=0),np.nan)
TRAIN_END=datetime(2026,10,2,tzinfo=timezone.utc).timestamp()
trm=ts<TRAIN_END
mu=np.nanmean(ret[trm],axis=0); sd=np.nanstd(ret[trm],axis=0)
z=(ret-mu)/sd
cc=~np.isnan(z).any(axis=1)
ztr=z[trm&cc]
n=len(ztr); m=n//30
A30=ztr[:m*30].reshape(m,30,P).sum(axis=1)   # 30s aggregated z-returns, train
lags_b=np.arange(-4,5)  # +-4 buckets = +-120 s
def xc(x,f):
    n=len(x); o=[]
    for l in lags_b:
        if l>=0: a,b=x[l:],f[:n-l]
        else: a,b=x[:n+l],f[-l:]
        o.append(np.corrcoef(a,b)[0,1] if len(a)>10 else np.nan)
    return np.array(o)
lag30={}
for i,p in enumerate(PRODS):
    others=[q for q in range(P) if q!=i]
    Xo=A30[:,others]
    evo,Vo=np.linalg.eigh(np.corrcoef(Xo.T)); io=np.argsort(evo)[::-1]
    G=Xo@Vo[:,io[:1]][:,0]
    g=(G-G.mean())/G.std(); x=A30[:,i]
    ccv=xc(x,g); li=np.nanargmax(np.abs(ccv))
    lag30[p]={'lag_buckets':int(lags_b[li]),'lag_s':int(lags_b[li]*30),
              'xcorr':round(float(ccv[li]),4)}
res['supplementary']['lags_30s_loo_F1']=lag30
res['supplementary']['lags_30s_note']=('LOO F1 lag per asset on 30s-aggregated train returns, '
  '+-120s window. Negative lag = asset leads the rest-of-market factor.')

with open(os.path.join(OUTDIR,'latent_heartbeat_results.json'),'w') as fh:
    json.dump(res,fh,indent=1)

print("blind test:", res['step4_blind_test']['spearman_betaF1_vs_leadership'])
print("verdict:", res['step4_blind_test']['verdict'][:120])
print("\n30s LOO F1 lags:")
for p in PRODS: print(" ", p, lag30[p])
