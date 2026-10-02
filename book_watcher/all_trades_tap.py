#!/usr/bin/env python3
"""All-asset trade-print tap (Mike: 'cover as many as possible').

Subscribes to PUBLIC Advanced Trade market_trades (no auth, no key) for ALL
402 USD products and logs every trade print as one compact JSON line:

  {"t": 1727745600, "d": [[12, 3, 450.0, 120.5], ...]}

d entries = [product_idx, n_trades, buy_notional, sell_notional], only for
products with >=1 trade that second. ~1000 trades/sec globally compress to
~5KB/s this way (vs ~60KB/s raw).

Trades-only (no level2): trade prints are sparse (~1 per hundreds of quote
updates), so 402 products stay well within websocket capacity.

Output: trades_all/trades_YYYY-MM-DD.jsonl, heartbeat at trades_all/heartbeat.txt.
Survives: reconnects on websocket drop. Does NOT survive VM restarts --
the all-trades-watchdog cron handles that. Self-ensures websocket-client.
"""
import sys, os, json, time, threading

try:
    import websocket
except ImportError:
    import subprocess
    subprocess.run([sys.executable, "-m", "pip", "install", "--quiet",
                    "--break-system-packages", "websocket-client"])
    import websocket
import websocket._handshake as hs

_orig = hs._get_resp_headers


def _patched(sock):
    status, resp = _orig(sock)
    if (isinstance(resp, dict) and status == 101
            and resp.get("connection", "").strip().lower() == "close"):
        resp["connection"] = "Upgrade"
    return status, resp


hs._get_resp_headers = _patched

BASE = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(BASE, "all_products.json")) as f:
    PRODUCTS = json.load(f)
PSET = set(PRODUCTS)

TDIR = os.path.join(BASE, "trades_all")
os.makedirs(TDIR, exist_ok=True)
HB = os.path.join(TDIR, "heartbeat.txt")
LOG = os.path.join(TDIR, "tap.log")

state = {"connected": False, "n": 0, "t0": time.time()}
cur_file, cur_date = None, None
bp_file, bp_date = None, None
flock = threading.Lock()
agg = {}  # epoch_sec -> {product_idx: [n, buy_notional, sell_notional]}
bigp = []  # individual prints >= BIG_N (exact-size bot fingerprint)
BIG_N = 400.0


def log(msg):
    with open(LOG, "a") as f:
        f.write("%s %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), msg))


def out_handle():
    global cur_file, cur_date
    day = time.strftime("%Y-%m-%d", time.gmtime())
    if day != cur_date:
        if cur_file:
            cur_file.close()
        cur_file = open(os.path.join(TDIR, "trades_%s.jsonl" % day), "a")
        cur_date = day
        log("rotated to trades_%s.jsonl" % day)
    return cur_file


def bp_handle():
    global bp_file, bp_date
    day = time.strftime("%Y-%m-%d", time.gmtime())
    if day != bp_date:
        if bp_file:
            bp_file.close()
        bp_file = open(os.path.join(TDIR, "bigprints_%s.jsonl" % day), "a")
        bp_date = day
    return bp_file


def on_message(ws, msg):
    try:
        d = json.loads(msg)
    except Exception:
        return
    if d.get("channel") != "market_trades":
        return
    with flock:
        for e in d.get("events", []):
            for t in e.get("trades", []):
                pid = t.get("product_id")
                if pid not in PSET:
                    continue
                try:
                    notional = float(t["price"]) * float(t["size"])
                except (TypeError, ValueError):
                    continue
                sec = int(time.time())  # bucket by ARRIVAL, not exchange ts:
                # on (re)subscribe the server replays a backlog of older prints;
                # exchange times would scatter them across phantom history.
                # Arrival-bucketing keeps the series causal; trim ~10 min after
                # each 'websocket connected' log line before analysis.
                a = agg.setdefault(sec, {})
                i = PRODUCTS.index(pid)
                r = a.get(i)
                if r is None:
                    a[i] = [0, 0.0, 0.0]
                    r = a[i]
                r[0] += 1
                r[1 if str(t.get("side", "")).lower() == "buy" else 2] += notional
                state["n"] += 1
                if notional >= BIG_N:
                    # exact-size fingerprint: individual large prints, precise
                    # notional, for predetermined-amount bot detection
                    bigp.append('{"t":%d,"p":%d,"s":%d,"n":%.2f}' % (
                        int(time.time() * 1000), i,
                        1 if str(t.get("side", "")).lower() == "buy" else 0,
                        notional))


def datetime_ts(s):
    try:
        import datetime
        return datetime.datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp() * 1000
    except Exception:
        return time.time() * 1000


def main():
    log("all-trades tap starting: %d products" % len(PRODUCTS))
    hb_at = 0
    stat_at = time.time()
    while True:
        state["connected"] = False
        ws = websocket.WebSocketApp(
            "wss://advanced-trade-ws.coinbase.com",
            header={"Origin": "https://advanced.trade.coinbase.com",
                    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) "
                                 "AppleWebKit/537.36"},
            on_message=on_message,
            on_error=lambda w, e: log("ws err %s" % repr(e)[:150]),
            on_close=lambda w, *a: state.update(connected=False))

        def opener(w):
            # one subscribe per ~100 products to stay under message limits
            for i in range(0, len(PRODUCTS), 100):
                w.send(json.dumps({"type": "subscribe",
                                   "product_ids": PRODUCTS[i:i + 100],
                                   "channel": "market_trades"}))
            state["connected"] = True
            log("websocket connected")

        ws.on_open = opener
        threading.Thread(target=ws.run_forever, daemon=True).start()
        for _ in range(150):
            if state["connected"]:
                break
            time.sleep(0.1)
        if not state["connected"]:
            log("connect timeout; retrying in 5s")
            time.sleep(5)
            continue
        try:
            last_flush = int(time.time())
            while state["connected"]:
                time.sleep(0.5)
                if not state["connected"]:
                    break
                now = int(time.time())
                # flush all complete seconds
                with flock:
                    done = [s for s in agg if s < now]
                    if done:
                        h = out_handle()
                        for s in sorted(done):
                            a = agg.pop(s)
                            # sparse: [idx, n, buy, sell] only for active products
                            h.write(json.dumps(
                                {"t": s, "d": [[i, r[0], round(r[1], 2), round(r[2], 2)]
                                               for i, r in a.items()]},
                                separators=(",", ":")) + "\n")
                        h.flush()
                    if bigp:
                        bh = bp_handle()
                        bh.write("\n".join(bigp) + "\n")
                        bh.flush()
                        bigp.clear()
                if time.time() - hb_at > 10:
                    open(HB, "w").write(str(time.time()))
                    hb_at = time.time()
                if time.time() - stat_at > 300:
                    el = time.time() - state["t0"]
                    log("trades=%d (%.0f/s)" % (state["n"], state["n"] / max(el, 1)))
                    stat_at = time.time()
        except Exception as e:
            log("loop err %s" % repr(e)[:150])
        try:
            ws.close()
        except Exception:
            pass
        log("disconnected; reconnecting in 5s")
        time.sleep(5)


if __name__ == "__main__":
    main()
