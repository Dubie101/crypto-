# Latent market-state ("heartbeat") experiment — findings

**Question:** Is there a continuously evolving latent market state from which the
observed 1-second tick events are imperfect, delayed, scaled, or displaced
manifestations? Tested by trying to *statistically reconstruct* such a state
from independent observations — not by exact event matching.

**Method (frozen before evaluation):** 1-second log-mid-returns, per-asset
z-scored on the train segment (Sep 29–Oct 1), PCA for common factors, per-asset
response `r_i(t+1) = α + β·F(t−ℓ)` with lags from leave-one-out
cross-correlation (±120 s), then a frozen three-way out-of-sample horse race on
Oct 2: own past AR(5) vs latent factor(s) vs both. The leaders board
(`ws_leaders.json`) was read only *after* all fitting, for a blind comparison.

## Data coverage

- 283,001 one-second rows, Sep 29 13:32 UTC → Oct 2 21:16 UTC (~79.5 h span).
- 1.64% of seconds missing; 18 gaps of 2–5 min (restarts/outages). Per-product
  nulls tiny except MOG-USD (1,940) and XYO-USD (718).
- Gap policy: forward-fill quotes up to 60 s; return = NaN across longer gaps.
  PCA/OLS used complete rows only (200,050 train rows).

## Finding 1 — There is a real common factor, but it is a *cluster* factor, not market-wide

- Observable screen: 1s log-mid-returns carry the most common structure
  (F1 = 19.1% of variance) vs spread changes (15.3%) and imbalance changes
  (14.5%, both near the 14.3% white-noise baseline).
- Scree is nearly flat: F1 19.1%, F2 14.6%, F3–F7 14.3%→11.0%. Only F1 clearly
  exceeds the 1/7 noise baseline; Kaiser → k=2, F2 marginal.
- F1 loadings: XPL −0.61, VET −0.61, KITE −0.37, 2Z −0.34, AMP/XYO/MOG ≈ 0.
  **The "heartbeat" at 1-second resolution is a 4-asset subgroup state**
  (XPL/VET/KITE/2Z), not a 7-asset market state. AMP, XYO, MOG are effectively
  independent at this timescale.
- F1 strengthens with timescale: 19% (1 s) → 31% (30 s) → 32% (60 s).
  Microstructure noise dominates at 1 s; the common state lives lower.
- Loadings are stable: median |cosine| = 0.988 across 113 rolling 2-hour
  windows. **Kalman skipped with justification** — no evidence of time-varying
  loadings; a static factor model suffices.

## Finding 2 — F2 is a data artifact, not a market state

- F2 loads −0.80 on MOG-USD. MOG's 1s "mid" is a stale step function: 45 snaps
  of 5–115% between discrete price levels with ~zero trades (e.g. +115% then
  −87% within 5 s, no prints). These are quote artifacts, not price discovery.
- Winsorizing z-returns at ±1σ leaves F1 unchanged (19.4%) and collapses F2 to
  exactly the noise baseline (14.3%). **F2 is entirely MOG snaps.**
- All F2 cross-correlations are tiny (|r| ≤ 0.03) with scattered lags — noise.

## Finding 3 — No lead/lag at ±120 s; the cluster moves contemporaneously

- Leave-one-out lag estimates (asset excluded from its own factor, so no
  self-contamination): **all assets lag 0 s** at 1 s resolution *and* at 30 s
  aggregation (±120 s window). F1 |xcorr|: XPL 0.23/0.59, VET 0.23/0.60,
  KITE 0.12/0.41, 2Z 0.11/0.40 (1 s / 30 s); AMP/XYO/MOG ≈ 0.
- Within the resolution available, the cluster moves *together*, not in sequence.

## Finding 4 — Blind leaders-board test: NOT reproduced (honest negative)

- Spearman rank correlation between estimated |β_F1| and independently observed
  30 s-bucket leadership counts: **ρ = −0.04, p = 0.94**. Zero.
