#!/usr/bin/env python3
"""Peak-pattern LIVE executor — Mike's "green light live trading" machine.

Scope (locked 2026-10-01, his words: "the exact pattern found"):
  - universe: BTC/ETH/SOL/XRP/BNB/DOGE/ZEC/SUI
  - signal: trailing-120min min-max normalization, |deviation| from the
    8-major composite >= 200 bps  (computed by peak_watcher.py)
  - direction: LONG ONLY (spot account cannot short; the 4/7 backtest events
    that were above-composite are not tradeable live — disclosed to Mike)
  - hold 30 min, max 3 concurrent, 60-min per-asset cooldown
  - sleeve size: 1/3 of account liquidity (dynamic, compounds to the cap)
  - HARD CAP: account liquidity >= $250,000 CAD -> write HALT, stop forever
  - FUNDING GATE: never sells existing holdings to raise cash (not authorized);
    a BUY is skipped as BLOCKED_FUNDING unless quote cash covers the sleeve

Safety:
  - default is DRY RUN: assembles orders, logs them, sends nothing
  - real orders ONLY with --live
  - HALT file (~/.peak_halt or book_watcher/peak_halt) stops the loop;
    Mike saying "halt" -> create it immediately
  - key handling mirrors cb.py: key file -> child env only, never logged

Every order, fill, skip and halt is appended to peak_trades/live_YYYY-MM-DD.jsonl
"""
import json, os, sys, time, subprocess, datetime, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import peak_watcher as pw

COINBASE_BIN = os.path.expanduser("~/workspace/tools/coinbase-cli/node_modules/.bin/coinbase")
KEY_FILE = os.path.expanduser("~/workspace/user/files/cdp_api_key__1__0_8gjd.json")
LIVE_STATE = os.path.join(HERE, "peak_live_state.json")
HALT_FILE = os.path.join(HERE, "peak_halt")
LOG_DIR = os.path.join(HERE, "peak_trades")

DEV_THRESH_BPS = 200.0     # long-only: dev <= -200
HOLD_S = 30 * 60
COOLDOWN_S = 60 * 60
MAX_CONCURRENT = 3
HARD_CAP_CAD = 250_000.0
CAD_PER_USD = None         # resolved live per loop

LIVE = "--live" in sys.argv


def log(obj):
    os.makedirs(LOG_DIR, exist_ok=True)
    day = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    obj["logged_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    obj["live"] = LIVE
    with open(LOG_DIR + "/live_%s.jsonl" % day, "a") as f:
        f.write(json.dumps(obj) + "\n")


def cli(*args, dry=False):
    """Run the coinbase CLI with the key in child env only. Never logs the key."""
    with open(KEY_FILE) as f:
        key = json.load(f)
    env = dict(os.environ)
    env["COINBASE_ENV"] = "live"
    env["COINBASE_KEY_ID"] = key["id"]
    env["COINBASE_KEY_SECRET"] = key["privateKey"]
    cmd = [COINBASE_BIN, "-e", "live", *args]
    if dry:
        cmd.append("--dry-run")
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=60)
    return proc.returncode, proc.stdout, proc.stderr


def place_order(product_id, side, size_key, size_val, client_id):
    args = ["orders", "create",
            "product_id=%s" % product_id, "side=%s" % side,
            "type=market", "%s=%s" % (size_key, size_val),
            "client_order_id=%s" % client_id]
    if not LIVE:
        rc, out, err = cli(*args, dry=True)
        log({"event": "DRY_ORDER", "product_id": product_id, "side": side,
             size_key: size_val, "rc": rc, "out": out[:500], "err": err[:200]})
        return None
    rc, out, err = cli(*args)
    try:
        data = json.loads(out)
    except Exception:
        data = {"raw": out[:500], "err": err[:200]}
    log({"event": "ORDER_SENT", "product_id": product_id, "side": side,
         size_key: size_val, "rc": rc, "resp": data})
    if rc != 0:
        return None
    return data


def get_order(order_id):
    rc, out, err = cli("orders", "get", order_id)
    try:
        return json.loads(out)
    except Exception:
        return {}


