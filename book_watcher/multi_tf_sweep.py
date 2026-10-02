#!/usr/bin/env python3
"""Multi-timeframe whole-market leader-lag sweep.

Mike's ask: test timeframes 5m, 30m, 1h, 2h, 3h, 4h, 5h, 6h, 12h, 1d, 2d, 3d,
4d, 5d individually, markets as a whole.

Data: candles/candles_{AMP,XPL,KITE,2Z,MOG,VET,XYO}_USD_1m.jsonl
  {t,o,h,l,c,v} one line per minute WITH trades. Minutes without trades are
  OMITTED by the source. NEVER forward-filled here.

Method per timeframe T:
  1. Aggregate 1m -> T candles (epoch-aligned bins). A T-candle is VALID only
     if >=80% of its constituent minutes are present AND the first and last
     minutes are present. Otherwise excluded. Coverage stats recorded.
  2. Lead events: per asset, T-candle absolute close-to-close moves; threshold
     = 90th percentile of that asset's own in-sample per-frame |moves| (bps).
     Two buckets: 'top' (>= thr) and 'strict' (>= 2x thr).
  3. Entry simulated 2 minutes after the lead candle closes (his tap latency).
  4. Outcome: equal-weight mean of per-asset moves over the next 1 T-candle
     (and next 2 for context), signed in the lead direction. Per-asset move
     uses that asset's own minute closes at entry/exit minutes; a market move
     is valid only with >=3 constituents. Also leader->follower pairs.
  5. Conditioning: leader identity, magnitude bucket, and UTC hour (intraday
     frames) or day-of-week (1d+ frames).

Discipline:
  - In-sample = first 60 days of the data span (DISCOVERY), out-of-sample =
    last 30 days (CONFIRMATION). Thresholds calibrated on in-sample only.
  - Discovery screen: n >= 200 AND |mean signed move| >= 40 bps.
  - Holm-Bonferroni within each timeframe family (all market + pair subgroups
    with n>=2 in-sample). Normal-approx t p-values.
  - Overlap dedup: per leader, drop events whose [m0, m0+2T] measurement
    window overlaps a kept earlier event's window.
  - Net expectancy: all-taker round trip 100 bps. FOUND only if a subgroup
    holds on the holdout (same sign, |mean|>=40bps, n_oos>=100) AND gross
    mean >= 150 bps (100 bps fees + 50 bps safety margin).

Read-only. No trading, no wallet, no auth.
"""
import json, math, os, statistics, sys, time
from datetime import datetime, timezone

BASE = os.path.dirname(os.path.abspath(__file__))
CDIR = os.path.join(BASE, "candles")
OUT_JSON = os.path.join(BASE, "multi_tf_leader_lag.json")
LOG = os.path.join(BASE, "multi_tf_sweep.log")

PRODUCTS = ["AMP", "XPL", "KITE", "2Z", "MOG", "VET", "XYO"]
FRAMES = [("5m", 5), ("30m", 30), ("1h", 60), ("2h", 120), ("3h", 180),
          ("4h", 240), ("5h", 300), ("6h", 360), ("12h", 720),
          ("1d", 1440), ("2d", 2880), ("3d", 4320), ("4d", 5760), ("5d", 7200)]
FEE_RT_BPS = 100.0       # all-taker round trip
FOUND_NET_MARGIN_BPS = 50.0  # safety margin above fees
MIN_CONSTITUENTS = 3
COVER_FRAC = 0.8


def log(msg):
    line = "%s %s" % (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), msg)
    print(line, flush=True)
    with open(LOG, "a") as f:
        f.write(line + "\n")


