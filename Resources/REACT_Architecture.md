# REACT — Architecture & Database Schema

**SMASH Research Lab · University of Florida · PI: Dr. Yonghwan Chang**

---

## Architecture Workflow

```
┌─────────────────────────────────────────────────────────────────────────┐
│  PARTICIPANT DEVICE                                                      │
│  Garmin Vivoactive 6  ──sync──►  Garmin Health API                      │
└────────────────────────────────────────┬────────────────────────────────┘
                                         │ push on device sync
                                         ▼
                                    ┌─────────┐
                                    │ Fitabase│  buffers & re-exposes data
                                    └────┬────┘
                                         │ Fitabase API (poll)
                    ┌────────────────────▼────────────────────┐
                    │  CELERY PERIODIC TASKS (Django + Redis)  │
                    │                                          │
                    │  ingest_wearable_data                    │
                    │  · HeartRateSample  (15-sec HR)          │
                    │  · StressSample     (3-min stress score) │
                    │  · SleepSummary     (nightly stages)     │
                    │                         │                │
                    │  evaluate_jitai_triggers◄┘               │
                    │  · compute MSSD from recent EMA rows     │
                    │  · check threshold / cooldown / cap      │
                    │  · write JITAILog (send_prompt T/F)      │
                    │  · fire Expo push if send_prompt=True    │
                    └────────────────────┬────────────────────┘
                                         │
              ┌──────────────────────────▼──────────────────────────┐
              │  POSTGRESQL (react_db)                               │
              │  user · user_data · wearable_device                 │
              │  heart_rate_sample · stress_sample · sleep_summary  │
              │  ema · jitai_log                                     │
              └──────────────────────────┬──────────────────────────┘
                                         │
          ┌──────────────────────────────┼──────────────────────────────┐
          │                             │                               │
          ▼                             ▼                               ▼
  ┌───────────────┐           ┌──────────────────┐           ┌─────────────────┐
  │ MOBILE APP    │           │ RESEARCHER DASH  │           │ EXPO PUSH →     │
  │ React Native  │           │ Django Admin ext.│           │ Firebase / APNs │
  │               │           │                  │           │                 │
  │ POST /ema/    │           │ EMA monitor      │           │ prompt_id only  │
  │ GET  /ema/    │           │ JITAI log viewer │           │ (no msg text —  │
  │ GET  /jitai/  │           │ Telemetry charts │           │  IRB constraint)│
  │ GET  /telemetry/hr/       │ CSV export       │           └─────────────────┘
  └───────────────┘           └──────────────────┘
```

**MSSD** (Mean Square Successive Difference) is computed by `evaluate_jitai_triggers`
from sequential EMA responses per participant. High MSSD indicates emotional volatility
and is the primary trigger signal for intervention delivery.

---

## Database Schema

### `user`
| Column | Type | Constraint |
|---|---|---|
| user_id | INT | PRIMARY KEY |
| email | VARCHAR(254) | UNIQUE |
| first_name | VARCHAR(100) | |
| last_name | VARCHAR(100) | |
| birthdate | DATE | |
| gender | VARCHAR(10) | |
| height_feet | VARCHAR(10) | |
| height_inches | VARCHAR(10) | |
| goal_weight | DECIMAL | |
| goal_to_lose_weight | BOOLEAN | |
| goal_to_feel_better | BOOLEAN | |
| password | VARCHAR(128) | |
| push_token | VARCHAR(128) | |
| is_enrolled | BOOLEAN | |
| enrolled_at | DATETIME | |

### `user_data`
| Column | Type | Constraint |
|---|---|---|
| data_id | INT | PRIMARY KEY |
| user_id | INT | FK → user |
| timestamp | DATETIME | |
| goal_type | VARCHAR(20) | |
| weight_value | DECIMAL | |
| feel_better_value | INT | |

### `wearable_device`
| Column | Type | Constraint |
|---|---|---|
| id | INT | PRIMARY KEY |
| user_id | INT | FK → user (UNIQUE) |
| fitabase_participant_id | VARCHAR(64) | UNIQUE |
| device_name | VARCHAR(100) | |
| is_active | BOOLEAN | |
| last_synced_at | DATETIME | |

### `heart_rate_sample`
| Column | Type | Notes |
|---|---|---|
| id | INT | PRIMARY KEY |
| user_id | INT | FK → user |
| timestamp | DATETIME | indexed |
| bpm | SMALLINT | 15-sec Garmin stream |
| source | VARCHAR(32) | default: garmin_fitabase |

### `stress_sample`
| Column | Type | Notes |
|---|---|---|
| id | INT | PRIMARY KEY |
| user_id | INT | FK → user |
| timestamp | DATETIME | indexed |
| stress_score | SMALLINT | 0–100 Garmin scale, 3-min epochs |
| source | VARCHAR(32) | default: garmin_fitabase |

### `ema`
| Column | Type | Notes |
|---|---|---|
| id | INT | PRIMARY KEY |
| user_id | INT | FK → user |
| prompt_id | VARCHAR(64) | template reference |
| sent_at | DATETIME | |
| responded_at | DATETIME | nullable |
| status | VARCHAR(16) | pending / completed / expired |
| mood | SMALLINT | 1–7 Likert |
| stress | SMALLINT | 1–7 Likert |
| energy | SMALLINT | 1–7 Likert |

### `jitai_log`
| Column | Type | Notes |
|---|---|---|
| id | INT | PRIMARY KEY |
| user_id | INT | FK → user |
| prompt_id | VARCHAR(64) | template reference — never message text (IRB) |
| triggered_at | DATETIME | |
| trigger_reason | VARCHAR(128) | e.g. `hr_elevated+stress_high` |
| hr_at_trigger | SMALLINT | nullable |
| stress_at_trigger | SMALLINT | nullable |
| ema_id | INT | FK → ema (EMA record that drove the decision) |
| observed_mssd | FLOAT | MSSD volatility score at decision time |
| send_prompt | BOOLEAN | False when cooldown/cap blocked delivery |
| status | VARCHAR(16) | delivered / opened / interacted / failed |

---

## Key Constraints

| Constraint | Reason |
|---|---|
| No Fitbit fields anywhere | Fitabase owns OAuth entirely |
| No notification message text in DB | IRB/privacy — only `prompt_id` stored |
| No Garmin OAuth tokens in DB | REACT never holds Garmin credentials |
| JITAI thresholds not hardcoded | Require PI sign-off before implementation |
| Fitabase Engage not integrated | Deferred — REACT owns its JITAI/EMA layer |

---

*Last updated: 2026-06-19 · Author: Dustin Nguyen*
