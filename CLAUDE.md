# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

# REACT — Claude Code Context

## Project Overview

REACT (working title) is a research-grade Just-In-Time Adaptive Intervention (JITAI) mHealth
system built at the SMASH Research Lab, University of Florida, under PI Dr. Yonghwan Chang
(Department of Sport Management). It extends the prior REACT platform, which
delivered push notifications to study participants.

REACT adds:
- EMA (Ecological Momentary Assessment) in-app surveys
- Garmin wearable physiological data collection via Labfront
- A more sophisticated JITAI decision engine driven by real-time biometric signals
- A researcher dashboard for study monitoring

Target deployment: ~300 participants across a UF football season (late August through late
November/early December, ~12–14 weeks).

---

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Django REST Framework (Python) |
| Database | PostgreSQL |
| Task Queue | Celery + Redis |
| Frontend | React Native (Expo) |
| Deployment | Heroku (`Procfile`) and GCP App Engine/Cloud Run (`cloudbuild.yaml`); Sentry for errors |
| Push Notifications | Expo Push Notification Service → Firebase/APNs |
| Wearable Data Layer | Labfront API (intermediary for Garmin Health API) |
| Wearable Device | Garmin Venu 3 |

---

## Commands

Backend commands run from `backend/`. Python is pinned to 3.11 (`runtime.txt`).

```bash
# Backend (Django) — from backend/
pip install -r requirements.txt          # use requirements_mac.txt on Apple Silicon
python manage.py migrate
python manage.py runserver 0.0.0.0:8000
python manage.py createsuperuser

# Tests run against SQLite in-memory via test_settings (do NOT hit the real DB)
python manage.py test --settings=project.test_settings                              # full suite
python manage.py test app.tests.EMAViewTests --settings=project.test_settings        # one class
python manage.py test app.tests.EMAViewTests.test_name --settings=project.test_settings  # one test

# Decision-engine scenario tests are plain unittest (pure pandas, no Django)
python -m unittest decision_engine.test_decision_engine_scenarios

# Celery (needs Redis; broker from CELERY_BROKER_URL / REDIS_URL)
celery -A project.celery worker --loglevel=info
celery -A project.celery beat   --loglevel=info
```

```bash
# Mobile (Expo SDK 56) — from mobile/. Needs Node 20.19.4+. Expo Go does NOT work; needs a dev client on a physical device.
cd mobile && npm install
npx expo run:ios --device        # first native build; afterwards use `npx expo start --dev-client`
npx expo run:android --device    # needs adb — verify with `adb devices`
# mobile/.env must set EXPO_PUBLIC_API_KEY equal to the backend API_KEY, or requests get 403.

# Checks (no test suite; this is what CI-equivalent verification looks like)
npx tsc --noEmit
npx expo export --platform ios --output-dir /tmp/x
```

```bash
# Analytics — from analytics/. Loaders bootstrap Django to read the live DB (scripts.py:_ensure_django).
pip install -r requirements.txt
streamlit run REACT-dashboard/app.py    # reads live data only if REACT_USE_MOCK_DATA=false and
                                         # REACT_DASHBOARD_API_KEY is set (matches backend DASHBOARD_API_KEY)
```

```bash
# Manual end-to-end QA scripts (not part of the automated test suite) — from backend/
python3 dress_rehearsal.py [--days N] [--reset]   # simulated participants at the ORM layer,
                                                   # exercises cooldown/daily-cap/missing-token paths
                                                   # through the real _evaluate_user + /jitai/receipt/ code
python3 full_circle_test.py [--base-url URL]       # hits a real running backend (prod or local) over HTTP
                                                    # to register/enroll/submit EMAs and poll for JITAI triggers
```

GitHub Actions (`.github/workflows/django-ci.yml`) runs on every push: decision-engine scenario
tests, then `manage.py migrate` + `manage.py test` against SQLite (via `DATABASE_URL=sqlite:///db.sqlite3`,
not `test_settings`). A push to `main` that passes then auto-deploys to Heroku.