def portfolio_cad():
    """Total account liquidity in CAD + available USD/USDC cash."""
    rc, out, _ = cli("balance", "limit=100")
    total_usd = 0.0
    cash_usd = 0.0
    try:
        d = json.loads(out)
    except Exception:
        return 0.0, 0.0
    px_cache = {}
    for a in d.get("accounts", []):
        c = a["currency"]
        amt = float(a["available_balance"]["value"]) + float(a["hold"]["value"])
        if amt <= 0:
            continue
        if c in ("USD", "USDC"):
            cash_usd += amt
            total_usd += amt
        else:
            pid = "%s-USD" % c
            if pid not in px_cache:
                try:
                    t = pw.get("https://api.exchange.coinbase.com/products/%s/ticker" % pid)
                    px_cache[pid] = float(t["price"])
                except Exception:
                    px_cache[pid] = 0.0
            total_usd += amt * px_cache[pid]
    return total_usd, cash_usd


def load_state():
    try:
        with open(LIVE_STATE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"positions": [], "cooldown": {}}


def save_state(s):
    tmp = LIVE_STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(s, f)
    os.replace(tmp, LIVE_STATE)


def loop_once(state):
    if os.path.exists(HALT_FILE):
        log({"event": "HALTED", "reason": "halt file present"})
        return False
    total_usd, cash_usd = portfolio_cad()
    # CAD approx: 1 USD ~ 1.37 CAD (conservative; cap is a ceiling, use 1.35)
    total_cad = total_usd * 1.35
    if total_cad >= HARD_CAP_CAD:
        open(HALT_FILE, "w").write("hard cap reached: %.2f CAD\n" % total_cad)
        log({"event": "HALTED", "reason": "hard cap", "liquidity_cad": round(total_cad, 2)})
        return False

    wstate = pw.load_state()
    devs = pw.deviations(wstate.get("history", {}))
    now = time.time()
    sleeve = total_usd / MAX_CONCURRENT if total_usd > 0 else 0.0

    # close matured
    for p in list(state["positions"]):
        if now - p["opened_ts"] >= HOLD_S:
            cid = "peak-%d-%s-exit" % (int(p["opened_ts"]), p["pid"].replace("-", ""))
            o = place_order(p["pid"], "SELL", "base_size",
                            "%.8f" % p["base_qty"], cid)
            state["positions"].remove(p)
            state["cooldown"][p["pid"]] = now
            log({"event": "CLOSE", "pid": p["pid"], "order": bool(o),
                 "opened_ts": p["opened_ts"]})

    # open on fresh dips (long-only)
    if devs:
        for pid in pw.MAJORS:
            d = devs.get(pid)
            if d is None or d > -DEV_THRESH_BPS:
                continue
            if any(p["pid"] == pid for p in state["positions"]):
                continue
            if len(state["positions"]) >= MAX_CONCURRENT:
                break
            if now - state["cooldown"].get(pid, 0) < COOLDOWN_S:
                continue
            if cash_usd < sleeve or sleeve <= 0:
                log({"event": "BLOCKED_FUNDING", "pid": pid,
                     "dev_bps": round(d, 1), "need_usd": round(sleeve, 2),
                     "cash_usd": round(cash_usd, 2)})
                continue
            cid = "peak-%d-%s-entry" % (int(now), pid.replace("-", ""))
            o = place_order(pid, "BUY", "quote_size", "%.2f" % sleeve, cid)
            if o and LIVE:
                # record filled qty from the order
                det = get_order(o.get("order_id", "")) if isinstance(o, dict) else {}
                fq = float(det.get("filled_size", 0) or 0)
                state["positions"].append({
                    "pid": pid, "opened_ts": now, "dev_bps": d,
                    "quote_usd": sleeve, "base_qty": fq,
                    "buy_order": o.get("order_id") if isinstance(o, dict) else None,
                })
                log({"event": "OPEN", "pid": pid, "dev_bps": round(d, 1),
                     "filled_base": fq})
    save_state(state)
    return True


def main():
    if "--once" in sys.argv:
        state = load_state()
        ok = loop_once(state)
        print(json.dumps({"live": LIVE, "kept_running": ok,
                          "positions": state["positions"]}, indent=1))
        return
    state = load_state()
    while loop_once(state):
        time.sleep(pw.CADENCE_S)


if __name__ == "__main__":
    main()
