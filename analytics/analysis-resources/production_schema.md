# Production Schema — `healthygatorsportfan` (Heroku Postgres)

Reference for the live production database, captured via `\dt` / `\di` / `\d <table>`.
This reflects the **actual deployed schema**, which is the source of truth for data analysis and
differs in places from the models documented in `CLAUDE.md` (added EMA/JITAI columns; legacy
`fitabase_*` index names on `app_wearabledevice`).

## Tables

The **Data dictionary** column maps each production table to its logical name in
[`data-dictionary.md`](./data-dictionary.md) (§2.x). Verified against the per-table structures below.

| Schema | Name | Data dictionary | Type | Owner |
|---|---|---|---|---|
| public | app_checkinreminder | `checkin_reminder` (§2.13) | table | ufa8u8gt63l2t2 |
| public | app_ema | `ema` (§2.2) | table | ufa8u8gt63l2t2 |
| public | app_emaitemresponse | `ema_item_response` (§2.3) | table | ufa8u8gt63l2t2 |
| public | app_engagementlog | `engagement_log` (§2.9) | table | ufa8u8gt63l2t2 |
| public | app_eventday | `event_day` (§2.4) | table | ufa8u8gt63l2t2 |
| public | app_heartratesample | `heart_rate_sample` (§2.5) | table | ufa8u8gt63l2t2 |
| public | app_hrvsample | — (new; 5-minute RMSSD) | table | ufa8u8gt63l2t2 |
| public | app_jitailog | `jitai_log` (§2.8) | table | ufa8u8gt63l2t2 |
| public | app_phonetelemetry | `phone_telemetry` (§2.10) | table | ufa8u8gt63l2t2 |
| public | app_stresssample | `stress_sample` (§2.6) | table | ufa8u8gt63l2t2 |
| public | app_user | `user` (§2.1) | table | ufa8u8gt63l2t2 |
| public | app_wearabledevice | `wearable_device` (§2.7) | table | ufa8u8gt63l2t2 |
| public | dashboard_alert | — (monitoring) | table | ufa8u8gt63l2t2 |
| public | dashboard_metricscohort | — (monitoring) | table | ufa8u8gt63l2t2 |
| public | dashboard_metricsdaily | — (monitoring) | table | ufa8u8gt63l2t2 |
| public | dashboard_metricsparticipant | — (monitoring) | table | ufa8u8gt63l2t2 |
| public | auth_group | — (framework) | table | ufa8u8gt63l2t2 |
| public | auth_group_permissions | — (framework) | table | ufa8u8gt63l2t2 |
| public | auth_permission | — (framework) | table | ufa8u8gt63l2t2 |
| public | auth_user | — (framework) | table | ufa8u8gt63l2t2 |
| public | auth_user_groups | — (framework) | table | ufa8u8gt63l2t2 |
| public | auth_user_user_permissions | — (framework) | table | ufa8u8gt63l2t2 |
| public | django_admin_log | — (framework) | table | ufa8u8gt63l2t2 |
| public | django_content_type | — (framework) | table | ufa8u8gt63l2t2 |
| public | django_migrations | — (framework) | table | ufa8u8gt63l2t2 |
| public | django_session | — (framework) | table | ufa8u8gt63l2t2 |

(25 rows)