---

## Runtime Architecture

Three cooperating processes (`Procfile`) plus the mobile client:

- **web** — `gunicorn project.wsgi` — the DRF API and Django Admin (the primary researcher
  surface; see Researcher Dashboard below for the separate Streamlit monitoring app).
- **worker** — `celery -A project.celery worker` — executes the JITAI / notification tasks.
- **beat** — `celery -A project.celery beat` — fires all three periodic tasks every **180 s**
  (`CELERY_BEAT_SCHEDULE` in `settings.py`): `ingest_wearable_data`, `evaluate_jitai_triggers`,
  `send_checkin_reminders`, all defined in `app/tasks.py`.

Auth is two-layered: `APIKeyMiddleware` (`app/middleware.py`) rejects API routes that lack a matching
`X-API-Key` when `API_KEY` is set (it keeps an exempt-path list), and DRF layers SimpleJWT on top.
The two `/dashboard/*` endpoints use a third scheme instead (`IsAdminUserOrDashboardAPIKey` in
`app/views.py`): staff session auth OR a bearer `DASHBOARD_API_KEY`, for the Streamlit dashboard.
`settings.py` is fully env-driven and picks the database by environment: `DATABASE_URL`
(Heroku/dj-database-url) → Cloud SQL when `K_SERVICE` is set (GCP) → discrete `DATABASE_*` vars. Key
env vars: `SECRET_KEY`, `API_KEY`, `DASHBOARD_API_KEY`, `REDIS_URL`, `SENTRY_DSN`,
`JITAI_RANDOMIZATION_PROBABILITY` (default `0.5` — coin-flip gate in `evaluate_jitai_triggers`).

Two push types reach the device (details in `mobile/README.md`): a **visible check-in reminder**
(`send_checkin_reminders`, gated to 9–21 participant-local time with a 120-min cooldown, skipped once
the daily EMA cap is hit) and a **silent JITAI prompt** (`evaluate_jitai_triggers`, sent only after a
newly completed EMA passes eligibility + randomization). The MSSD trigger math is isolated in
`backend/decision_engine/decision_engine.py` (`calculate_mssd`, `apply_decision_rules`) and is
regression-tested against a golden CSV (`scenario_test_outputs.csv`) in that directory.

---

## Repo Layout

```
backend/
  project/         # Django project: settings.py, celery.py, urls.py (all routes live here, not in app/),
                   #   wsgi/asgi, test_settings.py
  app/             # the single Django app: models, serializers, views, tasks, admin,
                   #   middleware.py (API key), notification_service.py (Expo push), ema_catalog.py
  decision_engine/ # standalone MSSD/JITAI logic + golden-CSV scenario tests (pure pandas)
  syntheticData/   # cohort generators: react_cohort.py (current, 1–7 scale), synthetic_generator.py (legacy)
  dress_rehearsal.py, full_circle_test.py  # manual end-to-end QA scripts, see Commands
mobile/            # Expo SDK 56 app; source under src/; committed android/ & ios/; dev-client required
analytics/         # offline analysis: scripts.py (ORM-backed loaders + pandas metrics),
                   #   REACT-dashboard/ (Streamlit, reads /dashboard/* endpoints),
                   #   sensitivity_analysis/ (MSSD parameter recovery / robustness notebooks)
analysis-resources/# data-dictionary.md and production_schema.md (authoritative live-schema map)
docs/superpowers/  # schema design specs/plans from the original REACT model buildout — historical
                   #   context for why the models look the way they do, not a live source of truth
```

---

## Schema authority

The **Django Models** section below is a simplified design reference and is intentionally leaner than
what is deployed. The live schema is richer — e.g. `JITAILog` has ~27 columns; `EMA` carries
`ema_type` and outcome-window fields; and there are additional tables (`EMAItemResponse`,
`EngagementLog`, `PhoneTelemetry`, `EventDay`, `CheckinReminder`). For the actual deployed schema,
trust `backend/app/models.py` and `analysis-resources/production_schema.md` (the latter maps every
production table to its `data-dictionary.md` logical name).

