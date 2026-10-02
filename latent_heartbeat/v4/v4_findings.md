# v4: from covariance to dynamics — 30-second state-persistence experiment

**Question carried over from v3:** v3 established a real contemporaneous common
factor (F1 = 32% of 30s mid-return variance, XPL/VET/KITE/2Z cluster). PCA proves
a low-dimensional *covariance structure*. It does not prove a latent *causal
state*. v4 moves from "observations → common factor" to
"past observations → estimated state → future observations", at 30s resolution.

**Method (frozen before OOS, per Mike's spec):** same 1s tick files aggregated
to 30s bars (9,650 bars, Sep 29–Oct 2). Four observable panels: (a) 30s log-mid
returns, (b) spread-bps changes, (c) imb5 changes, (d) trade-flow imbalance
(buy−sell)/(buy+sell). Train = Sep 29–Oct 1, test = Oct 2. Train-only z-score
normalization. k chosen on train scree before any OOS. Strictly leave-one-out
factor scores in every predictive step (v3's leakage lesson). Dynamics:
F_{t+1} = φF_t + ε; per asset and horizon h ∈ {30,60,120,300}s,
r_i(t+h) = α + βF_t + γ·r_i(t−lags) + ε, all parameters frozen from train.
Controls on the frozen test segment: AR own-past, factor-only, AR+factor,
SHUFFLED-factor, TIME-SHIFTED-factor (6h circular shift). ws_leaders.json read
only at the final blind step.

**Reporting discipline:** each row below is one observation, what it
*establishes*, and what it explicitly *does not establish*. The admissible
conclusions are (a) persistent factor, no observable ordering; (b) persistent
factor + trade-flow → imbalance → mid ordering; (c) contemporaneous covariance
without meaningful temporal state persistence. The data selects (c).

---

## The six observations

### 1. F_t persists → establishes temporal persistence; does NOT establish a causal latent state

| factor | φ (30s, train, LOO) |
|---|---|
| F1 (cluster: XPL/VET/KITE/2Z) | **+0.07 – +0.08** (full-sample 0.081; ~7σ vs 0, n≈6,800) |
| F2 (XYO idiosyncratic) | **−0.14** (anti-persistent bounce) |

φ ≈ 0.08 is statistically distinguishable from zero but economically
negligible: ~93% of the factor is fresh innovation each 30s. A slowly-evolving
latent state would show φ ≈ 0.5–0.9. **The observation does not even reach the
"establishes" half: temporal persistence is effectively absent.** And per the
template, persistence alone would never have established a causal state anyway.

### 2. R²(h) decays smoothly → establishes predictive information has a timescale; does NOT establish why that persistence exists

Pooled out-of-sample R² vs naive zero forecast (Oct 2, frozen train params,
n≈2,717 per asset):

| h | AR own-past | factor-only | AR+factor | SHUF-factor | TSHIFT-factor |
|---|---|---|---|---|---|
| 30s | 0.0079 | 0.0028 | **0.0146** | 0.0081 | 0.0083 |
| 60s | 0.0079 | −0.0012 | 0.0050 | 0.0047 | 0.0039 |
| 120s | 0.0077 | −0.0000 | 0.0064 | 0.0053 | 0.0051 |
| 300s | 0.0078 | 0.0001 | 0.0076 | 0.0069 | 0.0074 |

Incremental AR+factor over AR: **+0.0067 → −0.0029 → −0.0013 → −0.0002.**
There is no smooth decay to interpret — the shape is a one-horizon blip that
vanishes immediately, with frozen train parameters actively hurting beyond 30s.
The blip is F1-driven and cluster-coherent (F1-only factor R² at 30s: XPL
+0.0045, KITE +0.0122, 2Z +0.0014, VET +0.0046; F2-only ≈ 0 everywhere).
Footnote, reported not explained away: AMP shows the largest AR+factor jump
(+0.033) but its F1-only R² is 0.003 — not F1-driven, not cluster-coherent,
~1.2 SE: single-asset noise.

No smooth curve exists here to be tempted by — and the template's warning
stands regardless: even a clean decay would establish only that predictive
information *has a timescale*, never *why* (an autocorrelated omitted process
produces exactly that shape).

### 3. Shifted control collapses → establishes timing matters; does NOT establish causality by itself

At 30s: real synchronized factor 0.0146 vs time-shifted 0.0083 vs shuffled
0.0081 vs AR baseline 0.0079. Both controls collapse to the AR baseline.
**Timing matters — established.** The 30s predictive footprint depends on the
actual synchronized realization, not on persistent covariance alone. Causality
is not claimed: this rules out "spurious covariance", nothing more.

### 4. Trade flow leads imbalance → establishes order-flow precedence; does NOT establish that the factor causes it

**Untestable, not falsified:** 70.2% of 30s bars across these books contain
zero trades. The trade-flow panel (d) could not support a PCA (k=0, too sparse)
and no flow→imbalance lead-lag could be measured. These books barely trade —
hundreds of quote rewrites per print — so at 30s resolution there is no
transaction flow to carry, lead, or be led by anything. The order-flow leg of
the mechanism test is missing data, not a negative result.

### 5. Imbalance leads mid → establishes book-to-price propagation; does NOT establish a particular economic mechanism

Not observed. The imbalance panel's scree sits at the noise floor
(15.7/15.1/14.5/14.4% vs 14.3% baseline); its F1 correlates −0.17 with the
mid-price F1 at lag 0 — a weak contemporaneous trace in a near-noise panel, no
measurable lead. **No book-to-price propagation established.**

### 6. Mid only → establishes common quote dynamics; does NOT establish underlying trading pressure

Observed. The 32%-variance F1 exists in mid-price quote movements and
effectively nowhere else: spread-panel F1 correlation −0.007 (the spread panel
has its own distinct MOG/XYO structure — a different phenomenon), imbalance
near-noise, trade flow unmeasurable. **Common quote dynamics: established.
Underlying trading pressure: not established — and not inferable from quotes
alone.**

"Mid-only" is not a failed experiment. It falsifies the stronger mechanism
hypothesis (flow → imbalance → mid propagation of a latent state) while fully
preserving the weaker finding: the covariance itself is real, stable (v3:
median |cos| = 0.988 across rolling windows), and leakage-clean.

---

## Admissible conclusion: (c)

**Contemporaneous covariance without meaningful temporal state persistence.**
Concretely: synchronized quote co-movement among XPL/VET/KITE/2Z (32% of 30s
mid-return variance), φ ≈ 0.08, a synchronization-dependent but evanescent
one-step predictive footprint (+0.0067 pooled R² at 30s, gone by 60s), no
cross-observable propagation, no measurable trade flow. Not (a) — persistence
is ~absent. Not (b) — no ordering detected.

## Secondary results

- **k selection (train scree, before OOS):** mid returns k=2 (F1 32.4%, F2
  15.6% — F2 loads 0.97 on XYO alone: idiosyncratic mean-reversion, φ=−0.14,
  not market structure). Spread k=3, imbalance k=4 (near-flat screes —
  reported, not interpreted as structure). Trade-flow panel too sparse for PCA.
- **Blind leaders-board check (30s, read only at final step):** Spearman
  ρ = −0.07 between |β_F1| and observed leadership counts. Null again,
  confirming v3 (ρ=−0.04): discrete large-move leadership (currently
  XYO > XPL > AMP) is a distinct mechanism from continuous co-movement.
- **Coverage:** 9,650 thirty-second bars; 6,873 complete train rows (returns
  panel); 18 gaps of 2–5 min handled by forward-fill ≤60s / NaN across longer.

## Method notes (self-caught, as in v3)

- An AR(5) specification initially used lags 0–4, making lag-0 identical to
  the target (R² = 1.0 across the board). Caught on inspection of pooled
  results *before* any reporting; corrected to strict lags 1–5 and the full
  pipeline re-run. No reported number was ever produced by the buggy spec.
- LOO factor scores throughout; train-only normalization; k fixed on train
  scree; all horse-race parameters frozen before touching Oct 2.

## What would change this picture

- An asset set or venue with actual trade flow at 30s resolution — the flow leg
  was untestable here, which bounds but does not close the mechanism question.
- A longer-horizon persistence test (minutes→hours) — outside this design.
- The leadership/large-move-sequencing mechanism remains unexplained and is
  now twice-confirmed independent of the co-movement factor; it is the more
  interesting open phenomenon.
