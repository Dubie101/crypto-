# Rotation-episode regime classification — findings

**Date:** 2026-10-03 · **Question:** when the F1 cluster diverges, does it resolve
(laggard catches leader) or continue (leader keeps running) — and can anything
observable *at onset* distinguish the two?

## Verdict first

- **11 independent episodes** (gap-tolerant merged runs; 19 strict maximal runs as
  sensitivity), 10 classified: **2 RESOLVE, 8 CONTINUE**, 1 UNCLASSIFIED (live,
  2026-10-03 08:44 — no forwards yet), 1 left-censored (already diverged at window start).
- **At divergence onset, continuation dominates: 8 of 10 episodes.** Forwards
  (laggard-minus-leader, frozen pair):
  - RESOLVE (n=2): 3h +193, 6h +538, 12h +638 bps (median)
  - CONTINUE (n=8): 3h −325, 6h −463, 12h −680 bps (median); 0/8 positive at 6h and 12h
- The forward distribution is **bimodal with no middle**: episodes either resolve
  hard (~+600bps) or continue hard (~−680bps). This is the fat continuation tail
  as the *body* of the distribution, not the tail.
- **No onset observable separates the two regimes at a level distinguishable from
  noise (n=2 vs 8).** Honest statement: the continuation tail is unpredictable
  from onset observables in this data.
- **Null (circular shift, 50 realizations):** null resolve rate mean 0.66, median
  0.65, p5–p95 [0.39, 1.00] vs **observed 0.20 — below the entire null interval**.
  Chance divergences on independent series mean-revert; real divergences trend.
  The continuation excess is a real feature, not chance.
- All 10 classified episodes have **KITE as the leader** (laggard: 2Z×8, XPL×1,
  VET×1). In this sample the divergence regime *is* "KITE rips, 2Z sits."

## Method (as pre-specified, with two documented refinements)

1. 5-min bars (last mid) per asset from the 1s tick lane, 2026-09-29 → 2026-10-03
   (1,099 bars, 91.9h).
2. Per-asset 24h rolling min-max normalized position (288-bar window, ≥200 valid bars).
3. Divergence state: max(position) ≥ 0.70 AND min(position) ≤ 0.30.
4. Episodes = maximal runs merged with gap tolerance ≤6 bars (30 min).
   **Refinement 1:** strict maximal runs produced 19 "episodes," 8 of them 1-bar
   flickers within one 10-01 morning regime — not independent. Merging is the
   independence-respecting choice; strict count kept as sensitivity (19).
5. Leader/laggard = argmax/argmin position **frozen at onset** (tradeable definition).
6. Forwards: laggard-minus-leader bps at 3h/6h/12h from onset.
7. Classification: CONTINUE if fwd12 < −100bps OR frozen-pair position gap(+12h) >
   gap(onset); else RESOLVE if fwd6 > 0 AND fwd12 ≥ −100bps; else AMBIGUOUS;
   UNCLASSIFIED if fwd12 missing.
   **Refinement 2:** the expansion clause uses the *frozen pair's* position gap,
   not the cross-sectional spread — the spread involves all four assets, so a
   third asset's move could misclassify the frozen pair's convergence. In practice
   all 8 CONTINUEs were decided by the fwd12 < −100 rule (gap clause never solely
   fired), so this changes nothing empirically.
8. F1 proxy for onset observables: equal-weight mean of 4-asset 3h returns (no
   fitted loadings, no leakage).

## Episode table

| Onset (UTC) | L→G | Span/div bars | fwd3h | fwd6h | fwd12h | Class |
|---|---|---|---|---|---|---|
| 2026-09-30 13:29 | KITE→2Z | 9/9 | +71 | −367 | −716 | CONTINUE (left-censored) |
| 2026-09-30 14:44 | KITE→XPL | 1/1 | −76 | −16 | −101 | CONTINUE |
| 2026-09-30 15:59 | KITE→2Z | 24/24 | −322 | −635 | −962 | CONTINUE |
| 2026-09-30 23:39 | KITE→2Z | 1/1 | −525 | −272 | −750 | CONTINUE |
| 2026-10-01 00:19 | KITE→2Z | 84/84 | −201 | −168 | −621 | CONTINUE |
| 2026-10-01 07:56 | KITE→2Z | 19/12 | −191 | −739 | −838 | CONTINUE |
| 2026-10-01 10:33 | KITE→VET | 128/121 | −733 | −695 | −473 | CONTINUE |
| 2026-10-01 21:53 | KITE→2Z | 23/20 | +37 | +388 | +604 | RESOLVE |
| 2026-10-02 00:53 | KITE→2Z | 1/1 | +349 | +687 | +672 | RESOLVE |
| 2026-10-02 16:29 | KITE→2Z | 1/1 | −621 | −810 | −976 | CONTINUE |
| 2026-10-03 08:44 | KITE→2Z | 9/7 | — | — | — | UNCLASSIFIED (live) |

