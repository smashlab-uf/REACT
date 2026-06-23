# REACT — Claude Code Context

## Project Overview

REACT (working title) is a research-grade Just-In-Time Adaptive Intervention (JITAI) mHealth
system built at the SMASH Research Lab, University of Florida, under PI Dr. Yonghwan Chang
(Department of Sport Management). It extends the prior HealthyGatorSportsFan platform, which
delivered push notifications to UF sports fans during games.

REACT adds:
- EMA (Ecological Momentary Assessment) in-app surveys
- Garmin wearable physiological data collection via Fitabase
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
| Deployment | Heroku |
| Push Notifications | Expo Push Notification Service → Firebase/APNs |
| Wearable Data Layer | Fitabase API (intermediary for Garmin Health API) |
| Wearable Device | Garmin Vivoactive 6 |

---

## Codebase Structure (HealthyGator baseline)

```
HealthyGatorSportsFanDjango/
  app/
    models.py          # Django ORM models
    serializers.py     # DRF serializers (one per model)
    views.py           # DRF view functions
    urls.py            # URL routing
    admin.py           # Django Admin registrations
    tasks.py           # Celery tasks (notification logic)
    migrations/        # Django migration history

HealthyGatorSportsFanRN/
  App.tsx              # React Native entry point
  screens/             # One .tsx file per screen
  constants/           # URL management, shared constants
```

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
    height_feet = models.CharField(max_length=10, default="")
    height_inches = models.CharField(max_length=10, default="")
    goal_weight = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    goal_to_lose_weight = models.BooleanField(default=False)
    goal_to_feel_better = models.BooleanField(default=False)
    password = models.CharField(max_length=128, blank=True, null=True)
    push_token = models.CharField(max_length=128, blank=True, null=True)
    # REACT additions:
    is_enrolled = models.BooleanField(default=False)
    enrolled_at = models.DateTimeField(null=True, blank=True)
    # DO NOT add any Fitbit token fields — Fitabase owns OAuth entirely

