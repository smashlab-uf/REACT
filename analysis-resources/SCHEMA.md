# REACT Database Schema

**Source of truth:** `backend/app/models.py` (migrations through `0039_eventday`)
**Database:** PostgreSQL (`react_db`) · **ORM:** Django REST Framework
**Cross-references:** [`data-dictionary.md`](data-dictionary.md) (field meanings & IRB notes) · [`../Resources/react_schema.csv`](../Resources/react_schema.csv) (machine-readable ERD export)

Django generates table names as `app_<lowercasemodel>`. All tables live in the `public` schema.

---

## Tables

`_heroku.                           app_phonetelemetry_id_seq          auth_user_id_seq
app_checkinreminder                app_stresssample                   auth_user_user_permissions
app_checkinreminder_id_seq         app_stresssample_id_seq            auth_user_user_permissions_id_seq
app_ema                            app_user                           django_admin_log
app_ema_id_seq                     app_user_user_id_seq               django_admin_log_id_seq
app_emaitemresponse                app_wearabledevice                 django_content_type
app_emaitemresponse_id_seq         app_wearabledevice_id_seq          django_content_type_id_seq
app_engagementlog                  auth_group                         django_migrations
app_engagementlog_id_seq           auth_group_id_seq                  django_migrations_id_seq
app_eventday                       auth_group_permissions             django_session
app_eventday_id_seq                auth_group_permissions_id_seq      information_schema.
app_heartratesample                auth_permission                    pg_stat_statements
app_heartratesample_id_seq         auth_permission_id_seq             pg_stat_statements_info
app_jitailog                       auth_user                          public.
app_jitailog_id_seq                auth_user_groups                   
app_phonetelemetry                 auth_user_groups_id_seq`

## Entity Relationship Map

```
app_user (1)
  ├──[1:1]──► app_wearabledevice
  ├──[1:N]──► app_heartratesample
  ├──[1:N]──► app_stresssample
  ├──[1:N]──► app_phonetelemetry
  ├──[1:N]──► app_engagementlog
  ├──[1:N]──► app_jitailog
  │                │
  │                ├──[1:N]──► app_engagementlog (jitai_log_id, nullable)
  │                └──[0:N]──► app_ema           (ema_id, nullable)  ◄─┐
  │                                                                      │ bi-directional
  └──[1:N]──► app_ema ──[1:N]──► app_emaitemresponse                   │
                  └──[0:1]──────────────────────────────────────────────┘
                             (source_jitai_log_id, nullable)

app_eventday  (standalone — no foreign keys)
```

> `EMA ↔ JITAILog` carry optional references in both directions.
> `JITAILog.ema` links the EMA that preceded the decision;
> `EMA.source_jitai_log` links the intervention that spawned a post-prompt outcome window.
> Neither side is required.

---

## Tables

### `User` → `app_user`

| Column | PostgreSQL type | Constraints | Notes |
|---|---|---|---|
| `user_id` | `SERIAL` | **PK** | Auto-increment. |
| `email` | `VARCHAR(254)` | UNIQUE NOT NULL | Login identifier. |
| `first_name` | `VARCHAR(100)` | NOT NULL default `''` | |
| `last_name` | `VARCHAR(100)` | NOT NULL default `''` | |
| `birthdate` | `DATE` | NOT NULL | |
| `gender` | `VARCHAR(10)` | NOT NULL | Choices: `male` / `female` / `other` |
| `password` | `VARCHAR(128)` | NULLABLE | Django-hashed (PBKDF2). NULL for test/synthetic accounts. |
| `push_token` | `VARCHAR(128)` | NULLABLE | Expo push notification token; refreshed on app launch. |
| `is_enrolled` | `BOOLEAN` | NOT NULL default `false` | Active study enrollment flag. |
| `enrolled_at` | `TIMESTAMPTZ` | NULLABLE | Day 1 anchor timestamp. |

---

### `WearableDevice` → `app_wearabledevice`

