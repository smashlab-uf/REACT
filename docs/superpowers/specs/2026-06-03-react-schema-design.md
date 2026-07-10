# REACT Project — Database Schema Design

**Date:** 2026-06-03  
**Branch:** feat-Fitbit-auth  
**Stack:** Django / PostgreSQL / Celery + React Native / Expo

---

## Overview

The REACT project extends the existing REACT app with Fitbit OAuth2, wearable telemetry, EMA surveys, and JITAI-based interventions. The schema replaces `UserData` with `EMA` and `NotificationData` with `JITAILog`, and adds two new tables (`WearableDevice`, `HeartRateSample`, `ActivitySummary`).

Five logical models, six tables total.

---

## Models

### 1. User (extended — no new table)

Adds Fitbit OAuth2 token fields to the existing `User` model.

| Column | Type | Notes |
|---|---|---|
| fitbit_user_id | VARCHAR(64) | Fitbit's own user identifier |
| fitbit_access_token | TEXT | Store encrypted at rest |
| fitbit_refresh_token | TEXT | Store encrypted at rest |
| fitbit_token_expires | DATETIME | Refresh when past this time |

All existing fields (email, name, birthdate, gender, height, goals, password, push_token) are unchanged.

---

### 2. WearableDevice

Tracks each Fitbit device linked to a user. One user may have multiple devices over time (upgrades). `is_active` flags the current device without deleting history.

| Column | Type | Notes |
|---|---|---|
| device_id | INT PK | |
| user_id | INT FK → user | CASCADE on delete |
| fitbit_device_id | VARCHAR(64) | Fitbit's hardware identifier |
| device_type | VARCHAR(32) | e.g. "tracker", "scale" |
| device_name | VARCHAR(64) | e.g. "Charge 6" |
| last_synced_at | DATETIME | Nullable |
| is_active | BOOLEAN | Default True |
| created_at | DATETIME | Auto |

---

### 3. HeartRateSample

Per-minute intraday heart rate readings from Fitbit.

| Column | Type | Notes |
|---|---|---|
| sample_id | INT PK | |
| device_id | INT FK → wearable_device | CASCADE on delete |
| timestamp | DATETIME | |
| bpm | SMALLINT | |
| zone | VARCHAR(20) | out_of_range / fat_burn / cardio / peak |

---

### 4. ActivitySummary

Daily activity aggregates from Fitbit. One row per device per day.

| Column | Type | Notes |
|---|---|---|
| summary_id | INT PK | |
| device_id | INT FK → wearable_device | CASCADE on delete |
| date | DATE | |
| steps | INT | Nullable |
| active_minutes | INT | Nullable |
| calories_burned | INT | Nullable |
| distance_km | DECIMAL(6,3) | Nullable |

**Constraint:** `unique_together = (device_id, date)`

---

### 5. EMA (replaces UserData)

Ecological Momentary Assessment surveys. Fixed schema with standard health instrument fields. Column-level validators (1–10) enforced at the Django model layer.

| Column | Type | Notes |
|---|---|---|
| ema_id | INT PK | |
| user_id | INT FK → user | CASCADE on delete |
| timestamp | DATETIME | Auto |
| mood | SMALLINT | 1–10, nullable |
| energy | SMALLINT | 1–10, nullable |
| stress | SMALLINT | 1–10, nullable |
| physical_activity | VARCHAR(20) | none / light / moderate / vigorous, nullable |
| weight_lbs | DECIMAL(4,1) | Nullable |
| notes | TEXT | Nullable, free text |

Fields can be extended with additional columns via Django migrations when the research team finalises the instrument.

---

### 6. JITAILog (replaces NotificationData)

Logs every JITAI delivery with decision engine context and full engagement lifecycle. Replaces the simpler `NotificationData` model.

| Column | Type | Notes |
|---|---|---|
| log_id | INT PK | |
| user_id | INT FK → user | CASCADE on delete |
| timestamp | DATETIME | When intervention was sent |
| title | VARCHAR(255) | |
| message | TEXT | |
| trigger_reason | VARCHAR(100) | e.g. "low_steps", "high_stress_ema" |
| volatility_score | DECIMAL(6,3) | User's score at trigger time |
| threshold_used | DECIMAL(6,3) | Rule boundary that fired |
| prompt_status | VARCHAR(20) | sent → delivered → opened → dismissed / acted_on |
| prompt_count | INT | Cumulative prompts sent to this user |
| opened_at | DATETIME | Nullable — when notification was tapped |
| interacted_at | DATETIME | Nullable — when user engaged with content |

---

## Relationships

```
user ──< wearable_device ──< heart_rate_sample
                         ──< activity_summary
user ──< ema
user ──< jitai_log
```

- `heart_rate_sample` and `activity_summary` reach the user via `wearable_device` (one join)
- `ema` and `jitai_log` link directly to `user`

---

## ERD Source

The full schema CSV for SmartDraw ERD generation is at `Resources/react_schema.csv`.
