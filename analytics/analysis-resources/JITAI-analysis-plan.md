# **JITAI Feasibility Analysis Plan** 

# **Author: Tien Tyler Le**

**Date:** 09/02/2026  
**Study:** Just-In-Time Adaptive Intervention (JITAI) for REACT (emotions and reactive behaviors among college students)

# **Overview**

Three data streams are collected during the study period:

* Ecological Momentary Assessment (EMA) survey responses  
* JITAI intervention questionnaires  
* Garmin wearable telemetry

For each stream, this plan specifies the metrics to be computed, whether analysis runs live on the dashboard or offline, and the specific feasibility benchmark addressed.  
The feasibility thresholds are informed by synthetic sensitivity analysis calibrated for the 5-weeks study protocol (5-6 EMA prompts per participant per day).

### Important Figures:

- 40 participants total. Phase 1 rollouts to 5 participants. 
- 5-6 daily EMA check-in count.  
- 5-week study period.  
- 60-min cool down window between each intervention prompt.
- Intervention Dosage capped at 4 per day.  
- Calibration expects \~4/day at 80th percentile.  
- 30–min response window.

# **Feasibility Definitions & Criteria**

* 1\. EMA Response & Check-in Completion  
  * **Completed Check-in Definition:** An EMA is counted as completed when the scheduled prompt is delivered via the app and the participant successfully submits responses to **all** required items on screen within the 30-minute assigned response window. Missing required values cause the prompt to be treated as unanswered.  
  * **Prompt Response (Partial) Definition:** A prompt is considered "responded" if the participant submits at least one item response within the 30-minute window, even if required items remain unanswered.  
  * **Calculations:**  
    * **Numerator (Completed Check-ins):** Number of EMA prompts with all required items submitted within 30 minutes.  
    * **Numerator (Prompt Responses):** Number of EMA prompts with \&ge; 1 item submitted within 30 minutes.  
    * **Denominator:** Total EMA prompts successfully delivered during the 5-week study period. Prompts with documented push-notification or app-delivery failures are excluded from the denominator (reported separately).  
  * **Feasibility Benchmark vs. Analytic Requirement:**  
    * **Preregistered Feasibility Benchmark:** **75%** response rate across the 5-weeks protocol.  
    * **Analytic Requirement:** **80%** response rate. Parameter recovery (e.g., AR(1) estimation) degrades below \~80%, which serves as a statistical limitation and design recommendation for the full trial rather than a feasibility threshold.  
* 2\. Wear Time Computation  
  * **Definition:** Wear time is computed as a coverage percentage based on standardized waking hours (8:00 AM \- 10:00 PM local time; 14 total waking hours per day).  
  * **Calculations:**  
    * **Numerator:** Total waking time (14 hours/day) minus data gap periods that exceed **2 consecutive hours**.  
    * **Denominator:** Total expected waking time (14 hours per day).  
    * **Window:** Evaluated during 8:00 AM \- 10:00 PM local time. An interval is counted as a wear gap only when missing data exceeds 2 consecutive hours. BBI-based proxies are used to infer wear, with EMA records as supporting evidence.  
* 3\. Participant Retention  
  * **Definition:** A participant is retained if they remain enrolled through the full 5-week study period without dropping out. Missing EMA prompts does not constitute a dropout unless the participant formally withdraws or is withdrawn.  
  * **Calculations:**  
    * **Numerator:** Number of participants enrolled continuously from Day 1 through Day N (last day of study period).  
    * **Denominator:** Total participants who began the study on Day 1\. (Individuals consenting but withdrawing before Day 1 are excluded and reported separately).  
* 4\. Sub-Study Sample Rate  
  * **Definition:** Proportion of eligible sub-study participants who provide usable samples meeting quality standards within the study window.  
  * **Calculations:**  
    * **Numerator:** Eligible sub-study participants providing usable data within the collection window.  
    * **Denominator:** Total participants eligible for the sub-study who were active when the sample was requested.

# **Implementation & Metrics**

## **EMA Survey Responses**

* **Source Tables:** ema, user  
* **Metrics:**  
  * Completed EMA Check-in Rate (Preregistered target: 75%)  
  * Response Latency (responded\_at \- sent\_at)  
  * Item-level Missingness & Completeness  
  * Attention Check Accuracy  
  * Short-term Trajectories & Within-person MSSD  
  * AR(1) Parameter Recovery (rho;, sigma;)  
