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
| Deployment | Heroku (`Procfile`) — the only live target; Sentry for errors. `cloudbuild.yaml` exists but is dormant, and its build context is `./backend`, so it would miss the repo-root `dashboard/` app if revived |
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
# A custom TEST_RUNNER (project/test_runner.py) does two things, both load-bearing:
#   1. supplies the default labels ('app', 'dashboard') — bare discovery starts in backend/
#      and would never find the repo-root dashboard app;
#   2. blanks API_KEY and DASHBOARD_API_KEY for the run. All but two test classes assume
#      APIKeyMiddleware is off, so an exported API_KEY used to fail ~97 tests on a clean
#      tree. It lives in the runner, not test_settings.py, because CI runs bare
#      `manage.py test` and never loads that module.
python manage.py test --settings=project.test_settings                                  # full suite
python manage.py test app.tests.EMANextViewTests --settings=project.test_settings        # one class
python manage.py test dashboard.tests.WindowsTests.test_slot_index_boundaries_are_half_open --settings=project.test_settings

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
python reconcile_monitoring.py          # checks the live metric tables against scripts.py;
                                         # seeds a synthetic cohort, so never point it at production

# Monitoring prototype over the /api/monitor/* endpoints: a Streamlit multipage app with the
# Stage 2 participant grid and the Stage 3 participant timeline under monitor_app/pages/.
# It holds no DB credential and imports no Django; both vars are env-only.
# Do NOT export API_KEY here — it switches APIKeyMiddleware on for every route and silently
# 403s the dress rehearsal's delivery receipts. DASHBOARD_API_KEY is all the monitor needs.
# (The older analytics/REACT-dashboard/ Streamlit app was deleted in commit 1fdc6f3 and
#  is unrelated — it read only the two /dashboard/* status endpoints.)
export MONITOR_BASE_URL=http://localhost:8000 DASHBOARD_API_KEY=...
streamlit run monitor_app/app.py       # entry; pages appear in the sidebar
```

```bash
# Monitoring data layer — from backend/
python manage.py recompute_metrics                 # what the periodic task does: trailing 3 days
python manage.py recompute_metrics --all           # initial fill / after a definition change
python manage.py recompute_metrics --user 12 --days 2
python manage.py dump_item_bank --check            # fails if EMA_ITEM_BANK drifted from the frozen JSON
python manage.py backfill_withdrawals --dry-run    # backdate withdrawals from django_admin_log
python manage.py backfill_thresholds --dry-run     # fill JITAILog.threshold_at_decision on
                                                   #   rows written before the engine kept it
python manage.py import_baseline_distress_flags --dry-run --file export.csv
                                                   # score a Qualtrics baseline export for Section 11
                                                   #   signals, write DistressFlag(source='baseline').
                                                   #   Defaults --id-column=participant_id
                                                   #   --id-field=user_id; both still overridable.
# The dashboard tests do NOT run from backend/: the label 'dashboard' resolves to the directory
# backend/dashboard/ there, so bare `manage.py test` (and CI) skips them. Run them from the repo root:
python3 backend/manage.py test dashboard --settings=project.test_settings
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
  surface; see Researcher Dashboard below for the read-only monitoring API alongside it).
- **worker** — `celery -A project.celery worker` — executes the JITAI / notification tasks.
- **beat** — `celery -A project.celery beat` — fires four periodic tasks (`CELERY_BEAT_SCHEDULE`
  in `settings.py`). Three run every **180 s** and live in `app/tasks.py`:
  `ingest_wearable_data`, `evaluate_jitai_triggers`, `send_checkin_reminders`. The fourth,
  `dashboard.tasks.recompute_monitoring_metrics`, runs every **600 s** — an aggregate refresh
  paced to the Labfront batch cadence, so a shorter interval would only reread the same rows.

