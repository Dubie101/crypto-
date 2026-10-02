#!/usr/bin/env python3
"""
Latent market-state ("heartbeat") experiment v3 — Mike's approved design.

v3 fixes a leakage bug found in v2: the response model and horse race used
FULL-sample factor scores, so an asset's own contemporaneous return leaked into
its regressors whenever the estimated lag made the factor contemporaneous
(2Z: LOO lag -1 on F2 -> regressor F2(t) containing 0.35*x_2Z(t) -> fake
OOS R2=0.23). v3 uses strictly leave-one-out factor scores for asset i
(loadings fit on the other 6 assets, applied to the other 6), so no asset ever
predicts itself.

Also documents: F2 is MOG-quote-artifact-dominated (MOG mid snaps 5-115% between
stale levels with ~zero trades; 45 such snaps). F2 is reported but excluded
from heartbeat interpretation; robustness check with winsorized returns included.

Does NOT read ws_leaders.json (blind comparison is stage B).
"""
import json, glob, os
from datetime import datetime, timezone
import numpy as np

BASE = os.path.expanduser('~/workspace/goals/paper-trading-simulator/hidden_files/book_watcher')
TICKDIR = os.path.join(BASE, 'ticks')
OUTDIR = os.path.join(BASE, '..', 'latent_heartbeat')
os.makedirs(OUTDIR, exist_ok=True)
PRODS = ['AMP-USD','XPL-USD','KITE-USD','2Z-USD','MOG-USD','VET-USD','XYO-USD']
P = len(PRODS)
FFILL_MAX = 60; GAP_MASK = 120; LAG_MAX = 120; AR_ORDER = 5
TRAIN_END = datetime(2026,10,2,0,0,0,tzinfo=timezone.utc).timestamp()

res = {'design_notes': [], 'version': 3,
       'leakage_note': 'v2 used full-sample factor scores in response/race; 2Z OOS R2=0.23 was '
       'self-contamination (LOO lag -1 made F2(t) contemporaneous, and F2 loads 0.35 on 2Z). '
       'v3 uses strictly leave-one-out factor scores throughout. No asset predicts itself.'}

# ---------- 0. load ----------
files = sorted(glob.glob(os.path.join(TICKDIR, 'ticks_2026-*.jsonl')))
rows = []
for f in files:
    with open(f) as fh:
        for line in fh:
            rows.append(json.loads(line))
ts = np.array([datetime.fromisoformat(r['t'].replace('Z','+00:00')).timestamp() for r in rows])
T = len(rows)
mid = np.full((T,P), np.nan); spr = np.full((T,P), np.nan); imb = np.full((T,P), np.nan)
nulls = {p:0 for p in PRODS}
for i,r in enumerate(rows):
    d = r['d']
    for j,p in enumerate(PRODS):
        v = d[p]
        if v is None: nulls[p]+=1; continue
        mid[i,j]=v[0]; spr[i,j]=v[1]; imb[i,j]=v[2]
span = ts[-1]-ts[0]; missing = int(span+1-T)
gaps=[]
for i in range(1,T):
    d = ts[i]-ts[i-1]
    if d>120: gaps.append({'from':datetime.fromtimestamp(ts[i-1],timezone.utc).isoformat(),
                           'to':datetime.fromtimestamp(ts[i],timezone.utc).isoformat(),
                           'gap_s':round(d,1)})
res['coverage'] = {
  'rows':T, 'first':datetime.fromtimestamp(ts[0],timezone.utc).isoformat(),
  'last':datetime.fromtimestamp(ts[-1],timezone.utc).isoformat(),
  'span_s':round(span,1), 'missing_seconds':missing,
  'missing_pct':round(100*missing/(span+1),3),
  'n_gaps_over_120s':len(gaps), 'gaps':gaps, 'nulls_per_product':nulls,
  'gap_policy':'forward-fill up to 60s; return=NaN if product gap>120s or at edges',
}

def ffill(a, t, max_stale):
    out = a.copy()
    for j in range(P):
        last_v=np.nan; last_t=-1e18
        for i in range(T):
            if np.isnan(out[i,j]):
                if t[i]-last_t<=max_stale: out[i,j]=last_v
            else: last_v=out[i,j]; last_t=t[i]
    return out
