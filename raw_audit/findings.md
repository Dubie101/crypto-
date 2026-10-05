# Lane 6: independent raw-data audit — findings

## Verdict

**The aggregate results survive independent reconstruction. Zero discrepancies across 93 checks.**

Every claimed measurement re-derived from raw 1s tick data reproduces: leadership
identities and move sizes (30/30, bit-identical), divergence-episode onsets,
70/30 conditions, leader/laggard identities (18/18), positions/spreads
(bit-identical), ledger slope_in/leader_ret_30m/predictions (7/7 bit-identical),
forward realized returns (18/18 within 0.1 bps), and all timestamp conventions.
The laggard-labeling fix is verified as load-bearing (3 episodes where the old
asset-order bug would have given a different answer). No unflagged maker-side
directional-flow claims exist in the divergence/leadership work.

## Discrepancy rates per batch

| Batch | n | Discrepancies | Rate |
|---|---|---|---|
| 1. Leadership events (identity, lchg ±0.05bps, 30s alignment) | 30 | 0 | 0% |
| 2. Divergence episodes (cond, leader, laggard, spread/pos ±0.02, slope ±0.01, ret ±2bps) | 18 | 0 | 0% |
| 3a. Divergence controls (expect no 70/30) | 15 | 0 | 0% |
| 3b. Leadership controls (expect no cluster event per full pipeline rule) | 15 | 0 | 0% |
| Forward realized fwd_3/6/12h (±1bps) | 18 values | 0 | 0% |
| UTC consistency (5317 alignments, 7 ts↔string, 11 parses) | — | 0 | 0% |

Population note: only 18 unique divergence episodes exist (11 rotation_episodes +
7 forward_ledger, no overlap), so batch 2 audited the full population rather
than the requested 30.

## What the audit verified in detail

**Batch 1.** All 30 sampled leadership events: leader = argmax |30s chg| across
all 7 products matches the claimed asset; claimed lchg reproduces bit-identically
(diff 0.0); all t0 on the date-anchored 30s grid. Argmax over the 4 cluster
assets alone agrees with argmax over 7 in all 30 (no non-cluster product ever
outranks the claimed leader in the sample).

**Batch 2.** All 18 episode onsets satisfy max(24h pos) ≥ 0.70 AND min ≤ 0.30
in reconstruction; leader/laggard = argmax/argmin matches claims 18/18;
spreads and positions bit-identical. The left-censored 2026-09-30 13:29 episode:
condition/leader/laggard/spread verified; slope_in and leader_ret_30m not
computable (no 30m of position history) — the pipeline records null here too,
so this is agreement, not a gap.

**(a) Laggard-labeling fix.** Claimed laggard = argmin(position) in 18/18.
In 3 episodes (2026-09-30 15:59, 2026-10-01 00:19, 2026-10-03 08:44) the old
asset-order rule would have returned a different asset — the fix is verified
as load-bearing, not vacuous. The 2026-10-01 10:33 KIT→VET case is correctly
argmin (VET), which coincides with what the old bug would have said; it does
not discriminate but is correct.

**(b) Signed-flow text audit.** Grep over findings.md in causal_tracks,
rotation_episodes, divergence_derivative, leader_selection, xpl_scale,
bot_preset: the only directional (buy-vs-sell) flow statements are either
explicitly flagged INVALID (causal_tracks Track 5, bot_preset Track 5) or
computed under the disclosed corrected sign convention with direction-independent
main conclusions (xpl_scale: flat impact curve, 3% R², λ ranking). The
divergence/forward-ledger work treats signed flow as sign-uncertain and makes
no directional claims. **No unflagged maker-side directional claims found.**

**(c) UTC consistency.** All 5317 leadership t0 values sit exactly on their
date file's 30s grid ((t0−e0) % 30 == 0, e0 = first-row epoch — note 09-29
starts 13:32:52Z, not midnight). All 7 ledger onset_ts values render to their
onset_utc strings. All 11 rotation_episodes onset_utc strings parse as UTC.

**Batch 3b detail.** One control (2026-09-29 14:01:52, XPL −29.3bps, 7/7 valid)
initially looked like a pipeline miss. Root cause: the documented edge trim —
`iT − 1800 < 0` skips events without 1800s of baseline history (iT=1740 here).
Correctly excluded, not a miss. The 3b check mirrors the full rule including
edge trim and the cluster-leader filter.

## Self-caught methodology issues during this audit

1. **Control design v1 was invalid.** 6h exclusion from 5317 leadership events
   (median gap 30s, max gap 34min) is impossible inside the pipeline window,
   forcing all 30 controls into 10-03+, where the leadership pipeline never
   ran — 0/30 "quiet" was a design artifact, not a pipeline failure.
   Redesigned as two well-posed control types (3a: ≥6h from episode onsets
   with episode-span exclusion; 3b: pipeline-window bars with no event).
2. **3a v1 admitted controls inside long episode spans** (the 10-04 17:34
   episode runs 10.1h; 4 controls fell inside it). Fixed by excluding
   [run_start−6h, run_end+6h] around reconstructed merged runs.
3. Trivial: a numpy `bool_` leaked into JSON serialization; fixed with
   explicit `bool()`.

## Overall assessment

The four previously caught errors (laggard labeling, maker-side inversion,
factor leakage, T0/date-key issues) do not recur in the current outputs, and
no new discrepancies were found. The aggregate leadership and divergence
results — including the frozen forward-ledger predictions and realizations —
are faithful to the raw tape. Nothing in the divergence or leadership work
needs correction as a result of this lane.

Limitations: the audit verifies measurement fidelity, not the hypotheses
built on the measurements (e.g., whether the derivative predicts the branch
remains a small-n question). Batch 1 samples 30/5317 events; a rare
systematic error could hide below that resolution, but the bit-identical
reproduction and the 15/15 negative-control agreement make a structural
detection bug unlikely.
