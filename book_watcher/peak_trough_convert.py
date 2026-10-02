#!/usr/bin/env python3
"""Peak-to-trough (and trough-to-peak) conversion analysis on one day of
per-second tick data.

Input : ticks/ticks_YYYY-MM-DD.jsonl (one JSON line per wall-clock second)
        {"t": "2026-09-30T13:40:01Z",
         "d": {PRODUCT: [mid, spr_bps, imb5, bb_px, bb_sz, ba_px, ba_sz,
                         tr_n, buy_n, sell_n]}}
        Rows per product are null when the book was empty that second.
Output: peaks_bottoms_YYYY-MM-DD.json

RESOLUTION CAVEAT: sampling is 1 second. Mike asked for "137ms before each
peak" -- 137ms falls inside one sample and cannot be resolved from this data.
The script uses the trades in the second containing t0-137ms (tr_n, buy_n,
sell_n) as the pre-extremum trade read and labels it as such throughout.
No millisecond precision is claimed anywhere.

Method (per product):
  - Build mid series from non-null seconds.
  - Local PEAK = strict local maximum over a +-60 second window
    (first occurrence wins ties); local BOTTOM = strict local minimum.
    Window must have >=80% of its 121 seconds present.
    Peaks/bottoms whose window touches the series start/end are skipped
    (series edges). Detected extrema are forced >=60s apart.
  - For each peak: trade activity 137ms before the peak second (tr_n, buy_n,
    sell_n from the second containing t0-137ms); the LARGEST bottom after
    the peak = minimum mid from the peak second to the end of the captured
    day; drawdown in bps from the peak mid; seconds from peak to trough.
  - Symmetric for each bottom: trade activity 137ms before the bottom's
    second; largest subsequent peak; recovery in bps; seconds to it.
  - "Highest peaks must convert to largest bottom": peaks ranked by
    drawdown depth; top-10 reported.

O(peaks) trough lookups are O(1) via precomputed suffix min/max arrays.
"""
import bisect
import json
import os
import sys
import time
from datetime import datetime

BASE = os.path.dirname(os.path.abspath(__file__))
PRODUCTS = ["AMP-USD", "XPL-USD", "KITE-USD",
            "2Z-USD", "MOG-USD", "VET-USD", "XYO-USD"]
WINDOW_S = 60            # +-60s local extremum window (assumption)
COVER_FRAC = 0.80        # >=80% of window seconds present (assumption)
GAP_S = 60               # extrema forced >=60s apart
PRE_MS = 137             # trade read taken 137ms before the extremum second
                         # (1s samples: uses the second containing t0-137ms)

try:
    import numpy as np
    HAVE_NP = True
except ImportError:
    HAVE_NP = False


def log(msg):
    line = "%s %s" % (time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                    time.gmtime()), msg)
    print(line, flush=True)
    with open(os.path.join(BASE, "peak_trough_convert.log"), "a") as f:
        f.write(line + "\n")


def parse_t(s):
    return int(datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ")
               .replace(tzinfo=None).timestamp())


def load_series(path):
    """Return {product: list of (unix_t, mid, tr_n, buy_n, sell_n)}."""
    series = {p: [] for p in PRODUCTS}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            t = parse_t(r["t"])
            d = r.get("d", {})
            for p in PRODUCTS:
                row = d.get(p)
                if row is None:
                    continue
                try:
                    mid = float(row[0])
                except (TypeError, ValueError):
                    continue
                series[p].append((t, mid, row[7], row[8], row[9]))
    for p in PRODUCTS:
        series[p].sort(key=lambda x: x[0])
    return series


