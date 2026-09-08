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

- 5-6 daily EMA check-in count.  
- 5-week study period.  
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