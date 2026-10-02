#!/usr/bin/env python3
"""Tick-resolution direction hunt.

Question: after an AMP lead move detected at TICK resolution, does the
SUBSEQUENT direction become predictable at 5s-300s horizons?

Method:
- Read ticks/ticks_*.jsonl (per-second mids for the 7 books).
- Detect AMP events: |trailing-5s mid change| >= T bps, for T in {10,25,50}.
  Events are spaced >= 60s apart (no double counting).
- Entry delayed 2s (realistic detection+routing lag). Horizons H in
  {5,15,30,60,300}s: forward net displacement, signed with/against lead.
- Conditions at event time: lead-size bucket, imb5 tercile, spread vs median,
  trade-imbalance sign over the event window, hour of day.

Writes direction_hunt.json: {generated_at, n_ticks, n_events, horizons,
subgroups, alert}. Alert bar ("FOUND"): any subgroup with n>=30 and mean
signed pnl (fade OR follow, whichever wins) >= 40bps -- fee-clearing
territory at 0.25%/side. Prints a human summary to stdout.
"""
import json, os, glob, statistics
from collections import defaultdict

BASE = os.path.dirname(os.path.abspath(__file__))
TICKDIR = os.path.join(BASE, "ticks")
OUT = os.path.join(BASE, "direction_hunt.json")
# FIELDS = [mid, spr_bps, imb5, bb_px, bb_sz, ba_px, ba_sz, tr_n, buy_n, sell_n]

LEAD = "AMP-USD"
TRAIL_S = 5
ENTRY_DELAY_S = 2
THRESHOLDS = (10, 25, 50)
HORIZONS = (5, 15, 30, 60, 300)
MIN_GAP_S = 60
ALERT_N = 30
ALERT_BPS = 40.0


def load_ticks():
    """Return list of (ts_str, epoch_approx, {product: fields}) sorted by time."""
    files = sorted(glob.glob(os.path.join(TICKDIR, "ticks_*.jsonl")))
    rows = []
    for fp in files:
        with open(fp) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if "t" in r and "d" in r:
                    rows.append(r)
    rows.sort(key=lambda r: r["t"])
    return rows


def detect_events(rows):
    """AMP lead events: |5s trailing mid change| >= min(THRESHOLDS)."""
    mids, sprs, imbs, tbuys, tsells = [], [], [], [], []
    for r in rows:
        v = r["d"].get(LEAD)
        if v is None:
            mids.append(None); sprs.append(None); imbs.append(None)
            tbuys.append(0.0); tsells.append(0.0)
        else:
            mids.append(v[0]); sprs.append(v[1]); imbs.append(v[2])
            tbuys.append(v[8]); tsells.append(v[9])
    events = []
    last_ev = -10 ** 9
    for i in range(TRAIL_S, len(rows)):
        if i - last_ev < MIN_GAP_S:
            continue
        if mids[i] is None or mids[i - TRAIL_S] is None or mids[i - TRAIL_S] <= 0:
            continue
        chg = (mids[i] - mids[i - TRAIL_S]) / mids[i - TRAIL_S] * 1e4
        if abs(chg) < min(THRESHOLDS):
            continue
        # trade imbalance over the event window (trailing TRAIL_S seconds)
        bi = sum(tbuys[i - TRAIL_S:i + 1]); si = sum(tsells[i - TRAIL_S:i + 1])
        events.append({
            "i": i, "t": rows[i]["t"],
            "chg": chg, "dir": 1 if chg > 0 else -1,
            "thr": max(t for t in THRESHOLDS if abs(chg) >= t),
            "imb": imbs[i], "spr": sprs[i],
            "trd_imb": (bi - si) / (bi + si) if bi + si > 0 else 0.0,
            "hour": int(rows[i]["t"][11:13]),
        })
        last_ev = i
    return events, mids


def fwd_displacement(mids, i0, delay, horizon):
    """Net bps move from entry (i0+delay) over horizon seconds. None if gaps."""
    a = i0 + delay
    b = a + horizon
    if b >= len(mids) or mids[a] is None or mids[b] is None or mids[a] <= 0:
        return None
    return (mids[b] - mids[a]) / mids[a] * 1e4


