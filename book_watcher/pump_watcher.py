#!/usr/bin/env python3
"""pump.fun new-birth watcher — realtime launch + ignition detection.

Subscribes to pumpportal's realtime feed:
  - new token births the moment they're created
  - per-token trade flow for the youngest launches

Tracks buy velocity / sell pressure in the first minutes of life and raises:
  - IGNITION: a newborn showing real buy acceleration (potential runner)
  - DUMP_RISK: sell pressure overwhelming after ignition (get out first)

Read-only. No trading, no wallet. Detection + alerting only.

Data:  pumpfun/births_YYYY-MM-DD.jsonl   (raw creation events)
        pumpfun/alerts.jsonl              (IGNITION / DUMP_RISK records)
        pumpfun/heartbeat.txt             (unix ts, every 10s)
        pumpfun/watch.log
"""
import json, os, sys, time, threading
from collections import deque, defaultdict
from datetime import datetime, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "pumpfun")
os.makedirs(DATA, exist_ok=True)
LOG = os.path.join(DATA, "watch.log")
HB = os.path.join(DATA, "heartbeat.txt")
ALERTS = os.path.join(DATA, "alerts.jsonl")

WS_URL = "wss://pumpportal.fun/api/data"

# PumpPortal trade streams (subscribeTokenTrade / subscribeAccountTrade) require
# an API key + linked wallet (>=0.02 SOL, metered 0.01 SOL per 10k trades).
# Key lives ONLY in pumpfun/.env (chmod 600), never in code, logs, or chat.
def load_api_key():
    try:
        with open(os.path.join(DATA, ".env")) as f:
            for line in f:
                line = line.strip()
                if line.startswith("PUMPPORTAL_API_KEY="):
                    return line.split("=", 1)[1].strip().strip("'\"")
    except FileNotFoundError:
        pass
    return None

def ws_url():
    key = load_api_key()
    return WS_URL + ("?api-key=" + key if key else "")

TRADES_STATUS = os.path.join(DATA, "trades_status.json")

def set_trades_status(state, reason=""):
    try:
        with open(TRADES_STATUS, "w") as f:
            json.dump({"state": state, "reason": reason,
                       "utc": datetime.now(timezone.utc).isoformat(timespec="seconds")}, f)
    except Exception:
        pass

# ---- tuning ----
TRACK_AGE_SEC = 600        # track trades for tokens younger than this
MAX_TRACKED = 25           # cap concurrent trade subscriptions
IGNITE_AGE_SEC = 120       # ignition only counts this early in life
IGNITE_MIN_BUYS_30S = 8    # buy velocity bar (30s window)
IGNITE_MIN_SOL_30S = 1.5   # buy volume bar (SOL, 30s window)
DUMP_SELL_MULT = 1.5       # sells > buys * this -> dump risk
DUMP_DRAWDOWN = 0.30       # 30% off peak mcap -> dump risk
RESUB_INTERVAL = 15        # min seconds between subscription updates

# ---- paper trading (hypothetical only; no wallet, no orders) ----
PAPER_SIZE_USD = 50.0      # assumed notional per paper trade
PAPER_FEE_USD = 0.27       # Mike's stated flat buy fee; assume same each way
PAPER_STOP = 0.50          # exit if mcap falls 50% from entry
FEE_DRAG = 2 * PAPER_FEE_USD / PAPER_SIZE_USD  # round-trip fee as fraction

def log(msg):
    line = f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {msg}"
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")

def heartbeat_loop(stop):
    while not stop.is_set():
        try:
            with open(HB, "w") as f:
                f.write(str(time.time()))
        except Exception:
            pass
        stop.wait(10)

