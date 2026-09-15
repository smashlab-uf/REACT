# Paper A — MSSD Decision Engine: Methods (draft)

*Author: Tien Tyler Le. Draft assembled from `backend/decision_engine/`,
`backend/syntheticData/`, `analytics/sensitivity_analysis/`, and `analysis-resources/`.
Open items requiring confirmation before submission are marked **[OPEN]**.*

---

## 1. MSSD Definition and Decision Rules

### 1.1 Definition

For participant $i$, let $x_{i,t}$ denote the Likert-scale (1–7) response to a target Ecological
Momentary Assessment (EMA) item at the $t$-th completed prompt. The mean successive squared
difference (MSSD) is the classical measure of within-person instability across a full observed
series:

$$\text{MSSD}_i = \frac{1}{n-1}\sum_{t=2}^{n}\left(x_{i,t}-x_{i,t-1}\right)^2$$

The decision engine does not compute this whole-series quantity in real time. Instead, at each
new completed prompt $t$, it computes a **rolling, recency-weighted MSSD** over the most recent
$w=3$ successive differences:

$$\widehat{\text{MSSD}}_{i,t} = \frac{1}{\min(w,\,t-1)}\sum_{k=\max(2,\,t-w+1)}^{t}\left(x_{i,k}-x_{i,k-1}\right)^2$$

with $w=3$ and a minimum window of 1 (so the second and third observations for a participant use
a partial window rather than being undefined). This windowed form is a design choice, not an
approximation error: a rolling window keeps the signal sensitive to a participant's *current*
state rather than averaging over their entire study history, which is the property a real-time
trigger needs. Offline audits of study data should use the value logged in `JITAILog.observed_mssd`
at decision time, not a freshly recomputed whole-series MSSD — the two are defined differently and
will not agree.

*(Implementation: `backend/decision_engine/decision_engine.py::calculate_mssd`.)*

### 1.2 Decision rule

The engine evaluates, at every newly completed EMA, whether to fire a JITAI prompt. The full
pipeline, in the order it is applied, with each rule stated as a design decision and its
rationale:

1. **Burn-in.** The first observation per participant has no prior value and yields no MSSD; the
   next three yield an MSSD but not yet a threshold (§1.2, item 2, requires 3 prior MSSD values).
   Decisions are therefore only possible from a participant's 5th completed EMA onward.
   *Rationale: a percentile computed over fewer than three points is not a meaningful summary of
   a person's own variability.*
2. **Within-person threshold.** The trigger threshold is the 80th percentile of the participant's
   own $\widehat{\text{MSSD}}$ history, computed on an *expanding* window (all prior observations,
   equally weighted) and lagged by one observation, so today's decision never uses today's own
   value to define the bar it is compared against.
   *Rationale: benchmarking each participant against their own history, rather than a
   population-wide cutoff, makes the trigger idiographic; lagging the threshold keeps the
   comparison strictly out-of-sample.*
3. **Eligibility gate.** A prompt becomes eligible when
   $\widehat{\text{MSSD}}_{i,t} > \text{threshold}_{i,t}$.
   *Rationale: a volatility spike relative to one's own baseline — not an absolute EMA level — is
   the theoretically motivated JITAI trigger.*
4. **Daily cap.** No more than 4 prompts are sent to a participant per calendar day, checked
   before the cooldown rule below.
   *Rationale: bounds participant burden independent of how volatile a given day looks.*
5. **Cooldown.** A minimum of 60 minutes must elapse between two sent prompts.
   *Rationale: prevents a single sustained volatility episode from generating near-duplicate
   prompts.*
6. **Micro-randomization.** Among eligible, non-capped, non-cooldown-blocked decision points, a
   prompt is actually sent with fixed probability $p=0.50$ (applied downstream of the eligibility
   gate; not part of `decision_engine.py` itself).
   *Rationale: randomizing delivery among eligible moments is what allows the intervention's
   causal effect to be identified separately from the eligibility mechanism itself.*

All five numeric defaults above (80th percentile, 60-minute cooldown, 4/day cap, $p=0.50$
randomization) are PI-confirmed design decisions, not arbitrary code defaults — see
`Resources/TODO.md`, "PI Sign-Offs Received (2026-07-06)."

*(Implementation: `add_within_person_threshold`, `apply_decision_rules` in the same module.)*

