# Divergence branch-prediction — FORWARD PROTOCOL (frozen)

Locked 2026-10-03 ~10:35 UTC by Mike. This file is the specification.
Nothing here may be changed without his explicit word: no threshold tuning,
no added observables, no window changes, no case removal.

## The frozen prediction rule

For each NEW independent divergence episode, at onset (T0):

> **PREDICT_RESOLVE** iff `slope_in < 0` **AND** `leader_ret_30m < 0`,
> else **PREDICT_CONTINUE**.

Definitions (frozen, matching `rotation_episodes/build_episodes.py` and
`divergence_derivative/build_derivative.py`):

- 5-min bars, 24h (288-bar) rolling min-max positions per asset.
- Divergence state: `max(position) >= 0.70 AND min(position) <= 0.30`.
- Episodes: gap-tolerant merged runs (gap ≤ 6 bars merges). Leader/laggard
  frozen at onset = argmax/argmin position at the onset bar.
- `div(i)` = max(position[i]) − min(position[i]) (cross-sectional spread).
- `slope_in` = div(T0) − div(T−30m), in position units (6 bars).
- `leader_ret_30m` = leader's bps return over [T−30m, T0].

## Realized branch rule (frozen from rotation_episodes)

- CONTINUE if `fwd12 < −100bps` OR frozen-pair position gap(+12h) > gap(onset).
- Else RESOLVE if `fwd6 > 0` AND `fwd12 >= −100bps`.
- Else AMBIGUOUS. UNCLASSIFIED while fwd12 is unavailable.

## Safeguards (Mike's)

1. The two rule dimensions stay visible **separately** in the ledger
   (`slope_in`, `leader_ret_30m`, each as its own boolean) even if the AND
   rule performs well — a successful rule must not conceal which component
   carries the information.
2. Forward test starts at freeze time. The 11 pre-freeze episodes (including
   tonight's 2026-10-03 08:44 KITE→2Z episode, whose onset observables were
   seen before freezing) are **excluded** from the ledger.
3. Lane 1 record extension (Mike 2026-10-05): the ledger also records
   `f1_30m_bps` (equal-weight 4-asset 30m return at onset) and the realized
   outcome magnitudes (fwd_3h/6h/12h, already recorded) per episode.
   These are passive diagnostic fields — the prediction rule is unchanged.
3. Target: ~30 independent forward episodes (~4–6 weeks of tape) before
   judging the rule.
4. `score_forward.py` self-tests on every run: it re-detects episodes on the
   full history and asserts the 11 historical onsets reproduce exactly. On
   mismatch it aborts without writing — detection logic must not drift.
5. Methodological notes carried forward: a randomized null is not automatically
   valid — shuffling may destroy the temporal structure being measured; and
   aligned observables are not independent evidence (many are correlated).

## Files

- `score_forward.py` — the scorer (predictions + realizations + ledger).
- `forward_ledger.json` — the running scorecard. Pre-freeze episodes are not
  in it. This is the only file the scorer writes.
- `findings.md`, `divergence_derivative_results.json` — the formulation work
  (frozen sample; never counted in the forward score).
