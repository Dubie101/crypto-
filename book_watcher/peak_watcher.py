#!/usr/bin/env python3
"""Peak-dislocation paper trader — the 30-second-ahead lane.

Watches BTC/ETH/SOL/XRP/BNB/DOGE/ZEC/SUI every 30s, min-max normalizes each
over a trailing 120-minute window (Mike's vector-alignment method), and
measures each major's distance from the eight-major composite pattern in bps.

Rule (backtested 2026-10-01 on 7d of 1-min data):
  - open a paper peak trade when |deviation| >= 200 bps from the composite
  - direction: reversion — the rule-set bots drag strays back into formation
  - close after 30 minutes; capture = decay of |deviation| in bps
  - backtest: 7 events in 7d, 7/7 net-positive after 120 bps round-trip fees

All paper. Fixed notional. Public data only, no keys, no live orders.
"""
import json, math, os, sys, time, urllib.request, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
STATE = os.path.join(HERE, "peak_watcher_state.json")
HEARTBEAT = os.path.join(HERE, "peak_watcher_heartbeat.txt")
TRADE_DIR = os.path.join(HERE, "peak_trades")

MAJORS = ["BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD",
          "BNB-USD", "DOGE-USD", "ZEC-USD", "SUI-USD"]
TICK_URL = "https://api.exchange.coinbase.com/products/%s/ticker"
CANDLE_URL = "https://api.exchange.coinbase.com/products/%s/candles"

CADENCE_S = 30
WINDOW_PTS = 240          # 120 min at 30s cadence
MIN_PTS = 60              # minimum history before signals
DEV_THRESH_BPS = 200.0
HOLD_S = 30 * 60
COOLDOWN_S = 60 * 60
NOTIONAL_CAD = 100.0
FEE_BPS = 120.0           # 0.60% taker per side, round trip
MAX_CONCURRENT = 3


def get(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": "nova-peak/1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def fetch_tickers():
    out = {}
    for pid in MAJORS:
        try:
            t = get(TICK_URL % pid)
            out[pid] = (float(t["price"]), t.get("time"))
        except Exception:
            pass
    return out


def backfill(pid, minutes=120):
    """Seed history with 1-min candles so signals start immediately."""
    end = datetime.datetime.now(datetime.timezone.utc)
    start = end - datetime.timedelta(minutes=minutes)
    url = (CANDLE_URL % pid +
           "?granularity=60&start=%s&end=%s" % (start.isoformat(), end.isoformat()))
    try:
        rows = sorted(get(url, timeout=25), key=lambda c: c[0])
        return [[c[0], c[4]] for c in rows]
    except Exception:
        return []


def load_state():
    try:
        with open(STATE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"history": {}, "positions": [], "cooldown": {}}


def save_state(s):
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(s, f)
    os.replace(tmp, STATE)


def log_line(kind, obj):
    os.makedirs(TRADE_DIR, exist_ok=True)
    day = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    path = os.path.join(TRADE_DIR, "%s_%s.jsonl" % (kind, day))
    obj["logged_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    with open(path, "a") as f:
        f.write(json.dumps(obj) + "\n")


def deviations(history):
    """Per-major distance from the composite pattern, in bps of own price.
    Returns None until every major has MIN_PTS points."""
    devs = {}
    norms = {}
    ranges = {}
    for pid in MAJORS:
        h = history.get(pid, [])
        if len(h) < MIN_PTS:
            return None
        w = [p for _, p in h[-WINDOW_PTS:]]
        lo, hi = min(w), max(w)
        last = w[-1]
        norms[pid] = (last - lo) / (hi - lo) if hi > lo else 0.5
        ranges[pid] = (hi - lo) / last * 1e4 if last else 0.0
    comp = sum(norms.values()) / len(norms)
    for pid in MAJORS:
        devs[pid] = (norms[pid] - comp) * ranges[pid]
    return devs


def tick(state):
    now = time.time()
    ticks = fetch_tickers()
    for pid, (price, tstr) in ticks.items():
        h = state["history"].setdefault(pid, [])
        h.append([now, price])
        del h[:-WINDOW_PTS]

    # seed on first run
    if not state.get("seeded"):
        for pid in MAJORS:
            if len(state["history"].get(pid, [])) < MIN_PTS:
                seed = backfill(pid)
                state["history"][pid] = (seed + state["history"].get(pid, []))[-WINDOW_PTS:]
        state["seeded"] = True

    devs = deviations(state["history"])
    open_pids = {p["pid"] for p in state["positions"]}

    # close matured positions
    for p in list(state["positions"]):
        if now - p["opened_ts"] >= HOLD_S and devs is not None and p["pid"] in devs:
            exit_dev = devs[p["pid"]]
            capture = abs(p["entry_dev_bps"]) - abs(exit_dev)
            gross = capture / 1e4 * p["notional_cad"]
            fee = FEE_BPS / 1e4 * p["notional_cad"]
            log_line("paper", {
                "event": "CLOSE", "pid": p["pid"],
                "opened_at": datetime.datetime.fromtimestamp(
                    p["opened_ts"], datetime.timezone.utc).isoformat(),
                "entry_dev_bps": round(p["entry_dev_bps"], 1),
                "exit_dev_bps": round(exit_dev, 1),
                "capture_bps": round(capture, 1),
                "notional_cad": p["notional_cad"],
                "gross_cad": round(gross, 4),
                "fee_cad": round(fee, 4),
                "net_cad": round(gross - fee, 4),
            })
            state["positions"].remove(p)
            state["cooldown"][p["pid"]] = now
            open_pids.discard(p["pid"])

    # open on fresh dislocations
    if devs is not None:
        for pid in MAJORS:
            d = devs[pid]
            if (abs(d) >= DEV_THRESH_BPS and pid not in open_pids
                    and len(state["positions"]) < MAX_CONCURRENT
                    and now - state["cooldown"].get(pid, 0) >= COOLDOWN_S):
                ticks_now = fetch_tickers()
                price = ticks_now.get(pid, (None,))[0]
                state["positions"].append({
                    "pid": pid, "opened_ts": now,
                    "entry_dev_bps": d, "entry_price": price,
                    "notional_cad": NOTIONAL_CAD,
                })
                open_pids.add(pid)
                log_line("events", {
                    "event": "PEAK", "pid": pid,
                    "dev_bps": round(d, 1),
                    "direction": "short-deviation" if d > 0 else "long-deviation",
                    "price": price,
                })

    save_state(state)
    with open(HEARTBEAT, "w") as f:
        f.write(datetime.datetime.now(datetime.timezone.utc).isoformat())


def main():
    once = "--once" in sys.argv
    state = load_state()
    if once:
        tick(state)
        print(json.dumps({
            "positions": state["positions"],
            "devs": {k: round(v, 1) for k, v in
                     (deviations(state["history"]) or {}).items()},
        }, indent=1))
        return
    while True:
        try:
            tick(state)
        except Exception as e:
            log_line("events", {"event": "ERROR", "error": str(e)[:200]})
        time.sleep(CADENCE_S)


if __name__ == "__main__":
    main()
