# REACT — Open Items & TODOs

**Last updated:** 2026-07-31  
**Author:** Dustin Nguyen — SMASH Research Lab, UF

---

## Mobile App Gaps (2026-07-31 re-review)

Sai's real mobile app is now merged into `main` (via `mobile-only-update` → `mobile-app-gaps` →
PR #25, 2026-07-30/31). Full writeup: `docs/sai-handover-review-2026-07-28.md` (see the
"Update — 2026-07-31" section at the top). Ordered by severity:

### The app does not build
**File:** `mobile/babel.config.js` requires `babel-preset-expo`; `mobile/package.json`
`devDependencies` only lists `@types/react` and `typescript`.  
**Impact:** `npx expo export` / `npx expo start` / any EAS build fails immediately with
`Cannot find module 'babel-preset-expo'`. Nothing else about the mobile app can be verified
until this is fixed.  
**Fix:** `npm install --save-dev babel-preset-expo` in `mobile/`.

### No EMA submission anywhere (biggest functional gap)
**File:** `mobile/src/screens/ComposeScreen.tsx`  
**Status:** Free-text box with keystroke telemetry only; `ema.submit()` is defined in
`mobile/src/api/endpoints.ts` and never called; no mood/stress/energy input UI exists.  
**Impact:** No real EMA data can reach the decision engine at all.  
**Assigned:** Shawnick's first task (`docs/shawnick-onboarding.md`).

### Auth token refresh is broken and fails silently
**Files:** `mobile/src/api/client.ts`, `mobile/src/store/authStore.ts`  
**Status:** Both call backend routes that don't exist — `/auth/token/refresh/` and `/auth/me/`
are not registered in `backend/project/urls.py`. Access tokens expire after 30 minutes
(`SIMPLE_JWT.ACCESS_TOKEN_LIFETIME`). On failure the client clears local tokens but never logs
the Zustand store out, so the UI keeps showing the app as logged in while every API call 401s
silently.  
**Action needed:** Design decision — add the backend endpoints, or simplify the client.

### Receipt round trip — half fixed
**Status:** Tyler added a `POST /jitai/receipt/` call (2026-07-31), but only inside the
foreground `addNotificationReceivedListener` (`mobile/App.tsx`). The tap/response listener —
phone elsewhere, participant taps the notification later — still only logs engagement, never
posts a receipt. Given these are silent, data-only pushes, the foreground path may rarely fire
in practice, especially on iOS.

### Orphaned legacy code
**Files:** `mobile/app/`, `mobile/components/`, `mobile/hooks/`, `mobile/constants/`  
**Status:** Old HealthyGator/create-expo-app boilerplate, never deleted when Sai's app was
merged in. Now fails to typecheck (23 errors) since `package.json` dropped their dependencies
(`expo-router`, `@react-navigation/native`, etc.). Delete — nothing references them.

### Expo Go cannot run this app; push notification native config gaps
**Status:** `mobile/android/` and `mobile/ios/` native folders are committed (bare workflow, not
gitignored/CNG) — Expo Go can't load a bare-workflow app regardless of anything else. Separately:
- `android/app/src/main/AndroidManifest.xml` has no `POST_NOTIFICATIONS` permission — required
  on Android 13+ for any notification to post at all.
- `ios/REACT/Info.plist` has no `UIBackgroundModes: remote-notification` — needed for the
  silent/background pushes this project's architecture depends on.