| Column | PostgreSQL type | Constraints | Notes |
|---|---|---|---|
| `id` | `SERIAL` | **PK** | |
| `user_id` | `INTEGER` | **FK** → `app_user.user_id` UNIQUE NOT NULL ON DELETE CASCADE | OneToOne. |
| `labfront_participant_id` | `VARCHAR(64)` | UNIQUE NOT NULL | Labfront platform ID; used to query Garmin data. |
| `is_active` | `BOOLEAN` | NOT NULL default `true` | |
| `last_synced_at` | `TIMESTAMPTZ` | NULLABLE | Most recent successful Labfront sync. |

---

### `HeartRateSample` → `app_heartratesample`

| Column | PostgreSQL type | Constraints | Notes |
|---|---|---|---|
| `id` | `SERIAL` | **PK** | |
| `user_id` | `INTEGER` | **FK** → `app_user.user_id` NOT NULL ON DELETE CASCADE | |
| `timestamp` | `TIMESTAMPTZ` | NOT NULL INDEX | Real cadence ~15 sec from Labfront; 2–5 min sync lag. |
| `bpm` | `SMALLINT` | NOT NULL CHECK (≥ 0) | `0` / NULL = inferred non-wear (no explicit wear flag). |
| `source` | `VARCHAR(32)` | NOT NULL default `'garmin_labfront'` | Provenance tag. |

**Indexes:** `(user_id, timestamp)` composite · `timestamp` single-column

---

### `StressSample` → `app_stresssample`

| Column | PostgreSQL type | Constraints | Notes |
|---|---|---|---|
| `id` | `SERIAL` | **PK** | |
| `user_id` | `INTEGER` | **FK** → `app_user.user_id` NOT NULL ON DELETE CASCADE | |
| `timestamp` | `TIMESTAMPTZ` | NOT NULL INDEX | Garmin 3-min epoch. |
| `stress_score` | `SMALLINT` | NOT NULL CHECK (≥ 0) | Garmin proprietary 0–100; black-box HRV-derived, **not** a function of HR. |
| `source` | `VARCHAR(32)` | NOT NULL default `'garmin_labfront'` | |

**Indexes:** `(user_id, timestamp)` composite · `timestamp` single-column

---

### `EMA` → `app_ema`

| Column | PostgreSQL type | Constraints | Notes |
|---|---|---|---|
| `id` | `SERIAL` | **PK** | |
| `user_id` | `INTEGER` | **FK** → `app_user.user_id` NOT NULL ON DELETE CASCADE | |
| `prompt_id` | `VARCHAR(64)` | NOT NULL | Template identifier (e.g. `prompt-mood-0`). |
| `sent_at` | `TIMESTAMPTZ` | NOT NULL auto_now_add | Push delivery time. |
| `responded_at` | `TIMESTAMPTZ` | NULLABLE | NULL if unanswered. |
| `status` | `VARCHAR(16)` | NOT NULL default `'pending'` | Choices: `pending` / `completed` / `expired` |
| `ema_type` | `VARCHAR(32)` | NOT NULL default `'scheduled_check_in'` | Choices: `scheduled_check_in` / `post_prompt` / `extra_check_in` |
| `source_jitai_log_id` | `INTEGER` | **FK** → `app_jitailog.id` NULLABLE ON DELETE SET NULL | Non-NULL only when `ema_type = 'post_prompt'`; links the JITAI that opened this outcome window. |
| `outcome_window_start` | `TIMESTAMPTZ` | NULLABLE | Start of 2-hour post-prompt outcome window. |
| `outcome_window_end` | `TIMESTAMPTZ` | NULLABLE | End of 2-hour post-prompt outcome window. |
| `expires_at` | `TIMESTAMPTZ` | NULLABLE | Hard expiry for response collection. |
| `mood` | `SMALLINT` | NULLABLE CHECK (1–7) | 1 = very low, 7 = very high. |
| `stress` | `SMALLINT` | NULLABLE CHECK (1–7) | 1 = very low, 7 = very high. |
| `energy` | `SMALLINT` | NULLABLE CHECK (1–7) | 1 = very low, 7 = very high. |

**Ordering:** `-sent_at`

---

### `EMAItemResponse` → `app_emaitemresponse`