The four `dashboard_*` tables are **derived**, not collected. They are recomputed from the
`app_*` tables every 10 minutes and can be dropped and rebuilt at any time with
`manage.py recompute_metrics --all`. They belong to the monitoring app, not the study
schema, and carry no data of their own; see [Monitoring tables](#monitoring-tables).

**Data-dictionary tables with no production counterpart:** `hair_sample` (§2.11) and
`hair_hygiene_covariates` (§2.12) are documented in the analysis schema but are **not** present in
production (sourced/joined outside the Django backend).

---

## Application Tables

### app_user — data dictionary: `user` (§2.1)

| Column | Type | Nullable | Default |
|---|---|---|---|
| user_id | integer | not null | identity |
| email | varchar(254) | not null | |
| birthdate | date | not null | |
| gender | varchar(10) | not null | |
| password | varchar(128) | | |
| first_name | varchar(100) | not null | |
| last_name | varchar(100) | not null | |
| push_token | varchar(128) | | |
| is_enrolled | boolean | not null | |
| enrolled_at | timestamptz | | |

**PK:** user_id &nbsp;·&nbsp; **Unique:** email &nbsp;·&nbsp;
**Referenced by:** all `app_*` tables via `user_id`.

### app_ema — data dictionary: `ema` (§2.2)

| Column | Type | Nullable | Default |
|---|---|---|---|
| id | bigint | not null | identity |
| prompt_id | varchar(64) | not null | |
| sent_at | timestamptz | not null | |
| responded_at | timestamptz | | |
| status | varchar(16) | not null | |
| mood | smallint | | |
| stress | smallint | | |
| energy | smallint | | |
| user_id | integer | not null | |
| ema_type | varchar(32) | not null | |
| expires_at | timestamptz | | |
| outcome_window_end | timestamptz | | |
| outcome_window_start | timestamptz | | |
| source_jitai_log_id | bigint | | |
| served_sub_item_ids | jsonb | | |

**PK:** id &nbsp;·&nbsp; **Indexes:** user_id, source_jitai_log_id &nbsp;·&nbsp;
**Checks:** mood/stress/energy >= 0
**FKs:** user_id → app_user(user_id); source_jitai_log_id → app_jitailog(id)
**Referenced by:** app_emaitemresponse(ema_id), app_jitailog(ema_id)

`served_sub_item_ids` (migration `0044`) records which sub-items this check-in actually put on
screen, after the B4–B7 rotation and the `schedule_condition` filter. Without it, "how many
questions was this participant asked" is unrecoverable and item completeness can only be
inferred from what they happened to answer, which cannot see an item skipped entirely. NULL on
rows written before the field existed. The mobile client may echo the list back from
`/ema/next/`; when it does not, the server recomputes the same set at submit time.

### app_emaitemresponse — data dictionary: `ema_item_response` (§2.3)

| Column | Type | Nullable | Default |
|---|---|---|---|
| id | bigint | not null | identity |
| item_id | varchar(8) | not null | |
| ema_id | bigint | not null | |
| sub_item_id | varchar(32) | not null | |
| response_type | varchar(16) | not null | |
| value_numeric | integer | | |
| value_choice | varchar(64) | | |
| value_choices | jsonb | | |

**PK:** id &nbsp;·&nbsp; **Unique:** (ema_id, sub_item_id) &nbsp;·&nbsp; **Index:** ema_id
**FKs:** ema_id → app_ema(id)

### app_jitailog — data dictionary: `jitai_log` (§2.8)

| Column | Type | Nullable | Default |
|---|---|---|---|
| id | bigint | not null | identity |
| prompt_id | varchar(64) | not null | |
| triggered_at | timestamptz | not null | |
| trigger_reason | varchar(128) | not null | |
| hr_at_trigger | smallint | | |
| stress_at_trigger | smallint | | |
| status | varchar(16) | not null | |
| user_id | integer | not null | |
| ema_id | bigint | | |
| observed_mssd | double precision | | |
| send_prompt | boolean | not null | |
| decision_point_id | varchar(64) | | |
| randomization_draw | double precision | | |
| randomization_probability | double precision | | |
| eligible_prompt_ids | jsonb | | |
| ema_energy | smallint | | |
| ema_mood | smallint | | |
| ema_stress | smallint | | |
| trigger_signal | varchar(32) | | |
| decision_made_at | timestamptz | not null | |
| delivery_error | text | not null | |
| delivery_status | varchar(32) | not null | |
| device_received_at | timestamptz | | |
| push_sent_at | timestamptz | | |
| receipt_app_state | varchar(32) | not null | |
| receipt_platform | varchar(16) | not null | |
| receipt_reported_at | timestamptz | | |
| message_arm | varchar(16) | | |
| arm_randomization_probability | double precision | | |
| arm_randomization_draw | double precision | | |
| evaluated_items | jsonb | | |
| matched_categories | jsonb | | |
| category_drawn | varchar(64) | | |
| fallback_reason | varchar(128) | not null | |
| receipt_event_id | varchar(64) | | |
| threshold_at_decision | double precision | | |
| threshold_source | varchar(16) | not null | |
| rmssd_at_trigger | double precision | | |
| rmssd_baseline_at_trigger | double precision | | |
| hrv_class_at_trigger | varchar(16) | not null | |

**PK:** id &nbsp;·&nbsp; **Unique:** decision_point_id &nbsp;·&nbsp;
**Indexes:** decision_made_at, delivery_status, device_received_at, ema_id, push_sent_at, receipt_reported_at, user_id
**Checks:** ema_energy/ema_mood/ema_stress/hr_at_trigger/stress_at_trigger >= 0
**FKs:** user_id → app_user(user_id); ema_id → app_ema(id)
**Referenced by:** app_ema(source_jitai_log_id), app_engagementlog(jitai_log_id)

The last columns are newer than the "~27 columns" figure quoted elsewhere; the table now
has 40. Migration `0042` added the **second-stage randomization** (`message_arm`,
`arm_randomization_probability`, `arm_randomization_draw`) — a 0.5/0.5 coping-versus-active-control
draw taken only when the first-stage send decision succeeded, logged separately so the two
effects can be analysed independently. Migration `0043` added the **routing audit**
(`evaluated_items`, `matched_categories`, `category_drawn`, `fallback_reason`), recorded at every
decision point regardless of arm. In `evaluated_items`, a null value means the sub-item was not
part of that check-in's rotation, **not** that it was checked and found below threshold. Migration
`0044` added `receipt_event_id`, `0046` the **threshold audit** (`threshold_at_decision`,
`threshold_source`), and `0047` the **HRV annotation** (`rmssd_at_trigger`,
`rmssd_baseline_at_trigger`, `hrv_class_at_trigger`).

The HRV columns are **recorded, never consulted**: `_evaluate_user` writes what
`attach_rmssd_series_to_decisions` found at the decision point, and `send_prompt` stays a pure
MSSD decision because the ratio cutoffs behind `hrv_class_at_trigger` have no PI sign-off. A
blank `hrv_class_at_trigger` with a null `rmssd_at_trigger` means no HRV sample existed within
30 minutes before the decision — which is every row today, since nothing writes `app_hrvsample`
yet.

### app_engagementlog — data dictionary: `engagement_log` (§2.9)

| Column | Type | Nullable | Default |
|---|---|---|---|
| id | bigint | not null | identity |
| event_type | varchar(64) | not null | |
| occurred_at | timestamptz | not null | |
| recorded_at | timestamptz | not null | |
| jitai_log_id | bigint | | |
| user_id | integer | not null | |

**PK:** id &nbsp;·&nbsp; **Indexes:** (user_id, occurred_at), jitai_log_id, occurred_at, user_id
**FKs:** user_id → app_user(user_id); jitai_log_id → app_jitailog(id)

### app_heartratesample — data dictionary: `heart_rate_sample` (§2.5)

| Column | Type | Nullable | Default |
|---|---|---|---|
| id | bigint | not null | identity |
| timestamp | timestamptz | not null | |
| bpm | smallint | not null | |
| source | varchar(32) | not null | |
| user_id | integer | not null | |

**PK:** id &nbsp;·&nbsp; **Indexes:** (user_id, timestamp), timestamp, user_id &nbsp;·&nbsp;
**Checks:** bpm >= 0 &nbsp;·&nbsp; **FKs:** user_id → app_user(user_id)

### app_stresssample — data dictionary: `stress_sample` (§2.6)

| Column | Type | Nullable | Default |
|---|---|---|---|
| id | bigint | not null | identity |
| timestamp | timestamptz | not null | |
| stress_score | smallint | not null | |
| source | varchar(32) | not null | |
| user_id | integer | not null | |

**PK:** id &nbsp;·&nbsp; **Indexes:** (user_id, timestamp), timestamp, user_id &nbsp;·&nbsp;
**Checks:** stress_score >= 0 &nbsp;·&nbsp; **FKs:** user_id → app_user(user_id)

### app_hrvsample — data dictionary: no logical name yet (5-minute RMSSD)

| Column | Type | Nullable | Default |
|---|---|---|---|
| id | bigint | not null | identity |
| timestamp | timestamptz | not null | |
| rmssd_ms | double precision | not null | |
| beat_count | smallint | not null | 0 |
| source | varchar(32) | not null | |
| user_id | integer | not null | |

**PK:** id &nbsp;·&nbsp; **Indexes:** (user_id, timestamp), timestamp, user_id &nbsp;·&nbsp;
**Checks:** beat_count >= 0 &nbsp;·&nbsp; **FKs:** user_id → app_user(user_id)

The **derived** 5-minute RMSSD series, not raw beat-to-beat intervals. BBI arrives at roughly one
row per beat — ~100k rows per participant per day, tens of millions a day at n=300 — so ingestion
reduces it with `decision_engine.compute_rmssd_5min_series` and stores 288 rows per participant
per day instead. `timestamp` is the **interval start**, which is why the decision annotation reads
only samples stamped strictly before a decision point: a row stamped at the decision moment
summarizes the five minutes after it.

**Nothing writes this table today.** The intended writer is `/telemetry/ingest/` (`hrv_samples`),
fed by a Labfront BBI poll that does not exist — `ingest_wearable_data` is still a stub, and
beat-to-beat collection is an open IRB and Labfront-contract question. The monitoring layer
reports that as unmeasurable through one cohort alert (`no_hrv_data`), never as a per-participant
failure and never as zero.

### app_phonetelemetry — data dictionary: `phone_telemetry` (§2.10)

| Column | Type | Nullable | Default |
|---|---|---|---|
| id | bigint | not null | identity |
| session_id | varchar(64) | not null | |
| event_type | varchar(64) | not null | |
| occurred_at | timestamptz | not null | |
| recorded_at | timestamptz | not null | |
| screen_name | varchar(64) | | |
| latency_ms | integer | | |
| metadata | jsonb | | |
| user_id | integer | not null | |

**PK:** id &nbsp;·&nbsp; **Indexes:** (user_id, occurred_at), occurred_at, user_id
**FKs:** user_id → app_user(user_id)

### app_wearabledevice — data dictionary: `wearable_device` (§2.7)

| Column | Type | Nullable | Default |
|---|---|---|---|
| id | bigint | not null | identity |
| labfront_participant_id | varchar(64) | not null | |
| is_active | boolean | not null | |
| last_synced_at | timestamptz | | |
| user_id | integer | not null | |

**PK:** id &nbsp;·&nbsp; **Unique:** labfront_participant_id, user_id (one device per user)
**FKs:** user_id → app_user(user_id)
Note: unique/index names on this table are still prefixed `app_wearabledevice_fitabase_participant_id_*` (legacy naming); the column itself is `labfront_participant_id`.

### app_checkinreminder — data dictionary: `checkin_reminder` (§2.13)

| Column | Type | Nullable | Default |
|---|---|---|---|
| id | bigint | not null | identity |
| sent_at | timestamptz | not null | |
| daily_count_at_send | smallint | not null | |
| user_id | integer | not null | |

**PK:** id &nbsp;·&nbsp; **Index:** user_id &nbsp;·&nbsp; **Checks:** daily_count_at_send >= 0
**FKs:** user_id → app_user(user_id)

### app_eventday — data dictionary: `event_day` (§2.4)

| Column | Type | Nullable | Default |
|---|---|---|---|
| id | bigint | not null | identity |
| date | date | not null | |
| sport | varchar(64) | not null | |
| description | varchar(128) | not null | |

**PK:** id &nbsp;·&nbsp; **Unique:** date

---

## Monitoring tables

Derived tables owned by the `dashboard` app, written by
`dashboard.tasks.recompute_monitoring_metrics` every 10 minutes and readable through
`/api/monitor/*`. They hold no collected data: everything in them is recomputed from the
`app_*` tables above, so they are safe to drop and rebuild. Column definitions live in
`backend/dashboard/data/daily.py`, `participant.py` and `cohort.py`; the study constants they use are
all in `backend/dashboard/data/config.py`.

**Structural null versus zero.** These tables never collapse the two. A participant-day
outside the active range — before enrolment, past the last study day, or after withdrawal —
either has no row or has `is_active_day = false` with every metric NULL. A day inside the
range with no activity carries zeros. Every metric column is therefore nullable, including
ones that look like they could never be missing. Any query over them must preserve that
distinction.

### dashboard_metricsdaily

One row per participant per study day. 31 metric columns in six groups: EMA volume, check-in
slots, quality, MRT integrity, engagement, and wear/pipeline.

| Column | Type | Nullable | Notes |
|---|---|---|---|
| id | bigint | not null | identity |
| user_id | integer | not null | → app_user(user_id) |
| study_day | smallint | not null | 0-indexed; study_day 0 is protocol "Day 1" |
| local_date | date | not null | America/New_York |
| is_run_in | boolean | not null | study_day < 7 |
| is_active_day | boolean | not null | false ⇒ every metric below is NULL |
| computed_at | timestamptz | not null | |
| item_bank_version | varchar(16) | not null | which frozen EMA_ITEM_BANK scored completeness |
| ema_scheduled_n, ema_jitai_n, ema_post_prompt_n | smallint | | completed EMAs by type |
| slots_expected, slots_covered, slots_reminded_uncovered, slots_silent | smallint | | covered and reminded_uncovered are disjoint |
| reminders_sent | smallint | | |
| reminders_per_checkin_median | double precision | | 0 or 1 by construction, see the note below |
| completeness_mean | double precision | | answered over askable, 0–1 |
| ema_missing_b1b2_n | smallint | | EMAs missing a signal sub-item, which suppress MSSD |
| decision_points_n, eligible_n, sent_n, delivered_n | smallint | | eligible = `randomization_draw` non-null |
| cap_hit | boolean | | any `trigger_reason = 'daily cap reached'` |
| min_gap_min | integer | | minutes between the closest two sent prompts |
| cooldown_violations_n | smallint | | sent prompts closer than 60 min |
| runin_violation_n | smallint | | prompts sent during run-in; see Known engine defects |
| prompt_opened_n, prompt_acted_n, prompt_dismissed_n, outcome_captured_n | smallint | | |
| wear_valid_pct, wear_gap_pct | double precision | | fractions of the 840-minute waking window |
| gaps_gt2h_n, hr_minutes_valid | smallint | | |
| max_gap_min | integer | | |
| last_sync_age_h_eod | double precision | | |
| clock_skew_p95_ms | integer | | **signed**: a device clock ahead of the server is negative |
| delivery_failures_n | smallint | | |

**PK:** id &nbsp;·&nbsp; **Unique:** (user_id, study_day) &nbsp;·&nbsp;
**Indexes:** (user_id, local_date), user_id, local_date &nbsp;·&nbsp;
**Checks:** every `smallint` count >= 0 &nbsp;·&nbsp; **FKs:** user_id → app_user(user_id)

### dashboard_metricsparticipant

One row per participant, overwritten each run.

| Column | Type | Nullable |
|---|---|---|
| id | bigint | not null |
| user_id | integer | not null (unique, one per participant) |
| computed_at | timestamptz | not null |
| enrolled_at | timestamptz | |
| day1_date | date | |
| study_day_now | smallint | |
| phase | varchar(16) | not null |
| is_enrolled_snapshot | boolean | not null |
| first_seen_not_enrolled_at | timestamptz | |
| last_ema_at, last_sync_at | timestamptz | |
| active_retention | boolean | |
| risk_score | smallint | |
| slot_coverage_rate / _num / _den | double precision, integer, integer | |
| prompt_response_rate / _num / _den | double precision, integer, integer | |
| wear_rate / _num / _den | double precision, integer, integer | |

**PK:** id &nbsp;·&nbsp; **Unique:** user_id &nbsp;·&nbsp; **Checks:** risk_score >= 0 &nbsp;·&nbsp;
**FKs:** user_id → app_user(user_id)

`phase` is one of `pre_enrollment`, `run_in`, `mrt`, `complete`, `withdrawn`. Withdrawal wins
over completion, so a participant who left on day 10 still reads `withdrawn` after day 34.

`first_seen_not_enrolled_at` is a **withdrawal proxy**, not a recorded event: the first
recompute that observed `is_enrolled = false` for a participant who had been enrolled.
Resolution equals the polling interval, and unenrolling someone who already reached the last
study day is treated as study close-out rather than a dropout. Withdrawals predating the
monitoring layer are recovered from `django_admin_log` by
`manage.py backfill_withdrawals`, which only ever moves a timestamp earlier.

Each cumulative rate is stored with its numerator and denominator, because a rate over a thin
denominator is suppressed for display and the raw counts have to survive that. The
`prompt_response` denominator is prompts sent **less documented delivery failures**, which are
excluded and reported separately per `JITAI-analysis-plan.md`; that is deliberately not the
same as counting only confirmed receipts.

### dashboard_metricscohort

One snapshot per compute run per phase filter (`all` / `phase1` / `phase2`). Pruned after 30
days.

| Column | Type | Nullable |
|---|---|---|
| id | bigint | not null |
| as_of | timestamptz | not null |
| phase_filter | varchar(16) | not null |
| n_participants, n_active | integer | not null |
| benchmarks | jsonb | |
| series_14d | jsonb | |
| decision_points_n, eligible_n, sent_n, delivered_n | integer | |
| cooldown_violations_n, runin_violations_n, cap_hit_days, delivery_failures_n | integer | |

**PK:** id &nbsp;·&nbsp; **Indexes:** (phase_filter, as_of desc), as_of

`benchmarks` holds one entry per feasibility benchmark, keyed by name, each with `value`,
`wilson_low`, `wilson_high`, `numerator`, `denominator`, `participants`, `target`,
`suppressed` and `measurable`. **A rate is only emitted once its denominator carries at least
10 participants and 30 units**; below that `value` is null, `suppressed` is true, and the raw
counts stand alone. Phase 1 (n=5) is therefore always suppressed. `wear` is scored per
participant-day against the coverage target rather than as a minutes ratio, because 840
minutes a day are not independent trials and a Wilson interval on them would be meaningless;
the minutes ratio rides along as `mean_coverage`.

The hair sub-study benchmark is **not** reported. It belongs to a later stage of the study and
has no production table.

### dashboard_alert

| Column | Type | Nullable |
|---|---|---|
| id | bigint | not null |
| user_id | integer | nullable — NULL means a cohort-level alert |
| rule_id | varchar(64) | not null |
| severity | varchar(16) | not null (`critical` / `warning`) |
| fired_at | timestamptz | not null |
| resolved_at | timestamptz | |
| payload | jsonb | |

**PK:** id &nbsp;·&nbsp; **Indexes:** rule_id, fired_at, resolved_at, user_id &nbsp;·&nbsp;
**FKs:** user_id → app_user(user_id)

**Partial unique constraints:** `uniq_open_alert_per_user_rule` on `(user_id, rule_id)` where
`resolved_at IS NULL`, plus `uniq_open_cohort_alert_per_rule` on `(rule_id)` where
`resolved_at IS NULL AND user_id IS NULL`. Two are needed because SQL treats NULL user_ids as
distinct, so a single constraint would let a cohort alert duplicate on every poll.

An open alert always describes a live condition: each run opens what is newly firing, escalates
severity in place, and resolves what has cleared. `fired_at` is never moved on escalation, so it
records when the incident started. Payloads hold anchors rather than elapsed time, so a poll
that changes nothing writes nothing.

---

## Known engine defects the monitoring layer surfaces

Recorded here because the metrics make them visible and an analyst reading the tables will hit
them.

1. **No run-in gate.** `evaluate_jitai_triggers` (`backend/app/tasks.py`) sends prompts from
   study day 0, although week 1 is meant to be a non-interventional baseline used only to
   establish each participant's within-person MSSD threshold. `runin_violation_n` counts the
   prompts this produces and a critical alert fires. Nothing in the pipeline prevents it yet.
2. **The daily cap is counted in UTC.** `decision_engine.apply_decision_rules` groups by
   `row["timestamp"].date()` on UTC-aware timestamps, while every other boundary in the system
   is America/New_York. The two disagree for prompts between 19:00 Eastern and midnight, so an
   evening prompt can belong to the next day for cap purposes. The monitoring layer scores
   Eastern days throughout and does not work around this.

---

## Relationships (analysis-relevant)

- Every `app_*` data table has a `user_id` FK → `app_user(user_id)`.
- `app_ema` ↔ `app_jitailog` are mutually linked:
  - `app_jitailog.ema_id` → `app_ema.id` (the EMA a decision references)
  - `app_ema.source_jitai_log_id` → `app_jitailog.id` (the decision that spawned the EMA)
- Child / detail tables:
  - `app_emaitemresponse.ema_id` → `app_ema.id` (per-item EMA responses; one EMA → many items)
  - `app_engagementlog.jitai_log_id` → `app_jitailog.id` (engagement events tied to a prompt)

---

## Django Framework Tables (not research data)

Standard Django auth / admin plumbing — included for completeness, not analysis:

| Table | Purpose |
|---|---|
| auth_user | Django admin/staff accounts (separate from `app_user` participants) |
| auth_group / auth_group_permissions | Permission groups (e.g. researcher_pi / researcher_ra) |
| auth_permission | Individual permissions |
| auth_user_groups / auth_user_user_permissions | Admin user ↔ group/permission M2M |
| django_admin_log | Admin action audit log |
| django_content_type | Model registry for permissions |
| django_migrations | Applied migration history |
| django_session | Session store |

Each also has a corresponding `*_id_seq` sequence. The `public` schema additionally exposes the
`pg_stat_statements` / `pg_stat_statements_info` views (owned by `rdsadmin`). Schemas present:
`public` (owner `ufa8u8gt63l2t2`) and `_heroku` (owner `heroku_admin`).
