#!/usr/bin/env python3
"""Persistent per-second tick capture lane for the 7-book watch set.

Subscribes to PUBLIC Advanced Trade level2 + market_trades (no auth, no key)
and writes ONE compact JSON line per wall-clock second:

  {"t": "2026-09-29T13:40:01Z",
   "d": {"AMP-USD": [mid, spr_bps, imb5, bb_px, bb_sz, ba_px, ba_sz,
                     tr_n, buy_n, sell_n], ...}}

FIELDS order = [mid, spr_bps, imb5, bb_px, bb_sz, ba_px, ba_sz,
                tr_n, buy_n, sell_n]. imb5 is top-5 notional (bid-ask)/(bid+ask)
in [-1, 1]. tr_n/buy_n/sell_n are per-second trade prints (reset each second).

Daily rotation: ticks/ticks_YYYY-MM-DD.jsonl (~40MB/day).
Heartbeat: ticks/heartbeat.txt holds unix ts, refreshed every 10s --
the tick-capture-watchdog cron restarts this process when it goes stale.

Purpose: tick-resolution direction hunt. 30s buckets proved volatility is
abundant; direction is the scarce resource. This lane feeds direction_hunt.py,
which tests whether AMP lead moves predict subsequent DIRECTION at 5s-300s
horizons with realistic entry delay.

Survives: reconnects on websocket drop. Does NOT survive VM restarts --
the watchdog cron handles that. Self-ensures websocket-client (system pip
is wiped on restart).
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

# Cloudflare answers the 101 with "Connection: close"; accept it.
_orig = hs._get_resp_headers


def _patched(sock):
    status, resp = _orig(sock)
    if (isinstance(resp, dict) and status == 101
            and resp.get("connection", "").strip().lower() == "close"):
        resp["connection"] = "Upgrade"
    return status, resp


hs._get_resp_headers = _patched

BASE = os.path.dirname(os.path.abspath(__file__))
TICKDIR = os.path.join(BASE, "ticks")
os.makedirs(TICKDIR, exist_ok=True)
HB = os.path.join(TICKDIR, "heartbeat.txt")
LOG = os.path.join(TICKDIR, "capture.log")

PRODUCTS = ["AMP-USD", "XPL-USD", "KITE-USD",
            "2Z-USD", "MOG-USD", "VET-USD", "XYO-USD"]
# FIELDS = [mid, spr_bps, imb5, bb_px, bb_sz, ba_px, ba_sz, tr_n, buy_n, sell_n]

state = {"connected": False}
books = {p: {"bids": {}, "asks": {}} for p in PRODUCTS}
trades = {p: [0, 0.0, 0.0] for p in PRODUCTS}  # n, buy_notional, sell_notional
lock = threading.Lock()
cur_file, cur_date = None, None


def log(msg):
    with open(LOG, "a") as f:
        f.write("%s %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                          time.gmtime()), msg))


def out_handle():
    global cur_file, cur_date
    day = time.strftime("%Y-%m-%d", time.gmtime())
    if day != cur_date:
        if cur_file:
            cur_file.close()
        cur_file = open(os.path.join(TICKDIR, "ticks_%s.jsonl" % day), "a")
        cur_date = day
        log("rotated to ticks_%s.jsonl" % day)
    return cur_file


def on_message(ws, msg):
    try:
        d = json.loads(msg)
    except Exception:
        return
    ch = d.get("channel")
    for e in d.get("events", []):
        if ch == "l2_data" and e.get("type") == "update":
            pid = e.get("product_id")
            if pid not in books:
                continue
            for u in e.get("updates", []):
                side = u.get("side")
                if side not in ("bid", "offer"):
                    continue
                try:
                    px = float(u["price_level"])
                    sz = float(u["new_quantity"])
                except (TypeError, ValueError):
                    continue
                book = books[pid]["bids"] if side == "bid" else books[pid]["asks"]
                with lock:
                    if sz == 0:
                        book.pop(px, None)
                    else:
                        book[px] = sz
        elif ch == "market_trades" and e.get("type") == "update":
            for t in e.get("trades", []):
                pid = t.get("product_id")
                if pid not in books:
                    continue
                try:
                    notional = float(t["price"]) * float(t["size"])
                except (TypeError, ValueError):
                    continue
                s = str(t.get("side", "")).lower()
                with lock:
                    tr = trades[pid]
                    tr[0] += 1
                    tr[1 if s == "buy" else 2] += notional


def snapshot():
    row = {"t": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "d": {}}
    with lock:
        for pid in PRODUCTS:
            b = books[pid]["bids"]
            a = books[pid]["asks"]
            tr = trades[pid]
            trades[pid] = [0, 0.0, 0.0]
            if not b or not a:
                row["d"][pid] = None
                continue
            bb = max(b)
            ba = min(a)
            mid = (bb + ba) / 2
            tb = sorted(b.items(), reverse=True)[:5]
            ta = sorted(a.items())[:5]
            bn = sum(px * sz for px, sz in tb)
            an = sum(px * sz for px, sz in ta)
            row["d"][pid] = [round(mid, 10),
                             round((ba - bb) / mid * 1e4, 2),
                             round((bn - an) / (bn + an), 3) if bn + an else 0.0,
                             bb, round(b[bb], 4), ba, round(a[ba], 4),
                             tr[0], round(tr[1], 2), round(tr[2], 2)]
    return row


def main():
    log("tick capture starting")
    hb_at = 0
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
            w.send(json.dumps({"type": "subscribe", "product_ids": PRODUCTS,
                               "channel": "level2"}))
            w.send(json.dumps({"type": "subscribe", "product_ids": PRODUCTS,
                               "channel": "market_trades"}))
            state["connected"] = True
            log("websocket connected")

        ws.on_open = opener
        threading.Thread(target=ws.run_forever, daemon=True).start()
        for _ in range(150):  # wait up to 15s for connect
            if state["connected"]:
                break
            time.sleep(0.1)
        if not state["connected"]:
            log("connect timeout; retrying in 5s")
            time.sleep(5)
            continue
        try:
            while state["connected"]:
                time.sleep(max(1.0 - (time.time() % 1.0), 0.05))
                if not state["connected"]:
                    break
                out_handle().write(json.dumps(snapshot()) + "\n")
                out_handle().flush()
                if time.time() - hb_at > 10:
                    open(HB, "w").write(str(time.time()))
                    hb_at = time.time()
        except Exception as e:
            log("loop err %s" % repr(e)[:150])
        try:
            ws.close()
        except Exception:
            pass
        log("disconnected; reconnecting in 5s")
        time.sleep(5)


main()