* **Dashboard vs. Offline Analysis:**  
  * **Live Dashboard:** Daily response rates, completed check-in counts, delivery counts, attention check pass rates, missingness flags.  
  * **Offline:** MSSD calculations, AR(1) parameter recovery (threshold target: 80%), response quality, compliance regressions.

## **JITAI Check-ins and Intervention Log**

* **Source Tables:** jitai\_log, ema, engagement\_log  
* **Metrics:** **Intervention dosage (capped at 4 prompts/day), Daily check-in count (5-6 prompts/day),** push delivery funnel latency, decision audit logs, cooldown compliance (min 60-min trigger gap).  
* Expected intervention rate: \~4 per week (80th percentile)  
* **Dashboard vs. Offline Analysis:**  
  * **Live Dashboard:** Weekly prompts sent per user, delivery funnel conversion, live completed check-in rate.  
  * **Offline:** MSSD threshold calibration against simulation projections, dosage vs. compliance cross-tabulations.

## **Garmin Wearable Telemetry**

* **Source Tables:** heart\_rate\_sample, stress\_sample, wearable\_device  
* **Metrics:** Waking hour wear time coverage percentage, 2-hour gap counts, sync freshness, BBI-inferred wear proxy.  
* **Dashboard vs. Offline Analysis:**  
  * **Live Dashboard:** Daily waking-hour gap counts (\>2h), maximum gap duration, device sync freshness.  
  * **Offline:** Telemetry gap vs. EMA completion cross-analysis, hardware error classification, HR-MSSD vs. EMA-MSSD correlations.

# **Limitations & Methodological Notes**

* **Parameter Recovery vs. Feasibility:** While the study feasibility benchmark is set at a 75% response rate per preregistration, statistical parameter recovery for AR(1) time-series models **degrades** below \~80% response rates. This distinction is reported as an analytic limitation and design recommendation for subsequent trials.  
* **MSSD Missingness Suppression:** Missing EMA check-ins suppress MSSD calculation (EMA\_t \- EMA\_{t-1}) for both the missing observation and the immediate subsequent prompt.  
- **Telemetry Gap Confounding:** Timestamp gaps exceeding 2 hours during waking hours (8:00 AM \- 10:00 PM) are evaluated using watch duration data as the primary source, supplemented by BBI proxies and active EMA check-in records.

# Causal Infernece Framework

## Micro-randomized trial (MRT)

What: Estimates the proximal causal effect of sending a JITAI prompt at a given decision moment, "does a prompt right now move mood/stress/energy or engagement in the next few hours?" This is a within-person, repeated-measures design: each person contributes many randomized decision points over the study, not just one treatment assignment.

How:
- The randomization already exists: evaluate_jitai_triggers gates on JITAI_RANDOMIZATION_PROBABILITY (default 0.5) after apply_decision_rules clears eligibility (sufficient EMA history, above within-person MSSD threshold, daily cap/cooldown OK). Every eligible moment — sent or not — is a decision point.
- Build a decision-point-level dataset: every eligible moment (JITAILog rows plus the eligible-but-blocked-by-randomization moments, which need to be reconstructed from decision_reason/send_prompt=False cases, not just delivered prompts) with treatment indicator, timestamp, and a proximal outcome — e.g., the next EMA's mood/stress/energy, or engagement in the following 2–4 hours.
- Estimator: WCLS (weighted and centered least squares, Boruvka et al. 2018) — the standard MRT estimator. Regress outcome on treatment (centered at the known randomization probability, here a constant 0.5, so this simplifies but the framework generalizes if the probability ever becomes state-dependent) plus moderators (baseline MSSD, time of day, days since enrollment). Cluster/robust standard errors by participant.
- Moderator terms answer "for whom and when does the prompt help" — the core JITAI question — e.g., interact treatment with hour-of-day or volatility tercile.
- Why it fits N=5→35: power comes from repeated decision points per person, not between-person sample size, so it's usable even in the 5-person pilot. It only estimates proximal, not whole-study, effects.

## Regression discontinuity (RDD)

What: Uses the engine's deterministic cutoff--the within-person 80th-percentile MSSD threshold--as a source of as-if-random variation near the boundary, independent of the coin-flip. Estimates a local effect of crossing into "eligible" territory.

