"""CHEAP delayed-copy test (budget-conscious version).

One page of 300 1-min candles per product (~5h), 13 products total.
Leader: BTC-USD. Followers: 12 most-traded non-major alts from today's tape.
Stat: argmax lag in 0..15 of xcorr(BTC[t], F[t+lag]); null = 200 circular shifts.
"""
import json, math, random, time, urllib.request

FOLLOWERS = ['QNT-USD','ZRO-USD','NEAR-USD','UNI-USD','MON-USD','PLUME-USD',
             'LTC-USD','ADA-USD','AVAX-USD','TAO-USD','LINK-USD','ENA-USD']
MAXLAG = 15

def candles(p):
    url = f'https://api.exchange.coinbase.com/products/{p}/candles?granularity=60'
    req = urllib.request.Request(url, headers={'User-Agent': 'muse-cheap-lag'})
    data = json.load(urllib.request.urlopen(req, timeout=25))
    data = sorted(data)
    return [c[4] for c in data]

def rets(cl):
    return [(cl[i+1]-cl[i])/cl[i] for i in range(len(cl)-1) if cl[i]]

def xcorr(a, b, lag):
    n = len(a)-lag
    if n < 60: return None
    x, y = a[:n], b[lag:lag+n]
    mx, my = sum(x)/n, sum(y)/n
    sx = sum((v-mx)**2 for v in x); sy = sum((v-my)**2 for v in y)
    if sx == 0 or sy == 0: return None
    return sum((v-mx)*(w-my) for v, w in zip(x, y))/math.sqrt(sx*sy)

S = {}
for p in ['BTC-USD'] + FOLLOWERS:
    try:
        S[p] = rets(candles(p)); print(p, len(S[p]), 'returns')
    except Exception as e:
        print(p, 'FAIL', str(e)[:60])
    time.sleep(0.3)

L = min(len(v) for v in S.values())
S = {p: v[-L:] for p, v in S.items()}
print('aligned minutes:', L)
btc = S['BTC-USD']
random.seed(11)
rows = []
for f in FOLLOWERS:
    if f not in S: continue
    real = [xcorr(btc, S[f], l) for l in range(MAXLAG+1)]
    if any(v is None for v in real): continue
    lag = max(range(MAXLAG+1), key=lambda l: real[l])
    null_peaks, null_pos = [], 0
    for _ in range(200):
        sh = random.randint(31, L-31)
        shf = S[f][sh:] + S[f][:sh]
        cs = [xcorr(btc, shf, l) for l in range(MAXLAG+1)]
        bl = max(range(MAXLAG+1), key=lambda l: cs[l])
        null_peaks.append(cs[bl]); null_pos += (bl > 0)
    rows.append((f, lag, real[lag], real[0], sum(null_peaks)/len(null_peaks), null_pos/200))

json.dump([dict(f=f, lag=l, peak=p, c0=c0, nullpk=npk, nullposfrac=nf)
           for f, l, p, c0, npk, nf in rows],
          open('/home/hatch/workspace/goals/paper-trading-simulator/hidden_files/book_watcher/delayed_copy_cheap.json', 'w'), indent=1)
print(f'\n{len(rows)} followers')
lags = [r[1] for r in rows]
print('argmax lags:', sorted(lags))
print('lag>0 count:', sum(1 for l in lags if l > 0), '/', len(lags))
mp = sum(r[2] for r in rows)/len(rows); mc = sum(r[3] for r in rows)/len(rows)
print(f'mean lagged peak: {mp:+.3f}   mean contemporaneous: {mc:+.3f}')
for f, l, p, c0, npk, nf in sorted(rows, key=lambda r: -r[2]):
    flag = ' <<<' if (l > 0 and p > npk + 0.08) else ''
    print(f'  {f:10s} lag={l:2d} peak={p:+.3f} c0={c0:+.3f} nullpk={npk:+.3f}{flag}')
