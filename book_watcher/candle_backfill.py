#!/usr/bin/env python3
"""Backfill 1-minute candles for the 7-book watch set.

Source: PUBLIC Coinbase Advanced Trade candles endpoint -- no API key, no auth.
  GET https://api.coinbase.com/api/v3/brokerage/market/products/{P}/candles
      ?start={unix}&end={unix}&granularity=ONE_MINUTE

Empirical (2026-09-29): unix timestamps required (ISO rejected); ~4h windows
work; 6h+ windows return INVALID_ARGUMENT. Minutes with no trades are OMITTED
from the response (thin books!), so consecutiveness is verified here and gaps
are recorded -- never forward-filled.

Output:
  candles/candles_{PRODUCT}_1m.jsonl   one JSON obj/line: {t,o,h,l,c,v}
  candles/manifest.json               per-book depth/coverage stats
  candles/backfill.log                progress log
"""
import json
import os
import time
import urllib.request
import urllib.error

BASE = os.path.dirname(os.path.abspath(__file__))
CANDLEDIR = os.path.join(BASE, "candles")
LOG = os.path.join(CANDLEDIR, "backfill.log")
MANIFEST = os.path.join(CANDLEDIR, "manifest.json")

PRODUCTS = ["AMP-USD", "XPL-USD", "KITE-USD", "2Z-USD", "MOG-USD", "VET-USD", "XYO-USD"]
DAYS = 90
WINDOW_S = 4 * 3600          # 4h pages (6h+ rejected by API)
SLEEP_S = 0.30               # <= ~3 req/s, polite
MAX_RETRIES = 5
EMPTY_STOP = 8               # consecutive empty windows -> stop early for that book

URL = ("https://api.coinbase.com/api/v3/brokerage/market/products/{p}/candles"
       "?start={s}&end={e}&granularity=ONE_MINUTE")


def log(msg):
    line = "%s %s" % (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), msg)
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def fetch(product, start, end):
    """Return list of raw candle dicts. Retries with backoff on 429/5xx."""
    url = URL.format(p=product, s=start, e=end)
    last_err = None
    for attempt in range(MAX_RETRIES):
        req = urllib.request.Request(url, headers={"User-Agent": "tick-backfill/1.0"})
        try:
            r = urllib.request.urlopen(req, timeout=30)
            d = json.loads(r.read().decode())
            return d.get("candles", []) or []
        except urllib.error.HTTPError as e:
            last_err = "HTTP %s" % e.code
            if e.code == 429:
                wait = 2 ** attempt * 2.0
                log("  429 for %s, backing off %.0fs" % (product, wait))
                time.sleep(wait)
                continue
            if 500 <= e.code < 600:
                time.sleep(2 ** attempt)
                continue
            raise
        except Exception as e:  # network hiccup
            last_err = repr(e)[:120]
            time.sleep(2 ** attempt)
    raise RuntimeError("fetch failed %s [%d,%d]: %s" % (product, start, end, last_err))


def backfill_product(product, start_limit, now):
    seen = {}  # t -> (o,h,l,c,v)
    cur_end = now
    empty_run = 0
    windows = 0
    stopped_early = False
    while cur_end > start_limit:
        cur_start = max(cur_end - WINDOW_S, start_limit)
        try:
            raw = fetch(product, cur_start, cur_end)
        except Exception as e:
            log("  ERROR %s window [%d,%d]: %s -- skipping window" % (product, cur_start, cur_end, e))
            raw = []
        for c in raw:
            try:
                t = int(c["start"])
                seen[t] = (float(c["open"]), float(c["high"]),
                           float(c["low"]), float(c["close"]),
                           float(c.get("volume") or 0.0))
            except (KeyError, ValueError, TypeError):
                continue
        windows += 1
        if not raw:
            empty_run += 1
            if empty_run >= EMPTY_STOP:
                stopped_early = True
                log("  %s: %d consecutive empty windows, stopping early at %s"
                    % (product, EMPTY_STOP,
                       time.strftime("%Y-%m-%d", time.gmtime(cur_start))))
                break
        else:
            empty_run = 0
        if windows % 60 == 0:
            log("  %s: %d windows, %d candles so far" % (product, windows, len(seen)))
        cur_end = cur_start
        time.sleep(SLEEP_S)
    return seen, stopped_early, windows


def main():
    os.makedirs(CANDLEDIR, exist_ok=True)
    now = int(time.time())
    start_limit = now - DAYS * 86400
    log("backfill start: %d products, %d days, 4h windows" % (len(PRODUCTS), DAYS))
    manifest = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "days_requested": DAYS, "books": {}}
    for p in PRODUCTS:
        log("book %s ..." % p)
        t0 = time.time()
        seen, stopped_early, windows = backfill_product(p, start_limit, now)
        ts = sorted(seen)
        fp = os.path.join(CANDLEDIR, "candles_%s_1m.jsonl" % p.replace("-", "_"))
        with open(fp, "w") as f:
            for t in ts:
                o, h, l, c, v = seen[t]
                f.write(json.dumps({"t": t, "o": o, "h": h, "l": l, "c": c, "v": v}) + "\n")
        if ts:
            span_min = (ts[-1] - ts[0]) // 60
            gaps = span_min - len(ts)  # missing minutes inside the observed span
            info = {"oldest_ts": ts[0],
                    "oldest": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts[0])),
                    "newest_ts": ts[-1],
                    "newest": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts[-1])),
                    "n_candles": len(ts),
                    "span_min": int(span_min),
                    "gaps_count": int(gaps),
                    "coverage_pct": round(100.0 * len(ts) / max(span_min, 1), 1),
                    "windows_fetched": windows,
                    "stopped_early": stopped_early}
        else:
            info = {"n_candles": 0, "windows_fetched": windows,
                    "stopped_early": stopped_early, "note": "no candles returned"}
        manifest["books"][p] = info
        log("book %s done: %d candles in %.0fs %s"
            % (p, len(ts), time.time() - t0,
               ("oldest " + info.get("oldest", "?")) if ts else "(empty)"))
        with open(MANIFEST, "w") as f:
            json.dump(manifest, f, indent=1)
    log("backfill complete")


if __name__ == "__main__":
    main()
