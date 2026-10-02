"""OOS + fee-math check for the BTC-leads-alts signal.

Window A: most recent 300 1-min candles (in-sample, replicates cheap test).
Window B: 300 1-min candles ending 12h earlier (out-of-sample, non-overlapping).
For each follower: lag-1 xcorr in A vs in B (sign/magnitude stability),
regression beta of F[t+1] on BTC[t], and expected bps captured per
large-BTC-move event vs ~120 bps round-trip cost.
"""
import json, math, time, urllib.request, datetime

FOLLOWERS = ['QNT-USD','ZRO-USD','NEAR-USD','UNI-USD','MON-USD','PLUME-USD',
             'LTC-USD','ADA-USD','AVAX-USD','TAO-USD','LINK-USD','ENA-USD']
COST_BPS = 120.0

def candles(p, end=None):
    url = f'https://api.exchange.coinbase.com/products/{p}/candles?granularity=60'
    if end: url += '&end=' + end
    req = urllib.request.Request(url, headers={'User-Agent': 'muse-oos-lag'})
    data = json.load(urllib.request.urlopen(req, timeout=25))
    return sorted(data)

def rets(cl):
    return [(cl[i+1]-cl[i])/cl[i] for i in range(len(cl)-1) if cl[i]]

def xcorr(a, b, lag):
    n = len(a)-lag
    if n < 60: return None
    x, y = a[:n], b[lag:lag+n]
    mx, my = sum(x)/n, sum(y)/n
    sx = sum((v-mx)**2 for v in x); sy = sum((v-my)**2 for v in y)
    if sx == 0 or sy == 0: return None
    return sum((v-mx)*(w-my) for v, w in zip(x, y))/math.sqrt(sx*sb) if False else sum((v-mx)*(w-my) for v, w in zip(x, y))/math.sqrt(sx*sy)

def beta(x, y):
    n = len(x); mx, my = sum(x)/n, sum(y)/n
    sxx = sum((v-mx)**2 for v in x)
    if sxx == 0: return 0.0
    return sum((v-mx)*(w-my) for v, w in zip(x, y))/sxx

now = datetime.datetime.now(datetime.timezone.utc)
endB = (now - datetime.timedelta(hours=12)).isoformat()
S = {}
for p in ['BTC-USD'] + FOLLOWERS:
    try:
        A = rets([c[4] for c in candles(p)])
        time.sleep(0.25)
        B = rets([c[4] for c in candles(p, endB)])
        S[p] = (A, B)
        print(p, len(A), len(B))
    except Exception as e:
        print(p, 'FAIL', str(e)[:60])
    time.sleep(0.25)

LA = min(len(v[0]) for v in S.values()); LB = min(len(v[1]) for v in S.values())
A = {p: v[0][-LA:] for p, v in S.items()}
B = {p: v[1][-LB:] for p, v in S.items()}
print('windows:', LA, LB)

rows = []
for f in FOLLOWERS:
    if f not in A: continue
    cA = xcorr(A['BTC-USD'], A[f], 1)
    cB = xcorr(B['BTC-USD'], B[f], 1)
    b = beta(A['BTC-USD'][:-1], A[f][1:])
    # event study: BTC |1-min move| > 2 sigma -> follower next-min move
    x = A['BTC-USD'][:-1]; y = A[f][1:]
    sig = math.sqrt(sum((v-sum(x)/len(x))**2 for v in x)/len(x))
    ev = [(xi, yi) for xi, yi in zip(x, y) if abs(xi) > 2*sig]
    if ev:
        signed = sum(math.copysign(yi, xi) for xi, yi in ev)/len(ev)  # mean follow-through in BTC direction
    else:
        signed = 0.0
    rows.append(dict(f=f, cA=cA, cB=cB, beta=b, n_ev=len(ev),
                     follow_bps=signed*1e4, net_bps=signed*1e4 - COST_BPS))

json.dump(rows, open('/home/hatch/workspace/goals/paper-trading-simulator/hidden_files/book_watcher/delayed_copy_oos.json','w'), indent=1)
print('\nfollower      lag1(A)  lag1(B)  beta   events  follow_bps  net_vs_120bps')
for r in sorted(rows, key=lambda r: -(r['cA'] or -9)):
    print(f"{r['f']:10s}  {r['cA']:+.3f}  {r['cB']:+.3f}  {r['beta']:+.3f}  {r['n_ev']:4d}  {r['follow_bps']:+8.1f}  {r['net_bps']:+8.1f}")
stability = sum(1 for r in rows if r['cA'] and r['cB'] and r['cA']>0.15 and r['cB']>0.15)
print(f'\nstable (lag1>0.15 in BOTH windows): {stability}/{len(rows)}')
