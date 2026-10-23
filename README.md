# REACT

REACT is a Just-In-Time Adaptive Intervention (JITAI) mHealth system built at the SMASH Research Lab, University of Florida, under PI Dr. Yonghwan Chang (Department of Sport Management). Participants answer short in-app check-ins (EMA) while wearing a Garmin Venu 3. The backend computes MSSD over their recent check-ins, and at each eligible decision point it randomizes whether to send a coping prompt (a micro-randomized trial). Researchers monitor the study through Django Admin and a read-only monitoring API.

The target deployment is about 300 participants across a UF football season (late August to early December, 12–14 weeks).

## Tech stack

| Layer | Technology |
|---|---|
| Backend | Django REST Framework (Python 3.11), PostgreSQL |
| Task queue | Celery + Redis |
| Mobile | React Native, Expo SDK 56 (dev client; Expo Go does not work) |
| Push | Expo Push Notification Service → Firebase / APNs |
| Wearables | Garmin Venu 3 via the Labfront research platform |
| Hosting | Heroku (`Procfile`), Sentry for errors |

## Architecture

`Procfile` runs a `release` step (`manage.py migrate`) and three processes, all from `backend/`:

- **web**: `gunicorn project.wsgi`, the DRF API and Django Admin
- **worker**: `celery -A project.celery worker`, runs the notification and JITAI tasks
- **beat**: `celery -A project.celery beat`, schedules five periodic tasks (`CELERY_BEAT_SCHEDULE` in `backend/project/settings.py`):
  - every 180 s: `ingest_wearable_data`, `evaluate_jitai_triggers`, `send_checkin_reminders`, `check_expo_receipts`
  - every 600 s: `dashboard.tasks.recompute_monitoring_metrics`

Wearable data reaches REACT only through Labfront. REACT never talks to Garmin and never holds Garmin OAuth tokens.

```
Garmin Venu 3 → Garmin Health API → Labfront → REACT (ingest / import) → PostgreSQL
  → decision engine → Expo push → participant device
```

The app receives two kinds of push:

- **Check-in reminders** (visible). The day from 9:00 to 21:00 participant-local time is split into six two-hour slots. A reminder goes out 30 minutes into a slot only if no check-in has landed in it yet.
- **JITAI prompts** (silent). These are sent only after a newly completed check-in passes eligibility (MSSD threshold, run-in, distress, cooldown, daily cap) and the randomization draw. Every decision point, sent or not, opens a time-locked outcome window, so both arms are measured on the same schedule.

The MSSD math lives in `backend/decision_engine/decision_engine.py` and is regression-tested against a golden CSV. Every protocol constant (window, caps, cooldown, threshold quantile, randomization probability, timezone) is defined once, in `dashboard/data/config.py`.

## Repository layout

```
backend/
  project/           Django project: settings.py, celery.py, urls.py (all routes), test_settings.py
  app/               models, serializers, views, tasks, admin, distress.py, ema_catalog.py,
                     notification_service.py, management/commands/
  decision_engine/   standalone MSSD / JITAI logic + golden-CSV scenario tests (pure pandas)
  syntheticData/     synthetic cohort generators
  dress_rehearsal.py, full_circle_test.py   manual end-to-end QA scripts
dashboard/           monitoring Django app (repo root, NOT under backend/): derived metric
                     tables, /api/monitor/* views, recompute task, data/config.py
mobile/              Expo app; source in src/, native android/ and ios/ are committed
analytics/
  scripts.py                 ORM-backed loaders + pandas metrics
  reconcile_monitoring.py    live-vs-offline metric agreement check
  monitor_app/               Streamlit monitoring prototype (cohort board, participant grid, timeline)
  baseline_survey_scoring/   Qualtrics baseline scoring for the Section 11 distress screens
  sensitivity_analysis/      MSSD parameter recovery / robustness notebooks
  analysis-resources/        data dictionary, production schema map, analysis plan
Resources/           study documents (API contract, enrollment guide, schema PDFs)
docs/superpowers/    historical design specs from the original model build-out
```

## Getting started

### Backend

From `backend/`, with Python 3.11:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt          # requirements_mac.txt on Apple Silicon
python manage.py migrate
python manage.py createsuperuser
python manage.py runserver 0.0.0.0:8000

