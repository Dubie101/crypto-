#!/usr/bin/env python3
"""Daily burst-structure monitor — Mike's 3-traders-same-second observation.

Reads the last 24h of quote_events, recomputes the burst structure per
(product, side, second), appends a summary row to burst_structure_log.jsonl,
and reports ONLY on structural change vs the trailing 7-day baseline:
  - modal burst count shifts, or
  - add-vs-pull disagreement rate moves > 15pts, or
  - median cross-burst price spread moves > 2x.

Mike observed this pattern visually for 1+ weeks (2026-10-05); the logger
confirmed it instrumentally within minutes. This tracks whether it persists.
"""
import json, glob, os, datetime, statistics
from collections import defaultdict, Counter

BASE = os.path.expanduser('~/workspace/goals/paper-trading-simulator/hidden_files/book_watcher/quote_events')
LOG = os.path.join(BASE, 'burst_structure_log.jsonl')

now = datetime.datetime.now(datetime.timezone.utc).timestamp()
cutoff = now - 24 * 3600
persec = defaultdict(list)
n_events = 0
for f in sorted(glob.glob(os.path.join(BASE, 'events_*.jsonl'))):
    with open(f) as fh:
        for line in fh:
            d = json.loads(line)
            if d.get('kind') != 'l2':
                continue
            t = datetime.datetime.fromisoformat(d['ts']).timestamp()
            if t < cutoff:
                continue
            n_events += 1
            persec[(d['product'], d['side'], int(t))].append((t, d))

def burst_dir(b):
    s = 0
    for _, r in b:
        try:
            dq = float(r.get('new_qty', 0)) - float(r.get('old_qty', 0))
        except Exception:
            dq = 0
        if r.get('removal'):
            s -= 1
        elif dq > 0:
            s += 1
        elif dq < 0:
            s -= 1
    return 'add' if s > 0 else ('pull' if s < 0 else 'flat')

burst_counts = Counter()
disagree = 0
n3 = 0
spreads = []
for k, v in persec.items():
    if len(v) < 6:
        continue
    v = sorted(v, key=lambda x: x[0])
    bursts = [[v[0]]]
    for e in v[1:]:
        if e[0] - bursts[-1][-1][0] > 0.05:
            bursts.append([e])
        else:
            bursts[-1].append(e)
    burst_counts[len(bursts)] += 1
    if len(bursts) == 3:
        n3 += 1
        dirs = tuple(burst_dir(b) for b in bursts)
        if 'add' in dirs and 'pull' in dirs:
            disagree += 1
        means = [statistics.mean(float(r['price']) for _, r in b) for b in bursts]
        spreads.append((max(means) - min(means)) / statistics.mean(means) * 1e4)

row = {
    'date_utc': datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d'),
    'n_l2_events': n_events,
    'busy_seconds': sum(burst_counts.values()),
    'modal_bursts': burst_counts.most_common(1)[0][0] if burst_counts else None,
    'burst_dist': dict(sorted(burst_counts.items())),
    'disagree_rate': disagree / n3 if n3 else None,
    'median_spread_bps': statistics.median(spreads) if spreads else None,
}
with open(LOG, 'a') as fh:
    fh.write(json.dumps(row) + '\n')

# structural-change check vs trailing 7-day baseline (excluding today)
hist = []
if os.path.exists(LOG):
    with open(LOG) as fh:
        for line in fh:
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get('date_utc') != row['date_utc']:
                hist.append(r)
hist = hist[-7:]
alert = None
if len(hist) >= 3 and row['modal_bursts'] is not None:
    base_modal = Counter(r['modal_bursts'] for r in hist if r.get('modal_bursts') is not None).most_common(1)
    base_dis = [r['disagree_rate'] for r in hist if r.get('disagree_rate') is not None]
    base_spr = [r['median_spread_bps'] for r in hist if r.get('median_spread_bps') is not None]
    if base_modal and row['modal_bursts'] != base_modal[0][0]:
        alert = f"modal burst count shifted {base_modal[0][0]} -> {row['modal_bursts']}"
    elif base_dis and row['disagree_rate'] is not None and abs(row['disagree_rate'] - statistics.mean(base_dis)) > 0.15:
        alert = f"disagreement rate moved {statistics.mean(base_dis):.0%} -> {row['disagree_rate']:.0%}"
    elif base_spr and row['median_spread_bps'] is not None and statistics.mean(base_spr) > 0 and row['median_spread_bps'] / statistics.mean(base_spr) > 2:
        alert = f"cross-burst spread widened {statistics.mean(base_spr):.0f} -> {row['median_spread_bps']:.0f}bps"

print(json.dumps({'row': row, 'alert': alert, 'baseline_days': len(hist)}))
