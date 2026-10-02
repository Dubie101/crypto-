"""Mike's delayed-copy hypothesis test.

Claim: major assets set the pattern; the rest of the market copies with a delay.
If true, follower returns should correlate MORE with the leader at a positive lag
(follower shifted back by delta) than contemporaneously -- systematically, across
many assets, beyond what random time-shifts produce.

Method:
  leader   = BTC-USD 1-min returns (plus a majors index = mean of 8 majors)
  followers= ~36 assets stratified by liquidity (trade counts from today's tape)
  data     = 1-min public candles, ~20h
  stat     = argmax lag in 0..15 min of cross-correlation(leader, follower)
  null     = 200 circular shifts of each follower series (shift > 30 min),
             same argmax-lag procedure -> null distribution
Guards:
  - followers must be actually trading (min trade count) to avoid the stale-quote
    artifact that faked "lead-lag" in the earlier IOTX case
  - contemporaneous correlation reported alongside, so we can see whether the
    lagged peak is genuinely stronger or just noise reshuffled
"""
import json, math, random, time, urllib.request
from collections import defaultdict

BW = '/home/hatch/workspace/goals/paper-trading-simulator/hidden_files/book_watcher'
MAJORS = ['BTC-USD','ETH-USD','SOL-USD','XRP-USD','BNB-USD','DOGE-USD','ZEC-USD','SUI-USD']
MAXLAG = 15
PAGES = 4          # 4 x 300 one-minute candles ~= 20h
SLEEP = 0.15

def get(url):
    req = urllib.request.Request(url, headers={'User-Agent': 'muse-delayed-copy-test'})
    return json.load(urllib.request.urlopen(req, timeout=25))

def candles_1m(product):
    out = []
    end = None
    for _ in range(PAGES):
        u = f'https://api.exchange.coinbase.com/products/{product}/candles?granularity=60'
        if end: u += f'&end={end}'
        try:
            data = get(u)
        except Exception as e:
            print('  fetch fail', product, str(e)[:80]); return []
        if not data: break
        data = sorted(data)  # ascending by time
        out = data + out
        end = data[0][0] - 1
        time.sleep(SLEEP)
        if len(data) < 300: break
    # dedupe, keep ascending
    seen = {}
    for c in out: seen[c[0]] = c
    return [seen[t] for t in sorted(seen)]

def returns(closes):
    return [(closes[i+1]-closes[i])/closes[i] for i in range(len(closes)-1) if closes[i]]

def xcorr(a, b, lag):
    # corr(a[t], b[t+lag]): positive lag => b follows a
    n = len(a) - lag
    if n < 50: return None
    x, y = a[:n], b[lag:lag+n]
    mx, my = sum(x)/n, sum(y)/n
    sx = sum((v-mx)**2 for v in x); sy = sum((v-my)**2 for v in y)
    if sx == 0 or sy == 0: return None
    return sum((v-mx)*(w-my) for v, w in zip(x, y))/math.sqrt(sx*sy)

def main():
    prods = json.load(open(f'{BW}/all_products.json'))
    # liquidity ranking from today's trade tape
    tc = defaultdict(int)
    for line in open(f'{BW}/trades_all/trades_2026-10-01.jsonl'):
        d = json.loads(line)
        for pi, c, bn, sn in d['d']: tc[pi] += c
    ranked = sorted(((c, prods[i]) for i, c in tc.items()
                     if prods[i] not in MAJORS and c >= 60),
                    reverse=True)
    print('active non-major products:', len(ranked))
    picks = []
    for lo, hi, n in [(10, 60, 12), (60, 150, 12), (150, 400, 12)]:
        seg = ranked[lo:hi]
        step = max(1, len(seg)//n)
        picks += [p for _, p in seg[::step][:n]]
    print('followers:', len(picks))

    series = {}
    for p in MAJORS + picks:
        cs = candles_1m(p)
        if len(cs) < 400:
            print('  thin candles, dropping', p, len(cs)); continue
        closes = [c[4] for c in cs]
        series[p] = returns(closes)
        print(f'  {p}: {len(cs)} candles')
        time.sleep(SLEEP)

    majors = [p for p in MAJORS if p in series]
    folls = [p for p in picks if p in series]
    # align on common length (tail)
    L = min(len(series[p]) for p in majors + folls)
    S = {p: series[p][-L:] for p in majors + folls}
    btc = S['BTC-USD']
    midx = [sum(S[p][i] for p in majors)/len(majors) for i in range(L)]
    print('aligned minutes:', L)

    random.seed(7)
    results = []
    for f in folls:
        real = [xcorr(btc, S[f], lag) for lag in range(MAXLAG+1)]
        realm = [xcorr(midx, S[f], lag) for lag in range(MAXLAG+1)]
        if any(v is None for v in real): continue
        lag_btc = max(range(MAXLAG+1), key=lambda l: real[l])
        lag_midx = max(range(MAXLAG+1), key=lambda l: realm[l])
        # null: circular shifts
        null_lags = []
        null_peaks = []
        for _ in range(200):
            sh = random.randint(31, L-31)
            shf = S[f][sh:] + S[f][:sh]
            cs = [xcorr(btc, shf, lag) for lag in range(MAXLAG+1)]
            bl = max(range(MAXLAG+1), key=lambda l: cs[l])
            null_lags.append(bl); null_peaks.append(cs[bl])
        results.append(dict(f=f, lag_btc=lag_btc, peak_btc=real[lag_btc],
                            c0_btc=real[0], lag_midx=lag_midx,
                            peak_midx=realm[lag_midx], c0_midx=realm[0],
                            null_mean_peak=sum(null_peaks)/len(null_peaks),
                            null_pos_frac=sum(1 for l in null_lags if l > 0)/len(null_lags)))
    out = dict(n=len(results), L=L, results=results)
    json.dump(out, open(f'{BW}/delayed_copy_test.json', 'w'), indent=1)

    lags = [r['lag_btc'] for r in results]
    pos = sum(1 for l in lags if l > 0)
    print(f'\nBTC leader: {len(results)} followers, argmax-lag>0 in {pos}/{len(results)}')
    print('lag histogram:', {l: lags.count(l) for l in sorted(set(lags))})
    pk = [r['peak_btc'] for r in results]; c0 = [r['c0_btc'] for r in results]
    print('mean peak(lagged): %.3f  mean c0: %.3f' % (sum(pk)/len(pk), sum(c0)/len(c0)))
    beats = sum(1 for r in results if r['peak_btc'] > r['null_mean_peak'] + 0.05)
    print(f'followers beating null mean peak by >0.05: {beats}/{len(results)}')
    for r in sorted(results, key=lambda r: -r['peak_btc'])[:8]:
        print(f"  {r['f']:12s} lag={r['lag_btc']:2d} peak={r['peak_btc']:+.3f} c0={r['c0_btc']:+.3f} nullpk={r['null_mean_peak']:+.3f}")

if __name__ == '__main__':
    main()