---

## Django Models

### Existing (from HealthyGator — carry over unchanged)

```python
class User(models.Model):
    user_id = models.AutoField(primary_key=True)
    email = models.EmailField(unique=True)
    first_name = models.CharField(max_length=100, default="")
    last_name = models.CharField(max_length=100, default="")
    birthdate = models.DateField()
    gender = models.CharField(max_length=10, choices=[('male','Male'),('female','Female'),('other','Other')])
    password = models.CharField(max_length=128, blank=True, null=True)
    push_token = models.CharField(max_length=128, blank=True, null=True)
    # REACT additions:
    is_enrolled = models.BooleanField(default=False)
    enrolled_at = models.DateTimeField(null=True, blank=True)
    # DO NOT add any Fitbit token fields — Labfront owns OAuth entirely
```

### New Models (REACT)

```python
class WearableDevice(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    labfront_participant_id = models.CharField(max_length=64, unique=True)
    is_active = models.BooleanField(default=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)

class HeartRateSample(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    timestamp = models.DateTimeField(db_index=True)
    bpm = models.PositiveSmallIntegerField()
    source = models.CharField(max_length=32, default='garmin_labfront')
    class Meta:
        ordering = ['-timestamp']
        indexes = [models.Index(fields=['user', 'timestamp'])]

class StressSample(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    timestamp = models.DateTimeField(db_index=True)
    stress_score = models.PositiveSmallIntegerField()  # 0–100 Garmin scale
    source = models.CharField(max_length=32, default='garmin_labfront')
    class Meta:
        ordering = ['-timestamp']
        indexes = [models.Index(fields=['user', 'timestamp'])]

class EMA(models.Model):
    STATUS_CHOICES = [('pending','Pending'),('completed','Completed'),('expired','Expired')]
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    prompt_id = models.CharField(max_length=64)
    sent_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='pending')
    mood = models.PositiveSmallIntegerField(null=True, blank=True)    # 1–7 Likert
    stress = models.PositiveSmallIntegerField(null=True, blank=True)  # 1–7 Likert
    energy = models.PositiveSmallIntegerField(null=True, blank=True)  # 1–7 Likert
    class Meta:
        ordering = ['-sent_at']

class JITAILog(models.Model):
    STATUS_CHOICES = [('delivered','Delivered'),('opened','Opened'),('interacted','Interacted'),('failed','Failed')]
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    prompt_id = models.CharField(max_length=64)       # reference to notification template; NOT message text
    triggered_at = models.DateTimeField(auto_now_add=True)
    trigger_reason = models.CharField(max_length=128) # e.g. "hr_elevated+stress_high"
    hr_at_trigger = models.PositiveSmallIntegerField(null=True, blank=True)
    stress_at_trigger = models.PositiveSmallIntegerField(null=True, blank=True)
    ema = models.ForeignKey(EMA, on_delete=models.SET_NULL, null=True, blank=True)
    observed_mssd = models.FloatField(null=True, blank=True)
    send_prompt = models.BooleanField(default=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='delivered')
    class Meta:
        ordering = ['-triggered_at']
```

---

## API Endpoints

Source of truth: `backend/project/urls.py` (all routes are registered there, not in `app/`).
(`full_circle_test.py`'s docstring points at `docs/api-contract.md` for request/response shapes,
but that file doesn't exist in the repo — don't chase it.)

### Active endpoints