- `expo-doctor` confirms `app.json`'s `ios`/`android`/`icon`/`orientation` fields are now dead
  (ignored, since native folders exist and won't re-sync without `expo prebuild`).

### No enrollment / wearable-registration UI
Nothing in Login/Register/Compose calls `POST /wearable/` or sets `is_enrolled=True`. Confirm
with Sai whether this was planned as a separate flow.

### `BASE_URL` hardcoded to `'prod'`
**File:** `mobile/src/api/config.ts` — a `dev`/`prod` switch already exists, just needs flipping.

### `backend/requirements_mac.txt` is broken
**Status:** Missing `sentry_sdk`, `django-redis`, `djangorestframework_simplejwt`,
`argon2-cffi`, and `psycopg`. A fresh Mac clone can't run `manage.py` at all. ~10 minute fix —
diff against `requirements.txt`.

### No missing-check-in / non-response detection
**File:** `backend/app/tasks.py` → `_evaluate_user()`, `backend/app/models.py` → `EMA.status`  
`EMA.status='expired'` is declared but never assigned anywhere outside test fixtures.

### `trigger_signal` field always written `None`
**File:** `backend/app/tasks.py:105`. Declared on `JITAILog`, never populated.

### `celery.py` disables TLS certificate verification for `rediss://`
**File:** `backend/project/celery.py` — `ssl_cert_reqs: ssl.CERT_NONE`. Flag for a security pass.

### `.env` doesn't document all required/optional variables
`DASHBOARD_API_KEY` and `JITAI_RANDOMIZATION_PROBABILITY` both have code-level defaults but
aren't documented anywhere.

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

## Open Research / Contract Questions

| Question | Owner | Notes |
|---|---|---|
| Labfront API vs batch export | Prof. Chang | Determine pricing and access tier before writing `ingest_wearable_data` |
| Garmin Venu 3 Labfront compatibility | Prof. Chang | Verify all required data streams (15-sec HR, 3-min stress) are supported before signing contract |
| Beat-to-beat RR interval collection | Prof. Chang | Confirm device support and IRB permission; would enable HRV analysis |
| RA access scope | Prof. Chang + IRB | Which fields, which tables; needed before building researcher dashboard |
| Alcohol and eating construct — DB fields needed? | Prof. Chang | These constructs currently have zero DB backing; confirm if DB fields are required or Qualtrics is sole source |
| Cyber aggression construct mapping | Prof. Chang | Confirm compose churn signals (keystroke_count, delete_count) are the designated DB measure |

---

## PI Sign-Offs Received (2026-07-06)

| Item | Confirmed value |
|---|---|
| Randomization probability p | 0.5, fixed for whole pilot |
| Decision point trigger | EMA completion — one DP per new completed EMA |
| HR/stress eligibility gating | No — covariates only; MSSD is the sole eligibility signal |
| Cooldown between prompts | 60 minutes |
| Daily prompt cap | 4 per day |
| Failed delivery retry policy | One immediate retry for transient Expo errors; no delayed redelivery |
| JITAI trigger threshold quantile | 0.80 within-person expanding percentile (confirmed as default) |

---

## Completed Implementation

### JITAI MRT Celery Task (2026-07-06)
**Commits:** `9d87afb` → `a149033` (branch `feat-Final-Database-Model`)  
**Files changed:** `app/models.py`, `app/migrations/0035_jitailog_mrt_fields.py`, `decision_engine/decision_engine.py`, `app/tasks.py`, `app/notification_service.py`, `app/serializers.py`, `app/tests.py`  
**Test count:** 152 → 176 (all passing)

What was built:
- `JITAILog` schema — `decision_point_id` (UNIQUE, idempotency key), `randomization_probability`, `randomization_draw`; status choices expanded to include `pending` and `not_sent`; default changed to `pending`
- Decision engine — `apply_decision_rules()` now returns `eligible` column (not `send_prompt`); coin flip moved to task layer
- `_evaluate_user` — `get_or_create` idempotency, coin flip at p=0.5, full MRT record written before delivery
- `send_jitai_prompt` — one immediate retry on `PushServerError`/`PushTicketError`; `status='delivered'` set explicitly on success; `DeviceNotRegisteredError` clears token
- Serializers — `JITAILogSerializer` and `TelemetryJITAILogSerializer` expose all three new fields

---

## Technical Debt / Known Issues

### `screen_name` Vocabulary on PhoneTelemetry
**File:** `app/models.py` — `PhoneTelemetry.screen_name` field  
**Issue:** Field is nullable pending a controlled vocabulary from Prof. Chang  
**Action:** Once screen names are defined in the React Native app, add a `choices` constraint and make the field non-nullable

### JITAI: `'0.5'` hardcoded as env var fallback
**File:** `app/tasks.py:29`  
**Status:** Still present, but no longer a real risk — the PI Sign-Offs table above confirms
0.5 fixed for the whole pilot, so defaulting to it is now correct behavior. Low-priority: could
still raise/log loudly if the env var is absent, purely as a startup sanity check.

---

## Environment Variables Required (Not Yet Set in Heroku)

| Variable | Used In | Description |
|---|---|---|
| `LABFRONT_API_KEY` | `ingest_wearable_data` (pending) | Study-level Labfront API key |
| `JITAI_DEFAULT_PROMPT_ID` | `notification_service.py` | Default prompt template key sent to mobile app |
| `JITAI_HR_PROMPT_ID` | `notification_service.py` (future) | HR-triggered prompt template key |
| `JITAI_MOOD_PROMPT_ID` | `notification_service.py` (future) | Low-mood prompt template key |
| `JITAI_RANDOMIZATION_PROBABILITY` | `tasks.py` → `_evaluate_user` | Randomization probability p — confirmed 0.5 for all participants, whole pilot |
