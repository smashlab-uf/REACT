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
| public | app_jitailog | `jitai_log` (§2.8) | table | ufa8u8gt63l2t2 |
| public | app_phonetelemetry | `phone_telemetry` (§2.10) | table | ufa8u8gt63l2t2 |
| public | app_stresssample | `stress_sample` (§2.6) | table | ufa8u8gt63l2t2 |
| public | app_user | `user` (§2.1) | table | ufa8u8gt63l2t2 |
| public | app_wearabledevice | `wearable_device` (§2.7) | table | ufa8u8gt63l2t2 |
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

(21 rows)

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

**PK:** id &nbsp;·&nbsp; **Indexes:** user_id, source_jitai_log_id &nbsp;·&nbsp;
**Checks:** mood/stress/energy >= 0
**FKs:** user_id → app_user(user_id); source_jitai_log_id → app_jitailog(id)
**Referenced by:** app_emaitemresponse(ema_id), app_jitailog(ema_id)

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

**PK:** id &nbsp;·&nbsp; **Unique:** decision_point_id &nbsp;·&nbsp;
**Indexes:** decision_made_at, delivery_status, device_received_at, ema_id, push_sent_at, receipt_reported_at, user_id
**Checks:** ema_energy/ema_mood/ema_stress/hr_at_trigger/stress_at_trigger >= 0
**FKs:** user_id → app_user(user_id); ema_id → app_ema(id)
**Referenced by:** app_ema(source_jitai_log_id), app_engagementlog(jitai_log_id)

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