| Method | Path | Purpose |
|---|---|---|
| POST | `/user/` | Create user account |
| PUT | `/user/{user_id}/` | Update user profile (owner or staff only) |
| POST | `/user/login/` | Authenticate user |
| POST | `/user/checkemail/` | Check if email already exists |
| POST | `/auth/token/refresh/` | Refresh a SimpleJWT access token |
| GET | `/auth/me/` | Fetch the authenticated user's own profile |
| POST | `/wearable/` | Register Labfront participant ID on enrollment |
| GET/PATCH | `/wearable/{user_id}/` | Get / update device record (e.g. last_synced_at) |
| GET | `/ema/next/` | Fetch the next EMA prompt for the mobile app to show |
| POST | `/ema/responses/` | Submit an EMA response from the mobile app |
| GET | `/ema/{user_id}/` | Fetch EMA history (dashboard/admin use) |
| POST | `/jitai/` | Internal — Celery logs a triggered intervention |
| GET | `/jitai/{user_id}/` | Fetch JITAI history (dashboard/admin use) |
| POST | `/jitai/receipt/` | Mobile app reports delivery/open of a JITAI push (`jitai_log_id`) |
| POST | `/telemetry/ingest/` | Internal — Celery bulk-ingest wearable data |
| GET | `/telemetry/hr/{user_id}/` | Fetch recent HR samples (dashboard use) |
| GET | `/telemetry/stress/{user_id}/` | Fetch recent stress samples (dashboard use) |
| POST | `/telemetry/phone/` | Ingest compose surface event from mobile app |
| POST | `/telemetry/engagement/` | Ingest EMA/notification engagement event from mobile app |
| GET | `/dashboard/participants/` | Per-participant sync/push/receipt status + staleness (Streamlit dashboard) |
| GET | `/dashboard/latency-events/` | Recent push→receipt latency events (Streamlit dashboard) |
| GET | `/swagger/` | drf-yasg OpenAPI UI |

The two `/dashboard/*` endpoints are read-only and gated by `IsAdminUserOrDashboardAPIKey`
(staff session or `DASHBOARD_API_KEY`), not the standard `IsAuthenticated` used elsewhere.

---

## Wearable Data Architecture

REACT never communicates with Garmin directly. The data flow is:

```
Garmin Venu 3
  → Garmin Health API (push on device sync)
    → Labfront (research platform: buffers and re-exposes Garmin data)
      → REACT Celery ingestion task (polls Labfront API)
        → PostgreSQL (HeartRateSample, StressSample)
          → JITAI decision logic
            → Expo Push Notification → participant device
```

### Garmin Data Resolutions (via Labfront)

| Resolution | Dataset |
|---|---|
| Daily | Stress, Steps, Floors, VO2max, PulseOx, MoveIQ, Body composition, Calories, HRV nightly avg, Weartime, Activity logs |
| 15 min epochs | Steps, Heart rate, Distance, MET, Intensity, MeanMotion/MaxMotion |
| 3 min | Stress score (primary real-time JITAI signal alongside HR) |
| Per stage change | Sleep stage records |
| 15 sec | Heart rate (primary HR ingest stream; ~720 rows/participant/3hr session) |
| Optional add-on | Beat-to-beat RR intervals (enhanced HRV; not default) |

### Labfront API vs Batch Export
- API access and automated batch exports are separate paid add-ons (not included by default)
- REACT targets the Labfront API for programmatic access via Celery periodic tasks
- The Celery task polls at short intervals (every 2–3 min) to support near-real-time JITAI triggering
- The `labfront_participant_id` in `WearableDevice` is the key used to query Labfront per participant

---

## Celery Task Architecture

Celery Beat schedules periodic tasks. Redis is the message broker.

Key tasks:
- `ingest_wearable_data` — polls Labfront API for all enrolled participants, writes new
  HeartRateSample and StressSample rows
- `evaluate_jitai_triggers` — reads recent EMA and telemetry per participant, computes MSSD,
  fires Expo push notification and writes JITAILog if thresholds are met

---

## JITAI Decision Logic

JITAI triggers are based on combinations of biometric signals and EMA responses. The exact
threshold values are a research design decision requiring PI (Prof. Chang) sign-off before
implementation. Do not hardcode thresholds without confirmation.