**[OPEN]**: this subsection is written directly from the code and the PI sign-off table. The
"AI Days" draft has not yet been incorporated — fold its framing/structure in on top of the
material above rather than rewriting from scratch.

---

## 2. Synthetic Data Generation

### 2.1 Generative model

Each simulated participant's EMA trajectory is a stationary, discrete-time, first-order
autoregressive (AR(1)) Gaussian process, instantiated independently per participant and per
construct (energy, stress):

$$z_{i,1} \sim \mathcal{N}(0,\sigma_i^2), \qquad
z_{i,t} = \rho_i\, z_{i,t-1} + \varepsilon_{i,t},\quad
\varepsilon_{i,t}\sim\mathcal{N}\!\left(0,\ \sigma_i^2(1-\rho_i^2)\right)$$

$$x_{i,t} = \text{clip}\big(\text{round}(\mu_i + z_{i,t}),\ 1,\ 7\big)$$

The innovation variance is scaled by $\sigma_i^2(1-\rho_i^2)$ so that the stationary (marginal)
variance of $z_{i,t}$ is constant at $\sigma_i^2$ for all $t$ — this is the standard AR(1)
parameterization, not a random walk. The process is mean-reverting by construction: there is no
drift or trend term, so a participant's baseline $\mu_i$ does not change over the simulated study
unless explicitly perturbed (§2.4, §3.2).

### 2.2 Parameters varied per participant

The production-schema-aligned generator draws each participant's parameters independently and
uniformly, separately for the two production trigger constructs (energy = item `B1`, stress =
item `B2`):

| Construct | Baseline $\mu$ | Volatility $\sigma$ | Inertia $\rho$ |
|---|---|---|---|
| Energy (B1) | $\mathcal{U}(3,5)$ | $\mathcal{U}(0.4,1.4)$ | $\mathcal{U}(0.2,0.8)$ |
| Stress (B2) | $\mathcal{U}(2,4)$ | $\mathcal{U}(0.4,1.2)$ | $\mathcal{U}(0.2,0.8)$ |

$\mu$ is the participant's set point on the 1–7 scale; $\sigma$ is their stationary volatility;
$\rho$ is the autocorrelation (inertia) linking consecutive EMA responses. All values are drawn
fresh per participant, so no two simulated participants share a generating process.

*(Implementation: `backend/syntheticData/react_cohort.py::generate_react_cohort`.)*

### 2.3 Missingness

EMA non-response is modeled as a two-state Markov chain, not missing-completely-at-random: a
participant who has just missed a prompt is more likely to miss the next one than a participant
who just responded, producing clustered runs of non-response of mean length `mean_gap_length`
(default 3) at a long-run response rate `resp_rate` (default 0.80). *Rationale: this better
reflects real non-response mechanisms (e.g., a phone left on do-not-disturb for a stretch of the
day) than an independent-miss model would.*

*(Implementation: `_clustered_missing_mask`, shared by both generators in `backend/syntheticData/`.)*

### 2.4 What "scenario families" means in this simulator

The generator itself contains no hardcoded discrete scenario branches (no "stable participant,"
"crisis participant" code paths). Every participant is an independent draw from the same
continuous priors in §2.2. Two distinct constructs downstream of the generator introduce
controlled scenarios:

- **Post-hoc volatility labeling.** A participant's closed-form expected MSSD,
  $E[\text{MSSD}] = 2\sigma^2(1-\rho)$, is binned into Stable ($\le 0.4$), Mid, or Volatile
  ($\ge 1.6$) purely as a labeling convenience for reporting — not a separate generative process.
- **Volatility-path scenarios (§3.2, §7.2).** A separate, newer extension replaces the constant
  $\sigma$ with a time-varying $\sigma_t$ (stationary, step-shift up, step-shift down, or drifting)
  to test the decision rule's behavior when its stationarity assumption is violated. This is not
  part of the core generator described in §2.1–2.3; it is purpose-built for the robustness
  analysis in §7.2.

### 2.5 EMA delivery schedule

Scheduled prompt count is `n_prompts = days × ema_per_day`. At the default `ema_per_day = 5`,
prompts are scheduled at a fixed two-hour intraday grid (09:00, 11:00, 13:00, 15:00, 17:00). Each
scheduled EMA carries a **30-minute response window** (`app.ema_catalog.EMA_RESPONSE_WINDOW_MINUTES`);
answered prompts resolve uniformly within 3–27 minutes (median 15), so every answered prompt lands
inside its own window and no "late response" tail is generated.