def find_extrema(times, mids, kind):
    """kind: 'peak' -> local maxima; 'bottom' -> local minima.
    Returns list of indices. O(n log n) via time-sorted window search."""
    n = len(times)
    if n == 0:
        return []
    res = []
    last_t = -10 ** 18
    if HAVE_NP:
        ta = np.array(times)
    for i in range(n):
        t0 = times[i]
        # window [t0-60, t0+60]; count present seconds
        lo = t0 - WINDOW_S
        hi = t0 + WINDOW_S
        if HAVE_NP:
            l = int(np.searchsorted(ta, lo))
            r = int(np.searchsorted(ta, hi, side="right"))
            present = r - l
            if present < COVER_FRAC * (2 * WINDOW_S + 1):
                continue
            if l == 0 or r == n:  # window touches series edge
                continue
            w = np.array(mids[l:r])
            c = i - l
            if kind == "peak":
                if w[c] < np.max(w):
                    continue
                # first occurrence wins ties: no equal-or-greater before c
                if np.any(w[:c] >= w[c]):
                    continue
            else:
                if w[c] > np.min(w):
                    continue
                if np.any(w[:c] <= w[c]):
                    continue
        else:
            # plain-python fallback
            l = r = i
            while l > 0 and times[l - 1] >= lo:
                l -= 1
            while r < n - 1 and times[r + 1] <= hi:
                r += 1
            present = r - l + 1
            if present < COVER_FRAC * (2 * WINDOW_S + 1):
                continue
            if l == 0 or r == n - 1:
                continue
            w = mids[l:r + 1]
            c = i - l
            if kind == "peak":
                if w[c] < max(w):
                    continue
                if any(x >= w[c] for x in w[:c]):
                    continue
            else:
                if w[c] > min(w):
                    continue
                if any(x <= w[c] for x in w[:c]):
                    continue
        if t0 - last_t < GAP_S:  # keep extrema >=60s apart
            continue
        last_t = t0
        res.append(i)
    return res


def median(xs):
    if not xs:
        return None
    xs = sorted(xs)
    n = len(xs)
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def trades_137ms_before(times, rows, i):
    """Trade flow at t0-137ms. 1s samples can't resolve 137ms, so take the
    last captured second at or before (t0 - 1), which is the second that
    contains t0-137ms. Returns (tr_n, buy_n, sell_n, used_t)."""
    t0 = times[i]
    j = bisect.bisect_right(times, t0 - 1, 0, i) - 1
    if j < 0:
        return (0, 0, 0, None)
    r = rows[j]
    return (r[2], r[3], r[4], times[j])