| Column | PostgreSQL type | Constraints | Notes |
|---|---|---|---|
| `id` | `SERIAL` | **PK** | |
| `ema_id` | `INTEGER` | **FK** → `app_ema.id` NOT NULL ON DELETE CASCADE | |
| `item_id` | `VARCHAR(8)` | NOT NULL | Block identifier: `B1` / `B2` / `B3` / `B4` / `B5` / `B6` / `B7` / `B8` |
| `sub_item_id` | `VARCHAR(32)` | NOT NULL | Specific sub-question within the block. Unique per EMA. |
| `response_type` | `VARCHAR(16)` | NOT NULL | Choices: `likert` / `single_choice` / `multi_choice` / `number` / `yes_no` |
| `value_numeric` | `INTEGER` | nullable | Likert or numeric response (1–7 for Likert items). |
| `value_choice` | `VARCHAR(64)` | nullable | Single-choice text response. |
| `value_choices` | `JSONB` | nullable | Multi-choice response array. |

**Constraints:** `UNIQUE (ema_id, sub_item_id)` — one response per sub-item per EMA.
**Ordering:** `sub_item_id`

JITAI trigger signal: the decision engine reads `value_numeric` from rows where `item_id = 'B1'` (energy) and `item_id = 'B2'` (stress) to compute MSSD.

---

### `EventDay` → `app_eventday`

Standalone lookup table for sporting-event days. No foreign keys.

| Column | PostgreSQL type | Constraints | Notes |
|---|---|---|---|
| `id` | `SERIAL` | **PK** | |
| `date` | `DATE` | UNIQUE NOT NULL | Calendar date of the event. |
| `sport` | `VARCHAR(64)` | NOT NULL default `''` | e.g. `football`, `basketball`. |
| `description` | `VARCHAR(128)` | NOT NULL default `''` | Free-label (e.g. `Home vs. LSU`). |

**Ordering:** `-date`

---

### `PhoneTelemetry` → `app_phonetelemetry`

| Column | PostgreSQL type | Constraints | Notes |
|---|---|---|---|
| `id` | `SERIAL` | **PK** | |
| `user_id` | `INTEGER` | **FK** → `app_user.user_id` NOT NULL ON DELETE CASCADE | |
| `session_id` | `VARCHAR(64)` | NOT NULL | App session identifier. |
| `event_type` | `VARCHAR(64)` | NOT NULL | Choices: `draft_started` / `draft_deleted` / `draft_submitted` / `session_start` / `session_end` |
| `occurred_at` | `TIMESTAMPTZ` | NOT NULL INDEX | Device clock. |
| `recorded_at` | `TIMESTAMPTZ` | NOT NULL auto_now_add | Server persistence time. |
| `screen_name` | `VARCHAR(64)` | NULLABLE | Screen where the event originated. |
| `latency_ms` | `INTEGER` | NULLABLE | Event/interaction latency. |
| `metadata` | `JSONB` | NULLABLE | Free-form payload; string values must be ≤ 50 chars (validated). |

**Indexes:** `(user_id, occurred_at)` composite · `occurred_at` single-column
**Ordering:** `-occurred_at`

---

### `JITAILog` → `app_jitailog`

One row per JITAI decision point. Captures the full decision pipeline: eligibility → randomization → push delivery lifecycle.

**Decision columns**