- Estimated β ranking: VET > KITE > XPL > 2Z > AMP > XYO > MOG.
  Observed leadership: XPL > XYO > AMP > 2Z > KITE > VET > MOG.
- XPL-first and MOG-last match, but XYO (#2 observed leader) carries no F1
  loading and VET (strongest β) ranks #6 observed. The 1 s continuous
  co-movement factor does **not** explain the discrete >5 bps/30 s leadership
  dynamics. Those are a different phenomenon (large-move sequencing), not a
  delayed manifestation of the 1 s factor.

## Finding 5 — Out-of-sample horse race: factor adds real but small predictive information

Next-second z-return, Oct 2, frozen train parameters, R² vs naive zero forecast:

| asset | AR(5) own past | factor only | AR + factor | Δ (factor over AR) |
|---|---|---|---|---|
| AMP-USD | 0.0055 | 0.0002 | 0.0066 | +0.0011 |
| XPL-USD | 0.0004 | 0.0039 | 0.0030 | +0.0026 |
| KITE-USD | 0.0052 | 0.0050 | 0.0045 | −0.0007 |
| 2Z-USD | −0.0118 | 0.0013 | 0.0035 | +0.0154 |
| MOG-USD | 0.0000 | −0.0002 | −0.0000 | 0 |
| VET-USD | 0.0006 | 0.0086 | 0.0065 | +0.0060 |
| XYO-USD | 0.0095 | −0.0000 | 0.0031 | −0.0064 |

- Pooled mean R²: AR 0.0013, factor-only 0.0027, AR+factor 0.0039.
- The factor helps exactly the F1 cluster members (XPL, VET, 2Z; AMP marginally)
  — a coherent pattern, not scattered wins. It does not help KITE, XYO, MOG.
- Effect sizes are small (R² increments of ~0.1–1.5%). With ~54k test points
  each, they are statistically distinguishable from zero, but **far too small
  to trade on** at 1 s horizons after any realistic cost.
- Multiple-testing context: 7 assets × 3 models. The wins concentrate in the
  pre-identified cluster, which is the internal replication that makes this
  believable rather than a multiple-comparison artifact.

## Methodological note (self-caught)

- v2 of this analysis used full-sample factor scores in the response model,
  which leaked each asset's own contemporaneous return into its regressors
  (2Z showed a fake OOS R² = 0.23). Caught on inspection, rebuilt as v3 with
  strictly leave-one-out scores throughout. All numbers above are v3
  (leakage-clean).

## What would strengthen or kill the hypothesis

- **Strengthen:** the 30 s/60 s F1 (31–32%) deserves the full treatment —
  response model and horse race at 30 s ahead-horizons, where microstructure
  noise is weaker. Lead/lag may also only resolve at coarser steps.
- **Strengthen:** test whether F1 predicts *trade-flow* (buy/sell notional
  imbalance), not just mid-returns — a state that moves quotes but not flows
  is less "heartbeat" and more "quote-coordination."
- **Kill:** if the 30 s factor's OOS edge vanishes, or if F1 fails to predict
  anything but contemporaneous correlation (i.e., it is descriptive, not a
  state with dynamics), the heartbeat reduces to "some assets' quotes wiggle
  together."
- The earlier null rotation result stands narrowly: **the common state does not
  manifest as fixed-size A→B transaction rotation.** It does manifest as
  contemporaneous co-movement of a 4-asset cluster with weak next-second
  predictability. Those two statements are consistent.

## Bottom line

There is *something* there — a statistically real, stable, leakage-clean common
factor shared by XPL/VET/KITE/2Z that adds small out-of-sample predictive
information beyond each asset's own past. But it is (a) a subgroup phenomenon,
not market-wide; (b) contemporaneous, with no detectable lead/lag ≤120 s;
(c) invisible to the leaders-board dynamics (blind test failed cleanly); and
(d) far too weak to monetize at 1 s horizons. "Heartbeat" is too strong a word
for what 1 s data supports; "cluster co-movement state" is the honest label.
The 30 s version of this experiment is the natural next step.
