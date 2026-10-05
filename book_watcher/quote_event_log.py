#!/usr/bin/env python3
"""Quote-event logger for the XPL book-level investigation (Lane 3).

Logs EVERY level2 delta event and EVERY individual trade print for the
F1 cluster (XPL/VET/KITE/2Z), plus every mid-price change derived from the
in-memory book. This is the prerequisite instrument for reconstructing the
exact sequence around >=5bps XPL residual events:

  trade -> book change -> mid change   (execution-mediated)
  book change -> mid change -> trade   (quote-mediated)
  book change -> mid change, no trade  (pure quote repricing)

Public Advanced Trade websocket only (level2 + market_trades). No auth,
no trading, read-only.

SIDE SEMANTICS (verified 2026-10-03): Advanced Trade market_trades `side`
labels the MAKER side, not the aggressive side ("buy"-labeled flow coincided
with price down ~-3.3bps). Trade events record side AS LABELED -- "buy" means
a resting BID was the maker (aggressive seller hit it). Do NOT reinterpret.

Event schema (one JSON object per line, monotonic `seq` per process run):
  l2:    {seq, kind:'l2', ts, product, side:'bid'|'ask', price,
          old_qty, new_qty, removal:bool}
  mid:   {seq, kind:'mid', ts, product, old_mid, new_mid, chg_bps}
  trade: {seq, kind:'trade', ts, product, price, size, side:'buy'|'sell',
          trade_id}

ts = local receipt time, ISO-8601 UTC.
Output: quote_events/events_YYYY-MM-DD.jsonl (UTC date rollover).
Heartbeat: quote_events/heartbeat.txt (float unix ts), every 10s.

Durability: Cloudflare 101 handshake shim, reactive reconnect on close,
60s silence watchdog, reconnect-storm backoff (ported from ws_tap.py).
"""
import websocket, json, time, threading, os, sys
from collections import deque
import websocket._handshake as hs

# --- loud dependency check at startup ---
try:
    import websocket as _ws_check  # noqa: F401
except ImportError:
    print("FATAL: websocket-client not importable; install it first", file=sys.stderr)
    sys.exit(1)

# Cloudflare answers the 101 with "Connection: close"; accept it.
_orig_resp = hs._get_resp_headers
def _patched_resp(sock):
    status, resp = _orig_resp(sock)
    if isinstance(resp, dict) and resp.get("connection", "").strip().lower() == "close" and status == 101:
        resp["connection"] = "Upgrade"
    return status, resp
hs._get_resp_headers = _patched_resp

BASE = os.path.dirname(os.path.abspath(__file__))
OUTDIR = os.path.join(BASE, "quote_events")
os.makedirs(OUTDIR, exist_ok=True)
PRODUCTS = ["XPL-USD", "VET-USD", "KITE-USD", "2Z-USD"]
HEARTBEAT = os.path.join(OUTDIR, "heartbeat.txt")


def utc_iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + ".%03dZ" % int((t % 1) * 1000)