class UserData(models.Model):
    data_id = models.AutoField(primary_key=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    timestamp = models.DateTimeField(auto_now_add=True)
    goal_type = models.CharField(max_length=20, choices=[...])
    weight_value = models.DecimalField(...)
    feel_better_value = models.IntegerField(...)

class NotificationData(models.Model):
    notification_id = models.AutoField(primary_key=True)
    notification_message = models.CharField(max_length=255)
    timestamp = models.DateTimeField(auto_now_add=True)
    read_status = models.BooleanField(default=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
```

### New Models (REACT)

```python
class WearableDevice(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    fitabase_participant_id = models.CharField(max_length=64, unique=True)
    device_name = models.CharField(max_length=100, blank=True, null=True)
    is_active = models.BooleanField(default=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)

class HeartRateSample(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    timestamp = models.DateTimeField(db_index=True)
    bpm = models.PositiveSmallIntegerField()
    source = models.CharField(max_length=32, default='garmin_fitabase')
    class Meta:
        ordering = ['-timestamp']
        indexes = [models.Index(fields=['user', 'timestamp'])]

class StressSample(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    timestamp = models.DateTimeField(db_index=True)
    stress_score = models.PositiveSmallIntegerField()  # 0–100 Garmin scale
    source = models.CharField(max_length=32, default='garmin_fitabase')
    class Meta:
        ordering = ['-timestamp']
        indexes = [models.Index(fields=['user', 'timestamp'])]

class SleepSummary(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    date = models.DateField()
    total_minutes = models.PositiveSmallIntegerField(null=True, blank=True)
    light_minutes = models.PositiveSmallIntegerField(null=True, blank=True)
    deep_minutes = models.PositiveSmallIntegerField(null=True, blank=True)
    rem_minutes = models.PositiveSmallIntegerField(null=True, blank=True)
    awake_minutes = models.PositiveSmallIntegerField(null=True, blank=True)
    sleep_score = models.PositiveSmallIntegerField(null=True, blank=True)
    source = models.CharField(max_length=32, default='garmin_fitabase')
    class Meta:
        unique_together = ('user', 'date')

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
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='delivered')
    class Meta:
        ordering = ['-triggered_at']
```

---

## API Endpoints

### Existing (HealthyGator — do not break)

| Method | Path | Purpose |
|---|---|---|
| POST | `/user/` | Create user account |
| GET | `/user/login/` | Authenticate user |
| PUT | `/user/{user_id}/` | Update user profile |
| POST | `/user/checkemail/` | Check if email already exists |
| GET | `/userdata/latest/{user_id}/` | Get latest user progress |
| POST | `/userdata/{user_id}/` | Log user progress |
| GET | `/notificationdata/{user_id}/` | List notifications for user |
| POST | `/notificationdata/` | Create notification record |
| DELETE | `/notificationdata/delete/{notification_id}/` | Delete one notification |
| DELETE | `/notificationdata/deleteall/{user_id}/` | Delete all notifications for user |

### New (REACT)

| Method | Path | Purpose |
|---|---|---|
| POST | `/wearable/` | Register Fitabase participant ID on enrollment |
| GET | `/wearable/{user_id}/` | Get device record |
| PATCH | `/wearable/{user_id}/` | Update device record (e.g. last_synced_at) |
| POST | `/ema/` | Submit EMA response from mobile app |
| GET | `/ema/{user_id}/` | Fetch EMA history (dashboard/admin use) |
| POST | `/jitai/` | Internal — Celery logs a triggered intervention |
| GET | `/jitai/{user_id}/` | Fetch JITAI history (dashboard/admin use) |
| GET | `/telemetry/hr/{user_id}/` | Fetch recent HR samples (dashboard use) |
| GET | `/telemetry/stress/{user_id}/` | Fetch recent stress samples (dashboard use) |

Telemetry ingest (HeartRateSample, StressSample, SleepSummary) is written directly via the
Django ORM inside Celery tasks — no public REST endpoints for ingest.

---

## Wearable Data Architecture

REACT never communicates with Garmin directly. The data flow is:

```
Garmin Vivoactive 6
  → Garmin Health API (push on device sync)
    → Fitabase (buffers and re-exposes data)
      → REACT Celery ingestion task (polls Fitabase API)
        → PostgreSQL (HeartRateSample, StressSample, SleepSummary)
          → JITAI decision logic
            → Expo Push Notification → participant device
```

### Fitabase Garmin Data Resolutions

| Resolution | Dataset |
|---|---|
| Daily | Stress, Steps, Floors, VO2max, PulseOx, MoveIQ, Body composition, Calories, HRV nightly avg, Weartime, Activity logs |
| 15 min epochs | Steps, Heart rate, Distance, MET, Intensity, MeanMotion/MaxMotion |
| 3 min | Stress score (primary real-time JITAI signal alongside HR) |
| Per stage change | Sleep stage records |
| 15 sec | Heart rate (primary HR ingest stream; ~720 rows/participant/3hr game) |
| Optional add-on | Beat-to-beat RR intervals (enhanced HRV; not default) |

### Fitabase API vs Batch Export
- API access and automated batch exports are separate paid add-ons (not included by default)
- REACT targets the Fitabase API for programmatic access via Celery periodic tasks
- During game windows, the Celery task should poll at short intervals (every 2–3 min) to support
  near-real-time JITAI triggering
- The `fitabase_participant_id` in `WearableDevice` is the key used to query Fitabase per participant

---

## Celery Task Architecture

Celery Beat schedules periodic tasks. Redis is the message broker.

Key tasks:
- `ingest_wearable_data` — polls Fitabase API for all enrolled participants, writes new
  HeartRateSample, StressSample, SleepSummary rows
- `evaluate_jitai_triggers` — reads recent telemetry per participant, applies decision logic,
  fires Expo push notification and writes JITAILog if thresholds are met
- Game-window awareness — tasks should run at higher frequency during active UF football
  game windows and at low/no frequency outside game days

---

## JITAI Decision Logic

JITAI triggers are based on combinations of biometric signals and EMA responses. The exact
threshold values are a research design decision requiring PI (Prof. Chang) sign-off before
implementation. Do not hardcode thresholds without confirmation.

Candidate trigger signals:
- `HeartRateSample.bpm` — elevated HR during game window suggests physiological arousal
- `StressSample.stress_score` — high stress score complements EMA self-report
- `EMA.mood / EMA.stress / EMA.energy` — self-reported emotional state
- `SleepSummary` — prior-night sleep context for next-day intervention decisions

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

Built as a Django Admin extension — no separate React frontend. Key views:

- Participant overview — enrollment status, last EMA, last wearable sync, JITAI count this week
- EMA monitor — completion rates, response latency, Likert score trends per participant
- JITAI log viewer — delivery/open/interaction rates, trigger reason breakdown
- CSV export action on all views for offline analysis (R / SPSS)

Access control: two Django permission groups (`researcher_pi` — full access;
`researcher_ra` — read-only on EMA and JITAILog, no access to User PII). Confirm
RA access scope with Prof. Chang before implementing.

Fitabase's own dashboard handles device compliance monitoring (battery, sync times, wear
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
- Environment variables for all secrets (Fitabase API key, Redis URL, database URL) —
  never hardcode

---

## Key Constraints

- **No Fitbit anything** — Fitbit OAuth, token fields, and device IDs are fully dropped.
  Do not reintroduce any Fitbit-specific code.
- **No free-text storage** — EMA `notes` fields and notification message body must not be
  stored in the database. IRB constraint.
- **Fitabase owns OAuth** — REACT never holds Garmin OAuth tokens. The only Fitabase
  identifier stored per participant is `fitabase_participant_id`.
- **JITAI thresholds need PI sign-off** — do not implement numeric trigger thresholds
  without confirmation from Prof. Chang.
- **Fitabase Engage is deferred** — REACT builds its own JITAI and EMA delivery layer.
  Do not integrate with Fitabase Engage (preview Summer 2026).

---

## Open Questions (as of project start)

- Fitabase API vs batch export — confirm with Prof. Chang (determine pricing/access)
- JITAI trigger thresholds — numeric values require PI alignment before implementation
- RA access scope — which fields are RAs permitted to see under IRB protocol
- Garmin Vivoactive 6 Fitabase compatibility — verify all required data streams are
  supported before finalizing the Fitabase contract
- Beat-to-beat RR interval collection — confirm device support and IRB permission

---

## People

- **Dustin** — student software engineer, primary developer
- **Prof. Yonghwan Chang** — PI, Department of Sport Management, UF. Owns research
  design and IRB scope. Key sync: Fridays.
- **Prior platform team** — HealthyGatorSportsFan (football + basketball iterations) built
  by prior student teams. Codebase is the starting point for REACT.