def main():
    rows = load_ticks()
    result = {"generated_at": rows[-1]["t"] if rows else None,
              "n_ticks": len(rows), "alert": "NONE", "alert_detail": None}
    if len(rows) < 600:
        result["note"] = "insufficient tick data (<10 min)"
        json.dump(result, open(OUT, "w"), indent=1)
        print("insufficient tick data: %d seconds" % len(rows))
        return
    events, mids = detect_events(rows)
    result["n_events"] = len(events)
    if not events:
        result["note"] = "no lead events detected"
        json.dump(result, open(OUT, "w"), indent=1)
        print("no lead events")
        return

    # imbalance terciles from event distribution
    imbs = sorted(e["imb"] for e in events if e["imb"] is not None)
    sprs = sorted(e["spr"] for e in events if e["spr"] is not None)
    q = lambda s, p: s[min(int(len(s) * p), len(s) - 1)] if s else 0
    imb_lo, imb_hi = q(imbs, 1 / 3), q(imbs, 2 / 3)
    spr_med = q(sprs, 0.5)

    def cond_keys(e):
        keys = [("thr", e["thr"])]
        imb = e["imb"]
        if imb is None:
            keys.append(("imb", "na"))
        elif imb <= imb_lo:
            keys.append(("imb", "ask-heavy"))
        elif imb >= imb_hi:
            keys.append(("imb", "bid-heavy"))
        else:
            keys.append(("imb", "neutral"))
        keys.append(("spr", "wide" if (e["spr"] or 0) > spr_med else "tight"))
        ti = e["trd_imb"]
        keys.append(("trd", "buy-imb" if ti > 0.2 else ("sell-imb" if ti < -0.2 else "flat")))
        keys.append(("hour", "%02d" % e["hour"]))
        return keys

    groups = defaultdict(list)  # (cond, horizon) -> list of (follow_pnl, fade_pnl, abs)
    for e in events:
        for h in HORIZONS:
            d = fwd_displacement(mids, e["i"], ENTRY_DELAY_S, h)
            if d is None:
                continue
            follow = d * e["dir"]
            fade = -follow
            for ck in cond_keys(e):
                groups[(ck, h)].append((follow, fade, abs(d)))

    subgroups = []
    for (ck, h), v in sorted(groups.items()):
        n = len(v)
        if n < 10:
            continue
        fol = [x[0] for x in v]; fad = [x[1] for x in v]; ab = [x[2] for x in v]
        fol_w = sum(1 for x in fol if x > 0) / n
        fad_w = sum(1 for x in fad if x > 0) / n
        sg = {"cond": "%s=%s" % ck, "h": h, "n": n,
              "follow_win": round(fol_w, 3), "fade_win": round(fad_w, 3),
              "follow_mean": round(statistics.mean(fol), 1),
              "fade_mean": round(statistics.mean(fad), 1),
              "abs_mean": round(statistics.mean(ab), 1),
              "p_abs_ge_50": round(sum(1 for x in ab if x >= 50) / n, 3)}
        subgroups.append(sg)
        best = max(sg["follow_mean"], sg["fade_mean"])
        if n >= ALERT_N and best >= ALERT_BPS:
            result["alert"] = "FOUND"
            result["alert_detail"] = dict(sg, side="follow" if sg["follow_mean"] >= sg["fade_mean"] else "fade")

    result["subgroups"] = subgroups
    json.dump(result, open(OUT, "w"), indent=1)

    print("ticks=%d events=%d alert=%s" % (len(rows), len(events), result["alert"]))
    if result["alert_detail"]:
        print("ALERT:", json.dumps(result["alert_detail"]))
    # headline: threshold x horizon table, fade means
    print("\nfade mean bps by (thr, horizon):")
    tab = defaultdict(dict)
    for sg in subgroups:
        if sg["cond"].startswith("thr="):
            tab[sg["cond"]][sg["h"]] = (sg["n"], sg["fade_mean"], sg["fade_win"])
    for ck in sorted(tab):
        cells = " ".join("h%-3d n=%-4d %+.0f(%d%%)" % (h, n, m, int(w * 100))
                         for h, (n, m, w) in sorted(tab[ck].items()))
        print("  %s: %s" % (ck, cells))
    # best subgroups by fade_mean with n>=30
    best = sorted([s for s in subgroups if s["n"] >= 30],
                  key=lambda s: s["fade_mean"], reverse=True)[:8]
    print("\ntop fade subgroups (n>=30):")
    for s in best:
        print("  %s h=%ds n=%d fade %+.1fbps win %d%% | follow %+.1fbps"
              % (s["cond"], s["h"], s["n"], s["fade_mean"],
                 int(s["fade_win"] * 100), s["follow_mean"]))


main()