def phi(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def ttest_p(mean, sd, n):
    """Two-sided p-value, normal approx of t-stat."""
    if n < 2 or sd <= 0:
        return 1.0
    t = abs(mean) / (sd / math.sqrt(n))
    return 2.0 * (1.0 - phi(t))


def stats(vals):
    n = len(vals)
    if n == 0:
        return None
    mean = sum(vals) / n
    sd = math.sqrt(sum((v - mean) ** 2 for v in vals) / (n - 1)) if n > 1 else 0.0
    win = sum(1 for v in vals if v > 0) / n
    return {"n": n, "mean": mean, "sd": sd, "winrate": win,
            "p": ttest_p(mean, sd, n)}


def holm(ps):
    """Holm-Bonferroni. ps: list of (key, p). Returns {key: corrected p}."""
    order = sorted(ps, key=lambda kv: kv[1])
    m = len(order)
    corr, prev = {}, 0.0
    for i, (k, p) in enumerate(order):
        cp = min(1.0, p * (m - i))
        cp = max(cp, prev)  # enforce monotonicity
        corr[k] = cp
        prev = cp
    return corr


# ---------------- data ----------------
log("loading minute candles")
mins = {}   # product -> {minute: (o,h,l,c)}
m_lo, m_hi = None, None
for p in PRODUCTS:
    d = {}
    with open(os.path.join(CDIR, "candles_%s_USD_1m.jsonl" % p)) as f:
        for line in f:
            r = json.loads(line)
            m = r["t"] // 60
            d[m] = (r["o"], r["h"], r["l"], r["c"])
    mins[p] = d
    if d:
        m_lo = min(m_lo, min(d)) if m_lo is not None else min(d)
        m_hi = max(m_hi, max(d)) if m_hi is not None else max(d)
    log("  %s: %d minutes" % (p, len(d)))

SPLIT_M = m_lo + 60 * 24 * 60  # 60 days in minutes
log("span %s -> %s ; in-sample ends %s" % (
    datetime.fromtimestamp(m_lo * 60, timezone.utc).strftime("%Y-%m-%d"),
    datetime.fromtimestamp(m_hi * 60, timezone.utc).strftime("%Y-%m-%d"),
    datetime.fromtimestamp(SPLIT_M * 60, timezone.utc).strftime("%Y-%m-%d")))


def aggregate(p, T):
    """Epoch-aligned T-minute candles with the strict coverage rule."""
    d = mins[p]
    bins = {}
    for m, ohlc in d.items():
        bins.setdefault(m // T, []).append((m,) + ohlc)
    out = {}
    need = math.ceil(COVER_FRAC * T)
    for b, rows in bins.items():
        if len(rows) < need:
            continue
        ms = {r[0] for r in rows}
        if (b * T) not in ms or (b * T + T - 1) not in ms:
            continue
        rows.sort()
        o = rows[0][1]
        c = rows[-1][4]
        h = max(r[2] for r in rows)
        l = min(r[3] for r in rows)
        out[b] = (o, h, l, c)
    return out


def frame_moves(tc):
    """Consecutive-valid-bin close-to-close moves in bps: {bin: signed_bps}."""
    bs = sorted(tc)
    mv = {}
    for i in range(1, len(bs)):
        if bs[i] == bs[i - 1] + 1:
            c0 = tc[bs[i - 1]][3]
            c1 = tc[bs[i]][3]
            if c0 > 0:
                mv[bs[i]] = (c1 / c0 - 1.0) * 1e4
    return mv


def cond_of(m0_min, intraday):
    dt = datetime.fromtimestamp(m0_min * 60, timezone.utc)
    if intraday:
        return ("hour", dt.hour)
    return ("dow", dt.weekday())


results = {"generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "method": "see multi_tf_sweep.py docstring",
           "fee_model_bps_roundtrip": FEE_RT_BPS,
           "entry_assumption": "2 minutes after lead candle closes (tap-to-execution latency)",
           "in_sample": "first 60 days of span (threshold calibration + discovery)",
           "out_of_sample": "last 30 days of span (confirmation)",
           "discovery_screen": "n>=200 AND |mean|>=40bps AND Holm p<0.05 within timeframe family",
           "found_rule": "same sign on holdout, |mean_oos|>=40bps, n_oos>=100, gross>=150bps (100 fees + 50 margin)",
           "frames": {}}

for fname, T in FRAMES:
    t0 = time.time()
    intraday = T < 1440
    log("frame %s (T=%d min)" % (fname, T))
    fr = {"T_min": T, "intraday": intraday, "coverage": {}, "thresholds_bps": {},
          "n_events_in": 0, "n_events_oos": 0,
          "n_tests_market": 0, "n_tests_pair": 0,
          "market": [], "pairs": [], "discovery_hits": [],
          "confirmed": [], "found": [], "closest_misses": []}

    # aggregate + moves + coverage
    tc, mv = {}, {}
    for p in PRODUCTS:
        tc[p] = aggregate(p, T)
        b_lo = (m_lo // T)
        b_hi = (m_hi // T)
        slots = b_hi - b_lo + 1
        fr["coverage"][p] = {"slots": slots, "valid": len(tc[p]),
                             "valid_pct": round(100.0 * len(tc[p]) / slots, 2)}
        mv[p] = frame_moves(tc[p])
    # in-sample calibration
    thr = {}
    for p in PRODUCTS:
        ism = [abs(v) for b, v in mv[p].items()
               if (b * T + T) < SPLIT_M and abs(v) > 0]
        if len(ism) >= 50:
            thr[p] = statistics.quantiles(sorted(ism), n=10)[8]
            fr["thresholds_bps"][p] = round(thr[p], 1)
        else:
            fr["thresholds_bps"][p] = None

    # events (full sample, in-sample-calibrated thresholds), dedup overlaps
    events = []
    for p in PRODUCTS:
        if thr.get(p) is None:
            continue
        cand = []
        for b, v in mv[p].items():
            if abs(v) >= thr[p]:
                m0 = b * T + T  # lead candle close, in minutes
                bucket = "strict" if abs(v) >= 2 * thr[p] else "top"
                cand.append((m0, bucket, 1 if v > 0 else -1, abs(v)))
        cand.sort()
        last_kept = -10 ** 18
        for m0, bucket, sgn, ab in cand:
            if m0 - last_kept < 2 * T:
                continue  # overlapping measurement window: drop
            last_kept = m0
            events.append({"leader": p, "m0": m0, "bucket": bucket,
                           "sign": sgn, "abs_bps": ab,
                           "cond": cond_of(m0, intraday),
                           "sample": "in" if m0 < SPLIT_M else "oos"})
    fr["n_events_in"] = sum(1 for e in events if e["sample"] == "in")
    fr["n_events_oos"] = sum(1 for e in events if e["sample"] == "oos")
    log("  events: in=%d oos=%d" % (fr["n_events_in"], fr["n_events_oos"]))
    if not events:
        results["frames"][fname] = fr
        continue

    # measurements
    market_vals = {}  # (leader,bucket,cond) -> {'in':[], 'oos':[]}
    pair_vals = {}    # (leader,follower,bucket,cond) -> {'in':[], 'oos':[]}
    for e in events:
        m0, sgn = e["m0"], e["sign"]
        me, mx1, mx2 = m0 + 2, m0 + T, m0 + 2 * T
        key_m = (e["leader"], e["bucket"], e["cond"])
        # whole-market: equal-weight mean of per-asset signed moves
        rs1, rs2 = [], []
        for a in PRODUCTS:
            da = mins[a]
            if me in da and mx1 in da:
                pe, px = da[me][3], da[mx1][3]
                if pe > 0:
                    rs1.append((px / pe - 1.0) * 1e4 * sgn)
            if me in da and mx2 in da:
                pe, px = da[me][3], da[mx2][3]
                if pe > 0:
                    rs2.append((px / pe - 1.0) * 1e4 * sgn)
        if len(rs1) >= MIN_CONSTITUENTS:
            d = market_vals.setdefault(key_m, {"in": [], "oos": [], "in2": [], "oos2": []})
            d[e["sample"]].append(sum(rs1) / len(rs1))
            if len(rs2) >= MIN_CONSTITUENTS:
                d[e["sample"] + "2"].append(sum(rs2) / len(rs2))
            d["n_const"] = d.get("n_const", []) + [len(rs1)]
        # leader -> follower pairs
        for f_ in PRODUCTS:
            if f_ == e["leader"]:
                continue
            df = mins[f_]
            if me in df and mx1 in df:
                pe, px = df[me][3], df[mx1][3]
                if pe > 0:
                    key_p = (e["leader"], f_, e["bucket"], e["cond"])
                    pair_vals.setdefault(key_p, {"in": [], "oos": []})[
                        e["sample"]].append((px / pe - 1.0) * 1e4 * sgn)

    # statistics + Holm within the timeframe family
    fam = []  # (kind, key, stat_in)
    for key, d in market_vals.items():
        s = stats(d["in"])
        if s:
            fam.append(("market", key, s, d))
    for key, d in pair_vals.items():
        s = stats(d["in"])
        if s and s["n"] >= 2:
            fam.append(("pair", key, s, d))
    fr["n_tests_market"] = sum(1 for k, _, _, _ in fam if k == "market")
    fr["n_tests_pair"] = sum(1 for k, _, _, _ in fam if k == "pair")
    corr = holm([((kind, key), s["p"]) for kind, key, s, d in fam])

    def row(kind, key, s, d):
        so = stats(d["oos"]) if d.get("oos") else None
        r = {"kind": kind,
             "leader": key[0] if kind == "market" else key[0],
             "follower": None if kind == "market" else key[1],
             "bucket": key[1] if kind == "market" else key[2],
             "cond": "%s=%s" % (key[2][0], key[2][1]) if kind == "market"
                     else "%s=%s" % (key[3][0], key[3][1]),
             "n_in": s["n"], "mean_in_bps": round(s["mean"], 1),
             "net_in_bps": round(s["mean"] - FEE_RT_BPS, 1),
             "winrate_in": round(s["winrate"], 3),
             "p_holm": round(corr[(kind, key)], 4),
             "n_oos": so["n"] if so else 0,
             "mean_oos_bps": round(so["mean"], 1) if so else None,
             "net_oos_bps": round(so["mean"] - FEE_RT_BPS, 1) if so else None,
             "winrate_oos": round(so["winrate"], 3) if so else None}
        if kind == "market":
            r["mean_in_next2_bps"] = round(sum(d["in2"]) / len(d["in2"]), 1) if d.get("in2") else None
            r["mean_oos_next2_bps"] = round(sum(d["oos2"]) / len(d["oos2"]), 1) if d.get("oos2") else None
            r["avg_constituents"] = round(sum(d.get("n_const", [0])) / max(1, len(d.get("n_const", [0]))), 1)
        return r

    for kind, key, s, d in fam:
        r = row(kind, key, s, d)
        (fr["market"] if kind == "market" else fr["pairs"]).append(r)
        hit = (s["n"] >= 200 and abs(s["mean"]) >= 40.0
               and corr[(kind, key)] < 0.05)
        if hit:
            fr["discovery_hits"].append(r)
            so = stats(d["oos"]) if d.get("oos") else None
            if (so and so["n"] >= 100 and abs(so["mean"]) >= 40.0
                    and (so["mean"] > 0) == (s["mean"] > 0)):
                fr["confirmed"].append(r)
                if so["mean"] >= 150.0:
                    fr["found"].append(r)

    # closest misses: exploratory only, n>=50, sorted by |mean|
    pool = [r for r in fr["market"] + fr["pairs"] if r["n_in"] >= 50]
    pool.sort(key=lambda r: abs(r["mean_in_bps"]), reverse=True)
    fr["closest_misses"] = pool[:10]

    log("  tests mkt=%d pair=%d | hits=%d confirmed=%d FOUND=%d (%.1fs)" % (
        fr["n_tests_market"], fr["n_tests_pair"], len(fr["discovery_hits"]),
        len(fr["confirmed"]), len(fr["found"]), time.time() - t0))
    results["frames"][fname] = fr

with open(OUT_JSON, "w") as f:
    json.dump(results, f)
log("wrote %s" % OUT_JSON)
