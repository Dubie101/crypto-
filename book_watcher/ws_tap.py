#!/usr/bin/env python3
"""Durable public websocket tap: level2 + market_trades for the 7-book watch set.

Appends one aggregate bucket per product per 30s to ws_buckets.jsonl.
Cron-friendly: captures --seconds then exits. Uses only PUBLIC Advanced
Trade channels (level2, market_trades) -- no API key, no authentication.

Fill/cancel join: level2 removals are matched against trade prints at the
same price within MATCH_WINDOW seconds. Matched removals = fills (real
liquidity consumption); the rest = cancels. Fills are a conservative lower
bound -- a delayed or aggregated trade print misses the window and its
removal counts as a cancel.

Bucket fields per product:
  ts, product, updates, bid_set, bid_rm, ask_set, ask_rm,
  trades, buy_notional, sell_notional,
  bid_fills, ask_fills, bid_fill_notional, ask_fill_notional,
  bid_cancels, ask_cancels,
  mid, spread_bps, imb5 (top-5 notional imbalance), wall_bid, wall_ask,
  mid_chg_bps (vs previous bucket close)
"""
import websocket, json, time, threading, argparse, os, sys
from collections import defaultdict, deque
import websocket._handshake as hs

# Cloudflare answers the 101 with "Connection: close"; accept it.
_orig_resp = hs._get_resp_headers
def _patched_resp(sock):
    status, resp = _orig_resp(sock)
    if isinstance(resp, dict) and resp.get("connection", "").strip().lower() == "close" and status == 101:
        resp["connection"] = "Upgrade"
    return status, resp
hs._get_resp_headers = _patched_resp

BASE = os.path.dirname(os.path.abspath(__file__))
PRODUCTS = ["AMP-USD", "XPL-USD", "KITE-USD",
            "2Z-USD", "MOG-USD", "VET-USD", "XYO-USD"]
BUCKET = 30
MATCH_WINDOW = 2.0  # seconds: removal<->trade join window for fill classification
PX_TOL = 1e-9       # relative price tolerance for the join


