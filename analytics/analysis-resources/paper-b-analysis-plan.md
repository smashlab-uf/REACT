# Paper B — REACT Protocol and Feasibility: Analysis Plan (draft)

*Author: Tien (Tyler) Le. Section of the paper of record. Protocol language is quoted from
`REACT_IRB01_Protocol_v7_tracked.docx` (accepted-changes view; IRB-01, companion to IRB202600940) and the
message bank from `REACT_IRB01_Message_Bank_v1.docx` (IRB202601059). Build descriptions are taken from the
code at the paths cited. The decision engine itself (MSSD definition, threshold, calibration) is Paper A's
subject and is cited, not re-derived, here.*

*Conventions.* Quoted protocol text is in quotation marks and is verbatim. **[DISCREPANCY]** marks a place
where the approved protocol and the built system differ. Each one names both sides, the consequence for the
analysis, and the two ways to close it (amend the protocol or change the build). This draft does not
silently choose. **[OPEN]** marks a decision the protocol leaves unspecified that needs PI sign-off.

---

## 1. Primary Proximal Effect Estimand

The protocol defines the primary estimand as follows (§15.0, Primary analysis):

> "The primary estimand is the causal excursion effect: the average difference in the proximal outcome in
> the window following a decision point at which a prompt was delivered versus not delivered, averaged over
> available decision points."

Formally, let $t$ index decision points of participant $i$ during the micro-randomization period (study
weeks two through five; §9.3). Let $I_{it} \in \{0,1\}$ indicate that the decision point is available, and
$A_{it} \in \{0,1\}$ the randomized assignment to deliver a prompt. $Y_{i,t+1}$ is the proximal outcome
measured in the time-locked outcome window (§2 below), and $H_{it}$ is the participant's history up to the
decision point. The causal excursion effect, marginal over history, is

$$
\beta_0 \;=\; \mathbb{E}\!\left[\,Y_{i,t+1}(\bar A_{i,t-1}, 1) - Y_{i,t+1}(\bar A_{i,t-1}, 0) \;\middle|\; I_{it}=1\right],
$$

where $\bar A_{i,t-1}$ is the participant's own randomized treatment history, so the expectation averages
over the histories the trial itself generates (Boruvka et al., 2018). It is an *excursion* from the
protocol at a single decision point, not the effect of a policy of always versus never prompting. With a
moderator $S_{it} \subseteq H_{it}$, the moderated effect $\beta(S_{it})$ is the same contrast conditional
on $S_{it}$ (§5).