How:
- Running variable: observed_mssd - threshold_at_decision, centered at 0 per person (the threshold is within-person, so normalize before pooling across participants).
- This is a fuzzy RDD, not sharp: crossing the MSSD cutoff clears one gate but treatment (send_prompt) still depends on randomization, cooldown, and daily cap — so treatment probability jumps discontinuously at the cutoff but not 0→1.
- Estimator: local-linear regression on either side of the cutoff within an optimal bandwidth (e.g., Calonico–Cattaneo–Titiunik/rdrobust), then a ratio-of-discontinuities (2SLS): outcome-jump-at-cutoff ÷ treatment-probability-jump-at-cutoff.
- Validity checks: a McCrary density test on the running variable (no evidence participants can manipulate their own MSSD to dodge/trigger a prompt — plausible since MSSD is a computed statistic from self-report, not a visible score) and smoothness of covariates (time of day, response rate) across the cutoff.
- Data needed: observed_mssd, threshold_at_decision (reliable only on threshold_source='engine' rows, or backfilled via manage.py backfill_thresholds), decision_reason, send_prompt, outcome.
- Value: a fully separate identification strategy from the MRT — if RDD and MRT agree in direction/magnitude despite relying on completely different assumptions, that's strong triangulating evidence the effect is real, not a design artifact.

## Instrumental variables (IV)

What: Corrects for imperfect delivery/compliance. The quantity of interest isnt actually receiving/opening a prompt, but some assigned prompts fail to
deliver (push failures, sync issues, notification permissions). Naively comparing "opened" vs. "not opened" confounds the prompt's effect with who tends to be reachable/engaged.
Using the randomization draw as an instrument for actual receipt removes that

How:
- Instrument Z: the internal coin-flip assignment (send_prompt=True, i.e., the server decided to attempt a send) — invisible to the participant, so it can only affect them through actual delivery.
- Endogenous treatment D: actual delivery/opening (JITAILog.status ∈ {delivered, opened, interacted} vs. failed, reported via /jitai/receipt/).
- Outcome Y: same proximal outcomes as the MRT.
- This is one-sided noncompliance ($Z=0 ⇒ D=0$ deterministically; $Z=1 ⇒ D=1$ unless delivery fails), so it reduces to a Wald estimator: $(E[Y|Z=1] − E[Y|Z=0]) ÷ (E[D|Z=1] − E[D|Z=0])$ —
  essentially the naive ITT effect inflated by the delivery rate, giving the fect (effect on those who actually receive it).
- Relevance (first stage) is strong by construction; exclusion restriction is plausible since Z is a server-side draw with no other channel to affect the participant.
- Data needed: send_prompt (Z), status/receipt data (D), proximal outcome (Y)required.

## Difference-in-differences (staggered rollout)

What: Exploits the 5-then-35 staggered start dates to estimate a distal (aggrct — e.g., does weekly mood/stress/engagement shift once JITAI goes active,
compared to participants who haven't started yet in that same calendar week — differencing out shared calendar-time shocks (football season events, exam periods).

How:
- Unit: participant-week. Treatment: whether JITAI is "active" for that parti-in, using enrolled_at + fixed run-in length).
- Because units start at different times, do not use plain two-way-fixed-effects DiD — it's known to be biased under heterogeneous treatment-effect timing (Goodman-Bacon; Callaway & Sant'Anna 2021). Use a staggered-adoption estimator: ATT(g,t) per rollout ct, using not-yet-treated participants as the comparison group at each t.
- With only two waves (5 then 30), this is effectively a coarse 2-cohort design rather than a rich staggered one — more like a comparative interrupted time series — unless the 30-person wave itself trickles in over several weeks, which would give genullaway–Sant'Anna worth the extra machinery.
- Outcomes: weekly aggregates already computed by the dashboard layer (MetricsDaily/MetricsParticipant) — mood/stress/energy averages, response rate, HR/stress/HRV.
- Check parallel trends using the shared non-interventional run-in period bott-study plot of outcome by weeks-relative-to-start, both cohorts, pre-periodonly).
- This answers a different question than the MRT/RDD: "does the program, oncectory" vs. "does a single prompt move things right now" — complementary, notredundant.