def match_removals(dq, tpx, now):
    """Match a trade print at price tpx against recent level removals.

    Returns (matched, kept): matched removals at the trade price within
    MATCH_WINDOW are fills; the rest are kept (unexpired) or dropped.
    """
    tol = PX_TOL * max(tpx, 1e-18)
    matched = 0
    kept = deque()
    for (rt, rpx) in dq:
        if now - rt > MATCH_WINDOW:
            continue  # expired
        if abs(rpx - tpx) <= tol:
            matched += 1
        else:
            kept.append((rt, rpx))
    return matched, kept


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=int, default=270)
    ap.add_argument("--out", default=os.path.join(BASE, "ws_buckets.jsonl"))
    args = ap.parse_args()

    books = {p: {"bids": {}, "asks": {}} for p in PRODUCTS}
    lock = threading.Lock()
    cnt = {p: defaultdict(int) for p in PRODUCTS}
    trade_n = {p: defaultdict(float) for p in PRODUCTS}
    removals = {p: {"bid": deque(), "ask": deque()} for p in PRODUCTS}  # (t, px) of level removals
    fills = {p: defaultdict(float) for p in PRODUCTS}  # bid_n/ask_n, bid_notional/ask_notional
    prev_mid = {}
    out = open(args.out, "a")
    stop_at = time.time() + args.seconds
    bucket_end = (int(time.time()) // BUCKET + 1) * BUCKET

    def set_level(pid, side, px, sz):
        book = books[pid]["bids"] if side == "bid" else books[pid]["asks"]
        with lock:
            if sz == 0:
                book.pop(px, None)
            else:
                book[px] = sz

    def on_message(ws, msg):
        try:
            d = json.loads(msg)
        except Exception:
            return
        state["last_msg"] = time.time()
        ch = d.get("channel")
        for e in d.get("events", []):
            et = e.get("type")
            if ch == "l2_data":
                # l2_data events carry product_id; market_trades events do NOT
                # (product_id lives on each trade) -- filter per channel below.
                pid = e.get("product_id")
                if pid not in books:
                    continue
                for u in e.get("updates", []):
                    side = u.get("side")
                    if side not in ("bid", "offer"):
                        continue
                    try:
                        px = float(u.get("price_level")); sz = float(u.get("new_quantity"))
                    except (TypeError, ValueError):
                        continue
                    if et == "update":
                        with lock:
                            c = cnt[pid]
                            c["updates"] += 1
                            if side == "bid":
                                c["bid_set" if sz > 0 else "bid_rm"] += 1
                            else:
                                c["ask_set" if sz > 0 else "ask_rm"] += 1
                        if sz == 0:  # removal: log for the fill/cancel join
                            with lock:
                                removals[pid]["bid" if side == "bid" else "ask"].append((time.time(), px))
                    set_level(pid, side, px, sz)
            elif ch == "market_trades" and et == "update":
                for t in e.get("trades", []):
                    tpid = t.get("product_id")
                    if tpid not in books:
                        continue
                    try:
                        tpx = float(t.get("price")); tsz = float(t.get("size"))
                        notional = tpx * tsz
                    except (TypeError, ValueError):
                        continue
                    s = str(t.get("side", "")).lower()
                    side = "ask" if s == "buy" else "bid"  # taker BUY lifts the ask
                    with lock:
                        trade_n[tpid]["n"] += 1
                        trade_n[tpid]["buy_n" if s == "buy" else "sell_n"] += notional
                        # fill/cancel join: removals at the trade price within the window = fills
                        matched, kept = match_removals(removals[tpid][side], tpx, time.time())
                        removals[tpid][side] = kept
                        if matched:
                            f = fills[tpid]
                            f[side + "_n"] += matched
                            f[side + "_notional"] += notional

    def book_stats(pid):
        with lock:  # snapshot under lock; websocket thread mutates these dicts
            b = dict(books[pid]["bids"]); a = dict(books[pid]["asks"])
        if not b or not a:
            return None
        bb = max(b); ba = min(a); mid = (bb + ba) / 2
        if mid <= 0:
            return None
        tb = sorted(b.items(), reverse=True)[:5]
        ta = sorted(a.items())[:5]
        bn = sum(px * sz for px, sz in tb); an = sum(px * sz for px, sz in ta)
        wall_b = max(px * sz for px, sz in b.items())
        wall_a = max(px * sz for px, sz in a.items())
        return dict(mid=mid,
                    spread_bps=round((ba - bb) / mid * 1e4, 2),
                    imb5=round((bn - an) / (bn + an), 3) if bn + an else 0.0,
                    wall_bid=round(wall_b, 2), wall_ask=round(wall_a, 2))

    state = {"ws": None, "last_msg": time.time(), "closed": False,
             "reconnects": deque()}  # timestamps of recent reconnects, for backoff

    def on_close(w, *a):
        print("WS closed", file=sys.stderr)
        state["closed"] = True

    def safe_connect():
        """connect() guarded: a raising connect must not kill the tap."""
        try:
            connect()
        except Exception as e:
            print("connect failed", repr(e)[:200], file=sys.stderr, flush=True)

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
        return ws

    connect()

    while time.time() < stop_at:
        now = time.time()
        # reactive reconnect: connection dropped -> reconnect within ~3s
        # (the 60s silence watchdog below stays as backstop for silent stalls)
        if state["closed"]:
            print("WS dropped, reconnecting", file=sys.stderr, flush=True)
            state["closed"] = False
            try:
                state["ws"].close()
            except Exception:
                pass
            # backoff: a burst of rapid reconnects means the server is down,
            # not flaky -- cool down instead of hammering it every ~2s
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
        # silence watchdog: no messages for 60s -> reconnect (feeds stall or die silently)
        elif now - state["last_msg"] > 60:
            print("WS silent 60s, reconnecting", file=sys.stderr, flush=True)
            try:
                state["ws"].close()
            except Exception:
                pass
            time.sleep(2)
            safe_connect()
            state["last_msg"] = time.time()
        if now >= bucket_end:
            ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(bucket_end))
            try:
                for pid in PRODUCTS:
                    st = book_stats(pid); c = cnt[pid]; tn = trade_n[pid]; f = fills[pid]
                    bfn, afn = int(f["bid_n"]), int(f["ask_n"])
                    row = {"ts": ts, "product": pid,
                           "updates": c["updates"], "bid_set": c["bid_set"],
                           "bid_rm": c["bid_rm"], "ask_set": c["ask_set"], "ask_rm": c["ask_rm"],
                           "trades": int(tn["n"]), "buy_notional": round(tn["buy_n"], 2),
                           "sell_notional": round(tn["sell_n"], 2),
                           "bid_fills": bfn, "ask_fills": afn,
                           "bid_fill_notional": round(f["bid_notional"], 2),
                           "ask_fill_notional": round(f["ask_notional"], 2),
                           "bid_cancels": max(c["bid_rm"] - bfn, 0),
                           "ask_cancels": max(c["ask_rm"] - afn, 0)}
                    if st:
                        row.update(st)
                        pm = prev_mid.get(pid)
                        row["mid_chg_bps"] = round((st["mid"] - pm) / pm * 1e4, 2) if pm else 0.0
                        prev_mid[pid] = st["mid"]
                    out.write(json.dumps(row) + "\n")
                out.flush()
            except Exception as e:
                print("bucket flush err", repr(e)[:200], file=sys.stderr, flush=True)
            with lock:
                for pid in PRODUCTS:
                    cnt[pid] = defaultdict(int); trade_n[pid] = defaultdict(float)
                    fills[pid] = defaultdict(float)
                    removals[pid]["bid"].clear(); removals[pid]["ask"].clear()
            bucket_end += BUCKET
        time.sleep(1)
    try:
        state["ws"].close()
    except Exception:
        pass
    out.close()
    print(f"done, buckets appended to {args.out}")


main()