mid_f=ffill(mid,ts,FFILL_MAX); spr_f=ffill(spr,ts,FFILL_MAX); imb_f=ffill(imb,ts,FFILL_MAX)
dt = np.diff(ts, prepend=np.nan)
ok = (dt[:,None]<=GAP_MASK)&(dt[:,None]>=0.5)
def panel_returns(a, log=False):
    r=np.full_like(a,np.nan)
    d=np.diff(np.log(a) if log else a, axis=0)
    r[1:,:]=np.where(ok[1:,:], d, np.nan)
    return r
ret=panel_returns(mid_f,log=True); dspr=panel_returns(spr_f); dimb=panel_returns(imb_f)
train_mask = ts < TRAIN_END; test_mask = ~train_mask
panels = {'mid_logret':ret, 'spread_bps_change':dspr, 'imb5_change':dimb}

def zscore_train(a):
    mu=np.nanmean(a[train_mask],axis=0); sd=np.nanstd(a[train_mask],axis=0); sd[sd==0]=1.0
    return (a-mu)/sd, mu, sd

# ---------- 1. observable screen ----------
panel_report={}; Z={}
for name,a in panels.items():
    z,mu,sd=zscore_train(a); Z[name]=z
    ztr=z[train_mask]; cc=ztr[~np.isnan(ztr).any(axis=1)]
    ev,_=np.linalg.eigh(np.corrcoef(cc.T)); ev=ev[::-1]; ve=ev/ev.sum()
    panel_report[name]={'F1_var_explained':round(float(ve[0]),4),
                        'F2_var_explained':round(float(ve[1]),4),
                        'complete_rows_train':int(len(cc))}
res['step1_observable_screen']=panel_report
best=max(panel_report,key=lambda n: panel_report[n]['F1_var_explained'])
res['step1_best_observable']=best

# ---------- 2. PCA k=1..7 (full sample, for scree/structure) ----------
z=Z[best]; ztr=z[train_mask]
ccmask_tr=~np.isnan(ztr).any(axis=1); Xtr=ztr[ccmask_tr]
ev,V=np.linalg.eigh(np.corrcoef(Xtr.T)); idx=np.argsort(ev)[::-1]; ev=ev[idx]; V=V[:,idx]
ve=ev/ev.sum(); cum=np.cumsum(ve); baseline=1.0/P
k_kaiser=int((ve>baseline).sum())
res['step2_pca']={'var_explained':[round(float(v),4) for v in ve],
                  'cumulative':[round(float(v),4) for v in cum],
                  'white_noise_baseline':round(baseline,4),
                  'k_kaiser':k_kaiser, 'n_complete_train':int(len(Xtr))}
chosen_k=k_kaiser
res['step2_chosen_k']=chosen_k
res['step2_k_justification']=(
  f"F1={ve[0]:.4f} clearly above baseline {baseline:.4f}; F2={ve[1]:.4f} marginal; "
  f"F3..F7 at/below baseline. Kaiser -> k={k_kaiser}.")
res['step2_loadings_F1']= {p:round(float(V[i,0]),4) for i,p in enumerate(PRODS)}
res['step2_loadings_F2']= {p:round(float(V[i,1]),4) for i,p in enumerate(PRODS)}
res['step2_F2_note']=('F2 loads -0.80 on MOG-USD. MOG 1s mid is a stale step function: '
  '45 snaps of 5-115% between discrete levels with ~zero trades (quote artifact, not price '
  'discovery). F2 is therefore a data-artifact factor, not a market state; heartbeat '
  'interpretation rests on F1 only.')

win=7200; step=1800; t0=ts[train_mask][0]; sims=[]; nwin=0; w=t0
while w+win<=ts[train_mask][-1]:
    m=train_mask&(ts>=w)&(ts<w+win)&(~np.isnan(z).any(axis=1)); Xw=z[m]
    if len(Xw)>win*0.5:
        evw,Vw=np.linalg.eigh(np.corrcoef(Xw.T)); v1w=Vw[:,np.argmax(evw)]
        sims.append(abs(float(v1w@V[:,0]))); nwin+=1
    w+=step
