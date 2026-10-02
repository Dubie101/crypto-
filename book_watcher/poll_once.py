#!/usr/bin/env python3
"""Single-pass book rotation poll for cron. Appends snapshots + events, updates state."""
import json, urllib.request, datetime, statistics, os

BASE = "/home/hatch/workspace/goals/paper-trading-simulator/hidden_files/book_watcher"
PRODUCTS = ["AMP-USD","XPL-USD","KITE-USD","2Z-USD","MOG-USD","VET-USD","XYO-USD"]
SNAP = f"{BASE}/snapshots_rot.jsonl"
EVF = f"{BASE}/events_rot.jsonl"
STATE = f"{BASE}/state_rot.json"

def f(x):
    try: return float(x)
    except: return 0.0

def fetch(pid):
    req = urllib.request.Request(f"https://api.exchange.coinbase.com/products/{pid}/book?level=2",
                                 headers={"User-Agent":"beatmap/1.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.load(r)

def metrics(b):
    bids=[(f(p),f(s)) for p,s,_ in b['bids']]; asks=[(f(p),f(s)) for p,s,_ in b['asks']]
    if not bids or not asks: return None
    mid=(bids[0][0]+asks[0][0])/2
    spread=(asks[0][0]-bids[0][0])/mid*1e4
    def bandv(lv,lo,hi): return sum(p*s for p,s in lv if lo<=abs(p-mid)/mid*100<hi)
    out={}
    for name,(lo,hi) in {"near":(0.1,1.0),"deep":(1.0,5.0)}.items():
        bv,av=bandv(bids,lo,hi),bandv(asks,lo,hi)
        out[f"imb_{name}"]=(bv-av)/(bv+av) if bv+av>0 else 0.0
    return {"mid":mid,"spread_bps":spread,"n_bid":len(bids),"n_ask":len(asks),
            "imb_near":out["imb_near"],"imb_deep":out["imb_deep"],
            "wall_bid":max((p*s for p,s in bids[:10]), default=0),
            "wall_ask":max((p*s for p,s in asks[:10]), default=0)}

state = json.load(open(STATE)) if os.path.exists(STATE) else {}
ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
new_state, n_ev, n_ok = {}, 0, 0
with open(SNAP,"a") as sf, open(EVF,"a") as ef:
    for pid in PRODUCTS:
        try: m = metrics(fetch(pid))
        except Exception as e:
            print(f"{pid} fetch fail: {e}"); continue
        if not m: continue
        n_ok += 1
        sf.write(json.dumps({"ts":ts,"product":pid,**{k:(round(v,4) if isinstance(v,float) else v) for k,v in m.items()}})+"\n")
        new_state[pid] = {"m":m,"spreads":(state.get(pid,{}).get("spreads",[])+[m["spread_bps"]])[-24:]}
        p = state.get(pid,{}).get("m")
        if p:
            ev=None
            if p["wall_ask"]>0 and (p["wall_ask"]-m["wall_ask"])/p["wall_ask"]>0.30:
                ev=("wall_pull_ask",f"${p['wall_ask']:,.0f}->${m['wall_ask']:,.0f}")
            elif p["wall_bid"]>0 and (p["wall_bid"]-m["wall_bid"])/p["wall_bid"]>0.30:
                ev=("wall_pull_bid",f"${p['wall_bid']:,.0f}->${m['wall_bid']:,.0f}")
            elif p["wall_ask"]>0 and m["wall_ask"]/p["wall_ask"]>3:
                ev=("wall_slam_ask",f"${p['wall_ask']:,.0f}->${m['wall_ask']:,.0f}")
            elif p["wall_bid"]>0 and m["wall_bid"]/p["wall_bid"]>3:
                ev=("wall_slam_bid",f"${p['wall_bid']:,.0f}->${m['wall_bid']:,.0f}")
            med=statistics.median(state[pid]["spreads"]) if state.get(pid,{}).get("spreads") else 0
            if m["spread_bps"]>max(2*med,1.0):
                ev=("spread_blowout",f"{med:.1f}->{m['spread_bps']:.1f}bps")
            if p["imb_near"]*m["imb_near"]<0 and abs(m["imb_near"]-p["imb_near"])>0.3:
                ev=("imbalance_flip",f"{p['imb_near']:+.2f}->{m['imb_near']:+.2f}")
            if ev:
                ef.write(json.dumps({"ts":ts,"product":pid,"event":ev[0],"detail":ev[1],
                    "mid_chg_pct":round((m["mid"]-p["mid"])/p["mid"]*100,3)})+"\n")
                n_ev+=1
json.dump(new_state, open(STATE,"w"))
print(f"poll ok: {n_ok}/{len(PRODUCTS)} products, {n_ev} events @ {ts}")