The response window is one of three durations in this system that must not be conflated: the
30-minute *response* window here, the 60-minute *JITAI refractory* between two sent prompts (§3),
and the 2-hour *post-prompt outcome window* (`outcome_window_start`/`outcome_window_end`).

**[RESOLVED 2026-09-04]**: earlier drafts of this section reported a 60-minute response window with
an 88%/12% in-window/late split. That figure was inherited from a simulator whose late tail
postdated its own `expires_at` and was therefore unreachable in production — the mobile client
blocks submission once `expires_at` passes. `analysis-resources/JITAI-analysis-plan.md` specifies
30 minutes; the simulator, backend and analytics now agree with it.

### 2.6 Ground truth

The only ground-truth label attached to a simulated participant is the closed-form stationary
expected MSSD, $E[\text{MSSD}]=2\sigma^2(1-\rho)$, computed from that participant's own generating
parameters. There is no ground-truth "true state," "crisis onset," or "true trigger time" anywhere
in the simulator — recovery and calibration analyses (§5, §6) validate against this one continuous
magnitude, not against a labeled event.

**[OPEN]**: confirm whether this section should cite `analysis-resources/SCHEMA.md` or
`analysis-resources/production_schema.md` as the schema companion for reproducing the generator —
both exist; the generator's own code/README cite `production_schema.md`, while `SCHEMA.md` is the
more detailed standalone schema writeup. Recommend citing both with one sentence distinguishing
them.

---

## 3. Simulation Assumptions

Canonical parameter table for all simulation-based analyses in this paper:

| Parameter | Value | Note |
|---|---|---|
| EMA opportunities/day | 5 | Fixed simulation value. The study protocol document describes a 5–6/day range; simulations here fix it at 5. |
| JITAI daily cap | 4 | PI-confirmed (`Resources/TODO.md`, 2026-07-06) |
| Threshold quantile | 0.80, expanding, within-person | PI-confirmed, same table |
| EMA response window | 30 minutes | Per `JITAI-analysis-plan.md`. How long a participant has to answer a prompt. Distinct from Cooldown below — the two were conflated in earlier drafts. |
| Cooldown | 60 minutes | PI-confirmed, same table. The JITAI refractory between two *sent* prompts; unrelated to the response window above. |
| MSSD rolling window | 3 | Distinct from the *expanding* threshold window in the row above — the MSSD itself is a 3-observation rolling statistic; the threshold is computed on the full expanding history of that statistic |
| Burn-in | first observation + next 3 (emergent from `min_periods=3`) | Not a named parameter |
| Randomization probability | 0.50 | PI-confirmed, same table |
| EMA response rate | 0.80 (simulation default) | Distinct from two protocol-level figures: a 75% preregistered feasibility benchmark and an ~80% analytic requirement below which AR(1) recovery degrades — these are not the same quantity and should not be conflated in text |
| Study duration | 35 days (5 weeks) | Primary assumption throughout; see open item below |
| Response scale | 1–7 Likert (production) | The retired construct-validation harness (`synthetic_generator.py`) instead used 1–5; any numbers drawn from that harness must be flagged as pre-production-scale |

**[OPEN]**: `analytics/sensitivity_analysis/mssd_validation.py`'s own docstring still asks to
"confirm study length with Prof. Chang" — a 14-day feasibility sub-study (from the now-removed
`JITAI-analysis-plan-8.7.pdf`) versus the 35-day full deployment used everywhere else in the
current codebase. Recommend stating 35 days as the primary assumption and footnoting the 14-day
figure as a still-open confirmation, rather than resolving it silently.

---

## 4. Parameter Grid

To validate the recovery relationship in §6 under controlled (rather than incidental) volatility
conditions, a factorial grid crosses five volatility levels with three inertia levels:

$$\sigma \in \{0.3,\ 0.6,\ 0.9,\ 1.2,\ 1.5\}, \qquad \rho \in \{0.2,\ 0.5,\ 0.8\}$$

giving 15 cells, with 12 simulated participants per cell (default) and a fixed baseline $\mu=3.0$
on the legacy 1–5 scale. Each cell corresponds to a known, fixed $E[\text{MSSD}]=2\sigma^2(1-\rho)$,
against which the empirically recovered MSSD and AR(1) parameters (§6) are compared.

