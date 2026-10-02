#!/usr/bin/env python3
"""Lead/lag direction hunt on 1-minute candles (multi-month history).

Question: after a LEADER book moves >=25bps over trailing 5 minutes (any of
the 7 books -- history is cheap, so we generalize beyond AMP), does any OTHER
book's subsequent DIRECTION become predictable at 5/15/30/60-minute horizons?

Method mirrors direction_hunt.py (tick lane), adapted to sparse 1m candles:
- Candles with no trades are OMITTED by the API, so every reference/entry/
  exit lookup is tolerance-bounded. If no candle exists inside the tolerance,
  the event or horizon is SKIPPED (counted as a gap) -- never forward-filled.
- Lead: |close[m] vs close[ref]| >= 25bps, where ref = newest candle with
  t <= m-5min and t_ref >= m-10min (the "5-minute" window must be real).
  Events per book spaced >= 60min apart (no double counting).
- Entry: first candle of the FOLLOWER with t >= m+2min, t_entry <= m+7min
  (realistic detection+routing lag at minute resolution).
- Horizons H in {5,15,30,60}min: first follower candle with t >= t_entry+H,
  tolerance max(5, H/4) min past that.
- Signed with/against lead direction: follow = disp*dir, fade = -follow.
- Conditions: lead-size bucket (25/50bps), UTC hour, lead book, follower book.

Writes history_hunt.json mirroring direction_hunt.json's schema.
Alert bar ("FOUND"): n>=200 AND max(follow_mean, fade_mean) >= 40bps --
higher n bar than the tick hunt because history is cheap and multiple-testing
risk is real. 40bps ~= fee-clearing at 0.25%/side (50bps) with margin.
"""
import json
import os
import glob
import time
import bisect
import statistics
from collections import defaultdict

BASE = os.path.dirname(os.path.abspath(__file__))
CANDLEDIR = os.path.join(BASE, "candles")
OUT = os.path.join(BASE, "history_hunt.json")

THRESHOLDS = (25, 50)        # bps, trailing-5min lead move
TRAIL_MIN = 5
REF_TOL_MIN = 10             # ref candle must be within last 10 min
ENTRY_DELAY_MIN = 2
ENTRY_TOL_MIN = 7            # entry candle within [T+2, T+7] min
HORIZONS = (5, 15, 30, 60)   # minutes
MIN_GAP_MIN = 60
MIN_SUBGROUP_N = 10
ALERT_N = 200
ALERT_BPS = 40.0


def load_candles():
    """Return {product: (sorted_ts_list, {ts: close})}."""
    books = {}
    for fp in sorted(glob.glob(os.path.join(CANDLEDIR, "candles_*_1m.jsonl"))):
        prod = os.path.basename(fp)[len("candles_"):-len("_1m.jsonl")].replace("_", "-")
        # "AMP_USD" -> "AMP-USD"
        if prod.endswith("-USD"):
            pass
        else:
            prod = prod.replace("_", "-", 1) if "_" in prod else prod
        closes = {}
        with open(fp) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                    closes[int(r["t"])] = float(r["c"])
                except (KeyError, ValueError, TypeError):
                    continue
        ts = sorted(closes)
        if ts:
            books[prod] = (ts, closes)
    return books


def newest_le(ts, closes, t_max, t_min_allowed):
    """Newest candle with t <= t_max and t >= t_min_allowed. None if absent."""
    i = bisect.bisect_right(ts, t_max) - 1
    if i < 0 or ts[i] < t_min_allowed:
        return None
    return ts[i]


def first_ge(ts, t_min, t_max_allowed):
    """First candle with t >= t_min and t <= t_max_allowed. None if absent."""
    i = bisect.bisect_left(ts, t_min)
    if i >= len(ts) or ts[i] > t_max_allowed:
        return None
    return ts[i]


def detect_events(books):
    """Lead events across all 7 books. Returns (events, gap_skips)."""
    events = []
    gap_skips = 0
    for prod, (ts, closes) in books.items():
        last_ev = -10 ** 12
        for m in ts:
            if m - last_ev < MIN_GAP_MIN * 60:
                continue
            ref = newest_le(ts, closes, m - TRAIL_MIN * 60, m - REF_TOL_MIN * 60)
            if ref is None:
                gap_skips += 1
                continue
            if closes[ref] <= 0:
                continue
            chg = (closes[m] - closes[ref]) / closes[ref] * 1e4
            if abs(chg) < min(THRESHOLDS):
                continue
            events.append({
                "lead": prod, "m": m,
                "chg": chg, "dir": 1 if chg > 0 else -1,
                "thr": max(t for t in THRESHOLDS if abs(chg) >= t),
                "hour": int(time.strftime("%H", time.gmtime(m))),
            })
            last_ev = m
    events.sort(key=lambda e: e["m"])
    return events, gap_skips


