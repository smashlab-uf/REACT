# REACT — Open Items & TODOs

**Last updated:** 2026-07-01  
**Author:** Dustin Nguyen — SMASH Research Lab, UF

---

## Blocked on External Deliverable

### Labfront API Integration
**File:** `app/tasks.py` → `ingest_wearable_data()`  
**Status:** Stub — logs "not yet implemented"  
**Blocked by:** Labfront API access and documentation (paid add-on — pending contract confirmation with Prof. Chang)  
**What's needed:**
- Labfront API endpoint URL and auth scheme (study-level API key → `LABFRONT_API_KEY` env var)
- Endpoint for per-participant HR data (15-sec resolution)
- Endpoint for per-participant stress data (3-min epochs)
- Rate limit and pagination details

**When unblocked:** Replace the stub body in `ingest_wearable_data()` with:
1. Iterate enrolled participants (`WearableDevice.objects.filter(is_active=True)`)
2. Call Labfront API with `labfront_participant_id`
3. Write `HeartRateSample` and `StressSample` rows via Django ORM
4. Update `WearableDevice.last_synced_at`

---

## Blocked on PI Sign-Off

### JITAI Trigger Thresholds
**File:** `decision_engine/decision_engine.py` → `apply_decision_rules()`  
**Status:** Defaults are set (threshold_quantile=0.80, cooldown_minutes=60, max_prompts_per_day=4)  
**Action needed:** Prof. Chang to confirm or adjust these values before study launch  
**Reference:** Architecture doc, Section 5 (Key Constraints)

### Researcher Dashboard (Django Admin Extension)
**Status:** Deferred — waiting on PI availability  
**What's needed from Prof. Chang before building:**
- RA access scope: which fields RAs may see under the IRB protocol (EMA Likert scores? Push tokens? Enrollment timestamps?)
- Whether `researcher_ra` group gets read-only access to EMA + JITAILog only, or also HeartRateSample/StressSample
- CSV export requirements: which fields per table, any PII redaction needed for RA exports

**When unblocked:** Extend `app/admin.py` with:
- Django permission groups (`researcher_pi`, `researcher_ra`)
- CSV export actions on all ModelAdmin views
- Computed columns on `UserAdmin` (last EMA, last sync, JITAI count this week)
- EMA completion rate and Likert trend views

---

## Blocked on Prompt Library Design

### Multi-Prompt Support
**File:** `app/notification_service.py` → `PROMPT_LIBRARY`  
**Status:** Single default prompt — `JITAI_DEFAULT_PROMPT_ID` env var  
**What's needed:** Prof. Chang to define the prompt library (message templates keyed by trigger type)  
**How it connects:** The decision engine returns a `trigger_reason` string (`prompt sent`, etc.). Once a prompt library exists, map trigger reasons → prompt IDs in `PROMPT_LIBRARY`. The React Native app holds the message text locally and displays it by `prompt_id`. Only the key ever reaches the backend (IRB constraint).

---

## Open Research / Contract Questions

| Question | Owner | Notes |
|---|---|---|
| Labfront API vs batch export | Prof. Chang | Determine pricing and access tier before writing `ingest_wearable_data` |
| Garmin Venu 3 Fitabase/Labfront compatibility | Prof. Chang | Verify all required data streams (15-sec HR, 3-min stress) are supported before signing contract |
| Beat-to-beat RR interval collection | Prof. Chang | Confirm device support and IRB permission; would enable HRV analysis |
| RA access scope | Prof. Chang + IRB | Which fields, which tables; needed before building researcher dashboard |
| JITAI threshold values | Prof. Chang | Confirm or adjust decision engine defaults (0.80 quantile, 60 min cooldown, 4/day cap) |
| Push notification prompt library | Prof. Chang | Define prompt templates and trigger-reason → prompt_id mapping |

---

## Technical Debt / Known Issues

### Legacy `poll_cfbd` Management Command
**File:** `app/management/commands/poll_cfbd.py`  
**Issue:** Hardcoded `America/New_York` timezone (line 24); references 2024/2025 season logic (line 35)  
**Action:** Can be deleted entirely once REACT study launches — this is HealthyGator legacy code that has no role in REACT

### `dj-database-url` Missing from `requirements.txt`
**File:** `project/settings.py` imports `dj_database_url` but `requirements.txt` doesn't list it  
**Fix:** `dj-database-url==2.3.0` is in `requirements_mac.txt` — add it to `requirements.txt`

### Swagger API Title
**File:** `project/urls.py` line 27  
**Issue:** Still reads `"REACT API Viewer"` — should be updated to REACT  

### `screen_name` Vocabulary on PhoneTelemetry
**File:** `app/models.py` — `PhoneTelemetry.screen_name` field  
**Issue:** Field is nullable pending a controlled vocabulary from Prof. Chang  
**Action:** Once screen names are defined in the React Native app, add a `choices` constraint and make the field non-nullable

---

## Environment Variables Required (Not Yet Set in Heroku)

| Variable | Used In | Description |
|---|---|---|
| `LABFRONT_API_KEY` | `ingest_wearable_data` (pending) | Study-level Labfront API key |
| `JITAI_DEFAULT_PROMPT_ID` | `notification_service.py` | Default prompt template key sent to mobile app |
| `JITAI_HR_PROMPT_ID` | `notification_service.py` (future) | HR-triggered prompt template key |
| `JITAI_MOOD_PROMPT_ID` | `notification_service.py` (future) | Low-mood prompt template key |
