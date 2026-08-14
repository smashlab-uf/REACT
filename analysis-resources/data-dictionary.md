# Data Dictionary | REACT Biomarker and Extended-Measures Study

Author: Tien Tyler Le

Date: 08/13/2026

[Github](https://github.com/Geaboi/HealthyGatorSportFan/tree/data-dictionary/analysis-plan)

This document defines every table and column used in the REACT / JITAI feasibility
analysis so that **anyone** can understand
what each field is, where it comes from, and how it is computed.

For every column we record:

| Field | Meaning |
|-------|---------|
| **Name** | Column name as it appears in the database / synthetic data |
| **Type** | Data type (`INT`, `VARCHAR(n)`, `DATETIME`, `SMALLINT`, `FLOAT`, `BOOLEAN`, `JSONB`, `TEXT`, `DATE`) |
| **Source stream** | Where the value originates (see the controlled vocabulary below) |
| **Meaning** | What the value represents, including units, ranges, and encodings |

## Source-stream vocabulary

Aligning with Celia's physiological data limitation, Labfront limitation, Eliana's prompt analysis plan, and Abigail's feasbility definitions. 

Database and modeling is based on Dustin's [Django model.](https://www.dropbox.com/work/REACT/Engineering/Dustin/Archive?di=left_nav_browse)

| Tag | Definition |
|-----|------------|
| **Labfront/Garmin** | Imported from the participant's Garmin wearable via the Labfront research platform (batch CSV/ZIP export). See [How Labfront/Garmin data is produced](#how-labfrontgarmin-data-is-actually-produced-collection-mechanics--caveats). |
| **EMA push** | Self-reported by the participant in-app after a push-notification prompt (Ecological Momentary Assessment). |
| **Decision engine** | Computed / emitted server-side by the JITAI decision logic at each decision point. |
| **App telemetry** | Client-app events and push-delivery receipts reported by the phone. |
| **Study/enrollment** | Participant metadata set at consent / enrollment. |
| **Derived** | Computed offline from one or more of the above (e.g. MSSD, AR(1) parameters). |
| **REACT-Bio Lab** | Assays and measurements originating from the biomarker laboratory (Kertes Lab); hair steroid sub-study. |
| **Qualtrics / Baseline** | Measures captured during the baseline or exit survey blocks, or hair-hygiene intake questionnaire. |

## Conventions

- **Study window:** ~35-day protocol (5 weeks). **Week 1 is a non-interventional run-in baseline** — no JITAI randomization; used exclusively to establish each participant's within-person MSSD threshold. Micro-randomization runs **Weeks 2–5**. **5–6 EMA check-ins per participant per day** (expected N ≈ 175–210 prompts/participant). Feasibility thresholds are calibrated from the synthetic sensitivity analysis. (`JITAI-analysis-plan.md`)
- **Daily prompt cap:** Hard limit of **4 JITAI prompts per day** with a minimum **60-minute cooldown** between triggers (projected ~2–3 prompts delivered/day when eligible).
- **Synthetic epoch:** synthetic cohorts start at **YYYY-MM-DD 00:00**; EMA and HR rows are at 1-minute frequency, HRV is one row per night. (`syntheticData/SCHEMA.md`)
- **Authoritative schema:** this dictionary documents the **REACT analysis schema** (`analysis-plan/schema.md` + the current `syntheticData/db_seed.py`) ; the tables actually analyzed per `JITAI-analysis-plan.md`. The production Django backend (`HealthyGatorSportsFanDjango/app/models.py`) currently differs; those differences are catalogued in the [Schema reconciliation appendix](#appendix--schema-reconciliation--known-gaps). **No Django migrations exist yet for this schema** -- see the appendix.
- **Canonical prose definitions** live in `analysis-plan/Feasibility Definitions (2).docx` (binary; not reproduced here). This dictionary is intended to stay consistent with it.

---

## 1. Collection overview

Three data streams are collected during the study period:

| Stream | Delivery mechanism | Tables |
|--------|--------------------|--------|
| **EMA survey responses** | Push notification to in-app survey | [`ema`](#22-ema), [`user`](#21-user) |
| **JITAI interventions & engagement** | Decision engine + phone app | [`jitai_log`](#26-jitai_log), [`engagement_log`](#27-engagement_log), [`phone_telemetry`](#28-phone_telemetry) |
| **Garmin wearable telemetry** | Labfront import | [`heart_rate_sample`](#23-heart_rate_sample), [`stress_sample`](#24-stress_sample), [`wearable_device`](#25-wearable_device) |
| **Biomarker sub-study (Tier 2)** | Kertes Lab hair assay + Qualtrics hygiene intake | [`hair_sample`](#29-hair_sample), [`hair_hygiene_covariates`](#210-hair_hygiene_covariates) |

---

## How Labfront/Garmin data is produced (collection mechanics & caveats)

Labfront is a third-party research wrapper over Garmin's API/SDK. It simplifies
study management but shapes the meaning of every
`Labfront/Garmin` column below. (Source: `analysis-plan/labfront.md` or [Celia's LabFront Documentation](https://www.dropbox.com/work/REACT/Engineering/Celia_Mercier/Labfront%20Documentation?di=left_nav_browse&_p_luid=1ccede4f))

- **Black-box derived metrics.** Garmin **Stress Score** (0–100) and **HRV** are
  proprietary, pre-computed algorithms derived from HRV/PPG.
    - `stress_sample.stress_score` is a Garmin score -- **not** a function of heart rate. (The synthetic seeder fakes stress as `50 + (hr − 70) × 2`, `db_seed.py:208`; this is a stand-in only and does **not** reflect how real stress is produced.)
- **No wear-status field.** Exports contain **no** `is_worn` flag. **Non-wear must
  be inferred** from missing timestamps, low confidence scores, or sync status.
    - Consequently `bpm = 0`/NULL is treated as inferred non-wear, and wear-time uses
  the ">2-hour gap" proxy (see [3. Wear time](#3-derived--analysis-metrics)).
  Note BBI streams keep recording even when signal confidence drops to `0`.
- **Coarse HRV granularity.** HR is available at ~1-second/epoch resolution, but
  **HRV is delivered as 5-minute averages or single nightly summaries**
  (`garmin-connect-hrv-values`) 
    - too coarse for instantaneous JITAI triggers.
- **Sync latency.** Watch to Labfront processing adds **2–5 minutes** (longer for
  high-volume BBI streams). 
    - This affects `wearable_device.last_synced_at`
  freshness, the staleness of `jitai_log.hr_at_trigger` / `stress_at_trigger`, and
  every delivery-funnel timestamp. Real-time triggering is therefore constrained.
- **Batch delivery.** Data arrives as batch `.zip` files of fragmented,
  time-bucketed CSVs across per-measurement directories (not streaming). 
    - The Labfront dashboard groups loss into broad buckets ("No HR Data",
  "Haven't Synced"), obscuring non-compliance vs. hardware/sync failure.

---

## 2. Persisted tables (REACT analysis schema)

Column names, types, and foreign keys follow `analysis-plan/schema.md`; meanings
are enriched from `JITAI-analysis-plan.md` and `syntheticData/db_seed.py`.

### 2.1 `user`

Participant record and enrollment status.

| Name | Type | Source stream | Meaning |
|------|------|---------------|---------|
| `user_id` | INT (PK) | Study/enrollment | Primary key. |
| `email` | VARCHAR(254) | Study/enrollment | Login / unique identifier. Synthetic accounts use `<uuid>@synthetic.gatorfan` so seeds can be wiped without touching real users (`db_seed.py:29,146`). |
| `first_name` | VARCHAR(100) | Study/enrollment | Given name. |
| `last_name` | VARCHAR(100) | Study/enrollment | Family name. |
| `birthdate` | DATE | Study/enrollment | Date of birth. Synthetic cohort draws ages 18–24 (`db_seed.py:144`). |
| `gender` | VARCHAR(10) | Study/enrollment | `male` / `female` / `other`. |
| `password` | VARCHAR(128) | Study/enrollment | Hashed password; NULL for synthetic/mock accounts (`db_seed.py:151`). |
| `push_token` | VARCHAR(128) | App telemetry | Expo push-notification token; required to deliver EMA/JITAI prompts. |
| `is_enrolled` | BOOLEAN | Study/enrollment | Whether the participant is actively enrolled. Denominator for retention. |
| `enrolled_at` | DATETIME | Study/enrollment | Enrollment timestamp (Day 1 anchor). |

### 2.2 `ema`

One row per EMA prompt (delivered via push notification). A prompt is
**completed** when all required items are submitted within the 60-minute response
window; **responded (partial)** when ≥1 item is submitted in-window
(`JITAI-analysis-plan.md:21-27`).

| Name | Type | Source stream | Meaning |
|------|------|---------------|---------|
| `id` | INT (PK) | EMA push | Primary key. |
| `user_id` | INT (FK → `user.user_id`) | EMA push | Participant who received the prompt. |
| `prompt_id` | VARCHAR(64) | EMA push | Prompt template identifier (e.g. `prompt-mood-0`, `db_seed.py:229`). |
| `sent_at` | DATETIME | EMA push | When the prompt was delivered. |
| `responded_at` | DATETIME | EMA push | When the participant submitted; NULL if unanswered. Response latency = `responded_at − sent_at`. |
| `status` | VARCHAR(16) | EMA push | Response status, e.g. `completed`, `responded`, `not_responded` (seeder writes `completed`, `db_seed.py:231`). |
| `mood` | SMALLINT | EMA push | Self-reported mood (Likert item). |
| `stress` | SMALLINT | EMA push | Self-reported stress (Likert item). Not currently populated by the seeder (NULL, `db_seed.py:233`). |
| `energy` | SMALLINT | EMA push | Self-reported energy (Likert item). Not currently populated by the seeder (NULL, `db_seed.py:234`). |
| `high_arousal_anger` | SMALLINT | EMA push | Discrete affect: high-arousal anger (Likert). Maps to `high_arousal_anger` JITAI trigger tag. |
| `high_arousal_anxiety` | SMALLINT | EMA push | Discrete affect: high-arousal anxiety (Likert). Maps to `high_arousal_anxiety` JITAI trigger tag. |
| `low_mood_withdrawal` | SMALLINT | EMA push | Discrete affect: low mood / social withdrawal (Likert). Maps to `low_mood_withdrawal` JITAI trigger tag. |

**Domain rotation fields** — only the domain active in this prompt's rotation slot is non-NULL; all others are NULL.

| Name | Type | Source stream | Meaning |
|------|------|---------------|---------|
| `domain_eating` | SMALLINT | EMA push | Emotional / uncontrolled eating domain item; NULL if domain not active for this prompt. |
| `domain_emotion_reg` | SMALLINT | EMA push | Emotion regulation strategies domain item; NULL if domain not active. |
| `domain_alcohol` | SMALLINT | EMA push | Drinking motives / urges domain item; NULL if domain not active. |
| `domain_online_activity` | SMALLINT | EMA push | Reactive posting / digital behavior domain item; NULL if domain not active. |

**Context flags**

| Name | Type | Source stream | Meaning |
|------|------|---------------|---------|
| `is_morning_checkin` | BOOLEAN | EMA push | TRUE for the first check-in of each day; captures sleep quality and prior-evening recall. |
| `is_game_day_context` | BOOLEAN | EMA push | TRUE on relevant sporting-event days; activates win/loss context and fandom items. |
| `is_timelocked_outcome` | BOOLEAN | EMA push | TRUE if this check-in is the first prompt within 2 hours following a JITAI decision point; all 4 outcome domains are presented together in this window. |

> **Scale note:** the analysis schema stores these as `SMALLINT` (Likert items).
> The synthetic generator produces mood on a **1–5** scale; the production
> `models.py` `EMA` model validates **1–10**. Confirm the deployed item scale
> against the protocol before analysis.

### 2.3 `heart_rate_sample`

Heart-rate samples imported from Garmin via Labfront.

| Name | Type | Source stream | Meaning |
|------|------|---------------|---------|
| `id` | INT (PK) | Labfront/Garmin | Primary key. |
| `user_id` | INT (FK → `user.user_id`) | Labfront/Garmin | Participant. |
| `timestamp` | DATETIME | Labfront/Garmin | Sample time. Real cadence ~1-sec/epoch; subject to 2–5 min sync latency. Synthetic data is minute-level, optionally thinned via `--hr-every` (`db_seed.py:133`). |
| `bpm` | SMALLINT | Labfront/Garmin | Heart rate in beats per minute. `0`/NULL ≈ **inferred non-wear** (no explicit wear flag -- see [collection caveats](#how-labfrontgarmin-data-is-actually-produced-collection-mechanics--caveats)). |
| `source` | VARCHAR(32) | Labfront/Garmin | Import/provenance tag; seeder writes `garmin_labfront` (`db_seed.py:191`). |

### 2.4 `stress_sample`

Garmin Stress Score time series imported from Labfront.

| Name | Type | Source stream | Meaning |
|------|------|---------------|---------|
| `id` | INT (PK) | Labfront/Garmin | Primary key. |
| `user_id` | INT (FK → `user.user_id`) | Labfront/Garmin | Participant. |
| `timestamp` | DATETIME | Labfront/Garmin | Sample time. Synthetic data is sampled every 3 minutes (`db_seed.py:204`). |
| `stress_score` | SMALLINT | Labfront/Garmin | **Garmin proprietary stress score, 0–100**, derived from HRV by a black-box algorithm -- **not** raw and **not** a function of HR. Synthetic proxy only: `50 + (hr − 70) × 2` (`db_seed.py:208`). |
| `source` | VARCHAR(32) | Labfront/Garmin | Import/provenance tag; seeder writes `garmin_labfront` (`db_seed.py:213`). |

### 2.5 `wearable_device`

One device record per participant (the Garmin linked through Labfront).

| Name | Type | Source stream | Meaning |
|------|------|---------------|---------|
| `id` | INT (PK) | Labfront/Garmin | Primary key. |
| `user_id` | INT (FK → `user.user_id`) | Labfront/Garmin | Owner. |
| `labfront_participant_id` | VARCHAR(64) | Labfront/Garmin | Labfront platform participant identifier (`labfront-<uuid[:8]>`, `db_seed.py:173`). |
| `is_active` | BOOLEAN | Labfront/Garmin | Whether the device is active in the study. |
| `last_synced_at` | DATETIME | Labfront/Garmin | Most recent successful sync. Flag as stale if > 24 h old; note the inherent 2–5 min sync latency. |

### 2.6 `jitai_log`

One row per JITAI **decision point**. The decision is **two-stage**: (1) an
**eligibility** check (`observed_mssd` crosses the participant's within-person
threshold, typically the 80th percentile of their EMA history), then (2)
**randomization** (`send_prompt = TRUE` only if `randomization_draw <
randomization_probability`). These stages must not be conflated in reporting.

**Decision columns**

| Name | Type | Source stream | Meaning |
|------|------|---------------|---------|
| `id` | INT (PK) | Decision engine | Primary key. |
| `user_id` | INT (FK → `user.user_id`) | Decision engine | Participant. |
| `prompt_id` | VARCHAR(64) | Decision engine | Prompt template evaluated. |
| `triggered_at` | DATETIME | Decision engine | When the decision point was evaluated. |
| `decision_point_id` | VARCHAR(64) | Decision engine | Unique ID for this decision point. |
| `trigger_signal` | VARCHAR(32) | Decision engine | Signal that triggered evaluation (e.g. `mssd`). |
| `trigger_reason` | VARCHAR(128) | Decision engine | Human-readable trigger explanation (documents both stages). |
| `observed_mssd` | FLOAT | Decision engine | Within-person MSSD at decision time (see [§3](#3-derived--analysis-metrics)). |
| `randomization_probability` | FLOAT | Decision engine | Configured P(send \| eligible), e.g. 0.7. |
| `randomization_draw` | FLOAT | Decision engine | Uniform(0,1) draw; send if `< randomization_probability`. |
| `send_prompt` | BOOLEAN | Decision engine | TRUE only if eligibility **and** randomization pass. |
| `eligible_prompt_ids` | JSONB | Decision engine | Prompt IDs eligible at this decision point. |
| `decision_made_at` | DATETIME | Decision engine | When the decision was finalized. |

**Trigger context (wearable + linked EMA at decision time)**

| Name | Type | Source stream | Meaning |
|------|------|---------------|---------|
| `hr_at_trigger` | SMALLINT | Labfront/Garmin | Heart rate (bpm) at trigger; may lag by the 2–5 min sync buffer. |
| `stress_at_trigger` | SMALLINT | Labfront/Garmin | Garmin stress score (0–100) at trigger. |
| `ema_id` | INT (FK → `ema.id`) | EMA push | Linked EMA response. |
| `ema_mood` | SMALLINT | EMA push | Mood snapshot copied from the linked EMA. |
| `ema_stress` | SMALLINT | EMA push | Stress snapshot from the linked EMA. |
| `ema_energy` | SMALLINT | EMA push | Energy snapshot from the linked EMA. |

**Delivery funnel (push receipt lifecycle)**

| Name | Type | Source stream | Meaning |
|------|------|---------------|---------|
| `status` | VARCHAR(16) | App telemetry | Overall prompt delivery status. |
| `push_sent_at` | DATETIME | App telemetry | When the push was sent to the device. |
| `device_received_at` | DATETIME | App telemetry | When the device received the push. |
| `receipt_reported_at` | DATETIME | App telemetry | When the app reported receipt. |
| `receipt_app_state` | VARCHAR(32) | App telemetry | App state at receipt (`foreground`/`background`). |
| `receipt_platform` | VARCHAR(16) | App telemetry | Reporting platform (`iOS`/`Android`). |
| `delivery_status` | VARCHAR(32) | App telemetry | Push delivery outcome (`delivered`/`failed`). |
| `delivery_error` | TEXT | App telemetry | Error message if delivery failed. |

**Intervention message classification**

The 26-message bank (P001–P026) plus 4 active controls (C001–C004) are classified by therapeutic technique and trigger context.

| Name | Type | Source stream | Meaning |
|------|------|---------------|---------|
| `message_technique` | VARCHAR(32) | Decision engine | Therapeutic technique label. Values: `CBT: Thought record`, `CBT: Perspective-taking`, `CBT: Decatastrophizing`, `CBT: Cost-benefit check`, `CBT: Implementation intention`, `CBT: Behavioral redirect`, `ACT: Cognitive defusion`, `ACT: Acceptance`, `ACT: Values anchor`, `ACT: Urge surfing`, `ACT: Present-moment grounding`, `Active Control`. |
| `trigger_context_tag` | VARCHAR(32) | Decision engine | Emotional / behavioral context that warranted the prompt. Values: `high_arousal_anger`, `high_arousal_anxiety`, `interpersonal_conflict`, `urge_craving`, `low_mood_withdrawal`, `general_stress`, `drinking_context`, `celebration`. |
| `is_active_control` | BOOLEAN | Decision engine | TRUE if the assigned message is a neutral active-control message (C001–C004). |
| `distress_override_active` | BOOLEAN | Decision engine | TRUE if normal prompt delivery was suspended and replaced by the emergency resource card due to a distress signal or safety alert. |

### 2.7 `engagement_log`

User interactions with a delivered JITAI prompt.

| Name | Type | Source stream | Meaning |
|------|------|---------------|---------|
| `id` | INT (PK) | App telemetry | Primary key. |
| `user_id` | INT (FK → `user.user_id`) | App telemetry | Participant. |
| `jitai_log_id` | INT (FK → `jitai_log.id`) | App telemetry | The prompt interacted with. |
| `event_type` | VARCHAR(64) | App telemetry | Interaction type: `prompt_opened`, `prompt_acted`, `prompt_dismissed`. |
| `occurred_at` | DATETIME | App telemetry | When the interaction happened (device clock). |
| `recorded_at` | DATETIME | App telemetry | When the event was persisted server-side. |

### 2.8 `phone_telemetry`

Raw app-usage events for diagnostics and latency analysis.

| Name | Type | Source stream | Meaning |
|------|------|---------------|---------|
| `id` | INT (PK) | App telemetry | Primary key. |
| `user_id` | INT (FK → `user.user_id`) | App telemetry | Participant. |
| `session_id` | VARCHAR(64) | App telemetry | App session identifier. |
| `event_type` | VARCHAR(64) | App telemetry | Event category. |
| `occurred_at` | DATETIME | App telemetry | Event time (device clock). |
| `recorded_at` | DATETIME | App telemetry | Server persistence time. |
| `screen_name` | VARCHAR(64) | App telemetry | Screen where the event occurred. |
| `latency_ms` | INT | App telemetry | Event/interaction latency in milliseconds. |
| `metadata` | JSONB | App telemetry | Free-form event payload. |

### 2.9 `hair_sample`

One row per hair specimen collected during the enrollment visit (optional Tier 2 biomarker sub-study). Concentrations reflect cumulative steroid exposure over approximately the prior 1–3 months based on hair growth rate (~1 cm/month).

| Name | Type | Source stream | Meaning |
|------|------|---------------|---------|
| `sample_id` | INT (PK) | REACT-Bio Lab | Unique specimen identifier. |
| `user_id` | INT (FK → `user.user_id`) | Study/enrollment | Participant ID. |
| `collected_at` | DATETIME | REACT-Bio Lab | Collection timestamp during enrollment visit. |
| `hair_length_cm` | FLOAT | REACT-Bio Lab | Length of collected segment in cm (minimum ~1–3 cm required). |
| `sample_mass_mg` | FLOAT | REACT-Bio Lab | Total weight in milligrams (~50 mg target). |
| `assay_method` | VARCHAR(32) | REACT-Bio Lab | Assay technique used: `ELISA` or `LC-MS/MS`. |
| `cortisol_pg_mg` | FLOAT | REACT-Bio Lab | Hair cortisol concentration (pg/mg); values should be log-transformed and winsorized before analysis. |
| `testosterone_pg_mg` | FLOAT | REACT-Bio Lab | Hair testosterone concentration (pg/mg); values should be log-transformed and winsorized before analysis. |

### 2.10 `hair_hygiene_covariates`

One row per participant capturing biological covariates required to interpret steroid assay concentrations. Collected via Qualtrics at the baseline/enrollment visit.

| Name | Type | Source stream | Meaning |
|------|------|---------------|---------|
| `user_id` | INT (PK/FK → `user.user_id`) | Qualtrics / Baseline | Participant ID (one-to-one with `user`). |
| `chemical_treatment` | BOOLEAN | Qualtrics / Baseline | History of hair coloring, bleaching, or other chemical treatments that can alter steroid concentrations. |
| `wash_frequency_per_wk` | INT | Qualtrics / Baseline | Self-reported weekly hair wash frequency. |
| `hormonal_contraception` | BOOLEAN | Qualtrics / Baseline | Current use of hormonal contraceptives (affects cortisol and testosterone metabolism). |
| `steroid_medications` | BOOLEAN | Qualtrics / Baseline | Current use of synthetic glucocorticoid or androgen medications. |

### 2.11 Relationships (foreign keys)

- `wearable_device.user_id` → `user.user_id` (one-to-one / one-to-many)
- `heart_rate_sample.user_id` → `user.user_id` (many-to-one)
- `stress_sample.user_id` → `user.user_id` (many-to-one)
- `ema.user_id` → `user.user_id` (many-to-one)
- `jitai_log.user_id` → `user.user_id` (many-to-one)
- `jitai_log.ema_id` → `ema.id` (many-to-one)
- `phone_telemetry.user_id` → `user.user_id` (many-to-one)
- `engagement_log.user_id` → `user.user_id` (many-to-one)
- `engagement_log.jitai_log_id` → `jitai_log.id` (many-to-one)
- `hair_sample.user_id` → `user.user_id` (many-to-one)
- `hair_hygiene_covariates.user_id` → `user.user_id` (one-to-one)

---

## 3. Derived / analysis metrics

Computed offline (source stream = **Derived**) from the tables above. Formulas
follow `JITAI-analysis-plan.md` and `syntheticData/decision/`.

| Metric | Type | Definition / formula | Notes & benchmarks |
|--------|------|----------------------|--------------------|
| **Within-person MSSD** (`observed_mssd`) | FLOAT | Mean of squared successive differences: `mean((x_t − x_{t-1})²)` over **consecutive answered** EMA items. | The core JITAI trigger signal. Missing EMAs suppress the difference for both the missing observation and the immediately following prompt (`JITAI-analysis-plan.md:83`). |
| **Expected MSSD** | FLOAT | `2·σ²·(1−ρ)` from latent AR(1) parameters. | Ground-truth benchmark used to validate recovery (`syntheticData/SCHEMA.md`). |
| **AR(1) ρ̂** (autocorrelation) | FLOAT | Lag-1 autocorrelation over consecutive answered EMA pairs. | Recovery **degrades below ~80%** response rate -- an analytic requirement, distinct from the 75% feasibility benchmark. |
| **AR(1) σ̂** (residual SD) | FLOAT | Sample SD of demeaned answered EMA. | More robust than ρ̂ across response rates. |
| **Response latency** | INT (min) | `responded_at − sent_at`. | Must be ≤ 60 min to count in-window. |
| **Wear time** | FLOAT (%) | Coverage over standardized waking hours **8:00 AM–10:00 PM** (14 h/day): numerator = 14 h − gaps > **2 consecutive hours**; denominator = 14 h. | Benchmark ≥ 8 h/day, ≥ 5 days/week. Non-wear inferred (no wear flag); BBI/EMA used as supporting evidence (`JITAI-analysis-plan.md:31-36`). |
| **Intervention dosage** | INT / day | `COUNT(send_prompt = TRUE)` per participant per day (active study days in Weeks 2–5 only; Week 1 run-in excluded). | Hard cap = **4 prompts/day**; expected delivery ~2–3/day when eligible. Flag days exceeding cap or showing 0 deliveries when eligible. |
| **Cooldown compliance** | -- | Minimum gap between consecutive triggers per participant. | Flag if < **60 minutes**. Violations indicate a decision-engine logic error. |
| **Delivery-funnel conversion** | FLOAT (%) | Conversion across `push_sent_at → device_received_at → receipt_reported_at → engagement`. | Diagnoses loss at each stage. |
| **HR-MSSD vs EMA-MSSD** | FLOAT | Rolling MSSD on minute-level HR vs EMA-based MSSD. | Concordance between physiological and self-report volatility. |
| **Completed check-in rate** | FLOAT (%) | Completed EMAs / delivered EMAs (delivery failures excluded from denominator). | Preregistered benchmark **75%**. |
| **Participant retention** | FLOAT (%) | Participants enrolled continuously Day 1→35 / participants who began Day 1. | Missing prompts ≠ dropout. Run-in (Week 1) and active intervention (Weeks 2–5) retention are reported separately. |
| **Run-in baseline MSSD** | FLOAT | Within-person MSSD computed from Week 1 EMA responses only. | Used to calibrate each participant's 80th-percentile threshold before micro-randomization begins in Week 2. |

### 3.1 Three-tier moderation framework

Pre-specified baseline moderators assessed at enrollment. Used to test heterogeneous treatment effects.

| Metric / Construct | Data Type | Primary Source | Protocol Tier & Role |
|--------------------|-----------|----------------|----------------------|
| **Urgency (SUPPS-P)** | FLOAT | Qualtrics / Baseline | **Tier 1 — Confirmatory:** positive and negative urgency as a moderator of JITAI effect on reactive behavior outcomes. |
| **Emotion Regulation (DERS-16)** | FLOAT | Qualtrics / Baseline | **Tier 1 — Confirmatory:** difficulties in emotion regulation as a moderator. |
| **Eating Motives (TFEQ-R18)** | FLOAT | Qualtrics / Baseline | **Tier 1 — Confirmatory:** emotional and uncontrolled eating as a moderator of the eating domain outcomes. |
| **Drinking Motives (DMQ-R)** | FLOAT | Qualtrics / Baseline | **Tier 1 — Confirmatory:** coping drinking motives as a moderator of the alcohol domain outcomes. |
| **Body Listening (MAIA-2)** | FLOAT | Qualtrics / Baseline | **Tier 1 — Confirmatory:** interoceptive attention as a moderator of biometric-signal-triggered interventions. |
| **Childhood Adversity (ACE)** | INT (0–10) | Qualtrics / Baseline (Sensitive) | **Tier 2 — Confirmatory:** moderator of the Hair Cortisol × Reactive Behavior interaction; handled under IRB sensitive-data protocols. |
| **Subjective Social Status (MacArthur)** | INT (1–10) | Qualtrics / Baseline | **Tier 3 — Exploratory:** ladder score interacting with Hair Testosterone on reactive behavior. |
| **Everyday Discrimination** | FLOAT | Qualtrics / Baseline | **Tier 3 — Exploratory:** context covariate for chronic stress exposure. |
| **Chronotype (rMEQ)** | INT | Qualtrics / Baseline | **Tier 3 — Exploratory:** morningness-eveningness score as a covariate for EMA timing and biometric baseline variation. |

---

## 4. Synthetic generator columns (provenance for seeded data)

The synthetic module (`syntheticData/`) produces the DataFrames and CSVs that seed
the database and drive validation. Full generator I/O is documented in
[`syntheticData/SCHEMA.md`](../syntheticData/SCHEMA.md) §1–3; the columns an
analyst will actually encounter in CSV exports and validation outputs are
summarized here.

> `syntheticData/SCHEMA.md` **§4 (Database seed outputs) is stale** -- it
> describes an older `db_seed.py` that targeted the production `models.py` schema
> (device-FK `heart_rate_sample` with `zone`, EMA with only `mood`). The current
> `db_seed.py` targets the REACT schema documented above. Trust §1–3 for generator
> internals; trust **this file** for what gets persisted.

**EMA frame** (`generate_cohort`) : one row per prompt per user

| Name | Type | Source stream | Meaning |
|------|------|---------------|---------|
| `user_id` | str (UUID) | Derived | Synthetic user identifier (shared across frames). |
| `timestamp` | datetime | Derived | Prompt time (1-min freq from 2026-06-01). |
| `ema` | float | Derived | 1–5 Likert response, or NaN if missed. |
| `true_sigma` | float | Derived | Ground-truth latent volatility σ (AR(1) stationary SD). |
| `true_rho` | float | Derived | Ground-truth latent AR(1) autocorrelation ρ. |
| `true_expected_mssd` | float | Derived | `2·σ²·(1−ρ)`. |

**HR frame** (`generate_HR`) : one row per minute per user

| Name | Type | Source stream | Meaning |
|------|------|---------------|---------|
| `hr` | float | Derived | Heart rate (bpm), clipped [40, 200]. |
| `stress` | float | Derived | Continuous Garmin-style stress (0–100), synthetic. |
| `rmssd_ms` | float | Derived | Continuous all-day RMSSD (ms), per minute. |
| `hr_from_rr` | float | Derived | bpm recovered from simulated RR (`60000/RR`); sanity trace ≈ `hr`. |
| `source` | str | Derived | Garmin device model (Venu 3 / Vivoactive 5 / Vivoactive 6). |

**Overnight-HRV frame** (`generate_HRV`) : one row per night per user

| Name | Type | Source stream | Meaning |
|------|------|---------------|---------|
| `overnight_avg_rmssd` | float | Derived | Overnight RMSSD (ms), rounded 0.1. |
| `baseline_7d` | float | Derived | Trailing 7-night mean RMSSD (shifted to exclude the night itself). |
| `hrv_status` | str | Derived | `Balanced` / `Low` / `Unbalanced` / `No Status` (band = `max(0.5·rolling_std, 5.0)`; `No Status` until ≥ 3 nights). |

**Decision log** (`decision/decision_engine.py`) and **validation** (`decision/mssd_validation.py`)

| Name | Type | Source stream | Meaning |
|------|------|---------------|---------|
| `observed_mssd` | float | Derived | Rolling MSSD estimate (volatility proxy). |
| `adjusted_mssd` | float | Derived | `observed_mssd + hr_score` (arousal-adjusted). |
| `user_threshold` | float | Derived | Within-person 80th-pct threshold (expanding, shifted). |
| `send_prompt` | bool | Derived | Decision outcome. |
| `decision_reason` | str | Derived | Rule that produced the decision (e.g. `prompt sent`, `below within-person threshold`, `cooldown active`, `daily cap reached`). |
| `recovered_sigma` / `recovered_rho` | float | Derived | Per-user σ̂ / ρ̂ recovered from the realised EMA series. |
| `sigma_recovery_pct` / `rho_recovery_pct` | float | Derived | `100 · recovered / true` (100% = perfect recovery). |

---

## Appendix : Schema reconciliation & known gaps

The production Django backend (`HealthyGatorSportsFanDjango/app/models.py`,
migrations through `0024_jitailog`) does **not** match the REACT analysis schema
documented above. Analysts and backend engineers should be aware of these
divergences.

| REACT analysis schema (this doc) | Production `models.py` | Divergence |
|----------------------------------|------------------------|------------|
| `stress_sample` table | *(none)* | No `StressSample` model or migration exists in production. |
| `heart_rate_sample.user_id` (FK → user), `source` | `HeartRateSample.device` (FK → `WearableDevice`), `zone` (out_of_range/fat_burn/cardio/peak) | Different parent (user vs device) and different columns (`source` vs `zone`). |
| `wearable_device.labfront_participant_id` | `WearableDevice.fitbit_device_id`, `device_type`, `device_name`, `created_at` | Labfront vs Fitbit orientation; different identifier fields. |
| `ema`: `prompt_id`, `sent_at`, `responded_at`, `status`; mood/stress/energy | `EMA`: `timestamp`, `physical_activity`, `weight_lbs`, `notes`; mood/energy/stress **1–10** | REACT tracks prompt lifecycle; production tracks richer self-report on a 1–10 scale. |
| `user.is_enrolled`, `enrolled_at` | *(none)* | Not present in production `User`. Production adds height/goal/Fitbit-token fields not used by the analysis. |
| `jitai_log`: randomization + delivery-funnel columns (~28) | `JITAILog`: `title`, `message`, `volatility_score`, `threshold_used`, `prompt_status`, `prompt_count`, `opened_at`, `interacted_at` | Production is a simpler notification audit; REACT captures the full decision + delivery lifecycle. |
| `phone_telemetry`, `engagement_log` tables | *(none)* | No production models. |

**Known gaps / follow-ups:**

1. **No migrations for the REACT schema.** `syntheticData/db_seed.py` writes to
   tables (`app_stresssample`, `user.is_enrolled`, `heart_rate_sample.source`,
   etc.) that current Django migrations do not create. The seeder cannot run
   against the committed backend until migrations are authored.
2. **`syntheticData/SCHEMA.md` §4 is stale** and should be refreshed to match the
   current seeder (see [§4](#4-synthetic-generator-columns-provenance-for-seeded-data)).
3. **EMA item scale** differs (synthetic 1–5 vs production 1–10 vs schema
   `SMALLINT`); confirm the deployed scale against the study protocol / the
   `Feasibility Definitions` document before analysis.
