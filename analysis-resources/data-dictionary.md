# Data Dictionary | REACT Biomarker and Extended-Measures Study

Author: Tien Tyler Le

Date: 08/27/2026

[Github](https://github.com/smashlab-uf/REACT/blob/data-analysis/analysis-resources/data-dictionary.md)

The authoritative
schema is `Resources/react_schema.csv`.

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
- **Synthetic epoch:** synthetic cohorts (`react_cohort.py`) anchor Day 1 to `now − days` at 00:00 UTC (or an explicit `start`); EMA prompts follow a fixed daily schedule, HR is a 15-min waking-hour cadence, and Garmin stress is hourly. No HRV stream is generated.
- **Authoritative schema:** this dictionary is kept in sync with `Resources/react_schema.csv` (machine-readable column definitions) and `Resources/Database Schema v4.pdf` (ERD). The production Django backend may differ; divergences are catalogued in the [Schema reconciliation appendix](#appendix--schema-reconciliation--known-gaps).
- **Canonical prose definitions** live in `analysis-plan/Feasibility Definitions.docx`. This dictionary is intended to stay consistent with it.

---

## 1. Collection overview

Three data streams are collected during the study period:

| Stream | Delivery mechanism | Tables |
|--------|--------------------|--------|
| **EMA survey responses** | Push notification to in-app survey | [`app_ema`](#22-app_ema), [`app_emaitemresponse`](#23-app_emaitemresponse), [`app_checkinreminder`](#213-app_checkinreminder), [`app_user`](#21-app_user) |
| **JITAI interventions & engagement** | Decision engine + phone app | [`app_jitailog`](#28-app_jitailog), [`app_engagementlog`](#29-app_engagementlog), [`app_phonetelemetry`](#210-app_phonetelemetry) |
| **Garmin wearable telemetry** | Labfront import | [`app_heartratesample`](#25-app_heartratesample), [`app_stresssample`](#26-app_stresssample), [`app_wearabledevice`](#27-app_wearabledevice) |
| **Study calendar** | Backend / admin | [`app_eventday`](#24-app_eventday) |
| **Biomarker sub-study (Tier 2)** | Kertes Lab hair assay + Qualtrics hygiene intake | [`hair_sample`](#211-hair_sample), [`hair_hygiene_covariates`](#212-hair_hygiene_covariates) |

---

## How Labfront/Garmin data is produced (collection mechanics & caveats)

Labfront is a third-party research wrapper over Garmin's API/SDK. It simplifies
study management but shapes the meaning of every
`Labfront/Garmin` column below. (Source: `analysis-plan/labfront.md` or [Celia's LabFront Documentation](https://www.dropbox.com/work/REACT/Engineering/Celia_Mercier/Labfront%20Documentation?di=left_nav_browse&_p_luid=1ccede4f))

- **Black-box derived metrics.** Garmin **Stress Score** (0–100) and **HRV** are
  proprietary, pre-computed algorithms derived from HRV/PPG.
    - `stress_sample.stress_score` is a Garmin score -- **not** a function of heart rate. (The synthetic generator draws stress independently as `Normal(45, 18)` clipped to 0–100, `react_cohort.py`; a stand-in only that does **not** reflect how real stress is produced.)
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

Column names, types, and foreign keys follow `Resources/react_schema.csv`; meanings
are enriched from `JITAI-analysis-plan.md` and `syntheticData/react_cohort.py`.

### 2.1 `app_user`

**Production table:** `app_user` (see [`production_schema.md`](./production_schema.md)).

Participant record and enrollment status.

| Name | Type | Source stream | Meaning |
|------|------|---------------|---------|
| `user_id` | INT (PK) | Study/enrollment | Primary key. |
| `email` | VARCHAR(254) | Study/enrollment | Login / unique identifier. Synthetic accounts use `u<N>@synthetic.gatorfan` so seeds can be wiped without touching real users (`react_cohort.py`). |
| `first_name` | VARCHAR(100) | Study/enrollment | Given name. |
| `last_name` | VARCHAR(100) | Study/enrollment | Family name. |
| `birthdate` | DATE | Study/enrollment | Date of birth. Synthetic cohort uses a fixed `2003-01-01` birthdate (`react_cohort.py`). |
| `gender` | VARCHAR(10) | Study/enrollment | `male` / `female` / `other`. |
| `password` | VARCHAR(128) | Study/enrollment | Hashed password; NULL for synthetic/mock accounts (`react_cohort.py` omits it). |
| `push_token` | VARCHAR(128) | App telemetry | Expo push-notification token; required to deliver EMA/JITAI prompts. |
| `is_enrolled` | BOOLEAN | Study/enrollment | Whether the participant is actively enrolled. Denominator for retention. |
| `enrolled_at` | DATETIME | Study/enrollment | Enrollment timestamp (Day 1 anchor). |

### 2.2 `app_ema`

**Production table:** `app_ema` (see [`production_schema.md`](./production_schema.md)).

One row per EMA prompt (delivered via push notification). A prompt is
**completed** when all required items are submitted within the 60-minute response
window; **responded (partial)** when ≥1 item is submitted in-window
(`JITAI-analysis-plan.md:21-27`). Per-item Likert responses are stored in
[`app_emaitemresponse`](#23-app_emaitemresponse); the fields below are top-level
prompt metadata.

| Name | Type | Source stream | Meaning |
|------|------|---------------|---------|
| `id` | INT (PK) | EMA push | Primary key. |
| `user_id` | INT (FK → `user.user_id`) | EMA push | Participant who received the prompt. |
| `prompt_id` | VARCHAR(64) | EMA push | Prompt template identifier (synthetic form `p-<day>-<k>`, `react_cohort.py`). |
| `sent_at` | DATETIME | EMA push | When the prompt was delivered. |
| `responded_at` | DATETIME | EMA push | When the participant submitted; NULL if unanswered. Response latency = `responded_at − sent_at`. |
| `status` | VARCHAR(16) | EMA push | Response status, e.g. `completed`, `responded`, `not_responded` (the synthetic generator writes `completed` for answered prompts and `expired` otherwise, `react_cohort.py`). |
| `ema_type` | VARCHAR(32) | Decision engine | Prompt category, e.g. `jitai_triggered` vs `scheduled`. Determines which item blocks are presented. |
| `source_jitai_log_id` | INT (FK → `jitai_log.id`, nullable) | Decision engine | The JITAI decision point that triggered this prompt, if applicable; NULL for scheduled check-ins. |
| `outcome_window_start` | DATETIME | Decision engine | Start of the outcome measurement window for this prompt (used to identify post-JITAI EMA outcomes). |
| `outcome_window_end` | DATETIME | Decision engine | End of the outcome measurement window. |
| `expires_at` | DATETIME | EMA push | When the response window closes; typically `sent_at + 60 min`. Responses after this are out-of-window. |
| `mood` | SMALLINT | EMA push | Top-level self-reported mood (Likert, 1–7). Denormalized summary field; detailed per-item responses are in `ema_item_response`. |
| `stress` | SMALLINT | EMA push | Top-level self-reported stress (Likert, 1–7). |
| `energy` | SMALLINT | EMA push | Top-level self-reported energy (Likert, 1–7). |

> **Scale note:** the EMA Likert scale is **1–7**. Production `models.py` validates it
> (`MinValueValidator(1)`, `MaxValueValidator(7)`) and the current generator
> `syntheticData/react_cohort.py` emits 1–7 to match. The **1–5** scale was the retired
> `synthetic_generator.py` validation harness only (legacy). The JITAI trigger signal uses
> B1/B2 from `ema_item_response.value_numeric`, not these top-level summary fields.

### 2.3 `app_emaitemresponse`

**Production table:** `app_emaitemresponse` (see [`production_schema.md`](./production_schema.md)).

One row per individual item response within an EMA prompt. This is the
authoritative source for the JITAI trigger signal: `calculate_mssd()` in the
decision engine reads `value_numeric` where `item_id = 'B1'` (energy) and
`item_id = 'B2'` (stress). Do not use `ema.mood` for offline MSSD audit.

| Name | Type | Source stream | Meaning |
|------|------|---------------|---------|
| `id` | INT (PK) | EMA push | Primary key. |
| `ema_id` | INT (FK → `ema.id`) | EMA push | Parent prompt. |
| `item_id` | VARCHAR(8) | EMA push | Block identifier (e.g. `B1` = energy, `B2` = stress, `B3`–`B8` = other blocks). |
| `sub_item_id` | VARCHAR(32) | EMA push | Specific sub-question within the block. **UNIQUE** with `ema_id` — the constraint is `UNIQUE(ema_id, sub_item_id)`, not `item_id`. |
| `response_type` | VARCHAR(16) | EMA push | Item format: `likert` \| `single_choice` \| `multi_choice` \| `number` \| `yes_no`. |
| `value_numeric` | INT | EMA push | Numeric response value (populated for `likert` and `number` items; NULL otherwise). Likert range: 1–7 in production. |
| `value_choice` | VARCHAR(64) | EMA push | Single-choice response text; NULL if not applicable. |
| `value_choices` | JSONB | EMA push | Multi-choice response array; NULL if not applicable. |

### 2.4 `app_eventday`

**Production table:** `app_eventday` (see [`production_schema.md`](./production_schema.md)).

One row per study-calendar event (e.g. game day, bye week). Used by the prompt
scheduler to adjust EMA timing and context, and by the JITAI engine to set
game-day context for prompt selection.

| Name | Type | Source stream | Meaning |
|------|------|---------------|---------|
| `id` | INT (PK) | Study/enrollment | Primary key. |
| `date` | DATE (UNIQUE) | Study/enrollment | The calendar date of the event. |
| `sport` | VARCHAR(64) | Study/enrollment | Sport or event category (e.g. `football`, `basketball`). |
| `description` | VARCHAR(128) | Study/enrollment | Human-readable label (e.g. `UF vs Georgia — home`). |

### 2.5 `app_heartratesample`

**Production table:** `app_heartratesample` (see [`production_schema.md`](./production_schema.md)).

Heart-rate samples imported from Garmin via Labfront.

| Name | Type | Source stream | Meaning |
|------|------|---------------|---------|
| `id` | INT (PK) | Labfront/Garmin | Primary key. |
| `user_id` | INT (FK → `user.user_id`) | Labfront/Garmin | Participant. |
| `timestamp` | DATETIME | Labfront/Garmin | Sample time. Real cadence ~1-sec/epoch; subject to 2–5 min sync latency. Synthetic data uses a 15-min waking-hour cadence with a midday non-wear gap (`react_cohort.py`). |
| `bpm` | SMALLINT | Labfront/Garmin | Heart rate in beats per minute. `0`/NULL ≈ **inferred non-wear** (no explicit wear flag -- see [collection caveats](#how-labfrontgarmin-data-is-actually-produced-collection-mechanics--caveats)). |
| `source` | VARCHAR(32) | Labfront/Garmin | Import/provenance tag; the synthetic generator writes `garmin_labfront` (`react_cohort.py`). |

### 2.6 `app_stresssample`

**Production table:** `app_stresssample` (see [`production_schema.md`](./production_schema.md)).

Garmin Stress Score time series imported from Labfront.

| Name | Type | Source stream | Meaning |
|------|------|---------------|---------|
| `id` | INT (PK) | Labfront/Garmin | Primary key. |
| `user_id` | INT (FK → `user.user_id`) | Labfront/Garmin | Participant. |
| `timestamp` | DATETIME | Labfront/Garmin | Sample time. Real Garmin cadence is ~3 min; synthetic data is sampled hourly (`react_cohort.py`). |
| `stress_score` | SMALLINT | Labfront/Garmin | **Garmin proprietary stress score, 0–100**, derived from HRV by a black-box algorithm -- **not** raw and **not** a function of HR. Synthetic values are drawn independently as `Normal(45, 18)` clipped to 0–100 (`react_cohort.py`). |
| `source` | VARCHAR(32) | Labfront/Garmin | Import/provenance tag; the synthetic generator writes `garmin_labfront` (`react_cohort.py`). |

### 2.7 `app_wearabledevice`

**Production table:** `app_wearabledevice` (see [`production_schema.md`](./production_schema.md)).

One device record per participant (the Garmin linked through Labfront).

| Name | Type | Source stream | Meaning |
|------|------|---------------|---------|
| `id` | INT (PK) | Labfront/Garmin | Primary key. |
| `user_id` | INT (FK → `user.user_id`, UNIQUE) | Labfront/Garmin | Owner (one-to-one). |
| `labfront_participant_id` | VARCHAR(64) | Labfront/Garmin | Labfront platform participant identifier (synthetic form `labfront-<zero-padded user_id>`, e.g. `labfront-00000001`, `react_cohort.py`). |
| `is_active` | BOOLEAN | Labfront/Garmin | Whether the device is active in the study. |
| `last_synced_at` | DATETIME | Labfront/Garmin | Most recent successful sync. Flag as stale if > 24 h old; note the inherent 2–5 min sync latency. |

### 2.8 `app_jitailog`

**Production table:** `app_jitailog` (see [`production_schema.md`](./production_schema.md)).

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
| `decision_point_id` | VARCHAR(64) | Decision engine | Unique ID for this decision point (UNIQUE constraint). |
| `trigger_signal` | VARCHAR(32) | Decision engine | Signal that triggered evaluation (e.g. `mssd`). |
| `trigger_reason` | VARCHAR(128) | Decision engine | Human-readable trigger explanation (documents both stages). |
| `observed_mssd` | FLOAT | Decision engine | Within-person MSSD at decision time (see [§3](#3-derived--analysis-metrics)). |
| `randomization_probability` | FLOAT | Decision engine | Configured P(send \| eligible). Fixed at **0.50** per PI sign-off 2026-07-06. |
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

### 2.9 `app_engagementlog`

**Production table:** `app_engagementlog` (see [`production_schema.md`](./production_schema.md)).

User interactions with a delivered JITAI prompt.

| Name | Type | Source stream | Meaning |
|------|------|---------------|---------|
| `id` | INT (PK) | App telemetry | Primary key. |
| `user_id` | INT (FK → `user.user_id`) | App telemetry | Participant. |
| `jitai_log_id` | INT (FK → `jitai_log.id`) | App telemetry | The prompt interacted with. |
| `event_type` | VARCHAR(64) | App telemetry | Interaction type: `prompt_opened`, `prompt_acted`, `prompt_dismissed`. |
| `occurred_at` | DATETIME | App telemetry | When the interaction happened (device clock). |
| `recorded_at` | DATETIME | App telemetry | When the event was persisted server-side. |

### 2.10 `app_phonetelemetry`

**Production table:** `app_phonetelemetry` (see [`production_schema.md`](./production_schema.md)).

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

### 2.11 `hair_sample`

**Production table:** none — planned, not in [`production_schema.md`](./production_schema.md).

One row per hair specimen collected during the enrollment visit (optional Tier 2 biomarker sub-study). Concentrations reflect cumulative steroid exposure over approximately the prior 1–3 months based on hair growth rate (~1 cm/month).

> **Planned table** — not yet in production schema as of 2026-08-20. No Django migration exists.

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

### 2.12 `hair_hygiene_covariates`

**Production table:** none — planned, not in [`production_schema.md`](./production_schema.md).

One row per participant capturing biological covariates required to interpret steroid assay concentrations. Collected via Qualtrics at the baseline/enrollment visit.

> **Planned table** — not yet in production schema as of 2026-08-20. No Django migration exists.

| Name | Type | Source stream | Meaning |
|------|------|---------------|---------|
| `user_id` | INT (PK/FK → `user.user_id`) | Qualtrics / Baseline | Participant ID (one-to-one with `user`). |
| `chemical_treatment` | BOOLEAN | Qualtrics / Baseline | History of hair coloring, bleaching, or other chemical treatments that can alter steroid concentrations. |
| `wash_frequency_per_wk` | INT | Qualtrics / Baseline | Self-reported weekly hair wash frequency. |
| `hormonal_contraception` | BOOLEAN | Qualtrics / Baseline | Current use of hormonal contraceptives (affects cortisol and testosterone metabolism). |
| `steroid_medications` | BOOLEAN | Qualtrics / Baseline | Current use of synthetic glucocorticoid or androgen medications. |

### 2.13 `app_checkinreminder`

**Production table:** `app_checkinreminder` (see [`production_schema.md`](./production_schema.md)).

One row per **"time to check in" reminder push** sent to a participant. Distinct from `ema`,
which only gets a row once the participant actually submits a check-in — `checkin_reminder`
records that a nudge was *sent*, so analysts can separate "prompted but did not respond" from
"never prompted." Generated by the `send_checkin_reminders` Celery task
(`backend/app/tasks.py:192`): it fires only within the participant-local window (09:00–21:00),
skips participants who currently have an active JITAI prompt, enforces a 120-minute cooldown
between reminders, and stops once the participant reaches the daily check-in cap.

| Name | Type | Source stream | Meaning |
|------|------|---------------|---------|
| `id` | INT (PK) | Backend / scheduler | Primary key. |
| `user_id` | INT (FK → `user.user_id`) | Backend / scheduler | Participant who received the reminder. |
| `sent_at` | DATETIME | Backend / scheduler | When the reminder push was sent (`auto_now_add`). |
| `daily_count_at_send` | SMALLINT | Backend / scheduler | Number of EMA check-ins the participant had **already submitted that participant-local day** at the moment the reminder fired (from `_today_ema_count`). `0` = had not yet checked in today. **Not** a count of reminders. |

> **Semantics note:** `daily_count_at_send` increments only when a new `ema` row is recorded, so a
> run of reminders on the same day sharing the same value means the participant had still not
> checked in between those nudges. Use this to reconstruct how many reminders preceded each
> check-in — see §3 *Reminder-to-check-in latency* and *Reminders-to-check-in*.

### 2.14 Relationships (foreign keys)

- `wearable_device.user_id` → `user.user_id` (one-to-one)
- `heart_rate_sample.user_id` → `user.user_id` (many-to-one)
- `stress_sample.user_id` → `user.user_id` (many-to-one)
- `ema.user_id` → `user.user_id` (many-to-one)
- `ema.source_jitai_log_id` → `jitai_log.id` (many-to-one, nullable)
- `ema_item_response.ema_id` → `ema.id` (many-to-one); UNIQUE on `(ema_id, sub_item_id)`
- `jitai_log.user_id` → `user.user_id` (many-to-one)
- `jitai_log.ema_id` → `ema.id` (many-to-one)
- `phone_telemetry.user_id` → `user.user_id` (many-to-one)
- `engagement_log.user_id` → `user.user_id` (many-to-one)
- `engagement_log.jitai_log_id` → `jitai_log.id` (many-to-one)
- `checkin_reminder.user_id` → `user.user_id` (many-to-one)
- `hair_sample.user_id` → `user.user_id` (many-to-one)
- `hair_hygiene_covariates.user_id` → `user.user_id` (one-to-one)

---

## 3. Derived / analysis metrics

Computed offline (source stream = **Derived**) from the tables above. Formulas
follow `JITAI-analysis-plan.md` and `syntheticData/decision/`.

| Metric | Type | Definition / formula | Notes & benchmarks |
|--------|------|----------------------|--------------------|
| **Within-person MSSD** (`observed_mssd`) | FLOAT | Mean of squared successive differences: `mean((x_t − x_{t-1})²)` over **consecutive answered** EMA items. | The core JITAI trigger signal. Input is `ema_item_response.value_numeric` where `item_id = 'B1'` (energy) or `'B2'` (stress). Missing EMAs suppress the difference for both the missing observation and the immediately following prompt. |
| **Expected MSSD** | FLOAT | `2·σ²·(1−ρ)` from latent AR(1) parameters. | Ground-truth benchmark used to validate recovery (`synthetic_generator.py`). |
| **AR(1) ρ̂** (autocorrelation) | FLOAT | Lag-1 autocorrelation over consecutive answered EMA pairs. | Recovery **degrades below ~80%** response rate -- an analytic requirement, distinct from the 75% feasibility benchmark. |
| **AR(1) σ̂** (residual SD) | FLOAT | Sample SD of demeaned answered EMA. | More robust than ρ̂ across response rates. |
| **Response latency** | INT (min) | `responded_at − sent_at`. | Must be ≤ 60 min to count in-window. |
| **Reminder-to-check-in latency** | INT (min) | For each `checkin_reminder`, `MIN(ema.sent_at) − checkin_reminder.sent_at` over the same participant's `ema` rows submitted after that reminder and before the next reminder (or end of participant-local day); NULL if no check-in followed. | "Prompt response time" for nudges — how quickly a reminder converts to a check-in. Distinct from **Response latency** (which times an opened EMA prompt → submit). Long or NULL latencies flag ineffective reminders. |
| **Reminders-to-check-in** | INT | Per participant per participant-local day, the count of `checkin_reminder` rows sent before the next `ema` submission — i.e. the run of reminders sharing the same `daily_count_at_send` up to the check-in that increments it. | Diagnoses how many nudges it takes to get a response. High counts (or reaching the daily cap with many reminders but few check-ins) flag disengagement or reminder fatigue. |
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

The synthetic module (`syntheticData/`) produces the DataFrames that drive offline analysis
and MSSD validation. The production-aligned cohort is generated by
[`react_cohort.py`](../backend/syntheticData/react_cohort.py) and returns the `app_*` frames
documented in §2. The tables below document the **legacy** MSSD validation harness
[`synthetic_generator.py`](../backend/syntheticData/synthetic_generator.py) (`generate_cohort` /
`generate_HR`), whose ground-truth columns (`true_sigma`, `true_rho`, `true_expected_mssd`) an
analyst encounters in validation outputs.

**EMA frame** (`generate_cohort`) : one row per prompt per user

| Name | Type | Source stream | Meaning |
|------|------|---------------|---------|
| `user_id` | str (UUID) | Derived | Synthetic user identifier (shared across frames). |
| `timestamp` | datetime | Derived | Prompt time (1-min freq from 2026-06-01). |
| `ema` | float | Derived | Likert response — **legacy 1–5** validation frame (`synthetic_generator.py`); the current `react_cohort.py` seed uses 1–7. NaN if missed. |
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
| `user_threshold` | float | Derived | Within-person 80th-pct threshold (expanding, shifted). |
| `send_prompt` | bool | Derived | Decision outcome (eligibility gate only; coin-flip randomization is applied in the Celery task layer). |
| `decision_reason` | str | Derived | Rule that produced the decision (e.g. `prompt sent`, `below within-person threshold`, `cooldown active`, `daily cap reached`). |
| `recovered_sigma` / `recovered_rho` | float | Derived | Per-user σ̂ / ρ̂ recovered from the realised EMA series. |
| `sigma_recovery_pct` / `rho_recovery_pct` | float | Derived | `100 · recovered / true` (100% = perfect recovery). |

---

## Appendix : Schema reconciliation & known gaps

**Reconciliation status (verified 2026-08-27):** production now matches the REACT analysis
schema documented above. Migrations `0021`–`0041` (Labfront rename, MSSD/decision fields,
engagement/phone telemetry, EMA item responses, event day, check-in reminder) have been applied
and dropped the legacy Fitbit/HealthyGator fields. The authoritative source is the live
production dump in [`analysis-resources/production_schema.md`](./production_schema.md); the
`app.models` classes in `backend/app/models.py` match it, and `Resources/react_schema.csv`
remains a secondary reference. The earlier "divergences" below are **resolved**:

| Item | Earlier (pre-migration) claim | Current production state |
|------|-------------------------------|--------------------------|
| `stress_sample` | No `StressSample` model/migration | `StressSample` model + `app_stresssample` table exist |
| `heart_rate_sample` | `HeartRateSample.device` FK + `zone` | `user_id` FK + `source` column (no device/zone), matches this doc |
| `wearable_device` | Fitbit-oriented (`fitbit_device_id`, `device_name`) | Labfront-oriented (`labfront_participant_id`); legacy device fields removed in the `0025` overhaul, and the participant-id field renamed to `labfront_participant_id` in `0030` |
| `ema` | `timestamp`, `physical_activity`, `weight_lbs`, `notes` | Prompt lifecycle (`prompt_id`, `sent_at`, `status`, `ema_type`, `expires_at`); mood/stress/energy **1–7** |
| `user.is_enrolled`, `enrolled_at` | Not present; Fitbit-token/height/goal fields | Both present; legacy Fitbit/height/goal fields stripped (`0031`) |
| `jitai_log` | Simpler notification audit (~8 cols) | Full decision + delivery lifecycle, **27 columns** |
| `phone_telemetry`, `engagement_log`, `ema_item_response`, `event_day` | No production models | All exist as models + tables (`0029`, `0038`, `0039`, `0040`) |

**Known gaps / follow-ups:**

1. **`hair_sample` / `hair_hygiene_covariates` (§2.11–§2.12)** are analysis-plan tables that are
   **not** present in the production dump (no `app_hairsample*`). These remain the one genuine
   schema gap and are sourced/joined outside the Django backend.
2. **EMA item scale is settled at 1–7.** Production `models.py` enforces it
   (`MinValueValidator(1)`, `MaxValueValidator(7)`) and the current generator
   `syntheticData/react_cohort.py` emits 1–7 to match. The 1–5 scale was the retired
   `synthetic_generator.py` only; no open question remains.
3. **Wearable source is Labfront (Garmin Venu 3).** There is no Fitabase or Fitbit integration:
   `HeartRateSample.source` / `StressSample.source` default to `garmin_labfront` in the live
   models. The only residual legacy artifact is the **cosmetic index names**
   `app_wearabledevice_fitabase_participant_id_*` — the column itself is `labfront_participant_id`
   (see [`production_schema.md`](./production_schema.md)).