def analyze_product(p, rows):
    times = [r[0] for r in rows]
    mids = [r[1] for r in rows]
    n = len(times)
    out = {"seconds_present": n}
    if n == 0:
        out.update({"peaks": 0, "bottoms": 0,
                    "median_drawdown_bps_peak_to_trough": None,
                    "median_recovery_bps_bottom_to_peak": None,
                    "top10_conversions": [], "top10_recoveries": []})
        return out

    # suffix min (for peak -> largest later bottom) and suffix max
    if HAVE_NP:
        ma = np.array(mids)
        sufmin = np.minimum.accumulate(ma[::-1])[::-1]
        sufmax = np.maximum.accumulate(ma[::-1])[::-1]
        sufmin_t = np.empty(n, dtype=np.int64)
        sufmax_t = np.empty(n, dtype=np.int64)
        cur_t, cur_m = times[-1], mids[-1]
        for i in range(n - 1, -1, -1):
            if mids[i] < cur_m:
                cur_m, cur_t = mids[i], times[i]
            sufmin_t[i] = cur_t
        cur_t, cur_m = times[-1], mids[-1]
        for i in range(n - 1, -1, -1):
            if mids[i] > cur_m:
                cur_m, cur_t = mids[i], times[i]
            sufmax_t[i] = cur_t
        sufmin = sufmin.tolist()
        sufmax = sufmax.tolist()
        sufmin_t = sufmin_t.tolist()
        sufmax_t = sufmax_t.tolist()
    else:
        sufmin, sufmin_t = [0.0] * n, [0] * n
        sufmax, sufmax_t = [0.0] * n, [0] * n
        cur_t, cur_m = times[-1], mids[-1]
        for i in range(n - 1, -1, -1):
            if mids[i] < cur_m:
                cur_m, cur_t = mids[i], times[i]
            sufmin[i], sufmin_t[i] = cur_m, cur_t
        cur_t, cur_m = times[-1], mids[-1]
        for i in range(n - 1, -1, -1):
            if mids[i] > cur_m:
                cur_m, cur_t = mids[i], times[i]
            sufmax[i], sufmax_t[i] = cur_m, cur_t

    peak_idx = find_extrema(times, mids, "peak")
    bot_idx = find_extrema(times, mids, "bottom")

    conversions = []
    dds = []
    for i in peak_idx:
        t0, m0 = times[i], mids[i]
        tr_n, buy_n, sell_n, used_t = trades_137ms_before(times, rows, i)
        tm, tmid = sufmin_t[i], sufmin[i]
        dd = (m0 - tmid) / m0 * 1e4 if m0 else 0.0
        dds.append(dd)
        conversions.append({
            "peak_t": t0,
            "peak_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                      time.gmtime(t0)),
            "peak_mid": m0,
            # trade flow 137ms before the peak second (1s samples: the
            # second containing t0-137ms; see assumptions)
            "tr_n_m137ms": tr_n,
            "buy_n_m137ms": buy_n,
            "sell_n_m137ms": sell_n,
            "trades_utc": (time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                         time.gmtime(used_t))
                           if used_t is not None else None),
            "trough_t": tm,
            "trough_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                        time.gmtime(tm)),
            "trough_mid": tmid,
            "drawdown_bps": dd,
            "secs_to_trough": tm - t0,
        })
    conversions.sort(key=lambda c: c["drawdown_bps"], reverse=True)

    recs = []
    recoveries = []
    for i in bot_idx:
        t0, m0 = times[i], mids[i]
        tr_n, buy_n, sell_n, used_t = trades_137ms_before(times, rows, i)
        tm, tmid = sufmax_t[i], sufmax[i]
        rec = (tmid - m0) / m0 * 1e4 if m0 else 0.0
        recs.append(rec)
        recoveries.append({
            "bottom_t": t0,
            "bottom_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                        time.gmtime(t0)),
            "bottom_mid": m0,
            # trade flow 137ms before the bottom second
            "tr_n_m137ms": tr_n,
            "buy_n_m137ms": buy_n,
            "sell_n_m137ms": sell_n,
            "trades_utc": (time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                         time.gmtime(used_t))
                           if used_t is not None else None),
            "peak_t": tm,
            "peak_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ",
                                      time.gmtime(tm)),
            "peak_mid": tmid,
            "recovery_bps": rec,
            "secs_to_peak": tm - t0,
        })
    recoveries.sort(key=lambda c: c["recovery_bps"], reverse=True)

    out.update({
        "peaks": len(peak_idx),
        "bottoms": len(bot_idx),
        "median_drawdown_bps_peak_to_trough": median(dds),
        "median_recovery_bps_bottom_to_peak": median(recs),
        "top10_conversions": conversions[:10],
        "top10_recoveries": recoveries[:10],
    })
    return out


def main():
    day = sys.argv[1] if len(sys.argv) > 1 else "2026-09-30"
    path = os.path.join(BASE, "ticks", "ticks_%s.jsonl" % day)
    log("peak-trough analysis start for %s (file=%s)" % (day, path))
    series = load_series(path)
    result = {
        "day": day,
        "assumptions": {
            "extremum_window": "+-60s local max/min",
            "window_coverage": ">=80% of 121 window seconds present",
            "edge_rule": "extrema whose window touches series start/end skipped",
            "spacing": "extrema >=60s apart (first wins)",
            "pre_extremum_trade_read": (
                "trades in the last captured second at or before "
                "extremum_second - 1 (tr_n, buy_n, sell_n), i.e. the second "
                "containing t0-137ms; 137ms itself cannot be resolved from "
                "1s samples"),
            "trough_definition": (
                "minimum mid from peak second to end of captured day"),
        },
        "products": {},
    }
    for p in PRODUCTS:
        res = analyze_product(p, series[p])
        result["products"][p] = res
        log("%s: %d peaks, %d bottoms, median_dd=%.1f bps, "
            "median_rec=%.1f bps"
            % (p, res["peaks"], res["bottoms"],
               res["median_drawdown_bps_peak_to_trough"] or -1,
               res["median_recovery_bps_bottom_to_peak"] or -1))
    out_path = os.path.join(BASE, "peaks_bottoms_%s.json" % day)
    with open(out_path, "w") as f:
        json.dump(result, f, indent=1)
    log("wrote %s" % out_path)


if __name__ == "__main__":
    main()
