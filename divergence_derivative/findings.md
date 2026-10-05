# Divergence-derivative branch prediction — findings

**Verdict: the derivative hypothesis survives first contact, but the null does not confirm it. n=2 vs 8 cannot graduate this to a classifier.**

## Core test: onset divergence slope (div(T0) − div(T−30m))

| Class | slope_in values | median |
|---|---|---|
| RESOLVE (n=2) | −0.0377, −0.0363 | **−0.037** |
| CONTINUE (n=7, 1 missing*) | −0.009, −0.039, +0.060, +0.132, +0.111, +0.150, +0.208 | **+0.111** |

*The 8th CONTINUE is the left-censored 09-30 13:29 episode: 24h positions only become
valid at its onset bar, so no pre-onset slope exists.

Both resolves were **contracting** into onset; 5 of 7 continues were **expanding**.
Observed class separation (C median − R median) = **+0.148**. The branch also
persists after onset: slope_post median R −0.043 vs C +0.046, and divergence
deceleration (slope_accel) R −0.016 vs C +0.092.

Two continues (−0.009, −0.039) overlap the resolve range, so onset slope is not a
clean separator — it is a lean, not a rule.

## Null (circular shift per asset, 50 realizations)

- Null slope-separation: mean +0.012, median −0.005, p5–p95 [−0.135, +0.307].
- Observed +0.148 sits at the **~86th percentile** of the null; 14% of shuffled
  realizations produce separation this large or larger.
- Honest reading: **suggestive, not significant.** The null neither confirms the
  effect nor rules it out. Shuffling destroys the cross-asset temporal structure
  that may itself be the phenomenon — exactly the caveat Mike raised.

## What the branches look like (descriptive medians, n=2 vs 8)

The "exhausted + quiet → resolve / expanding + active → continue" pattern holds
across 18 of 21 checked observables (2 ties, 1 contradiction):

| Observable @/→T0 | RESOLVE | CONTINUE | direction |
|---|---|---|---|
| divergence at onset | 0.47 | 0.61 | R less extreme |
| leader return [T−30,T0] | **−77 bps** | **+30 bps** | leader fading vs still ripping |
| laggard return [T−30,T0] | −67 bps | −1 bps | ⚠ contradicts naive "laggard bouncing" |
| leader spread | 13.6 bps | 21.8 bps | R tighter book |
| leader top-of-book depth | $1,839 | $617 | R deeper |
| laggard depth (T−60m) | $480 | $203 | R deeper (sep 6.0 — fragile, n=2) |
| trade arrival, leader/laggard | 0.005/0.0017 /s | 0.010/0.015 /s | R quieter tape |
| quote-update intensity (mean) | 0.30 | 0.40 | R quieter |
| leader 1s vol [T−30,T0] | 1.40 | 1.89 bps/s | R calmer |
| idiosyncratic residual, both | 0.51/0.75 | 0.81/1.15 bps/s | R smaller |
| F1 factor move [T−30,T0] | **+127 bps** | **−44 bps** | cluster rallying vs falling |
| leader book imbalance | −0.03 | +0.18 | — |

The single contradiction is informative: in resolves the **laggard fell harder**
into onset (−67 vs −1 bps) while the leader was already fading (−77 vs +30 bps).
Resolve-type divergence looks like a **joint washout already turning**
(both weak, spread contracting, common factor rallying underneath); continue-type
divergence looks like a **leader rip against a flat-to-weak tape**
(leader +30 bps into onset, factor −44 bps, spread still expanding).

## Live episode (2026-10-03 08:44 UTC, KITE→2Z, UNCLASSIFIED)

slope_pre **+0.217**, slope_in **+0.099** — expanding into onset on both windows.
Resembles the CONTINUE class on the derivative and on the supporting observables.
Descriptive pattern-match on n=2, not a prediction.

## Limitations / what graduation requires

1. **n=2 resolves.** Every class median on the RESOLVE side is two data points.
   Nothing here establishes a classifier; the separation ranking (depth_G@Tm60
   sep 6.0, etc.) is illustrative of where to look, not a result.
2. **Null is inconclusive** (p≈0.14). The effect could be chance alignment of two
   episodes.
3. **Signed flow** (`signrep_*`) is reported sign-uncertain per the documented
   maker-side inversion; no directional conclusion is drawn from it.
4. **All 10 classified episodes are KITE-led.** The "branch" studied is really
   "KITE rips away: fade or run." Generalization to other leaders is untested.
5. Quote-update intensity is a proxy (fraction of seconds with mid change);
   sub-second cancel/replace activity remains invisible on this lane.

**Graduation criterion:** ~30 independent episodes (≈4–6 weeks of tape at the
observed ~11 episodes / 92h, assuming the 20% resolve rate holds). Pre-register
now: RESOLVE iff slope_in < 0 AND leader [T−30,T0] return < 0; then score
hit rate on new episodes only. Do not refit thresholds on this sample.

## Files

- `divergence_derivative_results.json` — per-episode slopes, snapshots,
  sub-window stats, class comparison, separation ranking, consistency map, null.
- `build_derivative.py` — full reproduction script.
- `build.log` — run log.