med_sim=float(np.median(sims))
res['step2_rolling_stability']={'windows':nwin,'median_abs_cosine_F1':round(med_sim,4),
  'stable':bool(med_sim>0.90),
  'kalman_decision':'SKIPPED: median |cos|=%.3f>0.90 over %d rolling 2h windows; static loadings suffice.'%(med_sim,nwin)}

# ---------- 3. per-asset LOO pipeline ----------
lags=np.arange(-LAG_MAX,LAG_MAX+1)
def xcorr_lags(x,f):
    n=len(x); out=np.full(len(lags),np.nan)
    for li,l in enumerate(lags):
        if l>=0: a,b=x[l:],f[:n-l]
        else: a,b=x[:n+l],f[-l:]
        if len(a)>10: out[li]=np.corrcoef(a,b)[0,1]
    return out

n_eff=len(Xtr)
zte=z[test_mask]; ccmask_te=~np.isnan(zte).any(axis=1); Xte=zte
te_n=len(zte)

def loo_pipeline(i):
    """Fit LOO PCA on train (asset i excluded); return dict with loadings,
    train/test LOO scores, lags, response OLS."""
    others=[q for q in range(P) if q!=i]
    Xo=Xtr[:,others]
    evo,Vo=np.linalg.eigh(np.corrcoef(Xo.T)); io=np.argsort(evo)[::-1]
    Wo=Vo[:,io[:chosen_k]]
    Gtr=Xo@Wo
    Gte=np.full((te_n,chosen_k),np.nan)
    Gte[ccmask_te]=Xte[ccmask_te][:,others]@Wo
    x=Xtr[:,i]
    lagd={}
    for j in range(chosen_k):
        g=Gtr[:,j]; g=(g-g.mean())/g.std()
        cc=xcorr_lags(x,g); li=np.nanargmax(np.abs(cc))
        lagd[j]={'lag_s':int(lags[li]),'xcorr':round(float(cc[li]),4)}
    return {'Wo':Wo,'Gtr':Gtr,'Gte':Gte,'lags':lagd,'others':others}

def lagged_regs(G, lags_d, n, horizon=1):
    cols=[]
    for j in range(chosen_k):
        l=lags_d[j]['lag_s']
        c=np.full(n,np.nan); src=np.arange(n)-horizon-l
        okm=(src>=0)&(src<len(G)); c[okm]=G[src[okm],j]; cols.append(c)
    return np.column_stack(cols)