Auth is two-layered: `APIKeyMiddleware` (`app/middleware.py`) rejects API routes that lack a matching
`X-API-Key` when `API_KEY` is set (it keeps an exempt-path list), and DRF layers SimpleJWT on top.
The `/dashboard/*` and `/api/monitor/*` endpoints use a third scheme instead
(`IsAdminUserOrDashboardAPIKey` in `app/views.py`): staff session auth OR a
`X-Dashboard-API-Key` header. `APIKeyMiddleware.DASHBOARD_PREFIXES` lists the path prefixes
that key is accepted for.
`settings.py` is fully env-driven and picks the database by environment: `DATABASE_URL`
(Heroku/dj-database-url) → Cloud SQL when `K_SERVICE` is set (GCP) → discrete `DATABASE_*` vars. Key
env vars: `SECRET_KEY`, `API_KEY`, `DASHBOARD_API_KEY`, `REDIS_URL`, `SENTRY_DSN`,
`JITAI_RANDOMIZATION_PROBABILITY` (default `0.5` — coin-flip gate in `evaluate_jitai_triggers`).

Two push types reach the device (details in `mobile/README.md`): a **visible check-in reminder**
(`send_checkin_reminders`, which divides 9–21 participant-local time into six fixed two-hour
slots and fires at most one reminder per slot, 30 min after it opens, only where no check-in
has landed and never as a catch-up; there is no cooldown, and the 120 minutes sometimes quoted
as one is just the slot length) and a **silent JITAI prompt** (`evaluate_jitai_triggers`, sent only after a
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
                   #   sensitivity_analysis/ (MSSD parameter recovery / robustness notebooks),
                   #   reconcile_monitoring.py (live-vs-offline metric agreement check),
                   #   monitor_app/ (Streamlit over /api/monitor/*: pages/ holds the Stage 2
                   #     participant grid and the Stage 3 participant timeline)
dashboard/         # monitoring Django app at the repo root, NOT under backend/:
                   #   data/ (config.py constants, windows.py, daily/participant/cohort/alerts),
                   #   models.py (4 derived metric tables), views.py (/api/monitor/*), tasks.py
analytics/analysis-resources/# data-dictionary.md and production_schema.md (authoritative live-schema map)
docs/superpowers/  # schema design specs/plans from the original REACT model buildout — historical
                   #   context for why the models look the way they do, not a live source of truth
```

---

## Schema authority

The **Django Models** section below is a simplified design reference and is intentionally leaner than
what is deployed. The live schema is richer — e.g. `JITAILog` has **36** columns (the message-arm
randomization and routing audit landed in migrations `0042`–`0043`, and
`threshold_at_decision` / `threshold_source` in `0046`); `EMA` carries `ema_type`,
outcome-window fields and `served_sub_item_ids`; there are additional tables (`EMAItemResponse`,
`EngagementLog`, `PhoneTelemetry`, `EventDay`, `CheckinReminder`, `WearableSync`, `DistressFlag`); and four derived
`dashboard_*` monitoring tables. For the actual deployed schema,
trust `backend/app/models.py` and `analytics/analysis-resources/production_schema.md` (the latter maps every
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
| GET | `/dashboard/participants/` | Per-participant sync/push/receipt status + staleness |
| GET | `/dashboard/latency-events/` | Recent push→receipt latency events |
| GET | `/api/monitor/cohort` | Latest cohort snapshot: benchmarks, Wilson bounds, 14-day series |
| GET | `/api/monitor/grid` | `MetricsDaily` pivoted to participant × study_day for one metric |
| GET | `/api/monitor/participant/{id}` | Participant rollup + every daily row + open alerts |
| GET | `/api/monitor/participant/{id}/timeline` | Participant-days from raw tables (`?days=1..14`) |
| GET | `/api/monitor/participant/{id}/funnel` | One participant's whole-study delivery waterfall |
| GET | `/api/monitor/alerts` | Open alerts, with severity counts |
| GET | `/swagger/` | drf-yasg OpenAPI UI |

The `/dashboard/*` and `/api/monitor/*` endpoints are read-only and gated by
`IsAdminUserOrDashboardAPIKey` (staff session or `DASHBOARD_API_KEY`), not the standard
`IsAuthenticated` used elsewhere. `APIKeyMiddleware.DASHBOARD_PREFIXES` is what lets the
dashboard key through for both path prefixes.

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

## Distress override (protocol Section 11)

Any distress signal suspends prompt randomization for that participant and the app shows a
resource card instead of a coping message. Logic lives in `backend/app/distress.py`; state lives
in `DistressFlag`. Two sources, both checked inside `_evaluate_user` **before** the randomization
draw, after the run-in gate (run-in is named first when both apply):

- **baseline** (`source='baseline'`): the Qualtrics screens, scored offline in
  `analytics/baseline_survey_scoring/` (`scoring.py`'s `phq9_self_harm_positive`,
  `phq9_severity_alert`, `scoff_positive`, `audit_c_alert`, `pgsi_problem_gambling`,
  `hunger_positive`, mapped to signal codes by `section11.py`) and turned into
  `DistressFlag` rows by `manage.py import_baseline_distress_flags --file ... --id-column ...
  --id-field user_id` (backend/; the export's `participant_id` is the numeric `user_id`). Re-running the same export is a no-op: a row is skipped
  once a `DistressFlag` with that exact signal set already exists for the user. `free_text_risk`
  is not produced by this pipeline at all — there is no free-text column in
  `analytics/baseline_survey_scoring/definitions.py` — and stays a manual staff process. Pauses
  coping prompts until staff mark the contact documented (Django Admin action "Mark contact
  documented" sets `contact_documented_at`); randomization then resumes. Only the signal
  codes are stored, never a score or the free-text disclosure.
- **momentary** (`source='momentary'`): raised when a submitted check-in has ANY of
  `B1_valence == 1` (scale floor), `B2_stress == 7` (scale ceiling), `B1_affect_sad == 5`, or
  `B1_affect_anxious == 5` (both scale ceilings) — exact values, confirmed by Dr. Chang
  2026-09-22 (`dashboard/data/config.py`'s `DISTRESS_B1_VALENCE_FLOOR` /
  `DISTRESS_B2_STRESS_CEILING` / `DISTRESS_B1_AFFECT_CEILING`). Separate from, and not to be
  confused with, the `ROUTING_TRIGGER_RULES` inequality cutoffs in `ema_catalog.py` used for
  coping-message topic selection. Pauses for `DISTRESS_MOMENTARY_PAUSE_HOURS` (24). Multiple
  signals can fire on one check-in (e.g. both sad and anxious at once) — all are recorded, not
  deduped.

**Importing the baseline** has two front ends over one function (`backend/app/baseline_import.py`,
`import_baseline_flags`): the management command, and an **"Import baseline export" button** on
Django Admin > Distress flags (`DistressFlagAdmin.import_baseline_view`). The button takes a CSV
upload (5 MB cap, read in memory, never saved) and previews by default; staff choose the file
again with "Preview only" unchecked to write. It refuses to write when any screening instrument's
items (`section11.REQUIRED_SCREEN_COLUMNS`) are missing from the export, since nobody could be
flagged on that screen, and it lists rows with a blank screening item, unrecognized columns and
unmatched IDs. Creation is all-or-nothing, and every flag it creates gets an Admin log entry
showing who imported it. Only staff with the add-flag permission can use it. The loader is
`qualtrics.read_export` (path or file-like; returns the frame plus diagnostics; `load_export`
wraps it and prints).

A suppressed decision point is logged with `send_prompt=False`, `randomization_draw` and
`randomization_probability` both null, `status` and `delivery_status` both `'suppressed'` (never
`not_sent`, which stays for ordinary ineligible or randomized-out decisions), and
`JITAILog.suppression_reason` set (`run_in`, `distress_baseline`, `distress_momentary`). That
column, not `trigger_reason`, is what the randomization audit and the timeline read, so a
suppressed row is never mistaken for a dropped prompt or an engine defect. It is exposed on the
JITAI API, on the monitor's decision events (outcome "suppressed (run-in)" / "suppressed
(distress)"), and in `analytics/scripts.py` (`load_jitai_log`, `audit_decision_stages`'s
`suppressed` column, the trajectory plot). `MetricsDaily.suppressed_n` counts them per day;
they stay in `decision_points_n` and out of `eligible_n`. Migration `0049` backfilled rows
written before the label existed (`run-in period` rows from commit `ded0498` got
`suppression_reason='run_in'`, and every suppressed row was relabeled `'suppressed'`); without
it the cohort audit reads those legacy rows as "eligible but no draw". `POST /ema/responses/` returns `resource_card` (null when no override
is active) after any check-in submitted under an active override. Card contents are
`RESOURCE_CARD_RESOURCES` in `distress.py`: the seven resources from the study's resource document
(the same seven as the baseline block), with digits-only contacts so tap-to-call works, and the
911 instruction in the card message. The backend cannot see the app version, so a participant on
an old build is suppressed but sees no card; every participant must be on the new build before Day 8.

Decided by Dr. Chang 2026-09-23, so do not add them to the backend: **staff alerts come from
Qualtrics** (it emails the PI and staff when a flagged baseline is submitted, so the same-day
alert exists outside this repo; the import only suppresses); **`contact_documented_at` is a
timestamp only**, with who called and what was given kept in the staff contact log, not Django
(no free-text storage); in-app flags are resource-card-only with no staff notice. Staff mark a
baseline flag documented in Admin after the log entry: same day for the two PHQ-9 flags, at the
next visit for alcohol, eating, gambling and food. An unmarked flag suppresses that participant
for the rest of the study, by design.

---

## Notification Pipeline

Silent pushes are used so notification content never transits Expo/Firebase servers as stored
data. Only the `prompt_id` (a reference to a template) is stored in `JITAILog`, never the
message text itself. This is a privacy/IRB constraint — do not store notification title or
message body in the database.

Expo push token is stored in `User.push_token` and updated on app launch.

---

## Researcher Dashboard

Three separate surfaces exist:

1. **Django Admin** (`app/admin.py`) — full CRUD over every model (`ReadableAdminMixin` +
   one `ModelAdmin` per model), the default researcher/PI surface today.
2. **Monitoring API** (`/api/monitor/*`, served by the `dashboard` app) — read-only
   feasibility and integrity metrics, auth'd with `DASHBOARD_API_KEY`. See Monitoring data
   layer below. Not a general study-data browser — that's still Django Admin.
   The unrelated Streamlit app at `analytics/REACT-dashboard/` was deleted in commit `1fdc6f3`;
   it only ever read the two `/dashboard/*` status endpoints and computed no feasibility metrics.
3. **Monitoring prototype** (`analytics/monitor_app/`) — a Streamlit multipage app: the
   participant grid ("who needs a phone call today") and the participant timeline ("what
   actually happened to this person"). Reads the monitoring API over HTTP with no database
   credential and no Django import, so it also serves as a check on those endpoints. Local
   prototype only; nothing deploys it. See `analytics/monitor_app/README.md`.
   Lanes are drawn as separate charts, never a composed one: Streamlit refuses selections on
   composed charts, and versions differ on whether they even allow rendering them.

### Monitoring data layer

The `dashboard` app lives at the **repo root**, not under `backend/`. `settings.py` puts the
repo root on `sys.path` so it is importable from web, worker and beat, all of which run with
`backend/` as their working directory.

**`dashboard/data/config.py` is the single authority for study constants.** Anything the
protocol fixes — the notification window, the caps, the cooldown, the threshold quantile, the
randomization probabilities, the benchmarks, the timezone — is defined there exactly once, and
`app/tasks.py`, `app/views.py` and `analytics/scripts.py` all import from it. `_evaluate_user`
passes those values explicitly into `apply_decision_rules` rather than relying on the engine's
defaults, so the engine and the monitor cannot drift apart silently. Do not reintroduce a
literal for any of them.

Four derived tables (`MetricsDaily`, `MetricsParticipant`, `MetricsCohort`, `Alert`) are
recomputed by `dashboard.tasks.recompute_monitoring_metrics` every **600 s**, on a trailing
3-day window that absorbs Labfront batch lag. They hold no collected data and can be dropped
and rebuilt. Only the timeline and funnel endpoints read raw tables, and only for one
participant at a time; the timeline is bounded to 14 days per request.

Two fields exist so the timeline can tell absence apart from failure, and both are
load-bearing:

- **`WearableSync`** is an append-only log of a device's sync clock advancing, written by
  `record_sync` from `/telemetry/ingest/` (source `ingest`) and the wearable PATCH (source
  `client`). `WearableDevice.last_synced_at` is a single mutable column, so without this
  table a sync outage cannot be told apart from genuine non-wear. **Nothing writes the sync
  clock today**: the mobile client is the intended writer but has no wearable code, and
  `ingest_wearable_data` is a stub. The layer reports that as unmeasurable — one cohort
  alert, no participant alerts, and no contribution to `risk_score` — rather than turning
  all 40 participants critical 72 hours after enrolling and leaving them there.
- **`JITAILog.threshold_at_decision`** records what `observed_mssd` was actually compared
  against; the engine always computed it as `user_threshold` and then discarded it.
  `threshold_source` is `engine` or `reconstructed` (via `manage.py backfill_thresholds`,
  which replays `app.tasks.build_decision_frame`, the same code path the live decision ran
  through). The dashboard's "should have been eligible" marker fires only on `engine` rows.

Three invariants the layer enforces, worth preserving in any change:

- **Structural null is not zero.** A participant-day outside the active range renders blank; a
  day inside it with no activity renders zero. Every metric column is nullable for this reason,
  and the grid endpoint carries the distinction to the wire.
- **Rates are suppressed on thin denominators.** A rate is emitted only above 10 participants
  and 30 units; below that the payload carries raw counts and a null value. Phase 1 (n=5) is
  therefore always counts, never percentages.
- **A benchmark with no source is marked unmeasurable**, never reported as zero. The same
  applies to alerts: a rule whose signal has no writer emits one cohort alert saying so,
  never one alert per participant.
- **An alert about the system is cohort-scoped, not per-participant.** `runin_violation` and
  `sync_stale`-with-no-writer are facts about the engine and the pipeline, and raising them
  per participant put an identical open critical on everyone, which flattened `risk_score`
  and buried the alerts view. Per-participant detail stays in `MetricsDaily`.

`Alert.SEVERITY_CHOICES` is a three-tier ladder, warning < high < critical, ordered by what is
at stake rather than by how long it has been true. **critical**: trial integrity is already
compromised or the whole pipeline is down (`runin_violation`, `cooldown_violation`,
`cap_exceeded`, `pipeline_stalled`, `no_wearable_data`). **high**: one participant's data is
being lost now and will keep being lost until someone acts (`sync_stale` past 72 h,
`no_ema_48h`, `wear_low`, `delivery_failures`). **warning**: worth watching, not worth a call
today. `Alert.SEVERITY_RANK` orders a response; sorting on the column puts critical before
warning only by accident of spelling. `Alert.ACTIONABLE_SEVERITIES` is what the risk score's
alert term reads.

**`risk_score` orders the participant grid** and is defined once, in
`dashboard/data/participant.py`. Six weighted terms over the trailing seven *active* days,
maximum 47, with each term's point contribution stored in `risk_components` so the ordering is
arguable rather than opaque. Weights are a starting point to tune after Phase 1. Two properties
to preserve: the score is **null, not zero**, for anyone the study is not currently asking
anything of (pre-enrollment, complete, withdrawn), since every term reads as maximally bad for
them and they would otherwise dominate the top of the RA's call list; and only `critical` alerts
move it, because `Alert.SEVERITY_CHOICES` has no `high`.

`EMA.served_sub_item_ids` records what a check-in actually put on screen, which is what makes
item completeness measurable at all. `/ema/next/` returns the list, the client may echo it back,
and the server recomputes the same set on submit when it does not.

One engine defect the monitor deliberately surfaces rather than works around, documented in
`analytics/analysis-resources/production_schema.md`: `apply_decision_rules` counts its **daily
cap over UTC days** while everything else is Eastern. The run-in gate lives in `_evaluate_user`
(study day `< RUN_IN_DAYS` from `enrolled_at`, fail-closed when `enrolled_at` is null); the
`runin_violation` alert is now the regression tripwire for it.

`analytics/reconcile_monitoring.py` is what keeps the ORM implementation and
`analytics/scripts.py` in step. Run it after changing any metric definition.

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