**Treatment is the randomized assignment.** In the analysis dataset,
$A_{it} = \mathbf{1}\{\texttt{randomization\_draw} < \texttt{randomization\_probability}\}$, computed from
the two values the protocol requires to be logged at every decision point (§9.3: "Every decision point,
whether or not a prompt is sent, is logged with its randomization probability, the draw outcome, the
eligible message pool, the selected message, and delivery timestamps").

> **[DISCREPANCY] Assignment vs. delivery.** The protocol's estimand contrasts decision points "at which a
> prompt was delivered versus not delivered." The build separates the two:
> - `JITAILog.send_prompt` is set back to False after a successful draw when no message can be selected
>   (`backend/app/tasks.py::_evaluate_user`, the empty-pool branch).
> - A push that fails keeps `send_prompt=True` with `status='failed'` (`notification_service.mark_delivery_failed`).
>
> Contrasting *delivered* prompts would condition on a post-randomization event and lose the protection of
> randomization.
>
> *Recommendation:* amend the estimand sentence to "at which a prompt was assigned versus not assigned"
> (intention-to-treat), and report the delivered-prompt effect as a secondary, instrumental-variable
> analysis in which the draw instruments delivery. The build already logs everything this needs, so no
> code change is required.

*(Implementation: `analytics/wcls_analysis.ipynb`, §4 `build_decision_points`, columns `available`, `A`, `p`.)*

---

## 2. Proximal Outcome Window

### 2.1 Time-Locked Outcome Assessment

Protocol, §9.2:

> "During the micro-randomization period, the first check-in within two hours after any decision point
> presents the three behavior domains together for a time-locked outcome window; if no scheduled check-in
> falls in that window the app inserts one brief extra check-in, capped at four per day."

Protocol, §10.0 (schedule of measures):

> "During the micro-randomization period, the first check-in within two hours after any decision point
> presents all four domain item sets together for a time-locked outcome window."

**As built.** Every *available* decision point opens a time-locked outcome window at `decision_made_at`,
whatever the randomized assignment (`_latest_outcome_window`, `backend/app/views.py`):

| Time after the decision point | What `/ema/next/` serves | Notification |
|---|---|---|
| 0 to 60 min | Nothing except, after a delivered prompt, the C0 post-prompt rating (§10.0). Ordinary scheduled check-ins are held back (`reason: outcome_window_pending`) in both arms. | None; scheduled check-in reminders are suppressed in both arms. |
| 60 min to 2 h | The outcome check-in: current emotions and situation plus the four domain item sets (`POST_PROMPT_ITEM_IDS`), linked to the decision point (`source_jitai_log`). Typed `post_prompt`, or `extra_check_in` when it follows the notification. | At +60 min, if not yet answered: one visible check-in notification, identical in both arms (`_maybe_send_outcome_reminder`). |
| After 2 h, or once answered | Ordinary scheduled check-ins resume. | Scheduled reminders resume. |

The outcome check-ins share the protocol's cap of four per day. The 60-minute lag
(`OUTCOME_CHECK_IN_DELAY_MINUTES`, `backend/dashboard/data/config.py`) makes the timing of the measurement
independent of assignment. Without it, a delivered prompt brings the participant into the app and the
outcome would be recorded within minutes in the treated arm but at the next scheduled check-in in the other.

**Analysis definition.** For each available decision point, the outcome is taken from the first completed
check-in linked to it (`source_jitai_log_id`, type `post_prompt` or `extra_check_in`) with
`decision_made_at` + 60 min $\le$ `sent_at` $\le$ `decision_made_at` + 2 h. Outcome values are read from
`EMAItemResponse.value_numeric`/`value_choice` by `sub_item_id`. The EMA-level `mood`, `stress` and `energy`
columns are not used, because the submit path does not populate them. A decision point with no such
check-in has a missing outcome (§6).

> **[RESOLVED — was the critical discrepancy] The outcome window opened only after a delivered prompt.**
> Before this change, `_latest_active_jitai` filtered `send_prompt=True` and anchored the window on
> `push_sent_at`. So an available decision point assigned $A=0$ got no time-locked check-in and no inserted
> extra check-in, and reminders were suppressed only after a send. Outcome measurement differed by arm in
> timing, in probability of observation and in item presence.
>
> *Change:* the backend now opens the window for every available decision point as described above, with no
> mobile change needed. It is covered by tests in `backend/app/tests.py` (`EMARotationEndpointTests`,
> `SendCheckinRemindersTests`).
>
> *Consequences for the paper:*
> - **Protocol wording.** §9.2 should read "after any **available** decision point". An unavailable decision
>   point was not randomized, and a window after every check-in would add unapproved burden.
> - **Legacy data.** Decision points before the deployment of this change used the old design. They are
>   excluded from the primary analysis and reported separately as legacy
>   (`DESIGN_CUTOVER` in `wcls_analysis.ipynb`; §6, S6).
> - **[OPEN] PI sign-off.** The 60-minute lag is provisional and needs PI sign-off.
>
> *Evidence (synthetic, `wcls_analysis.ipynb` §9.2):*
> - Under the old design the outcome was observed after 0.29 of sent and 0.38 of unsent decision points, and
>   the estimate was 0.52 against that dataset's true effect of 0.34.
> - Under the new design the rates are 0.76 and 0.75, and the estimate is 0.358 against a true effect of
>   0.356, on twice as many decision points.

> **[DISCREPANCY] Three or four domains.** §9.2 says "the three behavior domains"; §10.0 says "all four
> domain item sets." The build serves `POST_PROMPT_ITEM_IDS = ['B1', 'B2', 'B4', 'B5', 'B6', 'B7']`, i.e.
> the four rotating domains (B4 eating, B5 emotion regulation, B6 alcohol, B7 online activity) plus current
> emotions and situation (`backend/app/ema_catalog.py`).
>
> *Recommendation:* amend §9.2 to "four", matching §10.0 and the build.

### 2.2 Primary Behavioral Outcomes

Protocol, §15.0:

> "Proximal outcomes are drawn from the time-locked check-in within two hours after the decision point:
> self-reported urge to post reactively, self-reported reactive online behavior, self-reported drinking and
> drinking urge, and self-reported emotional or uncontrolled eating, each analyzed separately."

Candidate operationalizations, from the item bank as built (`EMA_ITEM_BANK`, per
`REACT_IRB01_StudyTeam_Measures_v2.docx` Part 1):

| Protocol outcome | Candidate sub-item(s) | Response type | Recall period | Model |
|---|---|---|---|---|
| Urge to post reactively | `B7_urge` ("Right now, how strong is your urge to post or reply about something that upset you?") | Likert 1–7 | Now | Linear WCLS |
| Reactive online behavior | `B7_posted`; `B7_charged`; `B7_self_stopped` | Yes/No; Likert 1–7; 3-level choice | "Past few hours" / "heat of the moment" | Binary: WCLS on the risk-difference scale |
| Drinking | `B6_consumed`; `B6_drink_count` | Yes/No; count | **"Today"** | See note |
| Drinking urge | `B6_urge` | Likert 1–7 | Now | Linear WCLS |
| Emotional or uncontrolled eating | `B4_emotion_influence`; `B4_loss_of_control` | Likert 1–7, shown only if `B4_ate = Yes` | "Since the last check-in" | See note |

Each outcome is analyzed in its own WCLS model. Binary outcomes are analyzed as risk differences by the
same estimator; a log-relative-risk version (the estimator of Qian et al., 2021) is a secondary
specification.

> **[OPEN] Item mapping.** The protocol names four constructs, not items. Which sub-item (or composite)
> operationalizes each outcome, and which of the two named drinking outcomes is primary, need PI sign-off
> before data are examined.

> **[OPEN] Outcomes the prompt can't be credited with, or that condition on post-treatment events.**
> - **Recall period.** `B6_consumed` and `B6_drink_count` ask about "today", so a value in the outcome window
>   includes drinking that happened before the decision point, which the prompt cannot have caused. A
>   time-locked drinking outcome needs either a "since the last check-in" wording or the within-day change in
>   `B6_drink_count` from the decision-point check-in to the outcome check-in.
> - **Conditional items.** `B4_emotion_influence` and `B4_loss_of_control` are shown only if the participant
>   ate. Analyzing them only among eaters conditions on a post-treatment event, since the prompt may change
>   whether someone eats. A composite defined for everyone avoids this, e.g.
>   `emotional_eating = 1{B4_ate = Yes and B4_emotion_influence ≥ 5}` (the ≥ 5 cut matches the routing
>   convention in `ema_catalog.py`).

*(Implementation: `EMA_ITEM_BANK`, `POST_PROMPT_ITEM_IDS`, `ROTATING_ITEM_IDS` in
`backend/app/ema_catalog.py`; outcome join in `wcls_analysis.ipynb` §4.)*

---

## 3. Weighted-and-Centered Least Squares Analysis

Protocol, §15.0:

> "This is estimated by weighted and centered least squares (Boruvka et al., 2018), with weights derived
> from the known randomization probability of 0.5 and centering on the randomization probability, which
> yields a consistent estimate of the marginal excursion effect without requiring a model for the outcome
> under no treatment. Standard errors use a sandwich estimator with small-sample correction, clustering at
> the participant level."

### 3.1 Treatment Assignment and Weighting

Protocol, §9.3:

> "At each decision point the algorithm first determines availability, then randomizes with probability
> 0.5 whether to deliver a prompt, and if a prompt is to be delivered, randomly selects a message type from
> those eligible in that context."

**As built**, randomization has two stages (`backend/app/tasks.py::_evaluate_user`):

1. **Send.** At an available decision point, draw $U \sim \mathcal{U}(0,1)$ and assign $A = \mathbf{1}\{U < p\}$,
   with $p$ = `JITAI_RANDOMIZATION_PROBABILITY` (default 0.5, PI-confirmed; `dashboard/data/config.py`). Both
   $p$ and $U$ are stored on the `JITAILog` row (`randomization_probability`, `randomization_draw`).
2. **Message arm.** If $A = 1$, a second draw assigns coping vs. active control with probability
   `JITAI_ARM_RANDOMIZATION_PROBABILITY` (default 0.5). The row stores `message_arm`,
   `arm_randomization_probability` and `arm_randomization_draw`. Message selection within an arm is
   described in §5.4.

**Weights.** Let $p_{it}$ be the logged probability and $\tilde p$ a reference probability that depends at
most on the moderators $S_{it}$. The analysis sets $\tilde p$ to the mean of the logged $p_{it}$, a constant.
Each available decision point receives

$$
W_{it} \;=\; \left(\frac{\tilde p}{p_{it}}\right)^{A_{it}}\left(\frac{1-\tilde p}{1-p_{it}}\right)^{1-A_{it}} .
$$

With $p_{it} \equiv 0.5$, as approved, $W_{it} = 1$ for every row. Weights are nonetheless computed from the
logged per-row probability, so a change to `JITAI_RANDOMIZATION_PROBABILITY` during the study (an
environment variable) is handled without re-specifying the analysis. Positivity requires
$0 < p_{it} < 1$, which is checked before estimation.

### 3.2 Centered Treatment Indicator

The treatment enters the model as $A_{it} - \tilde p$. For any history,

$$
\mathbb{E}\!\left[W_{it}(A_{it}-\tilde p)\mid H_{it}, I_{it}=1\right]
= p_{it}\tfrac{\tilde p}{p_{it}}(1-\tilde p) + (1-p_{it})\tfrac{1-\tilde p}{1-p_{it}}(0-\tilde p) = 0,
$$

so the weighted, centered treatment is orthogonal to every function of the history. This is what the
protocol means by "without requiring a model for the outcome under no treatment". The control terms below
can be misspecified without biasing $\beta$; misspecification affects only precision. This orthogonality
holds over *all* available decision points. It holds only approximately over the subset with an observed
outcome when outcome observation depends on assignment (§6).

### 3.3 Estimation of the Causal Excursion Effect

$\hat\theta = (\hat\alpha, \hat\beta)$ solves the weighted least-squares estimating equation over available
decision points with an observed outcome:

$$
\sum_{i=1}^{n}\sum_{t} I_{it}\, W_{it}\,
\Big(Y_{i,t+1} - g(H_{it})^{\!\top}\alpha - (A_{it}-\tilde p)\, f(S_{it})^{\!\top}\beta\Big)
\begin{pmatrix} g(H_{it}) \\ (A_{it}-\tilde p)\, f(S_{it}) \end{pmatrix} = 0 .
$$

- **Primary (marginal) model:** $f(S) = 1$, so $\beta = \beta_0$.
- **Control terms $g(H)$**, pre-specified:
  - an intercept
  - the decision-point check-in's `B1_valence` and `B2_stress`
  - hour of day (Eastern, centered at 15:00)
  - study day (the Eastern calendar date minus the Eastern date of `enrolled_at`, the same rule as the
    run-in gate), centered at mid-study

  For any moderated model, $g(H)$ also includes every element of $S$.
- **Working independence.** Rows are treated as independent when estimating. No working correlation is
  used, because the outcome of decision point $t$ is part of the history (`prior_valence`) at $t+1$.
  Within-participant dependence enters only through the variance (§3.4).

### 3.4 Statistical Inference

- **Variance.** $\widehat{\operatorname{Var}}(\hat\theta) = B^{-1} M B^{-1}$ with $B = \sum_i X_i^\top W_i X_i$ and
  $M = \sum_i X_i^\top W_i (I - H_{ii})^{-1} r_i r_i^\top (I - H_{ii})^{-\top} W_i X_i$, where
  $H_{ii} = X_i B^{-1} X_i^\top W_i$. This is the participant-clustered sandwich with the Mancl–DeRouen (2001)
  small-sample correction, the protocol's "sandwich estimator with small-sample correction."
- **Tests and intervals.** Two-sided, α = 0.05, referred to a $t$ distribution with
  $n - \dim\alpha - \dim\beta$ degrees of freedom. A model with fewer than 5 residual degrees of freedom is
  reported as descriptive. With the Phase 1 cohort (n = 5), only the marginal model with a single control
  term (`B1_valence`) is estimable.
- **Operating characteristics.** In `wcls_analysis.ipynb` §8, 200 simulated cohorts of 30 participants
  under the outcome design as built gave 95 % interval coverage of 0.925–0.97 for $\beta_0$ and for an
  hour-of-day moderator. At 8 participants, coverage was 0.97 with one control term, and 0.995
  (over-conservative) with the full control set, at 2 degrees of freedom.

**Sample size.** Protocol, §7.0:

> "Sample size follows the approach for micro-randomized trials in Liao et al. (2016). With about 80
> participants, micro-randomization active for roughly four weeks, up to six decision points per day, an
> assumed availability near 0.6, and a send probability of 0.5, each participant contributes on the order of
> a hundred available randomized decision points, giving several thousand across the sample and about half
> that with a prompt delivered."

**Enrollment (decided).** Enrollment is phased:
- Phase 1: 5 participants;
- two further waves of 40.

That is about 80–85 enrolled. The power calculation uses **N = 80** to allow for dropout, which matches the
protocol's "about 80 participants". `N_TARGET = 40` in `dashboard/data/config.py` is the size of one wave;
no code reads it.

> **Power result.** With 80 participants, an availability of 0.2, 28 days of micro-randomization at six
> decision points per day, and a send probability of 0.5, the primary test has 80 % power (two-sided
> α = 0.05) to detect the following standardized proximal effects:
> - **0.109** with full engagement;
> - **0.126** at the 75 % check-in completion benchmark;
> - **0.146** when 75 % of outcome check-ins are also answered;
> - **0.156** if only 70 participants are retained.
>
> The detectable effect stays within the protocol's stated range of 0.10 to 0.15, at its upper end. A
> Monte Carlo check through the analysis estimator confirmed these values, with simulated power
> 0.784–0.798 at each detectable effect and a type I error of 0.042–0.058. A full-pipeline simulation of
> the REACT data layout reached 0.80 power where the closed form predicted 0.75, so the stated values are
> slightly conservative.

**Power at the availability as built.** A decision point is available only when its MSSD exceeds the
participant's own 80th percentile of past MSSD (Paper A §1.2). Availability is therefore about **0.2**, not
the protocol's "availability near 0.6".

The primary test is the marginal effect $\beta_0$, a one-degree-of-freedom test, so the Liao et al. (2016)
calculation has a closed form:

$$
\lambda = N\, d^{2}\, p(1-p) \sum_t \mathbb{E}[I_t], \qquad
\text{power} = \Pr\!\big(F_{1,\;N-q-1}(\lambda) > F^{\,crit}_{1,\;N-q-1}\big),
$$

where:
- $d$ is the standardized effect (relative to the residual SD);
- $p = 0.5$;
- $q = 2$ control parameters;
- α = 0.05, two-sided.

The calculation assumes a constant effect, working independence and a known randomization probability,
which are the assumptions of Liao et al. The study has 28 days of micro-randomization with six decision
points per day.

| Scenario (N = 80 unless stated) | Available decision points per person | Total | Minimum detectable effect at 80 % power | Power at d = 0.10 / 0.15 |
|---|---|---|---|---|
| Protocol's assumption: availability 0.6 | 100.8 | 8,064 | 0.063 | 0.99 / 1.00 |
| **As built: availability 0.2** | 33.6 | 2,688 | **0.109** | 0.73 / 0.97 |
| + 75 % check-in completion (the feasibility benchmark) | 25.2 | 2,016 | 0.126 | 0.60 / 0.91 |
| + 75 % of outcome check-ins answered | 18.9 | 1,512 | 0.146 | 0.48 / 0.82 |
| Same, with 70 participants retained | 18.9 | 1,323 | 0.156 | 0.43 / 0.77 |

**The protocol's power claim still holds at availability 0.2.** §7.0 states detection of "a standardized
proximal main effect in the range of 0.10 to 0.15 at about 80 percent power". The table puts the minimum
detectable effect at 0.11 under full engagement and 0.13–0.15 at the benchmark completion and response rates.
That is the upper end of the stated range, not below it. The protocol's figures were conservative for its own
assumed availability of 0.6, which alone would detect 0.063.

**Simulation check** (`wcls_analysis.ipynb` §8e). The closed form was checked in two ways.

1. **Estimator actually used.** Each simulated dataset breaks the closed form's assumptions: availability
   varies at random per person, each person has their own intercept (SD 0.5), and errors are AR(1) within
   person (ρ = 0.3). Each is fitted with `fit_wcls` and its Mancl–DeRouen t test, 1,000 datasets per row.
   - At each row's MDE, simulated power was 0.784–0.798, within two Monte Carlo SEs (±0.026) of 0.80.
   - The type I error at zero effect was 0.042–0.058.
2. **Full REACT data layout** at the benchmark row: 1–7 rounding, the fixed-lag outcome window, the run-in,
   and the pre-specified controls, 300 datasets.
   - The pipeline yielded 17.7 analyzable decision points per person and an observed standardized effect of
     0.142. At those values the closed form predicts power 0.75.
   - Simulated power was **0.80** (SE 0.023). The controls absorb outcome variance, which an intercept-only
     calculation ignores.

The closed form is therefore mildly conservative for this design, and the table stands as stated.

> **[DISCREPANCY] Two protocol figures do not follow from availability 0.2.**
> - §7.0, "each participant contributes on the order of a hundred available randomized decision points":
>   it is about 34 at full engagement and 19–25 at benchmark engagement.
> - §9.3, "participants receive on average about two to three prompts per day": it is
>   6 × 0.2 × 0.5 ≈ 0.6 per day.
>
> *Recommendation:* amend §7.0 to "each participant contributes roughly 20 to 35 available randomized
> decision points, about 1,500 to 2,700 across the sample", and amend §9.3 to "about one prompt every one to
> two days, never more than four in a day". Recompute the table with the availability, completion and
> outcome variance observed in Phase 1 before the second wave starts.

*(Implementation: `fit_wcls` in `wcls_analysis.ipynb` §5; point estimates verified identical to
statsmodels `WLS` on the centered design, §8a.)*

---

## 4. Availability and Decision Points

Protocol, §9.3:

> "Availability. A decision point is unavailable, and no prompt is delivered, when the participant is asleep
> according to device data, when the device has not synced recently enough to evaluate the signal, when a
> prompt has been delivered within the preceding 60 minutes, when the daily cap has been reached, or when a
> safety override is in effect. Unavailable decision points are recorded and are handled explicitly in the
> analysis rather than being ignored."

Protocol, §15.0 (decision rule):

> "A decision point is flagged when the current MSSD exceeds the participant's own threshold, subject to the
> availability, cooldown, and daily-cap rules described in section 9.3."

Protocol, §9.3 (run-in):

> "Week one is a run-in period during which the device and app are worn and check-ins are completed but no
> coping prompts are delivered."

**As built** (`backend/app/tasks.py::evaluate_jitai_triggers`, `_evaluate_user`; Celery beat every 180 s):

- **Decision point.** Each completed check-in carrying `B1_valence`, `B2_stress` and `B1_arousal` produces
  exactly one `JITAILog` row, `decision_point_id = "ema_<id>"`, whether or not it is available.
- **Availability.** The row is available, and a draw is taken, when all of the following hold:
  1. the MSSD gate passes (Paper A §1.2): at least three prior MSSD values exist, and the current MSSD
     exceeds the lagged expanding 80th percentile;
  2. the 60-minute cooldown is not active;
  3. the daily cap of four is not reached;
  4. the decision point is on study day 7 or later. Study day is the Eastern date minus the Eastern date of
     `enrolled_at`, and a missing `enrolled_at` fails closed;
  5. the participant has no active distress flag (§4.1).
- **What an unavailable row records.** `randomization_draw` is null, and `trigger_reason` holds the reason
  (`below within-person threshold`, `cooldown active`, `daily cap reached`,
  `insufficient within-person history`, `run-in period`, `distress override (baseline|momentary)`). A row
  stopped by the run-in or the distress override additionally carries `suppression_reason`
  (`run_in`, `distress_baseline`, `distress_momentary`) and `status = 'suppressed'`, with
  `randomization_probability` also null, so it can never be mistaken for a prompt that was randomized and
  dropped.
- **Analysis definitions.** Availability is $I_{it} = \mathbf{1}\{\texttt{randomization\_draw}\text{ not null}\}$.
  Only rows written by the engine (`decision_point_id` prefixed `ema_`, `threshold_source = 'engine'`) are
  decision points; rows posted by a client through `POST /jitai/` are excluded.

> **[DISCREPANCY] Availability rules the build does not implement.**
> - **Asleep; not recently synced.** Not implemented. The wearable ingestion task (`ingest_wearable_data`) is
>   a stub, so device data never enter the decision. In practice decision points arise only from completed
>   check-ins, so the participant is awake at every decision point.
>
> The safety override is no longer in this list. It is implemented and described in §4.1.

> **[DISCREPANCY] What the cooldown and cap count.**
> - Protocol: availability excludes decision points where "a prompt has been delivered within the preceding
>   60 minutes." The engine's cooldown and daily cap count *engine-eligible* decision points, including those
>   randomized to no prompt and those in the run-in (`apply_decision_rules`). Availability is therefore more
>   restrictive than the protocol states. It is still unaffected by past assignments, so the WCLS conditions
>   remain valid.
> - The daily cap is counted over UTC calendar days, while every other daily quantity is Eastern.

> **[DISCREPANCY] Decision points per day.** The protocol says "up to six decision points per day" (§3.0,
> §9.3). The build creates a decision point from every completed check-in, including `post_prompt` and
> `extra_check_in` check-ins. These have a separate cap of four per day, so a participant can have up to ten
> decision points per day.
>
> Since the §2.1 change, this matters more. Every available decision point now opens an outcome window, and
> each outcome check-in is itself a decision point that can open the next window. The cap of four outcome
> check-ins per day bounds the chain, but it adds burden that the protocol's "up to six decision points per
> day" does not anticipate.

> **[OPEN] Below-threshold points.** In the protocol, below-threshold points are "not flagged" and never
> reach randomization. Are they unavailable decision points ("recorded and are handled explicitly") or not
> decision points at all? The build records them as unavailable rows. The paper should say which, since it
> determines the denominator of every feasibility rate reported against decision points.

*(Implementation: `decision_engine.apply_decision_rules`; `tasks._in_run_in`; `dashboard.data.windows.study_day_for`.)*

### 4.1 Safety Override

Protocol, §11.0:

> "During the intensive period, any distress signal suspends prompt randomization for that participant and
> the app shows a resource card instead of a coping message."

**As built** (PR #60/#61; `backend/app/distress.py`, `DistressFlag`, migrations 0048–0049):

| Flag | Raised by | Active until | Effect |
|---|---|---|---|
| **Momentary** | A submitted check-in with `B1_valence` = 1, `B2_stress` = 7, `B1_affect_sad` = 5 or `B1_affect_anxious` = 5, i.e. the scale floor or ceiling (exact-value cutoffs, PI-confirmed 2026-09-22) | 24 hours after it is raised | Randomization suspended; resource card shown |
| **Baseline** | The scored Qualtrics baseline export, imported by `manage.py import_baseline_distress_flags` or the Django Admin import. Signals: PHQ-9 self-harm or moderately severe/severe total; SCOFF; AUDIT-C; PGSI problem gambling; food insecurity; free-text risk disclosure | Staff mark contact documented in Django Admin (`contact_documented_at`) | Randomization suspended; resource card shown |

- **Where suppression happens.** `_evaluate_user` checks for an active flag before the draw. A suppressed
  decision point is therefore unavailable in the sense of §4: no draw and no probability are recorded, and
  it opens no outcome window (§2.1). The resource card, listing seven study resources, is returned with any
  check-in submitted while a flag is active, and the app displays it.
- **Consequence for the analysis.** Suppressed decision points drop out of the WCLS by construction.
  - A momentary flag can be raised by an *outcome* check-in, so the availability of later decision points can
    depend on earlier outcomes, and through them on earlier assignments. The causal excursion effect remains
    identified, because it conditions on availability at each decision point (Boruvka et al., 2018). It
    describes decision points at which the participant was not in acute distress.
  - Suppressed decision points are reported by reason. A prompt that raises distress, and so more
    suppressions afterwards, would show up there rather than in $\beta$.

> **Decided (PI, 2026-09-23): how staff learn of a flag.** Protocol §11.0 requires, for the PHQ-9 self-harm
> item, that the system "generates an immediate alert to trained study staff, who contact the participant the
> same day."
> - **Baseline screens.** The alert comes from **Qualtrics**, which emails the PI and staff when a flagged
>   baseline survey is submitted. The same-day alert therefore exists outside the REACT code. Importing the
>   export into REACT only suppresses randomization.
> - **Check-in (momentary) flags.** The participant sees the resource card and no immediate staff notice is
>   sent. The flags are reported to staff daily, through:
>   - the Django Admin *Distress flags* list;
>   - the monitoring layer's per-participant daily metrics (`MetricsDaily.suppressed_n`, recomputed every
>     10 minutes);
>   - the participant timeline, where the decision point shows as "suppressed (distress)".
>
>   `suppressed_n` also counts run-in suppressions, so distress counts are read from `suppression_reason`.
> - **Documenting contact.** Staff mark a baseline flag's contact documented in Django Admin after the staff
>   contact log entry: the same day for the two PHQ-9 flags, and at the next visit for alcohol, eating,
>   gambling and food. `contact_documented_at` is a timestamp only. Who called and what was given stay in the
>   staff contact log, since REACT stores no free text.
> - **Unmarked flags.** An unmarked baseline flag suppresses that participant for the rest of the study, by
>   design.

> **[OPEN] Scope of the pause.** Protocol §5.0 says "any distress signal routes the staff referral procedure
> in Section 11 rather than continued study contact". The build suspends only randomization: scheduled
> check-ins and their reminders continue while a flag is active. Confirm with the PI whether "rather than
> continued study contact" means pausing check-ins too.

---

## 5. Moderation Analyses

Protocol, §15.0:

> "Pre-specified moderators are entered as effect modifiers of the excursion effect. Momentary moderators
> include current arousal from the wearable, recent affect valence and discrete emotions, game context and
> win or loss state, location and social context, time of day, standing behavioral urge, and response to the
> previous prompt. Tier 1 baseline moderators are negative and positive urgency, coping drinking motives,
> emotional and uncontrolled eating, and the MAIA interoception subscales. Time in study is included as a
> moderator to test whether prompt effects attenuate with repeated exposure. Message type is compared
> against the active control and against other types."

**General specification.** Each moderator is tested in its own model, one per outcome, with $f(S) = (1, S)$.
$S$ is also entered in $g(H)$. Continuous moderators are centered, so the main-effect term is the effect at
the moderator's center. Including $S$ in $g(H)$ is required, not optional. When outcome observation depends
on assignment, a moderator left out of $g(H)$ can leak into $\beta$ (`wcls_analysis.ipynb` §8c, scenario 3).

### 5.1 Momentary Moderators

Each moderator is measured at, or before, the decision point:

| Protocol moderator | Source (as built) | Note |
|---|---|---|
| Current arousal from the wearable | `JITAILog.hr_at_trigger`, `stress_at_trigger`, `rmssd_at_trigger`, `hrv_class_at_trigger` | **Unavailable while wearable ingestion is a stub.** HRV is recorded, not used to decide. |
| Recent affect valence and discrete emotions | Decision check-in `B1_valence`; `B1_affect_{anxious,angry,sad,excited,happy,bored}` | |
| Game context and win or loss state | `B3_*` on event days; `EventDay` | Defined only on event days, so it is analyzed within event days. |
| Location and social context | `B2_location`, `B2_social` | Categorical; one indicator per level against a reference. |
| Time of day | Eastern hour of `decision_made_at` | |
| Standing behavioral urge | Decision check-in `B4_urge`, `B6_urge`, `B7_urge` | Present only when the domain was in that check-in's rotation (`evaluated_items` null = not served). |
| Response to the previous prompt | `C0_helpful`, `C0_behavior_change` for the participant's most recent delivered prompt | Defined only after a first delivered prompt. |

Rotation-dependent moderators are observed only at decision points whose check-in served the item. Rotation
happens before the draw, so restricting a moderator model to those rows does not break randomization. It
does change the population the estimate describes, and each moderator model reports its row count.

### 5.2 Tier 1 Confirmatory Moderators

Protocol, §1.0:

> **Tier 1, confirmatory moderators.** "Negative and positive urgency (SUPPS-P), emotion regulation
> difficulties (DERS-16), emotional and uncontrolled eating (TFEQ-R18), coping drinking motives (DMQ-R), and
> interoceptive attention (MAIA-2 Noticing and Body Listening). Each is tied to a specific momentary pathway
> and carries the confirmatory moderation analyses."

Baseline scores come from the scoring code in `analytics/baseline_survey_scoring/scoring.py` (`score_supps`,
`score_ders`, `score_tfeq`, `score_dmq`, `score_maia`). A baseline moderator is constant within participant,
so its interaction is informed by between-participant variation only. Its effective sample size is the
number of participants, not the number of decision points, and the small-sample correction matters most
here (in the notebook's evaluation it widened such SEs by about 15 %).

Proposed pathway pairing (each Tier 1 moderator is tested against its paired outcome):

| Tier 1 moderator | Paired proximal outcome |
|---|---|
| SUPPS-P negative urgency; positive urgency | Urge to post reactively; reactive online behavior |
| DMQ-R coping motives | Drinking; drinking urge |
| TFEQ-R18 emotional and uncontrolled eating | Emotional or uncontrolled eating |
| DERS-16 | All four outcomes |
| MAIA-2 Noticing; Body Listening | All four outcomes |

> **[DISCREPANCY] DERS-16.** DERS-16 is Tier 1 in §1.0 but absent from the Tier 1 list in §15.0.
>
> *Recommendation:* add it to §15.0, since §1.0 fixes the tiers "before data are examined."

> **[OPEN] Pairing and multiplicity.** The protocol says each Tier 1 moderator "is tied to a specific
> momentary pathway" but does not state the pairing or a multiplicity correction. The pairing above, and
> Holm's procedure within each outcome's family of Tier 1 tests, are proposals that need PI sign-off before
> data are examined.

### 5.3 Time-in-Study Moderation

The protocol tests "whether prompt effects attenuate with repeated exposure." The pre-specified model uses
$S$ = study day (centered), linear, over the micro-randomization period. A secondary specification uses
indicators for study week (weeks 3–5 against week 2), which does not assume the change is linear. A negative
$\beta_1$ is evidence of attenuation. Study day confounds exposure count with calendar time (for example
exams and the football schedule). The number of prior delivered prompts is reported descriptively and is not
used as a moderator, because it depends on past assignments.

### 5.4 Message-Type Comparisons

**As built**, message selection has three layers (`backend/app/notification_service.py`):

1. **Coping vs. active control** is randomized at the second stage (§3.1), with the probability logged per
   row.
2. **Coping messages are routed by content.** The rules in `ROUTING_TRIGGER_RULES` (cutoffs confirmed by the
   PI on 2026-09-07) match the decision check-in to emotional-state categories. A matched category is drawn
   uniformly, then a message uniformly within its pool. The last five delivered messages are excluded, and
   the pool actually used is logged (`eligible_prompt_ids`, `category_drawn`, `matched_categories`,
   `fallback_reason`). Two exceptions are deterministic:
   - an alcohol-severity override that selects P020;
   - a fixed general fallback pool when no category matches.
3. The reachable coping bank is currently the general-context subset. Cyber-specific messages are parked
   (PI, 2026-08-25/27).

**Estimands.**

- **Coping vs. active control (primary message-type comparison).** WCLS among decision points with $A=1$,
  treating `message_arm` as the treatment and `arm_randomization_probability` as its probability. The
  estimand is the effect of a coping message relative to a neutral message, given that a message is
  assigned. It separates the content of a coping message from the effect of receiving any message, which is
  the purpose of the active control (message bank: "so that receiving any message can be separated from
  receiving a coping message").
- **Technique contrasts (CBT vs. ACT), secondary.** Within a drawn category, the message is uniform over the
  logged pool, so $P(\text{CBT} \mid \text{pool}) = \#\text{CBT in pool} / |\text{pool}|$ is known exactly
  for each row. Within-category CBT and ACT assignments are therefore randomized with known probabilities,
  and WCLS applies with those probabilities. Rows selected deterministically (the alcohol override) and pools
  containing only one technique have no counterfactual and are excluded (positivity).

> **[DISCREPANCY] How the message is chosen.** The protocol: "if a prompt is to be delivered, randomly
> selects a message type from those eligible in that context" (§9.3). The build randomizes coping vs. control
> with a fixed probability, then chooses the coping message through category routing: a uniform draw within
> the matched categories, plus two deterministic paths.
>
> "Message type" is not defined in the protocol: it could mean the arm, the CBT/ACT technique family, or the
> individual technique. Only the arm contrast has a fixed, pre-set randomization probability. The protocol's
> "compared [...] against other types" is estimable only within categories and only for pools that mix types.
>
> *Recommendation:* amend §9.3 to describe the two-stage design and routing as built, and define "message
> type" as the coping/active-control arm (primary) and CBT/ACT family (secondary).

---

## 6. Missing Data and Sensitivity Analyses

Protocol, §15.0:

> "Missing data and sensitivity. Unanswered check-ins and unavailable decision points are handled within the
> weighted estimating-equation framework rather than imputed, since availability is part of the estimand's
> definition. [...] Sensitivity analyses vary the proximal outcome window, the definition of availability,
> the MSSD threshold quantile, and the handling of participants with low check-in completion."

**What the estimating equation handles, and what it doesn't.**

- **Unavailable decision points** are handled by definition. The estimand conditions on $I_{it} = 1$ and
  those rows enter with $I_{it} = 0$.
- **Unanswered check-ins have two different roles.**
  1. A check-in that is never completed creates no decision point (§4). Missed check-ins therefore reduce
     the number of decision points, and the estimand describes decision points that occur, i.e. moments when
     the participant was engaging with the app.
  2. An unanswered *outcome* check-in leaves an available, randomized decision point with a missing $Y$. The
     primary analysis is complete-case. That is valid if, given $A_{it}$ and the terms in $g(H_{it})$, whether
     the outcome is observed is unrelated to the potential outcomes.

  The outcome design as built (§2.1) serves, times and notifies the outcome check-in identically in both
  arms. That removes the *design*-induced dependence of observation on assignment, although the prompt itself
  may still change whether someone answers. When observation does depend on assignment, the treated share
  among observed rows departs from $\tilde p$, the centering in §3.2 holds only approximately, and a
  mis-specified control model can leak into $\beta$.
  - Under the pre-fix design, the notebook's evaluation found an observed treated share of about 0.45
    against $\tilde p = 0.5$, and a model omitting a moderator's main effect was biased by −0.017, exactly the
    predicted leak.
  - Under the design as built, the same model's bias was −0.001 (`wcls_analysis.ipynb` §8c, scenarios 3
    and 3b).

  Observation rates by arm are reported for every analysis.

> **[OPEN] Protocol wording.** "handled within the weighted estimating-equation framework rather than
> imputed" is accurate for availability but not for missing outcomes, which are excluded, not handled by the
> framework.
>
> *Recommendation:* amend §15.0 to state complete-case analysis for missing outcomes, with the
> inverse-probability-of-observation sensitivity analysis below as the pre-specified check.

**Pre-specified sensitivity analyses.** The four the protocol requires, made concrete, and two added by
this plan:

| # | Protocol item | Specification | Constraint |
|---|---|---|---|
| S1 | Proximal outcome window | Close the window at +90 min instead of +2 h | The window opens at +60 min by design, so it can only be shortened. A window longer than 2 h would reach past the time-locked check-in. |
| S2 | Definition of availability | (a) Drop decision points arising from `post_prompt`/`extra_check_in` check-ins, i.e. only scheduled check-ins, matching "up to six decision points per day"; (b) count cooldown and cap on delivered prompts only, replayed from the log | Availability can only be made **stricter**. A decision point that was never randomized cannot enter any version. |
| S3 | MSSD threshold quantile | Replay the threshold at 0.85 and 0.90 with `manage.py backfill_thresholds`/`build_decision_frame`, and restrict to decision points available under both the replayed and the deployed rule | Only quantiles **above** the deployed 0.80 are analyzable. Decision points that a looser quantile such as 0.75 would add were never randomized. |
| S4 | Participants with low check-in completion | Exclude participants below the 75 % check-in completion benchmark (§7.0) | Reported alongside the full sample, never instead of it. |
| S5 | Missing outcomes | Weight each complete case by the inverse of its estimated probability of an observed outcome, modeled on $A_{it}$, hour, study day and the decision check-in's affect, and cluster the variance on participant | Run if §4.2 of the notebook shows observation rates differing by arm. |
| S6 | Legacy outcome design | Decision points before the fixed-lag deployment, analyzed with the only outcome measured in both arms under that design: the next scheduled check-in within 2 h | Reported separately, never pooled with the primary analysis. |

Analyses use the engine-written decision points only (§4). The protocol states "Analyses are conducted in R
and Python." The primary implementation is Python (`analytics/wcls_analysis.ipynb`). Re-estimating the
primary model with the R `MRTAnalysis` package is recommended as an independent cross-check before
unblinding results.

*(Implementation: `wcls_analysis.ipynb` §4.2 (missingness by arm), §8 (evaluation), §9.1 (window length),
§9.2 (legacy design); `backend/dashboard/management/commands/backfill_thresholds.py`.)*

---

## Open items summary

Ranked by impact on the primary estimand.

1. **[RESOLVED, §2.1] Outcome window opened only after delivered prompts.** The window now opens after
   every available decision point in both arms, at a fixed 60-minute lag. What remains:
   - PI sign-off on the lag, and the protocol wording "any **available** decision point";
   - recording the deploy time as `DESIGN_CUTOVER`.
2. **[RESOLVED in code, §4.1] Safety override.** Implemented by PR #60/#61: distress flags suspend
   randomization before the draw, and the resource card is shown.
   - **Decided (PI, 2026-09-23):** staff alerts for baseline screens come from Qualtrics emails. Check-in flags
     show the resource card and are reported to staff daily.
   - **[OPEN]** Should a distress flag also pause check-ins (protocol §5.0 "rather than continued study
     contact")?
3. **[DECIDED, §3.4] Availability ≈ 0.2.** Power still holds: minimum detectable effect 0.11–0.15 at N = 80,
   within the protocol's 0.10–0.15. What remains:
   - amend §7.0's decision points per participant and §9.3's prompts per day;
   - recompute with Phase 1's realized availability before the second wave.
4. **[OPEN, §2.2] Outcome item mapping.** Fix the sub-item for each protocol outcome. Resolve the "today"
   recall of `B6_consumed`/`B6_drink_count` and the conditional eating items (use the proposed composite).
5. **[DISCREPANCY, §1] Delivered vs. assigned.** Amend the estimand to assignment (ITT). Delivered-prompt
   effect as a secondary IV analysis.
6. **[DISCREPANCY, §5.4] Message-type randomization.** Describe the two-stage design and routing. Define
   "message type" (arm primary; CBT/ACT secondary within category).
7. **[RESOLVED, §3.4] Enrollment target.** Phased rollout of 5 + 40 + 40 (about 80–85 enrolled); N = 80 for
   power. `N_TARGET = 40` is one wave. `CLAUDE.md` is corrected.
8. **[DISCREPANCY, §4] Decision points per day.** Up to six (protocol) vs. every completed check-in, up to
   ten (build).
9. **[DISCREPANCY, §4] Availability rules.** Sleep and sync checks are absent (ingestion stub). Cooldown and
   cap count eligible, not delivered, points. The cap is counted over UTC days.
10. **[OPEN, §4] Below-threshold points.** Unavailable decision points or not decision points? This fixes the
    feasibility denominators.
11. **[DISCREPANCY, §5.2] DERS-16** missing from §15.0's Tier 1 list.
12. **[OPEN, §5.2] Tier 1 pathway pairing and multiplicity correction** (Holm proposed).
13. **[OPEN, §6] Missing-outcome wording** in §15.0. State complete-case, with S5 as the check.
14. **[DISCREPANCY, §2.1] Three vs. four domains** (§9.2 vs. §10.0).
15. **[OPEN, §5.1] Wearable arousal moderator** is unmeasurable until wearable ingestion is implemented.

---

## References

- Boruvka, A., Almirall, D., Witkiewitz, K., & Murphy, S. A. (2018). Assessing time-varying causal effect
  moderation in mobile health. *Journal of the American Statistical Association*, 113(523), 1112–1121.
- Liao, P., Klasnja, P., Tewari, A., & Murphy, S. A. (2016). Sample size calculations for micro-randomized
  trials in mHealth. *Statistics in Medicine*, 35(12), 1944–1971.
- Mancl, L. A., & DeRouen, T. A. (2001). A covariance estimator for GEE with improved small-sample
  properties. *Biometrics*, 57(1), 126–134.
- Nahum-Shani, I., Smith, S. N., Spring, B. J., Collins, L. M., Witkiewitz, K., Tewari, A., & Murphy, S. A.
  (2018). Just-in-time adaptive interventions (JITAIs) in mobile health: Key components and design principles
  for ongoing health behavior support. *Annals of Behavioral Medicine*, 52(6), 446–462.
- Qian, T., Yoo, H., Klasnja, P., Almirall, D., & Murphy, S. A. (2021). Estimating time-varying causal
  excursion effects in mobile health with binary outcomes. *Biometrika*, 108(3), 507–527.
- Qian, T., Walton, A. E., Collins, L. M., Klasnja, P., Lanza, S. T., Nahum-Shani, I., et al. (2022). The
  microrandomized trial for developing digital interventions: Experimental design and data analysis
  considerations. *Psychological Methods*, 27(5), 874–894.
- Jahng, S., Wood, P. K., & Trull, T. J. (2008). Analysis of affective instability in ecological momentary
  assessment. *Psychological Methods*, 13(4), 354–375.