*(Design: `analytics/sensitivity_analysis/mssd_validation.py::build_validation_cohort`.)*

**[OPEN — implementation gap, not a design issue]**: as currently committed, the cohort generator
called by `build_validation_cohort()` does not accept explicit $\sigma$/$\rho$ overrides — it
draws them internally at random per participant — so this grid does not yet execute as specified
(it would raise a `TypeError`). The design above describes the intended grid; the generator needs
a small signature change (accept optional fixed $\mu$/$\sigma$/$\rho$) before this grid can be
regenerated and its cell-level numbers cited in §6. Until then, §6's numbers below are drawn from
the *uncontrolled* random-draw cohort (which already carries per-participant `true_sigma`/
`true_rho` labels and does not depend on this fix), not from this controlled grid.

---

## 5. Threshold Calibration

The 80th-percentile within-person threshold (§1.2, item 2) is a confirmed design input, tied to a
dosage target: the protocol analysis plan states the calibration is expected to yield
approximately 4 eligible prompts per week per participant at the 80th percentile
(`analysis-resources/JITAI-analysis-plan.md`, "Important Figures"), matching the PI-confirmed
daily cap of 4/day as an upper bound rather than a typical rate.

**[OPEN]**: the specific derivation of *why* the 80th percentile — as opposed to, say, the 75th or
90th — was selected is referenced in project history as an existing, already-reviewed result
("Tien's threshold calibration (the why-80 result) is in the paper and the IRB") but the
derivation itself was not located as a standalone document in this repository. Locate that source
(prior notebook, external document, or the IRB protocol text) before finalizing this section — the
constraint that MSSD must never be blended with other signals (HRV, sleep), only gated alongside
them, is preserved *because* altering MSSD would invalidate this existing calibration, per
project history (commits `d5effac`, `3bcef98`).

---

## 6. Recovery Analysis

Two questions are distinguished: (a) does the rolling/windowed empirical MSSD track the
participant's true stationary expected MSSD, and (b) can the underlying AR(1) parameters
($\hat\rho$, $\hat\sigma$) be recovered from the observed, possibly-missing series alone.

- **MSSD recovery** (uncontrolled random-draw cohort, `sensitivity_analysis.ipynb`, $n=89$
  simulated participants): empirical MSSD correlates with true expected MSSD at $r = 0.823$; mean
  empirical MSSD was 0.953 against a mean true expected MSSD of 1.155, at a response rate of 0.80.
  Under the default 0.80 threshold quantile, only approximately 2.6% of decision points met the
  eligibility threshold in this cohort.
- **AR(1) parameter recovery**: $\hat\rho$ recovery is markedly more sensitive to response rate
  than $\hat\sigma$ recovery — consistent with the analytic requirement (§3) that response rate
  stay at or above ~80% for reliable parameter recovery, distinct from the lower 75% preregistered
  feasibility benchmark.
- **Success criterion**: the validation harness treats $r \ge 0.70$ (empirical vs. true expected
  MSSD) as the construct-validity pass threshold.

*(Implementation: `recover_ar1_params`, `empirical_mssd`, `build_config_table`, `plot_recovery` in
`mssd_validation.py`.)*

Once the parameter-grid fix (§4) lands, these recovery statistics should be regenerated per-cell
on the controlled 15-cell grid rather than only on the uncontrolled random draws, so the paper can
report recovery quality as a function of true $\sigma$ and $\rho$ directly rather than only in
aggregate.

---

## 7. Sensitivity Analysis

Two sensitivity analyses exist and address different questions; they should not be merged into
one subsection.

### 7.1 Compliance and burn-in sensitivity

Sweeps EMA response rate (70–90%) crossed with burn-in length (0–7 days), 150 simulated
participants × 10 replications per cell (1,500 participant-weeks per cell). Findings: a response
rate above 70% is recommended for reliable dosage and recovery; response rate and threshold
quantile behave as separable "dials" — each independently shapes dosage without one substituting
for the other.

*(Source: `analytics/sensitivity_analysis/sensitivity_analysis.ipynb`, committed 2026-07-08.)*

### 7.2 Robustness to stationarity misspecification