| Column | PostgreSQL type | Constraints | Notes |
|---|---|---|---|
| `id` | `SERIAL` | **PK** | |
| `user_id` | `INTEGER` | **FK** → `app_user.user_id` NOT NULL ON DELETE CASCADE | |
| `prompt_id` | `VARCHAR(64)` | NOT NULL | Message template ID (P001–P026, C001–C004). |
| `triggered_at` | `TIMESTAMPTZ` | NOT NULL auto_now_add | When Celery evaluated this decision point. |
| `decision_point_id` | `VARCHAR(64)` | UNIQUE NULLABLE | Idempotency key; Celery tasks use this to avoid duplicate decision rows. |
| `decision_made_at` | `TIMESTAMPTZ` | NOT NULL default `now()` INDEX | When the send/no-send decision was finalized. |
| `trigger_reason` | `VARCHAR(128)` | NOT NULL | e.g. `ema_completed+mssd_above_threshold`. |
| `trigger_signal` | `VARCHAR(32)` | NULLABLE | Primary signal that triggered evaluation (e.g. `mssd`). |
| `observed_mssd` | `DOUBLE PRECISION` | NULLABLE | Within-person MSSD at decision time. |
| `randomization_probability` | `DOUBLE PRECISION` | NULLABLE | Configured P(send \| eligible); currently fixed at **0.5** (PI sign-off 2026-07-06). |
| `randomization_draw` | `DOUBLE PRECISION` | NULLABLE | Uniform(0,1) draw; send if `< randomization_probability`. |
| `send_prompt` | `BOOLEAN` | NOT NULL default `true` | TRUE only if eligibility AND randomization both pass. |
| `eligible_prompt_ids` | `JSONB` | NULLABLE | Array of prompt IDs eligible at this decision point. |

**Trigger context (wearable + EMA snapshot at decision time)**

| Column | PostgreSQL type | Constraints | Notes |
|---|---|---|---|
| `hr_at_trigger` | `SMALLINT` | NULLABLE | HR (bpm); may lag 2–5 min from Labfront. |
| `stress_at_trigger` | `SMALLINT` | NULLABLE | Garmin stress score 0–100 at trigger. |
| `ema_id` | `INTEGER` | **FK** → `app_ema.id` NULLABLE ON DELETE SET NULL | EMA that preceded this decision. |
| `ema_mood` | `SMALLINT` | NULLABLE | Mood snapshot copied from linked EMA. |
| `ema_stress` | `SMALLINT` | NULLABLE | Stress snapshot from linked EMA. |
| `ema_energy` | `SMALLINT` | NULLABLE | Energy snapshot from linked EMA. |

**Delivery funnel**

| Column | PostgreSQL type | Constraints | Notes |
|---|---|---|---|
| `status` | `VARCHAR(16)` | NOT NULL default `'pending'` | Choices: `pending` / `delivered` / `opened` / `interacted` / `failed` / `not_sent` |
| `delivery_status` | `VARCHAR(32)` | NOT NULL default `'pending'` INDEX | Choices: `pending` / `not_sent` / `accepted_by_expo` / `received_on_device` / `failed` |
| `push_sent_at` | `TIMESTAMPTZ` | NULLABLE INDEX | When push was dispatched to Expo. |
| `device_received_at` | `TIMESTAMPTZ` | NULLABLE INDEX | When device acknowledged receipt. |
| `receipt_reported_at` | `TIMESTAMPTZ` | NULLABLE INDEX | When app reported receipt server-side. |
| `receipt_platform` | `VARCHAR(16)` | NOT NULL default `''` | `iOS` / `Android`. |
| `receipt_app_state` | `VARCHAR(32)` | NOT NULL default `''` | `foreground` / `background`. |
| `delivery_error` | `TEXT` | NOT NULL default `''` | Error detail if delivery failed. |

**Ordering:** `-triggered_at`

---

### `EngagementLog` → `app_engagementlog`

| Column | PostgreSQL type | Constraints | Notes |
|---|---|---|---|
| `id` | `SERIAL` | **PK** | |
| `user_id` | `INTEGER` | **FK** → `app_user.user_id` NOT NULL ON DELETE CASCADE | |
| `jitai_log_id` | `INTEGER` | **FK** → `app_jitailog.id` NULLABLE ON DELETE SET NULL | NULL if engagement event is not tied to a specific JITAI. |
| `event_type` | `VARCHAR(64)` | NOT NULL | Choices: `ema_opened` / `ema_dismissed` / `ema_completed` / `notification_tapped` / `notification_dismissed` |
| `occurred_at` | `TIMESTAMPTZ` | NOT NULL INDEX | Device clock. |
| `recorded_at` | `TIMESTAMPTZ` | NOT NULL auto_now_add | Server persistence time. |

**Indexes:** `(user_id, occurred_at)` composite · `occurred_at` single-column
**Ordering:** `-occurred_at`

---

## Enums & Choices (consolidated)