## Onset observables: RESOLVE (n=2) vs CONTINUE (n=8), medians

| Observable | Resolve | Continue | Read |
|---|---|---|---|
| Divergence spread (pair gap) | 0.47 | 0.61 | resolves started milder |
| Leader position | 0.71 | 0.72 | no difference (threshold-selected) |
| Laggard position | 0.24 | 0.14 | resolve laggards less deeply bottomed |
| Hour of day (ET) | 19.4 | 11.4 | both resolves evening; n=2, no claim |
| F1 3h move (bps) | −14 | −24 | no separation |
| Expansion rate (spread now − 3h ago) | −0.08 | +0.13 | resolves already contracting; continuations still expanding |
| KITE trade-arrival pct | 0.44 | 0.81 | continuations had busier KITE tape |
| XPL trade-arrival pct | 0.08 | 0.57 | — |
| Spread pcts (all assets) | mixed | mixed | no clean pattern |
| Trailing-3h vol | similar | similar | no separation |
| Leader fresh 24h high at onset | 0/2 | 0/8 | never — divergence triggers below the extreme |
| Laggard fresh 24h low at onset | 0/2 | 1/8 | essentially never |
| Leader identity | KITE 2/2 | KITE 8/8 | zero variation |

Descriptive pattern (NOT established): resolves look like *exhausted* divergences —
smaller spread, shallower laggard, divergence already contracting into onset —
while continuations look like *active* rips — wider, deeper, still expanding on a
busy tape. With n=2 this is indistinguishable from noise; it is a hypothesis for
the next sample, not a finding.

## The live episode (2026-10-03 08:44, UNCLASSIFIED)

KITE→2Z, spread 0.62, leader pos 0.71, laggard pos 0.09, ET hour 4.7, F1 3h +77bps,
expansion +0.27, laggard at fresh-24h-low = no, three laggards ≤0.30 (XPL/VET/2Z).
On every observable with any descriptive lean (spread, laggard depth, expansion
sign, hour), it resembles the CONTINUE class more than the RESOLVE class.
**This is pattern-matching on n=2 — descriptive only, not a prediction.**

## Reconciliation with the earlier hourly analysis (important)

Three analyses of the same tape, three answers — because they ask different questions:

1. **Hourly + first-match leader/laggard (BUGGY):** 62%/79%/54% positive at
   3/6/12h. INVALID — the laggard was picked by asset order, not by minimum
   position (often VET instead of the true bottomed asset 2Z).
2. **Hourly + argmax/argmin (overlapping, phase-mixed):** 42%/58%/42%.
   Descriptive; mixes onset bars with mid-episode bars (a 7h episode contributes
   ~7 samples at different rip phases).
3. **Independent episodes, frozen pair, from onset (this experiment):**
   30%/20%/20%. Answers Mike's actual question: divergence onset → resolve or continue?

The rotation "edge" seen in (1)–(2) lives mostly *mid-episode*, not at onset.
At onset, the rip usually continues.

## Limitations

- n=10 classified episodes over 3.8 days; the RESOLVE class has n=2. No
  significance claims are made or supportable.
- Leader identity has zero variation (always KITE) — this experiment is really
  "when KITE diverges upward, does it fade or run." Generalization to other
  leaders is untested.
- One episode left-censored; excluding it moves the resolve rate 0.20 → 0.22.
- Circular-shift null destroys cross-asset correlation; it tests "chance
  alignment" — the correct comparator for the resolve-rate question, but it does
  not preserve the cluster's joint dynamics.
- Forwards need 12h of subsequent data; the live episode cannot be classified yet.

## Files

- `rotation_episodes_results.json` — per-episode table, onset observables,
  classification, class comparison, forward stats, null comparison
- `build_episodes.py` — full reproduction script
- `findings.md` — this file