def main():
    books = load_candles()
    result = {"generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
              "alert": "NONE", "alert_detail": None}
    n_candles = sum(len(ts) for ts, _ in books.values())
    result["n_candles"] = n_candles
    result["n_books"] = len(books)
    if len(books) < 2:
        result["note"] = "need >=2 books with candles, found %d" % len(books)
        json.dump(result, open(OUT, "w"), indent=1)
        print("insufficient books: %d" % len(books))
        return

    events, gap_skips = detect_events(books)
    result["n_events"] = len(events)
    result["lead_gap_skips"] = gap_skips
    if not events:
        result["note"] = "no lead events detected"
        json.dump(result, open(OUT, "w"), indent=1)
        print("no lead events")
        return

    horizon_skips = 0
    groups = defaultdict(list)  # (cond, horizon) -> [(follow, fade, abs)]
    for e in events:
        lm, ldir = e["m"], e["dir"]
        for fprod, (fts, fcl) in books.items():
            if fprod == e["lead"]:
                continue
            entry = first_ge(fts, lm + ENTRY_DELAY_MIN * 60,
                             lm + ENTRY_TOL_MIN * 60)
            if entry is None:
                horizon_skips += 1
                continue
            for h in HORIZONS:
                tol = max(5 * 60, (h * 60) // 4)
                ex = first_ge(fts, entry + h * 60, entry + h * 60 + tol)
                if ex is None:
                    horizon_skips += 1
                    continue
                if fcl[entry] <= 0:
                    continue
                d = (fcl[ex] - fcl[entry]) / fcl[entry] * 1e4
                follow, fade = d * ldir, -d * ldir
                keys = [("thr", e["thr"]),
                        ("hour", "%02d" % e["hour"]),
                        ("lead", e["lead"]),
                        ("fol", fprod)]
                for ck in keys:
                    groups[(ck, h)].append((follow, fade, abs(d)))
    result["horizon_gap_skips"] = horizon_skips

    subgroups = []
    for (ck, h), v in sorted(groups.items()):
        n = len(v)
        if n < MIN_SUBGROUP_N:
            continue
        fol = [x[0] for x in v]
        fad = [x[1] for x in v]
        ab = [x[2] for x in v]
        sg = {"cond": "%s=%s" % ck, "h": h, "n": n,
              "follow_win": round(sum(1 for x in fol if x > 0) / n, 3),
              "fade_win": round(sum(1 for x in fad if x > 0) / n, 3),
              "follow_mean": round(statistics.mean(fol), 1),
              "fade_mean": round(statistics.mean(fad), 1),
              "abs_mean": round(statistics.mean(ab), 1),
              "p_abs_ge_50": round(sum(1 for x in ab if x >= 50) / n, 3)}
        subgroups.append(sg)
        best = max(sg["follow_mean"], sg["fade_mean"])
        if n >= ALERT_N and best >= ALERT_BPS:
            result["alert"] = "FOUND"
            result["alert_detail"] = dict(
                sg, side="follow" if sg["follow_mean"] >= sg["fade_mean"] else "fade")

    result["subgroups"] = subgroups
    json.dump(result, open(OUT, "w"), indent=1)

    print("books=%d candles=%d events=%d lead_gaps=%d horizon_gaps=%d alert=%s"
          % (len(books), n_candles, len(events), gap_skips, horizon_skips,
             result["alert"]))
    if result["alert_detail"]:
        print("ALERT:", json.dumps(result["alert_detail"]))

    print("\nfade mean bps by (thr, horizon):")
    tab = defaultdict(dict)
    for sg in subgroups:
        if sg["cond"].startswith("thr="):
            tab[sg["cond"]][sg["h"]] = (sg["n"], sg["fade_mean"], sg["fade_win"])
    for ck in sorted(tab):
        cells = " ".join("h%-3d n=%-5d %+.0f(%d%%)" % (h, n, m, int(w * 100))
                         for h, (n, m, w) in sorted(tab[ck].items()))
        print("  %s: %s" % (ck, cells))

    for side, key in (("fade", "fade_mean"), ("follow", "follow_mean")):
        best = sorted([s for s in subgroups if s["n"] >= ALERT_N],
                      key=lambda s: s[key], reverse=True)[:6]
        print("\ntop %s subgroups (n>=%d):" % (side, ALERT_N))
        for s in best:
            print("  %s h=%dm n=%d %s %+.1fbps win %d%% | other %+.1fbps"
                  % (s["cond"], s["h"], s["n"], side,
                     s[key], int(s[side + "_win"] * 100),
                     s["follow_mean"] if side == "fade" else s["fade_mean"]))


if __name__ == "__main__":
    main()