| Model | Field | Allowed values |
|---|---|---|
| `User` | `gender` | `male` · `female` · `other` |
| `EMA` | `status` | `pending` · `completed` · `expired` |
| `EMA` | `ema_type` | `scheduled_check_in` · `post_prompt` · `extra_check_in` |
| `EMAItemResponse` | `item_id` | `B1` · `B2` · `B3` · `B4` · `B5` · `B6` · `B7` · `B8` (block; `B1`=energy, `B2`=stress per decision engine) |
| `EMAItemResponse` | `response_type` | `likert` · `single_choice` · `multi_choice` · `number` · `yes_no` |
| `JITAILog` | `status` | `pending` · `delivered` · `opened` · `interacted` · `failed` · `not_sent` |
| `JITAILog` | `delivery_status` | `pending` · `not_sent` · `accepted_by_expo` · `received_on_device` · `failed` |
| `PhoneTelemetry` | `event_type` | `draft_started` · `draft_deleted` · `draft_submitted` · `session_start` · `session_end` |
| `EngagementLog` | `event_type` | `ema_opened` · `ema_dismissed` · `ema_completed` · `notification_tapped` · `notification_dismissed` |

---

## Planned Tables (not yet in production)

These two tables are specified in the updated IRB-01 protocol (`REACT Biomarker and Extended-Measures Study`) and documented in [`data-dictionary.md §2.9–2.10`](data-dictionary.md). **No Django migrations exist yet.** They support the optional Tier 2 hair steroid sub-study (Kertes Lab).

### `hair_sample` (planned)

| Column | Type | Notes |
|---|---|---|
| `sample_id` | `SERIAL` PK | |
| `user_id` | `INTEGER` FK → `app_user` | |
| `collected_at` | `TIMESTAMPTZ` | Enrollment visit. |
| `hair_length_cm` | `DOUBLE PRECISION` | Min ~1–3 cm. |
| `sample_mass_mg` | `DOUBLE PRECISION` | ~50 mg target. |
| `assay_method` | `VARCHAR(32)` | `ELISA` · `LC-MS/MS` |
| `cortisol_pg_mg` | `DOUBLE PRECISION` | Log-transform + winsorize before analysis. |
| `testosterone_pg_mg` | `DOUBLE PRECISION` | Log-transform + winsorize before analysis. |

### `hair_hygiene_covariates` (planned)

| Column | Type | Notes |
|---|---|---|
| `user_id` | `INTEGER` PK/FK → `app_user` (one-to-one) | |
| `chemical_treatment` | `BOOLEAN` | Coloring/bleaching history. |
| `wash_frequency_per_wk` | `INTEGER` | Self-reported. |
| `hormonal_contraception` | `BOOLEAN` | Affects steroid metabolism. |
| `steroid_medications` | `BOOLEAN` | Synthetic glucocorticoid or androgen use. |

---

## Notes

- **Django table naming:** `app_<lowercasemodel>` (e.g. `JITAILog` → `app_jitailog`).
- **Likert scale:** `mood`, `stress`, `energy` on `EMA` and `EMAItemResponse.value_numeric` are validated 1–7 in production. The synthetic data generator and validation harness (`mssd_validation.py`) use 1–5; construct-validity benchmarks should be re-run at the deployed 1–7 scale before the feasibility report.
- **JITAI trigger signal:** The decision engine reads `EMAItemResponse.value_numeric` where `item_id = 'B1'` (energy) and `item_id = 'B2'` (stress). Offline MSSD analysis in `analytics/scripts.py` must use the same B1/B2 source — not `EMA.mood` — to replicate trigger decisions.
- **IRB constraint — no free text:** notification body and EMA notes are never stored. Only `prompt_id` (a template reference) appears in `jitai_log`. See `data-dictionary.md` for full IRB notes.
- **Idempotency:** `JITAILog.decision_point_id` is UNIQUE; Celery tasks set this before writing to prevent duplicate decision rows on retry.
- **Non-wear inference:** `HeartRateSample.bpm = 0` or NULL is treated as inferred non-wear. Garmin exports carry no explicit `is_worn` flag.
