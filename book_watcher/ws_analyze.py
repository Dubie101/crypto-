#!/usr/bin/env python3
"""Leader-lag + rotation analysis over ws_buckets.jsonl.

Reads per-30s microstructure buckets and writes:
  ws_leaders.json  - which book moves first: leader counts and leader->follower pairs
  ws_activity.json - per-hour quote-update totals per book (the rotation scoreboard)
  ws_rotation.json - hot-book sequence per hour and hot->hot transitions

Leader definition (exploratory): in each bucket, the product with the largest
|mid_chg_bps| >= LEAD_THRESH is the candidate leader; products moving the same
direction in the NEXT bucket count as followers. This measures who moves
first, not why -- co-movement is not proof of causality.

Usage: python3 ws_analyze.py [--buckets path] [--outdir dir]
"""
import json, os, argparse
from collections import Counter, defaultdict

BASE = os.path.dirname(os.path.abspath(__file__))
LEAD_THRESH = 5.0  # bps over 30s to qualify as a leadership move


def load(path):
    rows = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--buckets", default=os.path.join(BASE, "ws_buckets.jsonl"))
    ap.add_argument("--outdir", default=BASE)
    args = ap.parse_args()

    rows = load(args.buckets)
    by_ts = defaultdict(dict)
    for r in rows:
        by_ts[r["ts"]][r["product"]] = r
    tss = sorted(by_ts)

    leader_counts = Counter()
    pair_counts = Counter()
    n_moves = 0
    for i, ts in enumerate(tss[:-1]):
        cur, nxt = by_ts[ts], by_ts[tss[i + 1]]
        cands = [(p, r.get("mid_chg_bps", 0.0)) for p, r in cur.items()
                 if abs(r.get("mid_chg_bps", 0.0)) >= LEAD_THRESH and "mid" in r]
        if not cands:
            continue
        lead_pid, lead_chg = max(cands, key=lambda kv: abs(kv[1]))
        n_moves += 1
        leader_counts[lead_pid] += 1
        for p, r in nxt.items():
            if p == lead_pid or "mid_chg_bps" not in r:
                continue
            fc = r["mid_chg_bps"]
            if (lead_chg > 0 and fc > 0) or (lead_chg < 0 and fc < 0):
                pair_counts[(lead_pid, p)] += 1

    hourly = defaultdict(Counter)
    hot_seq = []
    for ts in tss:
        hr = ts[:13] + ":00:00Z"
        for p, r in by_ts[ts].items():
            hourly[hr][p] += r.get("updates", 0)
    for hr in sorted(hourly):
        hot = hourly[hr].most_common(1)
        hot_seq.append({"hour": hr, "hot": hot[0][0] if hot else None,
                        "updates": dict(hourly[hr])})
    rot = Counter()
    for a, b in zip(hot_seq, hot_seq[1:]):
        if a["hot"] and b["hot"] and a["hot"] != b["hot"]:
            rot[(a["hot"], b["hot"])] += 1

    def w(name, obj):
        p = os.path.join(args.outdir, name)
        json.dump(obj, open(p, "w"), indent=1)
        print("wrote", p)

    w("ws_leaders.json", {
        "buckets": len(tss), "lead_moves": n_moves, "thresh_bps": LEAD_THRESH,
        "leaders": dict(leader_counts),
        "pairs": {f"{l}->{f}": n for (l, f), n in pair_counts.most_common(40)}})
    w("ws_activity.json", {hr: dict(c) for hr, c in sorted(hourly.items())})
    w("ws_rotation.json", {
        "hot_sequence": hot_seq,
        "transitions": {f"{a}->{b}": n for (a, b), n in rot.most_common(40)}})

    print(f"\n{len(tss)} buckets, {n_moves} leadership moves (>{LEAD_THRESH} bps/30s)")
    print("leader counts:", dict(leader_counts.most_common()))


main()
