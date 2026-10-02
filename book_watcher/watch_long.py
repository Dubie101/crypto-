import json, urllib.request, time, datetime, statistics, sys

PRODUCTS = ["AMP-USD","XPL-USD","KITE-USD","2Z-USD","MOG-USD","VET-USD","XYO-USD"]
BASE = "/home/hatch/workspace/goals/paper-trading-simulator/hidden_files/book_watcher"
INTERVAL = 30
POLLS = 360  # 3 hours

def f(x):
    try: return float(x)
    except: return 0.0

def fetch(pid):
    req = urllib.request.Request(f"https://api.exchange.coinbase.com/products/{pid}/book?level=2",
                                 headers={"User-Agent":"beatmap/1.0"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)

def metrics(b):
    bids=[(f(p),f(s)) for p,s,_ in b['bids']]; asks=[(f(p),f(s)) for p,s,_ in b['asks']]
    if not bids or not asks: return None
    mid=(bids[0][0]+asks[0][0])/2
    spread=(asks[0][0]-bids[0][0])/mid*1e4
    def bandv(lv,lo,hi): return sum(p*s for p,s in lv if lo<=abs(p-mid)/mid*100<hi)
    nb, na = len(bids), len(asks)
    out={}
    for name,(lo,hi) in {"near":(0.1,1.0),"deep":(1.0,5.0)}.items():
        bv,av=bandv(bids,lo,hi),bandv(asks,lo,hi)
        out[f"imb_{name}"]=(bv-av)/(bv+av) if bv+av>0 else 0.0
    top_bid=max((p*s for p,s in bids[:10]), default=0)
    top_ask=max((p*s for p,s in asks[:10]), default=0)
    return {"mid":mid,"spread_bps":spread,"n_bid":nb,"n_ask":na,
            "imb_near":out["imb_near"],"imb_deep":out["imb_deep"],
            "wall_bid":top_bid,"wall_ask":top_ask}

prev={}; spread_hist={p:[] for p in PRODUCTS}; events=[]
snap=open(f"{BASE}/snapshots_long.jsonl","a"); evf=open(f"{BASE}/events_long.jsonl","a")
t0=time.time()
for i in range(POLLS):
    ts=datetime.datetime.now(datetime.timezone.utc).isoformat()
    for pid in PRODUCTS:
        try: m=metrics(fetch(pid))
        except Exception as e:
            print(f"[{ts}] {pid} fetch fail: {e}", flush=True); continue
        if not m: continue
        snap.write(json.dumps({"ts":ts,"product":pid,**{k:(round(v,4) if isinstance(v,float) else v) for k,v in m.items()}})+"\n")
        spread_hist[pid].append(m["spread_bps"])
        if pid in prev:
            p=prev[pid]; ev=None
            if p["wall_ask"]>0 and (p["wall_ask"]-m["wall_ask"])/p["wall_ask"]>0.30:
                ev=("wall_pull_ask", f"ask wall ${p['wall_ask']:,.0f}->${m['wall_ask']:,.0f}")
            elif p["wall_bid"]>0 and (p["wall_bid"]-m["wall_bid"])/p["wall_bid"]>0.30:
                ev=("wall_pull_bid", f"bid wall ${p['wall_bid']:,.0f}->${m['wall_bid']:,.0f}")
            elif p["wall_ask"]>0 and m["wall_ask"]/p["wall_ask"]>3:
                ev=("wall_slam_ask", f"ask wall ${p['wall_ask']:,.0f}->${m['wall_ask']:,.0f}")
            elif p["wall_bid"]>0 and m["wall_bid"]/p["wall_bid"]>3:
                ev=("wall_slam_bid", f"bid wall ${p['wall_bid']:,.0f}->${m['wall_bid']:,.0f}")
            med=statistics.median(spread_hist[pid][-12:])
            if m["spread_bps"] > max(2*med, 1.0):  # floor kills sub-bps noise
                ev=("spread_blowout", f"{med:.1f}->{m['spread_bps']:.1f} bps")
            if p["imb_near"]*m["imb_near"]<0 and abs(m["imb_near"]-p["imb_near"])>0.3:
                ev=("imbalance_flip", f"{p['imb_near']:+.2f}->{m['imb_near']:+.2f}")
            if ev:
                rec={"ts":ts,"product":pid,"event":ev[0],"detail":ev[1],
                     "mid_chg_pct":round((m["mid"]-p["mid"])/p["mid"]*100,3)}
                events.append(rec); evf.write(json.dumps(rec)+"\n"); evf.flush()
                print(f"[{ts}] {pid} {ev[0]}: {ev[1]} (mid {rec['mid_chg_pct']:+.3f}%)", flush=True)
        prev[pid]=m
    snap.flush()
    time.sleep(max(0, INTERVAL-(time.time()-t0-(i+1)*INTERVAL)))
snap.close(); evf.close()
print(f"DONE polls={POLLS} events={len(events)}")
for e in events: print(json.dumps(e))