# in separate terminals (needs a running Redis)
celery -A project.celery worker --loglevel=info
celery -A project.celery beat   --loglevel=info
```

`settings.py` reads everything from the environment. It picks the database from `DATABASE_URL`, then Cloud SQL (when `K_SERVICE` is set), then the discrete `DATABASE_*` variables. Other variables:

| Variable | Purpose |
|---|---|
| `SECRET_KEY` | Django secret |
| `REDIS_URL` / `CELERY_BROKER_URL` | Celery broker |
| `API_KEY` | When set, API routes require a matching `X-API-Key` header |
| `DASHBOARD_API_KEY` | Key for `/dashboard/*` and `/api/monitor/*` |
| `SENTRY_DSN` | Error reporting |
| `JITAI_RANDOMIZATION_PROBABILITY` | Randomization probability (default `0.5`) |

> [!WARNING]
> Check which database your `.env` points at before running anything that writes. A local `.env` can point at production, and `dress_rehearsal.py --reset` and `analytics/reconcile_monitoring.py` both write to whatever database it names.

### Mobile

You need Node 20.19.4 or newer and a physical device. Expo Go does not work with this app, so it runs as a development build.

```bash
cd mobile && npm install
npx expo run:ios --device        # or: npx expo run:android --device
npx expo start --dev-client      # afterwards, once the native build exists
```

`mobile/.env` must set `EXPO_PUBLIC_API_KEY` to the backend's `API_KEY`, or requests get a 403. The app targets production by default. To use a local backend, set `ACTIVE_ENV` to `'dev'` in `mobile/src/api/config.ts`, and don't commit that change. Full device setup, notifications and troubleshooting are in [mobile/README.md](mobile/README.md).

### Monitoring prototype

The prototype is a Streamlit app that reads `/api/monitor/*` over HTTP. It has no database credential and does not import Django.

```bash
cd analytics && pip install -r requirements.txt
export MONITOR_BASE_URL=http://localhost:8000 DASHBOARD_API_KEY=...
streamlit run monitor_app/app.py
```

Do not export `API_KEY` in this shell. It is not needed here, and it would switch on API-key checks for every route of a local backend sharing that shell. See [analytics/monitor_app/README.md](analytics/monitor_app/README.md).

## Testing

```bash
# from backend/ — SQLite in-memory; the custom runner supplies labels and blanks API keys
python manage.py test --settings=project.test_settings
python manage.py test app.tests.EMANextViewTests --settings=project.test_settings

# dashboard tests must run from the repo root
python3 backend/manage.py test dashboard --settings=project.test_settings

# decision-engine scenario tests (plain unittest, from backend/)
python -m unittest decision_engine.test_decision_engine_scenarios

# mobile (no test suite; these are the checks)
cd mobile && npx tsc --noEmit && npx expo export --platform ios --output-dir /tmp/x
```

GitHub Actions (`.github/workflows/django-ci.yml`) runs on every push: first the decision-engine scenario tests, then `migrate` and `manage.py test` against SQLite. A passing push to `main` deploys to Heroku automatically.

## Management commands

Run these from `backend/`. The import, backfill and test-push commands accept `--dry-run`.

| Command | What it does |
|---|---|
| `recompute_metrics [--all] [--user N --days D]` | Rebuild the monitoring tables (the periodic task covers the trailing 3 days) |
| `import_labfront_export <path>` | Load a Labfront export (zip, CSV or folder) into the HR / stress / HRV tables |
| `import_baseline_distress_flags --file export.csv` | Score a Qualtrics baseline export and write Section 11 `DistressFlag` rows (also available as a button in Django Admin) |
| `import_event_days <csv>` | Load UF sporting event dates into `EventDay` |
| `dump_item_bank --check` | Fail if the EMA item bank drifted from the frozen JSON |
| `backfill_thresholds`, `backfill_withdrawals`, `backfill_enrollment`, `backfill_distress_flag_active` | One-off backfills for columns added after data was collected |
| `send_test_jitai_push --user-id N` | Send a content-free test JITAI push |

There are also two manual QA scripts (not part of CI): `dress_rehearsal.py` simulates participants at the ORM layer, and `full_circle_test.py --base-url URL` exercises a running backend over HTTP.

## API

All routes are registered in [backend/project/urls.py](backend/project/urls.py). Interactive docs are served at `/swagger/`.

Authentication has three parts:

- `APIKeyMiddleware` requires `X-API-Key` when `API_KEY` is set.
- SimpleJWT handles user auth on top of that.
- The read-only `/dashboard/*` and `/api/monitor/*` endpoints take either a staff session or an `X-Dashboard-API-Key` header.

## Research and privacy constraints

- **No free text or message bodies in the database (IRB).** `JITAILog` stores only a `prompt_id` template reference, and JITAI pushes are silent.
- **Labfront owns wearable OAuth.** The only Labfront identifier stored is `labfront_participant_id`. There is no Fitbit or Fitabase integration.
- **Protocol values need PI sign-off.** That covers thresholds, caps and other protocol values. Change them only in `dashboard/data/config.py`, and never hardcode a literal elsewhere.
- **Distress override (protocol Section 11).** A baseline-screen flag or a momentary check-in flag suspends randomization for that participant. The app then shows a resource card instead of a coping message. See `backend/app/distress.py`.
- **Monitoring payloads name participants by study ID, never by email.**

## Deployment

Production runs on Heroku and deploys from `main` through CI. The base URL is `https://react-backend-prod-8db300645555.herokuapp.com`, with Django Admin at `/admin` and API docs at `/swagger/`. `cloudbuild.yaml` (GCP) is dormant.

## Further documentation

- [CLAUDE.md](CLAUDE.md): detailed architecture, endpoint table, distress override, monitoring invariants
- [mobile/README.md](mobile/README.md): device builds, notifications, check-in behavior
- [analytics/monitor_app/README.md](analytics/monitor_app/README.md): monitoring prototype
- [analytics/analysis-resources/production_schema.md](analytics/analysis-resources/production_schema.md) and [data-dictionary.md](analytics/analysis-resources/data-dictionary.md): live schema
- [Resources/REACT_Admin_Enrollment.md](Resources/REACT_Admin_Enrollment.md): enrolling participants

## People

- **Prof. Yonghwan Chang**: PI, Department of Sport Management, UF
- SMASH Research Lab student engineers
- REACT builds on the HealthyGator platform developed by earlier UF student teams.
