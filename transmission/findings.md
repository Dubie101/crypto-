# Lane 5: cross-asset transmission at event time — findings

## Verdict: common forcing. No directional transmission identified.

Across 1,545 ≥5bps/30s leader events in the F1 cluster (XPL/VET/KITE/2Z,
5.76 days of 1s mids), **no ordered pair shows directional temporal
asymmetry that survives all controls.** The 30s factor finding stands as
contemporaneous covariance: they move together because a common process
moves them, not because one transmits to another at 1s resolution.

## What was measured

- **Events:** ≥5bps 30s mid move, leader = argmax |30s move| at detection
  (T0 = detection time), ≥120s separation. 1,545 events (~268/day).
- **First moves (stated threshold):** first ≥3bps 5s mid move per asset
  within T0±60s, at 1s resolution. Unsigned; no directional flow claims
  (maker-side labeling caveat).
- **Pair statistic:** for ordered pair (A,B),
  asym = (# A-first-move strictly earlier than B by >2s
           − # B earlier than A by >2s) / (# decisive).
- **Controls:** (i) time-shift ±120s of B's series, (ii) 40-rep
  circular-shift null per asset with full pipeline re-run,
  (iii) asset-label permutations.

## Results

**Event classification** (first-move ordering vs the detected leader):

| class | n | share |
|---|---|---|
| leader_first | 237 | 15% |
| follower_first | 638 | 41% |
| zero_lag (all within ±2s) | 81 | 5% |
| indeterminate (some asset quiet) | 589 | 38% |

The leader is the *largest* 30s move, not the temporal originator:
follower-first dominates, and 63% of follower-first cases are XPL moving
first (237/638) — XPL's first wiggle precedes even when another asset has
the larger 30s excursion.

**Strongest pair asymmetries:**

| pair | asym | circular-null p95 | time-shift ±120s |
|---|---|---|---|
| XPL→VET | +0.354 | 0.294 (exceeds all 40) | +0.274 / +0.273 (persists) |
| 2Z→VET | +0.299 | 0.256 (exceeds all 40) | +0.231 / +0.262 (persists) |
| XPL→KIT | +0.157 | 0.151 (borderline) | +0.078 / +0.108 (persists) |

Two pairs exceed the circular-shift null — but **both persist under ±120s
time-shift**, failing the pre-registered bar: *a transmission claim
requires the real lead/lag distribution to differ from ALL controls.*
A seconds-scale causal lag cannot survive a 120s shift of one leg.

**The dt distributions have no transmission mode.** Median |dt| ≤ 3s for
every pair, histogram mode ≈ +1s (i.e., zero at 2s bins), IQRs span
roughly ±20s. A genuine lead would put a mode at the lag; there is none.

**The "first move" metric measures wiggle onset, not the impulse.**
The leader's own first qualifying move sits at the window start
(median −55s, p25/p75 −60/−41s): events are embedded in already-volatile
periods, consistent with the causal-tracks finding that agitation builds
5–10 min before leadership. Ordering first-wiggles in a volatile regime
measures *move texture*, not transmission.

**Same-sign sensitivity** (first move in the direction of the leader's 30s
move): leader_first 373 vs follower_first 816 — worse, not better.

## Interpretation (inferred, not measured)

The surviving asymmetries are threshold-crossing texture effects. XPL
reprices in discrete ~4bps quote steps (established in the XPL-scale
work); a step crosses the 3bps/5s threshold almost immediately, while an
asset ramping the same 5bps over 30s (VET's typical texture) crosses it
later in the same volatile window. 2Z is similarly spiky vs VET. So
"XPL precedes VET" = XPL's quote-maker reacts to the common shock in
discrete steps while VET's ramps — a **reaction-speed difference under
common forcing**, which this design cannot distinguish from causation,
and which the shift control rules out as fixed-lag transmission.

## Controls audit

- Circular-shift null (40 reps, full re-run): valid; two pairs exceed it.
- Time-shift ±120s: valid; kills the transmission interpretation for both.
- Asset-label permutation: **degenerate as implemented** — the
  max-over-pairs statistic is invariant under label permutation (renaming
  pairs doesn't change the set). Replaced with the per-pair null, which is
  just the multiset of the 12 observed ordered-pair asyms — weak by
  construction with 12 pairs. Reported for completeness; the verdict does
  not rest on it.

## Data-quality note for Lane 6 (audit)

The tick lane contains **~22,096 duplicate seconds (4.4%)**, all
within-file, with mid differences up to **164bps** between rows of the
same second — consistent with overlapping writers (watchdog restart while
the old capture process still lived). An earlier version of this analysis
resolved duplicates arbitrarily (unstable argsort); it now uses
deterministic last-row-wins. Verdict unchanged across both resolutions
(1544 vs 1545 events). Edge event counts on the live lane may vary by a
few as the tape grows; detection is deterministic within a fixed tape
(verified: identical onsets across repeat runs).

## Limits

- 1s resolution: sub-second transmission is out of scope (Lane 3).
- First-move definition is threshold- and noise-sensitive by construction.
- Unsigned moves only (maker-side labeling caveat).
- 5.76 days of tape; 1,545 events.

## Files

- `build_transmission.py` — full reproduction (event detection, first
  moves, pair stats, all three controls).
- `transmission_results.json` — per-pair stats with control comparisons,
  classification counts, leader shares, meta.