The decision rule (§1.2) assumes each participant's volatility is a fixed personal trait,
calibrated once during an initial run-in period and never revisited. This analysis tests what
happens when that assumption is violated mid-study — explicitly designed at Prof. Chang's request
to "break the assumption and report how the threshold behaves," rather than to validate it under
the assumptions it already makes.

**Design**: four latent volatility paths $\sigma_t$, applied to synthetic participants otherwise
generated as in §2:

| Scenario | Path | Represents |
|---|---|---|
| Stationary | $\sigma_t = \sigma_0$ | Control; matches the engine's built-in assumption |
| Shift up | $\sigma_0 \to 2\sigma_0$ at day 14 | Acute stressor onset |
| Shift down | $\sigma_0 \to 0.5\sigma_0$ at day 14 | Habituation or recovery |
| Drift | $\log\sigma_t$ follows AR(1), $\phi=0.97$ | Gradual, non-stationary volatility with no clean breakpoint |

Each scenario is scored against the real production decision functions
(`calculate_mssd`, `apply_decision_rules`, imported unmodified), and against a diagnostic
alternative — a rolling 7-day threshold window in place of the expanding window — evaluated only
as a contrast, not proposed as a production change. Design: 4 scenarios × 2 threshold arms × 10
replications × 150 participants, common random numbers held across scenarios for paired
comparison, detection metrics scored on threshold-crossing itself (not on `send_prompt`) so that
cooldown and the daily cap do not contaminate the sensitivity/specificity measurement.

**Results**:

| | Stationary | Shift up | Shift down | Drift |
|---|---|---|---|---|
| True positive rate | 0.51 | 0.70 | 0.26 | 0.46 |
| False positive rate | 0.08 | 0.19 | 0.06 | 0.11 |
| Precision | 0.61 | 0.48 | 0.54 | 0.52 |
| Prompts/week, pre-shift | 4.6 | 4.6 | 4.6 | 4.8 |
| Prompts/week, post-shift | 4.1 | 9.2 | 1.0 | 4.4 |
| Tracking ratio, post-shift | 1.00 | 0.62 | 1.71 | 1.00 |
| Days to recalibrate | 0 | never | never | 0 |

Under the production (expanding) threshold, neither shift is ever recovered from within the
35-day protocol — a mid-study volatility change is permanent for the remainder of the study. The
shift-down case is the more consequential failure mode: dosage collapses to about one prompt per
week and sensitivity halves, with no error, alert, or dashboard signal — the intervention simply
goes quiet. The shift-up case is the opposite failure — dosage roughly doubles while precision
falls, i.e., more prompts, a smaller share of them warranted. The rolling 7-day alternative
recalibrates within 5–6 days of a shift and restores tracking and dosage, at a small precision
cost on the stationary control (0.61 → 0.57).

**Scope of the claim**: this analysis supports (a) recommending further discussion with Prof.
Chang of whether the within-person reference window should be trailing rather than full-history,
and (b) adding a monitoring check that flags participants whose prompt rate collapses relative to
their own earlier weeks. It does **not** support adopting any specific replacement threshold rule
— only one alternative (a 7-day rolling window), one shift magnitude, and one breakpoint day were
tested, on synthetic AR(1) data with a clean step change rather than a realistic gradual
transition, and with all participants shifting on the same study day. $\rho$ was held fixed
throughout, so autocorrelation misspecification is untested. Absolute decision-point rates in this
analysis (~4/day) reflect EMA completions and are not directly comparable to the 2–3/day
production dosage projection in `data-dictionary.md`; only the cross-scenario *ratios* reported
above should be treated as the finding.

*(Source: `analytics/sensitivity_analysis/robustness_misspecification.ipynb`, 2026-09-04; outputs
in `analytics/sensitivity_analysis/outputs/` and `analytics/sensitivity_analysis/figures/robustness/`.)*

---

## Open items summary

1. Incorporate the AI Days draft's framing into §1 once supplied.
2. Locate and cite the original "why-80" threshold-calibration derivation for §5.
3. Fix `generate_cohort()`/`generate_user()` to accept explicit $\mu$/$\sigma$/$\rho$ so the §4
   parameter grid runs as designed, then regenerate §6's per-cell recovery numbers.
4. Confirm study duration for §3/§4 — 35 days primary vs. the still-open 14-day sub-study
   question flagged in `mssd_validation.py`.
5. Confirm whether §2 cites `SCHEMA.md`, `production_schema.md`, or both.
