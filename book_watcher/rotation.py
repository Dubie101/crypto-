import json, csv
from collections import defaultdict, Counter
from datetime import datetime, timezone

import argparse
ap = argparse.ArgumentParser(); ap.add_argument("--bucket-minutes", type=int, default=5)
ap.add_argument("--events-file", default="events.jsonl"); ap.add_argument("--tag", default="")
A = ap.parse_args()
BASE = "/home/hatch/workspace/goals/paper-trading-simulator/hidden_files/book_watcher"

events = [json.loads(l) for l in open(f"{BASE}/{A.events_file}")]
print(f"events: {len(events)}")
if not events:
    print("no events yet"); raise SystemExit

def bucket(ts):
    dt = datetime.fromisoformat(ts)
    total = dt.hour*60 + dt.minute
    b = (total // A.bucket_minutes) * A.bucket_minutes
    return f"{dt.strftime('%m-%d')} {b//60:02d}:{(b%60):02d}"

buckets = defaultdict(Counter)
for e in events:
    buckets[bucket(e["ts"])][e["product"]] += 1

bs = sorted(buckets)
print(f"\n== event counts per product per {A.bucket_minutes}-min bucket ==")
hdr = ["bucket"] + sorted({p for c in buckets.values() for p in c})
print(" | ".join(f"{h:9s}" for h in hdr))
for b in bs:
    print(" | ".join(f"{b:9s}" if i==0 else f"{buckets[b].get(h,0):9d}" for i,h in enumerate(hdr)))

# concentration per bucket (Herfindahl): 1/n = scattered, 1.0 = one book owns it
print("\n== concentration (Herfindahl) per bucket ==")
for b in bs:
    c = buckets[b]; tot = sum(c.values())
    h = sum((v/tot)**2 for v in c.values())
    hot = c.most_common(2)
    print(f"  {b}: H={h:.2f} n={tot} hot={hot[0][0]}({hot[0][1]})" + (f", {hot[1][0]}({hot[1][1]})" if len(hot)>1 else ""))

# hot-book rotation sequence + transition matrix
hot_seq = [buckets[b].most_common(1)[0][0] for b in bs]
print(f"\nhot-book sequence: {' -> '.join(hot_seq)}")
trans = Counter(zip(hot_seq[:-1], hot_seq[1:]))
print("hot-book transitions:", dict(trans))
prods = sorted(set(hot_seq))
print("\nrotation matrix (row=hot now, col=hot next):")
print("        " + " ".join(f"{p[:6]:>7s}" for p in prods))
for a in prods:
    tot = sum(trans[(a,b)] for b in prods)
    row = " ".join(f"{(trans[(a,b)]/tot if tot else 0):7.2f}" for b in prods)
    print(f"{a[:6]:>7s} {row}")

# same-poll co-occurrence: events on >=2 products in the same 30s poll = coordinated quoting
polls = defaultdict(set)
for e in events:
    polls[e["ts"][:19]].add(e["product"])
multi = {t:ps for t,ps in polls.items() if len(ps)>=2}
print(f"\nco-quoting polls (events on 2+ products same 30s): {len(multi)}/{len(polls)}")
for t in sorted(multi)[:10]:
    print(f"  {t}: {sorted(multi[t])}")

with open(f"{BASE}/rotation_report_{A.bucket_minutes}m.csv","w",newline="") as fo:
    w=csv.writer(fo); w.writerow(["bucket","product","events"])
    for b in bs:
        for p,c in buckets[b].most_common(): w.writerow([b,p,c])
print("\nrotation_report.csv written")