Candidate trigger signals:
- `HeartRateSample.bpm` — elevated HR suggests physiological arousal
- `StressSample.stress_score` — high stress score complements EMA self-report
- `EMA.mood / EMA.stress / EMA.energy` — self-reported emotional state (MSSD computed across recent EMAs)

Trigger reason string format (for `JITAILog.trigger_reason`): concatenate active signals,
e.g. `"hr_elevated+stress_high"` or `"ema_low_mood+hr_elevated"`.

---

## Notification Pipeline

Silent pushes are used so notification content never transits Expo/Firebase servers as stored
data. Only the `prompt_id` (a reference to a template) is stored in `JITAILog`, never the
message text itself. This is a privacy/IRB constraint — do not store notification title or
message body in the database.

Expo push token is stored in `User.push_token` and updated on app launch.

---

## Researcher Dashboard

Two separate surfaces exist:

1. **Django Admin** (`app/admin.py`) — full CRUD over every model (`ReadableAdminMixin` +
   one `ModelAdmin` per model), the default researcher/PI surface today.
2. **Streamlit feasibility dashboard** (`analytics/REACT-dashboard/`) — a read-only monitoring
   view fed by the two `/dashboard/*` API endpoints (participant sync/push/receipt staleness,
   push→receipt latency events), auth'd with `DASHBOARD_API_KEY`. Not a general study-data
   browser — that's still Django Admin.

The originally planned `researcher_pi` (full access) / `researcher_ra` (read-only, no User PII)
Django permission-group split is **not implemented** — everyone with Django Admin access currently
sees everything. Confirm RA access scope with Prof. Chang before implementing it.

Labfront's own dashboard handles device compliance monitoring (battery, sync times, wear
gaps) — do not duplicate this in Django Admin.

---

## Serializer Pattern

Follow the existing pattern in `serializers.py` — one `ModelSerializer` subclass per model,
explicit `fields` list (avoid `fields = '__all__'` for new models), `read_only_fields` for
auto-generated PKs and timestamps.

---

## Coding Conventions

- No comments in code
- Django ORM only — no raw SQL
- All new endpoints require DRF permission classes (at minimum `IsAuthenticated`)
- Migrations are committed to version control
- One serializer per model, kept in `serializers.py`
- Celery tasks in `tasks.py`
- Environment variables for all secrets (Labfront API key, Redis URL, database URL) —
  never hardcode

---

## Key Constraints

- **No Fitbit anything** — Fitbit OAuth, token fields, and device IDs are fully dropped.
  Do not reintroduce any Fitbit-specific code.
- **No free-text storage** — EMA `notes` fields and notification message body must not be
  stored in the database. IRB constraint.
- **Labfront owns OAuth** — REACT never holds Garmin OAuth tokens. The only Labfront
  identifier stored per participant is `labfront_participant_id`.
- **JITAI thresholds need PI sign-off** — do not implement numeric trigger thresholds
  without confirmation from Prof. Chang.
- **No Fitabase** — the wearable data layer is Labfront, not Fitabase. REACT builds its own
  JITAI and EMA delivery layer; do not integrate Fitabase or Fitabase Engage.

---

## Open Questions (as of project start)

- Labfront API vs batch export — confirm with Prof. Chang (determine pricing/access)
- JITAI trigger thresholds — numeric values require PI alignment before implementation
- RA access scope — which fields are RAs permitted to see under IRB protocol
- Garmin Venu 3 Labfront compatibility — verify all required data streams are supported before finalizing the Labfront contract
- Beat-to-beat RR interval collection — confirm device support and IRB permission

---

## People

- **Dustin** — student software engineer, primary developer
- **Prof. Yonghwan Chang** — PI, Department of Sport Management, UF. Owns research
  design and IRB scope. Key sync: Fridays.
- **Prior platform team** — REACT (football + basketball iterations) built
  by prior student teams. Codebase is the starting point for REACT.