def main():
    books = {p: {"bids": {}, "asks": {}} for p in PRODUCTS}
    prev_mid = {}
    lock = threading.Lock()
    pending = deque()          # serialized event dicts awaiting file write
    seq = [0]                  # monotonic sequence, mutated under lock
    state = {"ws": None, "last_msg": time.time(), "closed": False,
             "reconnects": deque()}

    def emit(ev):
        with lock:
            seq[0] += 1
            ev["seq"] = seq[0]
            pending.append(ev)

    def book_mid(pid):
        b = books[pid]["bids"]; a = books[pid]["asks"]
        if not b or not a:
            return None
        bb = max(b); ba = min(a)
        mid = (bb + ba) / 2
        return mid if mid > 0 else None

    def on_message(ws, msg):
        try:
            d = json.loads(msg)
        except Exception:
            return
        t_recv = time.time()
        state["last_msg"] = t_recv
        ch = d.get("channel")
        for e in d.get("events", []):
            et = e.get("type")
            if ch == "l2_data" and et == "update":
                pid = e.get("product_id")
                if pid not in books:
                    continue
                touched = False
                for u in e.get("updates", []):
                    s = u.get("side")
                    if s == "offer":
                        s = "ask"
                    if s not in ("bid", "ask"):
                        continue
                    try:
                        px = float(u.get("price_level")); sz = float(u.get("new_quantity"))
                    except (TypeError, ValueError):
                        continue
                    with lock:
                        book = books[pid]["bids"] if s == "bid" else books[pid]["asks"]
                        old = book.get(px, 0.0)
                        if sz == 0:
                            book.pop(px, None)
                        else:
                            book[px] = sz
                        removal = (sz == 0)
                    emit({"kind": "l2", "ts": utc_iso(t_recv), "product": pid,
                          "side": s, "price": px, "old_qty": old,
                          "new_qty": sz, "removal": removal})
                    touched = True
                if touched:
                    with lock:
                        mid = book_mid(pid)
                    pm = prev_mid.get(pid)
                    if mid is not None and pm is not None and mid != pm:
                        emit({"kind": "mid", "ts": utc_iso(t_recv), "product": pid,
                              "old_mid": pm, "new_mid": mid,
                              "chg_bps": (mid - pm) / pm * 1e4})
                    if mid is not None:
                        prev_mid[pid] = mid
            elif ch == "market_trades" and et == "update":
                for t in e.get("trades", []):
                    tpid = t.get("product_id")
                    if tpid not in books:
                        continue
                    try:
                        tpx = float(t.get("price")); tsz = float(t.get("size"))
                    except (TypeError, ValueError):
                        continue
                    emit({"kind": "trade", "ts": utc_iso(t_recv), "product": tpid,
                          "price": tpx, "size": tsz,
                          "side": str(t.get("side", "")).lower(),  # AS LABELED (maker side)
                          "trade_id": t.get("trade_id")})

    def on_close(w, *a):
        print("WS closed", file=sys.stderr, flush=True)
        state["closed"] = True

    def safe_connect():
        try:
            connect()
        except Exception as ex:
            print("connect failed", repr(ex)[:200], file=sys.stderr, flush=True)

    def connect():
        ws = websocket.WebSocketApp(
            "wss://advanced-trade-ws.coinbase.com",
            header={"Origin": "https://advanced.trade.coinbase.com",
                    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"},
            on_message=on_message,
            on_error=lambda w, e: print("ERR", repr(e)[:200], file=sys.stderr),
            on_close=on_close)

        def opener(w):
            w.send(json.dumps({"type": "subscribe", "product_ids": PRODUCTS, "channel": "level2"}))
            w.send(json.dumps({"type": "subscribe", "product_ids": PRODUCTS, "channel": "market_trades"}))
        ws.on_open = opener
        threading.Thread(target=ws.run_forever, daemon=True).start()
        state["ws"] = ws

    connect()
    out = None
    out_day = None
    last_hb = 0.0

    try:
        while True:
            now = time.time()
            # reactive reconnect on drop
            if state["closed"]:
                print("WS dropped, reconnecting", file=sys.stderr, flush=True)
                state["closed"] = False
                try:
                    state["ws"].close()
                except Exception:
                    pass
                now2 = time.time()
                rq = state["reconnects"]
                while rq and now2 - rq[0] > 120:
                    rq.popleft()
                rq.append(now2)
                if len(rq) >= 5:
                    print("WS reconnect storm, backing off 30s", file=sys.stderr, flush=True)
                    rq.clear()
                    time.sleep(30)
                else:
                    time.sleep(2)
                safe_connect()
                state["last_msg"] = time.time()
            elif now - state["last_msg"] > 60:
                print("WS silent 60s, reconnecting", file=sys.stderr, flush=True)
                try:
                    state["ws"].close()
                except Exception:
                    pass
                time.sleep(2)
                safe_connect()
                state["last_msg"] = time.time()

            # drain pending events to the day's file (flush errors can't stall the run)
            try:
                day = time.strftime("%Y-%m-%d", time.gmtime(now))
                if out is None or day != out_day:
                    if out is not None:
                        out.close()
                    out = open(os.path.join(OUTDIR, f"events_{day}.jsonl"), "a")
                    out_day = day
                batch = []
                with lock:
                    while pending:
                        batch.append(pending.popleft())
                for ev in batch:
                    out.write(json.dumps(ev) + "\n")
                if batch:
                    out.flush()
            except Exception as ex:
                print("drain err", repr(ex)[:200], file=sys.stderr, flush=True)

            # heartbeat every 10s
            if now - last_hb >= 10:
                try:
                    with open(HEARTBEAT, "w") as fh:
                        fh.write(str(time.time()))
                except Exception as ex:
                    print("heartbeat err", repr(ex)[:120], file=sys.stderr, flush=True)
                last_hb = now

            time.sleep(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            if out is not None:
                out.close()
        except Exception:
            pass
        try:
            state["ws"].close()
        except Exception:
            pass


main()