def alert(kind, mint, symbol, detail):
    rec = {"ts": time.time(),
           "utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "kind": kind, "mint": mint, "symbol": symbol, "detail": detail}
    with open(ALERTS, "a") as f:
        f.write(json.dumps(rec) + "\n")
    log(f"ALERT {kind} {symbol} {mint[:8]} {detail}")

_birth_cache = {"mtime": 0, "map": {}}

def enrich_birth(mint):
    """Look up symbol/name/initial-mcap for a mint from today's birth log."""
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    path = os.path.join(DATA, f"births_{day}.jsonl")
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return {}
    if mtime != _birth_cache["mtime"]:
        m = {}
        try:
            with open(path) as f:
                for line in f:
                    try:
                        ev = json.loads(line).get("event", {})
                    except Exception:
                        continue
                    mk = ev.get("mint")
                    if mk and mk not in m:
                        m[mk] = {"symbol": ev.get("symbol"), "name": ev.get("name"),
                                 "mcap0": ev.get("marketCapSol"), "dev": ev.get("traderPublicKey")}
        except Exception:
            pass
        _birth_cache["mtime"] = mtime
        _birth_cache["map"] = m
    return _birth_cache["map"].get(mint, {})

def handle_migration(e):
    """Graduation events (bonding curve complete -> PumpSwap). Free stream."""
    mint = str(e.get("mint", ""))
    info = enrich_birth(mint)
    symbol = info.get("symbol") or "?"
    try:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with open(os.path.join(DATA, f"migrations_{day}.jsonl"), "a") as f:
            f.write(json.dumps({"ts": time.time(),
                                "utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                "mint": mint, "symbol": symbol,
                                "birth": info or None}) + "\n")
    except Exception:
        pass
    mcap0 = info.get("mcap0")
    alert("GRADUATED", mint, symbol,
          f"graduated bonding curve; birth mcap={mcap0} dev={str(info.get('dev') or '')[:8]}")

class Tracker:
    def __init__(self):
        self.tokens = {}  # mint -> dict(mint, symbol, name, born, dev, trades=deque, peak_mcap, ignited, dumped, subbed)
        self.lock = threading.Lock()
        self.births = 0
        self.last_resub = 0
        self.paper = {}   # mint -> {sym, entry_ts, entry_mcap}
        self.paper_day = None
        self.paper_f = None

    def paper_file(self):
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if day != self.paper_day:
            if self.paper_f:
                self.paper_f.close()
            self.paper_day = day
            self.paper_f = open(os.path.join(DATA, f"paper_{day}.jsonl"), "a", buffering=1)
        return self.paper_f

    def paper_open(self, mint, sym, mcap):
        if mcap <= 0 or mint in self.paper:
            return
        now = time.time()
        self.paper[mint] = {"sym": sym, "entry_ts": now, "entry_mcap": mcap}
        self.paper_file().write(json.dumps({
            "ts": now, "utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "side": "open", "mint": mint, "symbol": sym, "mcap": round(mcap, 3)}) + "\n")
        log(f"PAPER OPEN {sym} mcap={mcap:.2f}")

    def paper_close(self, mint, mcap, reason):
        pos = self.paper.pop(mint, None)
        if not pos or mcap <= 0 or pos["entry_mcap"] <= 0:
            return
        now = time.time()
        gross = mcap / pos["entry_mcap"]
        net = gross * (1 - FEE_DRAG)
        rec = {"ts": now, "utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
               "side": "close", "mint": mint, "symbol": pos["sym"],
               "entry_mcap": round(pos["entry_mcap"], 3), "exit_mcap": round(mcap, 3),
               "hold_s": round(now - pos["entry_ts"], 1), "reason": reason,
               "gross_mult": round(gross, 4), "net_mult": round(net, 4)}
        self.paper_file().write(json.dumps(rec) + "\n")
        log(f"PAPER CLOSE {pos['sym']} {reason} gross={gross:.2f}x net={net:.2f}x")

    def on_birth(self, e):
        mint = e.get("mint")
        if not mint:
            return
        now = time.time()
        with self.lock:
            if mint in self.tokens:
                return
            self.tokens[mint] = {
                "mint": mint,
                "symbol": e.get("symbol", "?"),
                "name": e.get("name", "?"),
                "born": now,
                "dev": (e.get("traderPublicKey") or "")[:12],
                "trades": deque(),
                "peak_mcap": 0.0,
                "ignited": False,
                "dumped": False,
            }
            self.births += 1
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with open(os.path.join(DATA, f"births_{day}.jsonl"), "a") as f:
            f.write(json.dumps({"ts": now, "event": e}) + "\n")

    def on_trade(self, e):
        mint = e.get("mint")
        if not mint:
            return
        now = time.time()
        with self.lock:
            t = self.tokens.get(mint)
            if not t:
                return
            tx = (e.get("txType") or "").lower()
            try:
                sol = float(e.get("solAmount") or 0)
            except Exception:
                sol = 0.0
            try:
                mcap = float(e.get("marketCapSol") or 0)
            except Exception:
                mcap = 0.0
            t["trades"].append((now, tx, sol))
            if mcap > t["peak_mcap"]:
                t["peak_mcap"] = mcap
            # prune old trades
            while t["trades"] and now - t["trades"][0][0] > 60:
                t["trades"].popleft()
            t["_mcap"] = mcap
            if not getattr(self, "_trades_live_flagged", False):
                self._trades_live_flagged = True
                set_trades_status("live", "trade events flowing")

    def scan(self):
        """Called once/sec: score tracked tokens, raise alerts, run paper book."""
        now = time.time()
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        bars_f = open(os.path.join(DATA, f"bars_{day}.jsonl"), "a", buffering=1)
        try:
            with self.lock:
                for mint, t in list(self.tokens.items()):
                    age = now - t["born"]
                    if age > TRACK_AGE_SEC:
                        # aged out: close any paper position at last mcap
                        if mint in self.paper:
                            self.paper_close(mint, t.get("_mcap", 0), "timeout")
                        continue
                    recent = [x for x in t["trades"] if now - x[0] <= 30]
                    buys = sum(1 for _, tx, _ in recent if tx == "buy")
                    sells = sum(1 for _, tx, _ in recent if tx == "sell")
                    buy_sol = sum(s for _, tx, s in recent if tx == "buy")
                    mcap = t.get("_mcap", 0)
                    # 1-second bar for active tokens only (keeps volume bounded)
                    sec = [x for x in t["trades"] if now - x[0] <= 1]
                    if sec:
                        b1 = sum(1 for _, tx, _ in sec if tx == "buy")
                        s1 = sum(1 for _, tx, _ in sec if tx == "sell")
                        bs = round(sum(s for _, tx, s in sec if tx == "buy"), 3)
                        ss = round(sum(s for _, tx, s in sec if tx == "sell"), 3)
                        bars_f.write(json.dumps({
                            "t": round(now, 1), "mint": mint, "b1": b1, "s1": s1,
                            "bs": bs, "ss": ss,
                            "mcap": round(mcap, 3) if mcap else 0}) + "\n")
                    if (not t["ignited"] and age <= IGNITE_AGE_SEC
                            and buys >= IGNITE_MIN_BUYS_30S and buy_sol >= IGNITE_MIN_SOL_30S):
                        t["ignited"] = True
                        alert("IGNITION", mint, t["symbol"],
                              f"age={age:.0f}s buys30={buys} buySOL30={buy_sol:.1f} mcap={mcap:.1f}")
                        self.paper_open(mint, t["symbol"], mcap)
                    if t["ignited"] and not t["dumped"]:
                        if sells > buys * DUMP_SELL_MULT and sells >= 4:
                            t["dumped"] = True
                            alert("DUMP_RISK", mint, t["symbol"],
                                  f"sells30={sells} buys30={buys} mcap={mcap:.1f}")
                            self.paper_close(mint, mcap, "dump_signal")
                        elif t["peak_mcap"] > 0 and mcap > 0 and mcap < t["peak_mcap"] * (1 - DUMP_DRAWDOWN):
                            t["dumped"] = True
                            alert("DUMP_RISK", mint, t["symbol"],
                                  f"drawdown {(1 - mcap / t['peak_mcap']) * 100:.0f}% off peak mcap={mcap:.1f}")
                            self.paper_close(mint, mcap, "dump_drawdown")
                    # paper stop-loss while position open
                    if mint in self.paper and mcap > 0:
                        pos = self.paper[mint]
                        if mcap < pos["entry_mcap"] * PAPER_STOP:
                            self.paper_close(mint, mcap, "stop_50pct")
        finally:
            bars_f.close()

    def subs_needed(self):
        now = time.time()
        with self.lock:
            cands = [(t["born"], m) for m, t in self.tokens.items()
                     if now - t["born"] <= TRACK_AGE_SEC]
        cands.sort(reverse=True)
        return [m for _, m in cands[:MAX_TRACKED]]

def main():
    try:
        import websocket
    except ImportError:
        import subprocess
        subprocess.run([sys.executable, "-m", "pip", "install", "--quiet",
                        "--break-system-packages", "websocket-client"], check=True)
        import websocket

    tracker = Tracker()
    stop = threading.Event()
    threading.Thread(target=heartbeat_loop, args=(stop,), daemon=True).start()

    # scanner thread
    def scanner():
        while not stop.is_set():
            try:
                tracker.scan()
            except Exception as e:
                log(f"scan err {e}")
            stop.wait(1)
    threading.Thread(target=scanner, daemon=True).start()

    # subscription manager
    subbed = set()
    def manage_subs(ws):
        nonlocal subbed
        now = time.time()
        if now - tracker.last_resub < RESUB_INTERVAL:
            return
        want = set(tracker.subs_needed())
        if want == subbed:
            return
        tracker.last_resub = now
        drop = subbed - want
        add = want - subbed
        try:
            if drop:
                ws.send(json.dumps({"method": "unsubscribeTokenTrade", "keys": list(drop)}))
            if add:
                ws.send(json.dumps({"method": "subscribeTokenTrade", "keys": list(add)}))
            subbed = want
            if add or drop:
                log(f"subs: +{len(add)} -{len(drop)} tracked={len(want)} births={tracker.births}")
        except Exception as e:
            log(f"sub err {e}")

    def on_message(ws, msg):
        try:
            e = json.loads(msg)
        except Exception:
            return
        if isinstance(e, dict) and "message" in e and "mint" not in e:
            note = str(e.get("message", ""))
            if "only available when connecting with an API key" in note:
                log("TRADE STREAM DENIED by provider (needs API key + funded wallet); births continue")
                set_trades_status("blocked", "api_key_required")
            return  # subscription ack / provider note
        tx = (e.get("txType") or "").lower()
        if tx.startswith("migrat"):
            handle_migration(e)
            return
        if tx == "create" or ("mint" in e and "symbol" in e and "txType" not in e):
            tracker.on_birth(e)
        elif tx in ("buy", "sell"):
            tracker.on_trade(e)
        manage_subs(ws)

    def on_error(ws, err):
        log(f"ws err {err}")

    def on_close(ws, *a):
        log("ws closed")

    def on_open(ws):
        log("ws connected; subscribing to births + migrations"
            + (" [api-key present]" if load_api_key() else " [no api-key: trade stream blocked]"))
        ws.send(json.dumps({"method": "subscribeNewToken"}))
        ws.send(json.dumps({"method": "subscribeMigration"}))

    while not stop.is_set():
        try:
            log("pump watcher starting")
            ws = websocket.WebSocketApp(ws_url(), on_open=on_open,
                                        on_message=on_message,
                                        on_error=on_error, on_close=on_close)
            ws.run_forever(ping_interval=20, ping_timeout=10)
        except Exception as e:
            log(f"loop err {e}")
        log("disconnected; reconnecting in 5s")
        time.sleep(5)

if __name__ == "__main__":
    main()