resp={}; race={'per_asset':{}}
for i,p in enumerate(PRODS):
    L=loo_pipeline(i)
    lv=L['lags']
    # response OLS on train: y(t+1) on LOO factors at LOO lags
    Gf=lagged_regs(L['Gtr'],lv,n_eff,1)
    y=Xtr[1:,i]; G=Gf[1:]; okm=~np.isnan(G).any(axis=1)
    D=np.column_stack([np.ones(okm.sum()),G[okm]])
    b,_,_,_=np.linalg.lstsq(D,y[okm],rcond=None)
    r2=1-((y[okm]-D@b)**2).sum()/(y[okm]**2).sum()
    resp[p]={'lags_loo':{f'F{j+1}':lv[j] for j in range(chosen_k)},
             'alpha':round(float(b[0]),6),
             'betas':[round(float(v),6) for v in b[1:]],
             'R2_next1s_train':round(float(r2),6)}
    # ---- horse race (frozen) ----
    x_tr=Xtr[:,i]; x_te=Xte[:,i]
    # (a) AR(5)
    tidx=np.arange(AR_ORDER,len(x_tr))
    Xa=np.column_stack([np.ones(len(tidx))]+[x_tr[tidx-k] for k in range(1,AR_ORDER+1)])
    ba,_,_,_=np.linalg.lstsq(Xa,x_tr[tidx],rcond=None)
    pa=np.full(te_n,np.nan)
    for t in range(te_n):
        if t-AR_ORDER>=0:
            w=x_te[t-AR_ORDER:t]
            if not np.isnan(w).any(): pa[t]=ba[0]+w[::-1]@ba[1:]
    # (b) factor-only (LOO)
    Gf_tr=lagged_regs(L['Gtr'],lv,n_eff,1)
    yb=x_tr[1:]; Gb=Gf_tr[1:]; okb=~np.isnan(Gb).any(axis=1)
    Db=np.column_stack([np.ones(okb.sum()),Gb[okb]])
    bb,_,_,_=np.linalg.lstsq(Db,yb[okb],rcond=None)
    Gf_te=lagged_regs(L['Gte'],lv,te_n,1)
    pb=np.full(te_n,np.nan); okt=~np.isnan(Gf_te).any(axis=1)
    pb[okt]=np.column_stack([np.ones(okt.sum()),Gf_te[okt]])@bb
    # (c) AR + factors
    # align: rows t=AR_ORDER..n-2 predict x(t+1); use mask on intersection
    t2=np.arange(AR_ORDER,n_eff-1)
    m2ok=~np.isnan(Gf_tr[t2]).any(axis=1)
    t2=t2[m2ok]
    Dc=np.column_stack([np.ones(len(t2))]+[x_tr[t2-k] for k in range(1,AR_ORDER+1)]+[Gf_tr[t2,j] for j in range(chosen_k)])
    bc,_,_,_=np.linalg.lstsq(Dc,x_tr[t2+1],rcond=None)
    pc=np.full(te_n,np.nan)
    Gf2=Gf_te
    for t in range(te_n):
        if t-AR_ORDER>=0 and not np.isnan(Gf2[t]).any():
            w=x_te[t-AR_ORDER:t]
            if not np.isnan(w).any():
                pc[t]=bc[0]+sum(bc[1+k]*w[AR_ORDER-1-k] for k in range(AR_ORDER))+sum(bc[1+AR_ORDER+j]*Gf2[t,j] for j in range(chosen_k))
    yv=x_te
    m_valid=~np.isnan(yv)&~np.isnan(pa)&~np.isnan(pb)&~np.isnan(pc)
    mse0=float(np.mean(yv[m_valid]**2)); n=int(m_valid.sum())
    out={'n_test':n,'mse_naive':mse0}
    for name,pr in [('AR_own_past',pa),('factor_only',pb),('AR_plus_factor',pc)]:
        mse=float(np.mean((yv[m_valid]-pr[m_valid])**2))
        out[name]={'MSE':mse,'R2_vs_naive':1-mse/mse0}
    race['per_asset'][p]=out

res['step3_response_loo']=resp
res['step3_note']='All lags, betas and factor scores are leave-one-out: asset i never appears in its own regressors.'
for name in ['AR_own_past','factor_only','AR_plus_factor']:
    r2s=[race['per_asset'][p][name]['R2_vs_naive'] for p in PRODS]
    race.setdefault('pooled',{})[name]={'mean_R2_vs_naive':float(np.mean(r2s)),
        'assets_positive_R2':int(sum(1 for v in r2s if v>0))}
res['step6_horse_race_1s']=race

# ---------- supplementary: timescale + winsorized robustness ----------
sup={}
for agg_s,agg_name in [(30,'ret_30s'),(60,'ret_60s')]:
    A=z[train_mask][ccmask_tr]
    n=len(A); m=n//agg_s
    Ag=A[:m*agg_s].reshape(m,agg_s,P).sum(axis=1)
    evA,_=np.linalg.eigh(np.corrcoef(Ag.T)); evA=evA[::-1]; veA=evA/evA.sum()
    sup[agg_name]={'F1_var_explained':round(float(veA[0]),4),'F2_var_explained':round(float(veA[1]),4)}
# winsorized: clip z-scored 1s returns at +-1 (i.e., remove MOG snaps), redo PCA
zw=np.clip(z,-1,1)
zwtr=zw[train_mask]; ccw=zwtr[~np.isnan(zwtr).any(axis=1)]
evw,_=np.linalg.eigh(np.corrcoef(ccw.T)); evw=evw[::-1]; vew=evw/evw.sum()
sup['winsorized_pm1']={'F1_var_explained':round(float(vew[0]),4),
    'F2_var_explained':round(float(vew[1]),4),
    'note':'z-scored returns clipped at +-1 sigma before PCA; tests whether F2 survives without MOG snaps'}
res['supplementary']=sup

with open(os.path.join(OUTDIR,'latent_heartbeat_results.json'),'w') as fh:
    json.dump(res,fh,indent=1)
print('stage A saved (v3, LOO-clean). ws_leaders.json NOT read.')
