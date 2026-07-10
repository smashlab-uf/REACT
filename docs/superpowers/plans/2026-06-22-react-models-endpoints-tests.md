# REACT New Models, Endpoints & Tests Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `SleepSummary`, `PhoneTelemetry`, and `EngagementLog` models with serializers, REST endpoints, and full test coverage matching the REACT_Architecture.docx spec.

**Architecture:** All new code follows the existing single-file-per-layer pattern: models in `app/models.py`, serializers in `app/serializers.py`, views in `app/views.py`, URLs in `project/urls.py`, admin in `app/admin.py`, tests in `app/tests.py`. `PhoneTelemetry.metadata` enforces an IRB no-free-text constraint via a DRF serializer-level validator. `game_clock_state` is stamped server-side on ingest using a new `get_game_clock_state()` utility. All new endpoints require `IsAuthenticated`.

**Tech Stack:** Django REST Framework, SimpleJWT (`rest_framework_simplejwt`), Django ORM, `models.JSONField` (works with both PostgreSQL and SQLite ≥ Django 3.1)

## Global Constraints

- Django ORM only — no raw SQL
- All new endpoints: `permission_classes = [IsAuthenticated]`
- One `ModelSerializer` per model, explicit `fields` list, `read_only_fields` for auto PKs and timestamps
- No comments in code
- Tests run from `backend/` with: `python3 manage.py test app --settings=project.test_settings`
- Baseline: 95 tests, all passing — do not break them
- `PhoneTelemetry.metadata` string values must not exceed 50 chars (IRB)
- `JITAILog.prompt_id` stores template references only — never message text (IRB)
- `game_clock_state` is always server-stamped on `POST /telemetry/phone/` and `POST /telemetry/engagement/` — never accepted from client

---

## File Map

| File | Action | What changes |
|---|---|---|
| `app/models.py` | Modify | Add `SleepSummary`, `PhoneTelemetry`, `EngagementLog`, `validate_phone_metadata`, choice tuples |
| `app/migrations/` | Auto-generate | `python3 manage.py makemigrations` after Task 1 |
| `app/serializers.py` | Modify | Fix `JITAILogSerializer`; add `SleepSummarySerializer`, `PhoneTelemetrySerializer`, `EngagementLogSerializer` |
| `app/views.py` | Modify | Add 11 new view classes |
| `project/urls.py` | Modify | Wire 11 new URL patterns |
| `app/admin.py` | Modify | Register 3 new models |
| `app/utils.py` | Modify | Add `get_game_clock_state()` |
| `app/tests.py` | Modify | Add ~50 new test methods covering all new code |

---

## Task 1: New Models + Migration + Admin

**Files:**
- Modify: `app/models.py`
- Create: migration via `python3 manage.py makemigrations`
- Modify: `app/admin.py`
- Modify: `app/tests.py`

**Interfaces:**
- Produces: `SleepSummary`, `PhoneTelemetry`, `EngagementLog`, `validate_phone_metadata` importable from `app.models`
- Produces: `PHONE_EVENT_TYPES`, `ENGAGEMENT_EVENT_TYPES`, `GAME_CLOCK_STATES` tuples on the module

- [ ] **Step 1: Write the failing model tests**

Append to `app/tests.py`:

```python
# ---------------------------------------------------------------------------
# Model: SleepSummary
# ---------------------------------------------------------------------------

@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class SleepSummaryModelTests(TestCase):

    def test_creates_sleep_summary(self):
        from app.models import SleepSummary
        user = make_user()
        summary = SleepSummary.objects.create(
            user=user,
            date='2026-08-31',
            total_minutes=420,
            deep_minutes=90,
            sleep_score=78,
        )
        self.assertEqual(summary.total_minutes, 420)
        self.assertEqual(summary.sleep_score, 78)
        self.assertEqual(summary.source, 'garmin_fitabase')

    def test_unique_together_user_date(self):
        from django.db import IntegrityError
        from app.models import SleepSummary
        user = make_user()
        SleepSummary.objects.create(user=user, date='2026-08-31', total_minutes=420)
        with self.assertRaises(IntegrityError):
            SleepSummary.objects.create(user=user, date='2026-08-31', total_minutes=380)

    def test_all_minute_fields_are_nullable(self):
        from app.models import SleepSummary
        user = make_user()
        summary = SleepSummary.objects.create(user=user, date='2026-09-01')
        self.assertIsNone(summary.total_minutes)
        self.assertIsNone(summary.light_minutes)
        self.assertIsNone(summary.deep_minutes)
        self.assertIsNone(summary.rem_minutes)
        self.assertIsNone(summary.awake_minutes)
        self.assertIsNone(summary.sleep_score)

    def test_deleting_user_deletes_sleep_summaries(self):
        from app.models import SleepSummary
        user = make_user()
        SleepSummary.objects.create(user=user, date='2026-08-31', total_minutes=420)
        user.delete()
        self.assertEqual(SleepSummary.objects.count(), 0)


# ---------------------------------------------------------------------------
# Model: PhoneTelemetry
# ---------------------------------------------------------------------------

@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class PhoneTelemetryModelTests(TestCase):

    def test_creates_phone_telemetry_event(self):
        from app.models import PhoneTelemetry
        user = make_user()
        event = PhoneTelemetry.objects.create(
            user=user,
            session_id='SESSION_ABC',
            event_type='draft_started',
            occurred_at=timezone.now(),
            game_clock_state='live',
        )
        self.assertEqual(event.event_type, 'draft_started')
        self.assertEqual(event.game_clock_state, 'live')
        self.assertIsNotNone(event.recorded_at)

    def test_metadata_is_nullable(self):
        from app.models import PhoneTelemetry
        user = make_user()
        event = PhoneTelemetry.objects.create(
            user=user,
            session_id='SESSION_ABC',
            event_type='session_start',
            occurred_at=timezone.now(),
        )
        self.assertIsNone(event.metadata)

    def test_irb_validator_rejects_long_string_in_metadata(self):
        from django.core.exceptions import ValidationError
        from app.models import PhoneTelemetry
        user = make_user()
        event = PhoneTelemetry(
            user=user,
            session_id='SESSION_ABC',
            event_type='draft_submitted',
            occurred_at=timezone.now(),
            metadata={'text': 'x' * 51},
        )
        with self.assertRaises(ValidationError):
            event.full_clean()

    def test_irb_validator_accepts_short_string_in_metadata(self):
        from app.models import PhoneTelemetry
        user = make_user()
        event = PhoneTelemetry(
            user=user,
            session_id='SESSION_ABC',
            event_type='draft_submitted',
            occurred_at=timezone.now(),
            metadata={'label': 'submit'},
        )
        event.full_clean()

    def test_irb_validator_accepts_integer_metadata_values(self):
        from app.models import PhoneTelemetry
        user = make_user()
        event = PhoneTelemetry(
            user=user,
            session_id='SESSION_ABC',
            event_type='draft_submitted',
            occurred_at=timezone.now(),
            metadata={'keystroke_count': 42, 'delete_count': 5},
        )
        event.full_clean()

    def test_deleting_user_deletes_phone_telemetry(self):
        from app.models import PhoneTelemetry
        user = make_user()
        PhoneTelemetry.objects.create(
            user=user, session_id='S1', event_type='session_start', occurred_at=timezone.now()
        )
        user.delete()
        self.assertEqual(PhoneTelemetry.objects.count(), 0)


# ---------------------------------------------------------------------------
# Model: EngagementLog
# ---------------------------------------------------------------------------

@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class EngagementLogModelTests(TestCase):

    def test_creates_engagement_event(self):
        from app.models import EngagementLog
        user = make_user()
        log = EngagementLog.objects.create(
            user=user,
            event_type='ema_completed',
            occurred_at=timezone.now(),
            game_clock_state='live',
        )
        self.assertEqual(log.event_type, 'ema_completed')
        self.assertIsNone(log.jitai_log)
        self.assertIsNotNone(log.recorded_at)

    def test_jitai_log_is_nullable(self):
        from app.models import EngagementLog
        user = make_user()
        log = EngagementLog.objects.create(
            user=user, event_type='ema_opened', occurred_at=timezone.now()
        )
        self.assertIsNone(log.jitai_log)

    def test_jitai_log_deletion_sets_null(self):
        from app.models import EngagementLog
        user = make_user()
        jitai = JITAILog.objects.create(
            user=user, prompt_id='T1', trigger_reason='hr_elevated'
        )
        log = EngagementLog.objects.create(
            user=user,
            jitai_log=jitai,
            event_type='notification_tapped',
            occurred_at=timezone.now(),
        )
        jitai.delete()
        log.refresh_from_db()
        self.assertIsNone(log.jitai_log)

    def test_deleting_user_deletes_engagement_logs(self):
        from app.models import EngagementLog
        user = make_user()
        EngagementLog.objects.create(
            user=user, event_type='ema_completed', occurred_at=timezone.now()
        )
        user.delete()
        self.assertEqual(EngagementLog.objects.count(), 0)
```

- [ ] **Step 2: Run tests to verify they all fail**

```bash
cd backend
python3 manage.py test app.tests.SleepSummaryModelTests app.tests.PhoneTelemetryModelTests app.tests.EngagementLogModelTests --settings=project.test_settings 2>&1 | tail -5
```
Expected: `ImportError` or `AttributeError` — models do not exist yet.

- [ ] **Step 3: Add the three models to `app/models.py`**

Insert after the `EMA` model class (before `JITAILog`), then update the import at the top to include `ValidationError`:

At the **top** of `app/models.py` add this import:
```python
from django.core.exceptions import ValidationError
```

After the `EMA` class, insert:

```python
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

    def __str__(self):
        return f"Sleep for {self.user.email} on {self.date}"


PHONE_EVENT_TYPES = [
    ('draft_started', 'Draft Started'),
    ('draft_deleted', 'Draft Deleted'),
    ('draft_submitted', 'Draft Submitted'),
    ('session_start', 'Session Start'),
    ('session_end', 'Session End'),
]

ENGAGEMENT_EVENT_TYPES = [
    ('ema_opened', 'EMA Opened'),
    ('ema_dismissed', 'EMA Dismissed'),
    ('ema_completed', 'EMA Completed'),
    ('notification_tapped', 'Notification Tapped'),
    ('notification_dismissed', 'Notification Dismissed'),
]

GAME_CLOCK_STATES = [
    ('pre', 'Pre-Game'),
    ('live', 'Live'),
    ('post', 'Post-Game'),
]


def validate_phone_metadata(value):
    if value is None:
        return
    for v in value.values():
        if isinstance(v, str) and len(v) > 50:
            raise ValidationError(
                "metadata string values must not exceed 50 characters"
            )


class PhoneTelemetry(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    session_id = models.CharField(max_length=64)
    event_type = models.CharField(max_length=64, choices=PHONE_EVENT_TYPES)
    occurred_at = models.DateTimeField(db_index=True)
    recorded_at = models.DateTimeField(auto_now_add=True)
    game_clock_state = models.CharField(max_length=16, choices=GAME_CLOCK_STATES, default='pre')
    screen_name = models.CharField(max_length=64, null=True, blank=True)
    latency_ms = models.IntegerField(null=True, blank=True)
    metadata = models.JSONField(null=True, blank=True, validators=[validate_phone_metadata])

    class Meta:
        ordering = ['-occurred_at']
        indexes = [models.Index(fields=['user', 'occurred_at'])]

    def __str__(self):
        return f"{self.event_type} for {self.user.email} at {self.occurred_at}"
```

Then insert `EngagementLog` **after** `JITAILog` (it needs the `JITAILog` FK):

```python
class EngagementLog(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    jitai_log = models.ForeignKey(JITAILog, on_delete=models.SET_NULL, null=True, blank=True)
    event_type = models.CharField(max_length=64, choices=ENGAGEMENT_EVENT_TYPES)
    occurred_at = models.DateTimeField(db_index=True)
    recorded_at = models.DateTimeField(auto_now_add=True)
    game_clock_state = models.CharField(max_length=16, choices=GAME_CLOCK_STATES, default='pre')

    class Meta:
        ordering = ['-occurred_at']
        indexes = [models.Index(fields=['user', 'occurred_at'])]

    def __str__(self):
        return f"{self.event_type} for {self.user.email} at {self.occurred_at}"
```

- [ ] **Step 4: Generate and apply migration**

```bash
python3 manage.py makemigrations --settings=project.test_settings
python3 manage.py migrate --settings=project.test_settings
```
Expected: migration file created and applied without errors.

- [ ] **Step 5: Register new models in `app/admin.py`**

Add to imports:
```python
from .models import (
    EMA, EngagementLog, HeartRateSample, JITAILog,
    PhoneTelemetry, SleepSummary, StressSample, User, UserData, WearableDevice,
)
```

Append these three admin classes at the bottom of `app/admin.py`:

```python
@admin.register(SleepSummary)
class SleepSummaryAdmin(ReadableAdminMixin, admin.ModelAdmin):
    list_display = ("id", "user", "date", "total_minutes", "sleep_score", "source")
    list_filter = ("source", "date")
    search_fields = ("user__email",)
    date_hierarchy = "date"
    ordering = ("-date",)
    autocomplete_fields = ("user",)


@admin.register(PhoneTelemetry)
class PhoneTelemetryAdmin(ReadableAdminMixin, admin.ModelAdmin):
    list_display = ("id", "user", "session_id", "event_type", "occurred_at", "game_clock_state", "screen_name")
    list_filter = ("event_type", "game_clock_state", "occurred_at")
    search_fields = ("user__email", "session_id")
    date_hierarchy = "occurred_at"
    ordering = ("-occurred_at",)
    autocomplete_fields = ("user",)
    readonly_fields = ("recorded_at",)


@admin.register(EngagementLog)
class EngagementLogAdmin(ReadableAdminMixin, admin.ModelAdmin):
    list_display = ("id", "user", "event_type", "occurred_at", "game_clock_state", "jitai_log")
    list_filter = ("event_type", "game_clock_state", "occurred_at")
    search_fields = ("user__email",)
    date_hierarchy = "occurred_at"
    ordering = ("-occurred_at",)
    autocomplete_fields = ("user",)
    readonly_fields = ("recorded_at",)
```

- [ ] **Step 6: Run model tests — expect PASS**

```bash
python3 manage.py test app.tests.SleepSummaryModelTests app.tests.PhoneTelemetryModelTests app.tests.EngagementLogModelTests --settings=project.test_settings 2>&1 | tail -5
```
Expected: `Ran 13 tests in X.XXXs` / `OK`

- [ ] **Step 7: Run full suite — expect no regressions**

```bash
python3 manage.py test app --settings=project.test_settings 2>&1 | grep -E "^(OK|FAIL|ERROR|Ran)"
```
Expected: `Ran 108 tests in X.XXXs` / `OK`

- [ ] **Step 8: Commit**

```bash
git add app/models.py app/admin.py app/migrations/
git commit -m "feat: add SleepSummary, PhoneTelemetry, EngagementLog models and admin"
```

---

## Task 2: New Serializers + Fix JITAILogSerializer

**Files:**
- Modify: `app/serializers.py`
- Modify: `app/tests.py`

**Interfaces:**
- Consumes: `SleepSummary`, `PhoneTelemetry`, `EngagementLog` from `app.models`
- Produces: `SleepSummarySerializer`, `PhoneTelemetrySerializer`, `EngagementLogSerializer` importable from `app.serializers`
- Produces: `JITAILogSerializer` now exposes `ema`, `observed_mssd`, `send_prompt`

- [ ] **Step 1: Write the failing serializer tests**

Append to `app/tests.py`:

```python
# ---------------------------------------------------------------------------
# Serializer: SleepSummary
# ---------------------------------------------------------------------------

@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class SleepSummarySerializerTests(TestCase):

    def test_serializer_creates_sleep_summary(self):
        from app.models import SleepSummary
        from app.serializers import SleepSummarySerializer
        user = make_user()
        data = {
            'user': user.user_id,
            'date': '2026-08-31',
            'total_minutes': 420,
            'deep_minutes': 90,
            'sleep_score': 78,
        }
        serializer = SleepSummarySerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        summary = serializer.save()
        self.assertEqual(summary.total_minutes, 420)
        self.assertEqual(summary.source, 'garmin_fitabase')

    def test_id_is_read_only(self):
        from app.serializers import SleepSummarySerializer
        user = make_user()
        data = {'id': 999, 'user': user.user_id, 'date': '2026-09-01'}
        serializer = SleepSummarySerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        summary = serializer.save()
        self.assertNotEqual(summary.id, 999)


# ---------------------------------------------------------------------------
# Serializer: PhoneTelemetry
# ---------------------------------------------------------------------------

@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class PhoneTelemetrySerializerTests(TestCase):

    def _base_data(self, user):
        return {
            'user': user.user_id,
            'session_id': 'SESSION_001',
            'event_type': 'draft_submitted',
            'occurred_at': '2026-09-01T15:00:00Z',
            'metadata': {'keystroke_count': 42, 'delete_count': 5},
        }

    def test_serializer_creates_event_with_integer_metadata(self):
        from app.serializers import PhoneTelemetrySerializer
        user = make_user()
        serializer = PhoneTelemetrySerializer(data=self._base_data(user))
        self.assertTrue(serializer.is_valid(), serializer.errors)
        event = serializer.save()
        self.assertEqual(event.event_type, 'draft_submitted')
        self.assertEqual(event.metadata['keystroke_count'], 42)

    def test_irb_violation_long_string_returns_validation_error(self):
        from app.serializers import PhoneTelemetrySerializer
        user = make_user()
        data = self._base_data(user)
        data['metadata'] = {'text': 'x' * 51}
        serializer = PhoneTelemetrySerializer(data=data)
        self.assertFalse(serializer.is_valid())
        self.assertIn('metadata', serializer.errors)

    def test_irb_allows_string_under_50_chars(self):
        from app.serializers import PhoneTelemetrySerializer
        user = make_user()
        data = self._base_data(user)
        data['metadata'] = {'label': 'submit'}
        serializer = PhoneTelemetrySerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_recorded_at_is_read_only(self):
        from app.serializers import PhoneTelemetrySerializer
        user = make_user()
        data = self._base_data(user)
        data['recorded_at'] = '2020-01-01T00:00:00Z'
        serializer = PhoneTelemetrySerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        event = serializer.save()
        self.assertNotEqual(str(event.recorded_at.year), '2020')

    def test_game_clock_state_is_read_only(self):
        from app.serializers import PhoneTelemetrySerializer
        user = make_user()
        data = self._base_data(user)
        data['game_clock_state'] = 'live'
        serializer = PhoneTelemetrySerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        event = serializer.save()
        self.assertEqual(event.game_clock_state, 'pre')


# ---------------------------------------------------------------------------
# Serializer: EngagementLog
# ---------------------------------------------------------------------------

@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class EngagementLogSerializerTests(TestCase):

    def test_serializer_creates_engagement_event(self):
        from app.serializers import EngagementLogSerializer
        user = make_user()
        data = {
            'user': user.user_id,
            'event_type': 'ema_completed',
            'occurred_at': '2026-09-01T15:05:00Z',
        }
        serializer = EngagementLogSerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        log = serializer.save()
        self.assertEqual(log.event_type, 'ema_completed')
        self.assertIsNone(log.jitai_log)

    def test_serializer_accepts_nullable_jitai_log(self):
        from app.serializers import EngagementLogSerializer
        user = make_user()
        jitai = JITAILog.objects.create(
            user=user, prompt_id='T1', trigger_reason='hr_elevated'
        )
        data = {
            'user': user.user_id,
            'jitai_log': jitai.id,
            'event_type': 'notification_tapped',
            'occurred_at': '2026-09-01T15:05:00Z',
        }
        serializer = EngagementLogSerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        log = serializer.save()
        self.assertEqual(log.jitai_log, jitai)


# ---------------------------------------------------------------------------
# Serializer: JITAILog — verify new fields present
# ---------------------------------------------------------------------------

@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class JITAILogSerializerFieldsTests(TestCase):

    def test_serializer_includes_ema_observed_mssd_send_prompt(self):
        user = make_user()
        ema = EMA.objects.create(user=user, prompt_id='P1', mood=5, stress=3, energy=4)
        data = {
            'user': user.user_id,
            'prompt_id': 'TEMPLATE_001',
            'trigger_reason': 'hr_elevated',
            'ema': ema.id,
            'observed_mssd': 12.5,
            'send_prompt': True,
        }
        serializer = JITAILogSerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        log = serializer.save()
        self.assertEqual(log.observed_mssd, 12.5)
        self.assertTrue(log.send_prompt)
        self.assertEqual(log.ema, ema)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 manage.py test app.tests.SleepSummarySerializerTests app.tests.PhoneTelemetrySerializerTests app.tests.EngagementLogSerializerTests app.tests.JITAILogSerializerFieldsTests --settings=project.test_settings 2>&1 | tail -5
```
Expected: `ImportError` — serializers not defined yet.

- [ ] **Step 3: Update `app/serializers.py`**

Update the model import at the top:
```python
from .models import (
    EMA, EngagementLog, HeartRateSample, JITAILog, PhoneTelemetry,
    SleepSummary, StressSample, User, UserData, WearableDevice,
)
```

Fix `JITAILogSerializer.Meta.fields` (lines 107-111):
```python
class JITAILogSerializer(serializers.ModelSerializer):
    class Meta:
        model = JITAILog
        fields = [
            'id', 'user', 'prompt_id', 'triggered_at', 'trigger_reason',
            'hr_at_trigger', 'stress_at_trigger', 'ema', 'observed_mssd',
            'send_prompt', 'status',
        ]
        read_only_fields = ('id', 'triggered_at')
```

Append the three new serializers at the bottom of `app/serializers.py`:

```python
class SleepSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = SleepSummary
        fields = [
            'id', 'user', 'date', 'total_minutes', 'light_minutes',
            'deep_minutes', 'rem_minutes', 'awake_minutes', 'sleep_score', 'source',
        ]
        read_only_fields = ('id',)


class PhoneTelemetrySerializer(serializers.ModelSerializer):
    class Meta:
        model = PhoneTelemetry
        fields = [
            'id', 'user', 'session_id', 'event_type', 'occurred_at',
            'recorded_at', 'game_clock_state', 'screen_name', 'latency_ms', 'metadata',
        ]
        read_only_fields = ('id', 'recorded_at', 'game_clock_state')

    def validate_metadata(self, value):
        if value is None:
            return value
        for v in value.values():
            if isinstance(v, str) and len(v) > 50:
                raise serializers.ValidationError(
                    "metadata string values must not exceed 50 characters"
                )
        return value


class EngagementLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = EngagementLog
        fields = [
            'id', 'user', 'jitai_log', 'event_type', 'occurred_at',
            'recorded_at', 'game_clock_state',
        ]
        read_only_fields = ('id', 'recorded_at', 'game_clock_state')
```

- [ ] **Step 4: Run serializer tests — expect PASS**

```bash
python3 manage.py test app.tests.SleepSummarySerializerTests app.tests.PhoneTelemetrySerializerTests app.tests.EngagementLogSerializerTests app.tests.JITAILogSerializerFieldsTests --settings=project.test_settings 2>&1 | tail -5
```
Expected: `Ran 9 tests in X.XXXs` / `OK`

- [ ] **Step 5: Run full suite — expect no regressions**

```bash
python3 manage.py test app --settings=project.test_settings 2>&1 | grep -E "^(OK|FAIL|ERROR|Ran)"
```
Expected: `Ran 117 tests in X.XXXs` / `OK`

- [ ] **Step 6: Commit**

```bash
git add app/serializers.py app/tests.py
git commit -m "feat: add SleepSummary/PhoneTelemetry/EngagementLog serializers; fix JITAILogSerializer fields"
```

---

## Task 3: Wearable Device Endpoints

**Files:**
- Modify: `app/views.py`
- Modify: `project/urls.py`
- Modify: `app/tests.py`

**Interfaces:**
- Consumes: `WearableDevice`, `User` from `app.models`; `WearableDeviceSerializer` from `app.serializers`
- Produces:
  - `POST /wearable/` → 201 with device data or 400
  - `GET /wearable/{user_id}/` → 200 with device data or 404
  - `PATCH /wearable/{user_id}/` → 200 with updated device or 404

- [ ] **Step 1: Write the failing endpoint tests**

Append to `app/tests.py`:

```python
# ---------------------------------------------------------------------------
# API: POST /wearable/, GET /wearable/<id>/, PATCH /wearable/<id>/
# ---------------------------------------------------------------------------

@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class WearableEndpointTests(TestCase):

    def setUp(self):
        self.user = make_user(email='wearable@ufl.edu')
        self.client = authenticated_client(self.user)

    def test_post_creates_device_and_returns_201(self):
        response = self.client.post('/wearable/', {
            'user': self.user.user_id,
            'fitabase_participant_id': 'FITABASE_001',
            'device_name': 'Garmin Vivoactive 6',
        }, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_201_CREATED)
        self.assertTrue(WearableDevice.objects.filter(user=self.user).exists())

    def test_post_without_auth_returns_401(self):
        response = APIClient().post('/wearable/', {
            'user': self.user.user_id,
            'fitabase_participant_id': 'FITABASE_001',
        }, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_401_UNAUTHORIZED)

    def test_get_returns_device_for_enrolled_user(self):
        WearableDevice.objects.create(
            user=self.user, fitabase_participant_id='FITABASE_001'
        )
        response = self.client.get(f'/wearable/{self.user.user_id}/')
        self.assertEqual(response.status_code, http_status.HTTP_200_OK)
        self.assertEqual(response.data['fitabase_participant_id'], 'FITABASE_001')

    def test_get_returns_404_when_no_device(self):
        response = self.client.get(f'/wearable/{self.user.user_id}/')
        self.assertEqual(response.status_code, http_status.HTTP_404_NOT_FOUND)

    def test_patch_updates_device_name(self):
        WearableDevice.objects.create(
            user=self.user, fitabase_participant_id='FITABASE_001'
        )
        response = self.client.patch(
            f'/wearable/{self.user.user_id}/',
            {'device_name': 'Garmin Vivoactive 6 Pro'},
            format='json',
        )
        self.assertEqual(response.status_code, http_status.HTTP_200_OK)
        device = WearableDevice.objects.get(user=self.user)
        self.assertEqual(device.device_name, 'Garmin Vivoactive 6 Pro')

    def test_patch_nonexistent_device_returns_404(self):
        response = self.client.patch(
            f'/wearable/{self.user.user_id}/',
            {'device_name': 'X'},
            format='json',
        )
        self.assertEqual(response.status_code, http_status.HTTP_404_NOT_FOUND)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 manage.py test app.tests.WearableEndpointTests --settings=project.test_settings 2>&1 | tail -5
```
Expected: `404` errors — URLs not wired yet.

- [ ] **Step 3: Add views to `app/views.py`**

Add to the import block at the top of `views.py`:
```python
from .serializers import (
    TelemetryIngestSerializer,
    UserDataSerializer,
    UserSerializer,
    WearableDeviceSerializer,
)
```

Append after the existing view classes:

```python
class WearableDeviceView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = WearableDeviceSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def get(self, request, user_id):
        try:
            device = WearableDevice.objects.get(user__user_id=user_id)
        except WearableDevice.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(WearableDeviceSerializer(device).data)

    def patch(self, request, user_id):
        try:
            device = WearableDevice.objects.get(user__user_id=user_id)
        except WearableDevice.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)
        serializer = WearableDeviceSerializer(device, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        serializer.save()
        return Response(serializer.data)
```

- [ ] **Step 4: Wire URLs in `project/urls.py`**

Add to the import line:
```python
from app.views import (
    index, CreateUserView, poll_cfbd_view, home_tile_view, schedule_view,
    CreateUserDataView, UserLoginView, LatestUserDataView, UserUpdateView,
    CheckEmailView, me_view, TelemetryIngestView, WearableDeviceView,
)
```

Add to `urlpatterns`:
```python
path('wearable/', WearableDeviceView.as_view(), name='wearable-create'),
path('wearable/<int:user_id>/', WearableDeviceView.as_view(), name='wearable-detail'),
```

- [ ] **Step 5: Run wearable tests — expect PASS**

```bash
python3 manage.py test app.tests.WearableEndpointTests --settings=project.test_settings 2>&1 | tail -5
```
Expected: `Ran 6 tests in X.XXXs` / `OK`

- [ ] **Step 6: Run full suite**

```bash
python3 manage.py test app --settings=project.test_settings 2>&1 | grep -E "^(OK|FAIL|ERROR|Ran)"
```
Expected: `OK`

- [ ] **Step 7: Commit**

```bash
git add app/views.py project/urls.py app/tests.py
git commit -m "feat: add POST/GET/PATCH /wearable/ endpoints"
```

---

## Task 4: EMA Endpoints

**Files:**
- Modify: `app/views.py`
- Modify: `project/urls.py`
- Modify: `app/tests.py`

**Interfaces:**
- Consumes: `EMA` from `app.models`; `EMASerializer` from `app.serializers`
- Produces:
  - `POST /ema/` → 201 with EMA data; if Likert fields present, sets `status='completed'` and `responded_at=now()`
  - `GET /ema/{user_id}/` → 200 with list (ordered `-sent_at`)

- [ ] **Step 1: Write failing EMA endpoint tests**

Append to `app/tests.py`:

```python
# ---------------------------------------------------------------------------
# API: POST /ema/, GET /ema/<user_id>/
# ---------------------------------------------------------------------------

@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class EMAEndpointTests(TestCase):

    def setUp(self):
        self.user = make_user(email='ema@ufl.edu')
        self.client = authenticated_client(self.user)

    def test_post_creates_ema_and_returns_201(self):
        response = self.client.post('/ema/', {
            'user': self.user.user_id,
            'prompt_id': 'EMA_TEMPLATE_01',
            'mood': 5,
            'stress': 3,
            'energy': 6,
        }, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_201_CREATED)
        self.assertTrue(EMA.objects.filter(user=self.user).exists())

    def test_post_with_likert_responses_sets_completed_and_responded_at(self):
        response = self.client.post('/ema/', {
            'user': self.user.user_id,
            'prompt_id': 'EMA_TEMPLATE_01',
            'mood': 4,
            'stress': 2,
            'energy': 7,
        }, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_201_CREATED)
        ema = EMA.objects.get(user=self.user)
        self.assertEqual(ema.status, 'completed')
        self.assertIsNotNone(ema.responded_at)

    def test_post_without_likert_responses_leaves_status_pending(self):
        response = self.client.post('/ema/', {
            'user': self.user.user_id,
            'prompt_id': 'EMA_TEMPLATE_01',
        }, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_201_CREATED)
        ema = EMA.objects.get(user=self.user)
        self.assertEqual(ema.status, 'pending')
        self.assertIsNone(ema.responded_at)

    def test_post_without_auth_returns_401(self):
        response = APIClient().post('/ema/', {
            'user': self.user.user_id,
            'prompt_id': 'EMA_TEMPLATE_01',
        }, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_401_UNAUTHORIZED)

    def test_get_returns_ema_history_for_user(self):
        EMA.objects.create(user=self.user, prompt_id='P1', mood=5, stress=3, energy=4)
        EMA.objects.create(user=self.user, prompt_id='P2', mood=3, stress=6, energy=2)
        response = self.client.get(f'/ema/{self.user.user_id}/')
        self.assertEqual(response.status_code, http_status.HTTP_200_OK)
        self.assertEqual(len(response.data), 2)

    def test_get_returns_empty_list_for_user_with_no_emas(self):
        response = self.client.get(f'/ema/{self.user.user_id}/')
        self.assertEqual(response.status_code, http_status.HTTP_200_OK)
        self.assertEqual(len(response.data), 0)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 manage.py test app.tests.EMAEndpointTests --settings=project.test_settings 2>&1 | tail -5
```
Expected: `404` errors — URLs not wired.

- [ ] **Step 3: Add views to `app/views.py`**

Update the serializers import to include `EMASerializer`:
```python
from .serializers import (
    EMASerializer,
    TelemetryIngestSerializer,
    UserDataSerializer,
    UserSerializer,
    WearableDeviceSerializer,
)
```

Append:
```python
class EMAView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = EMASerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        ema = serializer.save()
        if ema.mood is not None and ema.stress is not None and ema.energy is not None:
            ema.status = 'completed'
            ema.responded_at = timezone.now()
            ema.save(update_fields=['status', 'responded_at'])
        return Response(EMASerializer(ema).data, status=status.HTTP_201_CREATED)

    def get(self, request, user_id):
        emas = EMA.objects.filter(user__user_id=user_id).order_by('-sent_at')
        return Response(EMASerializer(emas, many=True).data)
```

Add to `views.py` imports at the top: `from django.utils import timezone` (if not already present).

- [ ] **Step 4: Wire URLs in `project/urls.py`**

Add `EMAView` to the import. Add to `urlpatterns`:
```python
path('ema/', EMAView.as_view(), name='ema-create'),
path('ema/<int:user_id>/', EMAView.as_view(), name='ema-list'),
```

- [ ] **Step 5: Run EMA tests — expect PASS**

```bash
python3 manage.py test app.tests.EMAEndpointTests --settings=project.test_settings 2>&1 | tail -5
```
Expected: `Ran 6 tests in X.XXXs` / `OK`

- [ ] **Step 6: Run full suite**

```bash
python3 manage.py test app --settings=project.test_settings 2>&1 | grep -E "^(OK|FAIL|ERROR|Ran)"
```
Expected: `OK`

- [ ] **Step 7: Commit**

```bash
git add app/views.py project/urls.py app/tests.py
git commit -m "feat: add POST /ema/ and GET /ema/{user_id}/ endpoints"
```

---

## Task 5: JITAI Log Endpoints

**Files:**
- Modify: `app/views.py`
- Modify: `project/urls.py`
- Modify: `app/tests.py`

**Interfaces:**
- Consumes: `JITAILog` from `app.models`; `JITAILogSerializer` from `app.serializers`
- Produces:
  - `POST /jitai/` → 201 with log data (internal Celery use)
  - `GET /jitai/{user_id}/` → 200 with list ordered `-triggered_at`

- [ ] **Step 1: Write failing JITAI endpoint tests**

Append to `app/tests.py`:

```python
# ---------------------------------------------------------------------------
# API: POST /jitai/, GET /jitai/<user_id>/
# ---------------------------------------------------------------------------

@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class JITAIEndpointTests(TestCase):

    def setUp(self):
        self.user = make_user(email='jitai@ufl.edu')
        self.client = authenticated_client(self.user)

    def test_post_creates_jitai_log_and_returns_201(self):
        response = self.client.post('/jitai/', {
            'user': self.user.user_id,
            'prompt_id': 'TEMPLATE_HR_HIGH',
            'trigger_reason': 'hr_elevated+stress_high',
            'hr_at_trigger': 110,
            'stress_at_trigger': 75,
            'observed_mssd': 18.4,
            'send_prompt': True,
        }, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_201_CREATED)
        self.assertEqual(JITAILog.objects.count(), 1)

    def test_post_without_auth_returns_401(self):
        response = APIClient().post('/jitai/', {
            'user': self.user.user_id,
            'prompt_id': 'T1',
            'trigger_reason': 'hr_elevated',
        }, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_401_UNAUTHORIZED)

    def test_get_returns_jitai_history(self):
        JITAILog.objects.create(
            user=self.user, prompt_id='T1', trigger_reason='hr_elevated', send_prompt=True
        )
        JITAILog.objects.create(
            user=self.user, prompt_id='T2', trigger_reason='ema_low_mood', send_prompt=False
        )
        response = self.client.get(f'/jitai/{self.user.user_id}/')
        self.assertEqual(response.status_code, http_status.HTTP_200_OK)
        self.assertEqual(len(response.data), 2)

    def test_get_returns_empty_list_for_user_with_no_logs(self):
        response = self.client.get(f'/jitai/{self.user.user_id}/')
        self.assertEqual(response.status_code, http_status.HTTP_200_OK)
        self.assertEqual(len(response.data), 0)

    def test_send_prompt_false_is_stored(self):
        self.client.post('/jitai/', {
            'user': self.user.user_id,
            'prompt_id': 'TEMPLATE_001',
            'trigger_reason': 'cooldown',
            'send_prompt': False,
        }, format='json')
        log = JITAILog.objects.get(user=self.user)
        self.assertFalse(log.send_prompt)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 manage.py test app.tests.JITAIEndpointTests --settings=project.test_settings 2>&1 | tail -5
```
Expected: `404` errors.

- [ ] **Step 3: Add views to `app/views.py`**

Add `JITAILogSerializer` to serializers import. Append:

```python
class JITAILogView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = JITAILogSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        log = serializer.save()
        return Response(JITAILogSerializer(log).data, status=status.HTTP_201_CREATED)

    def get(self, request, user_id):
        logs = JITAILog.objects.filter(user__user_id=user_id).order_by('-triggered_at')
        return Response(JITAILogSerializer(logs, many=True).data)
```

- [ ] **Step 4: Wire URLs in `project/urls.py`**

Add `JITAILogView` to import. Add to `urlpatterns`:
```python
path('jitai/', JITAILogView.as_view(), name='jitai-create'),
path('jitai/<int:user_id>/', JITAILogView.as_view(), name='jitai-list'),
```

- [ ] **Step 5: Run JITAI tests — expect PASS**

```bash
python3 manage.py test app.tests.JITAIEndpointTests --settings=project.test_settings 2>&1 | tail -5
```
Expected: `Ran 5 tests in X.XXXs` / `OK`

- [ ] **Step 6: Run full suite**

```bash
python3 manage.py test app --settings=project.test_settings 2>&1 | grep -E "^(OK|FAIL|ERROR|Ran)"
```
Expected: `OK`

- [ ] **Step 7: Commit**

```bash
git add app/views.py project/urls.py app/tests.py
git commit -m "feat: add POST /jitai/ and GET /jitai/{user_id}/ endpoints"
```

---

## Task 6: Telemetry Read Endpoints (HR + Stress)

**Files:**
- Modify: `app/views.py`
- Modify: `project/urls.py`
- Modify: `app/tests.py`

**Interfaces:**
- Consumes: `HeartRateSample`, `StressSample` from `app.models`; `HeartRateSampleSerializer`, `StressSampleSerializer` from `app.serializers`
- Produces:
  - `GET /telemetry/hr/{user_id}/` → 200 with list, most recent first. Optional `?limit=N` (default 100)
  - `GET /telemetry/stress/{user_id}/` → 200 with list, most recent first. Optional `?limit=N` (default 100)

- [ ] **Step 1: Write failing telemetry read tests**

Append to `app/tests.py`:

```python
# ---------------------------------------------------------------------------
# API: GET /telemetry/hr/<user_id>/, GET /telemetry/stress/<user_id>/
# ---------------------------------------------------------------------------

@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class TelemetryReadEndpointTests(TestCase):

    def setUp(self):
        self.user = make_user(email='telread@ufl.edu')
        self.client = authenticated_client(self.user)

    def test_hr_returns_samples_for_user(self):
        HeartRateSample.objects.create(user=self.user, timestamp=timezone.now(), bpm=72)
        HeartRateSample.objects.create(user=self.user, timestamp=timezone.now(), bpm=85)
        response = self.client.get(f'/telemetry/hr/{self.user.user_id}/')
        self.assertEqual(response.status_code, http_status.HTTP_200_OK)
        self.assertEqual(len(response.data), 2)

    def test_hr_returns_empty_list_when_no_samples(self):
        response = self.client.get(f'/telemetry/hr/{self.user.user_id}/')
        self.assertEqual(response.status_code, http_status.HTTP_200_OK)
        self.assertEqual(response.data, [])

    def test_hr_limit_param_caps_results(self):
        for bpm in range(1, 6):
            HeartRateSample.objects.create(user=self.user, timestamp=timezone.now(), bpm=bpm * 10)
        response = self.client.get(f'/telemetry/hr/{self.user.user_id}/?limit=2')
        self.assertEqual(response.status_code, http_status.HTTP_200_OK)
        self.assertEqual(len(response.data), 2)

    def test_hr_requires_auth(self):
        response = APIClient().get(f'/telemetry/hr/{self.user.user_id}/')
        self.assertEqual(response.status_code, http_status.HTTP_401_UNAUTHORIZED)

    def test_stress_returns_samples_for_user(self):
        StressSample.objects.create(user=self.user, timestamp=timezone.now(), stress_score=55)
        response = self.client.get(f'/telemetry/stress/{self.user.user_id}/')
        self.assertEqual(response.status_code, http_status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)

    def test_stress_limit_param_caps_results(self):
        for score in range(1, 6):
            StressSample.objects.create(user=self.user, timestamp=timezone.now(), stress_score=score * 10)
        response = self.client.get(f'/telemetry/stress/{self.user.user_id}/?limit=3')
        self.assertEqual(response.status_code, http_status.HTTP_200_OK)
        self.assertEqual(len(response.data), 3)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 manage.py test app.tests.TelemetryReadEndpointTests --settings=project.test_settings 2>&1 | tail -5
```
Expected: `404` errors.

- [ ] **Step 3: Add views to `app/views.py`**

Add `HeartRateSampleSerializer`, `StressSampleSerializer` to serializers import. Append:

```python
class HeartRateListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, user_id):
        limit = int(request.query_params.get('limit', 100))
        samples = HeartRateSample.objects.filter(
            user__user_id=user_id
        ).order_by('-timestamp')[:limit]
        return Response(HeartRateSampleSerializer(samples, many=True).data)


class StressListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, user_id):
        limit = int(request.query_params.get('limit', 100))
        samples = StressSample.objects.filter(
            user__user_id=user_id
        ).order_by('-timestamp')[:limit]
        return Response(StressSampleSerializer(samples, many=True).data)
```

- [ ] **Step 4: Wire URLs in `project/urls.py`**

Add `HeartRateListView`, `StressListView` to import. Add to `urlpatterns`:
```python
path('telemetry/hr/<int:user_id>/', HeartRateListView.as_view(), name='telemetry-hr'),
path('telemetry/stress/<int:user_id>/', StressListView.as_view(), name='telemetry-stress'),
```

- [ ] **Step 5: Run telemetry read tests — expect PASS**

```bash
python3 manage.py test app.tests.TelemetryReadEndpointTests --settings=project.test_settings 2>&1 | tail -5
```
Expected: `Ran 6 tests in X.XXXs` / `OK`

- [ ] **Step 6: Run full suite**

```bash
python3 manage.py test app --settings=project.test_settings 2>&1 | grep -E "^(OK|FAIL|ERROR|Ran)"
```
Expected: `OK`

- [ ] **Step 7: Commit**

```bash
git add app/views.py project/urls.py app/tests.py
git commit -m "feat: add GET /telemetry/hr/ and GET /telemetry/stress/ endpoints"
```

---

## Task 7: PhoneTelemetry Endpoint + `get_game_clock_state` Helper

**Files:**
- Modify: `app/utils.py`
- Modify: `app/views.py`
- Modify: `project/urls.py`
- Modify: `app/tests.py`

**Interfaces:**
- Consumes: `PhoneTelemetry` from `app.models`; `PhoneTelemetrySerializer` from `app.serializers`; `get_game_clock_state` from `app.utils`
- Produces:
  - `get_game_clock_state() -> Literal['pre', 'live', 'post']` in `app/utils.py`
  - `POST /telemetry/phone/` → 201. Server stamps `game_clock_state`; IRB violation returns 400

- [ ] **Step 1: Write failing phone telemetry tests**

Append to `app/tests.py`:

```python
# ---------------------------------------------------------------------------
# API: POST /telemetry/phone/
# ---------------------------------------------------------------------------

@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class PhoneTelemetryEndpointTests(TestCase):

    def setUp(self):
        self.user = make_user(email='phone@ufl.edu')
        self.client = authenticated_client(self.user)

    def _payload(self):
        return {
            'user': self.user.user_id,
            'session_id': 'SESSION_001',
            'event_type': 'draft_submitted',
            'occurred_at': '2026-09-01T15:00:00Z',
            'metadata': {'keystroke_count': 42, 'delete_count': 5},
        }

    @patch('app.views.get_game_clock_state', return_value='live')
    def test_post_creates_event_and_stamps_game_clock_state(self, mock_gcs):
        from app.models import PhoneTelemetry
        response = self.client.post('/telemetry/phone/', self._payload(), format='json')
        self.assertEqual(response.status_code, http_status.HTTP_201_CREATED)
        event = PhoneTelemetry.objects.get(user=self.user)
        self.assertEqual(event.game_clock_state, 'live')

    @patch('app.views.get_game_clock_state', return_value='pre')
    def test_irb_violation_long_string_metadata_returns_400(self, mock_gcs):
        payload = self._payload()
        payload['metadata'] = {'text': 'x' * 51}
        response = self.client.post('/telemetry/phone/', payload, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_400_BAD_REQUEST)

    @patch('app.views.get_game_clock_state', return_value='pre')
    def test_client_supplied_game_clock_state_is_ignored(self, mock_gcs):
        from app.models import PhoneTelemetry
        payload = self._payload()
        payload['game_clock_state'] = 'live'
        response = self.client.post('/telemetry/phone/', payload, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_201_CREATED)
        event = PhoneTelemetry.objects.get(user=self.user)
        self.assertEqual(event.game_clock_state, 'pre')

    def test_post_without_auth_returns_401(self):
        response = APIClient().post('/telemetry/phone/', self._payload(), format='json')
        self.assertEqual(response.status_code, http_status.HTTP_401_UNAUTHORIZED)

    @patch('app.views.get_game_clock_state', return_value='pre')
    def test_invalid_event_type_returns_400(self, mock_gcs):
        payload = self._payload()
        payload['event_type'] = 'not_a_real_event'
        response = self.client.post('/telemetry/phone/', payload, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_400_BAD_REQUEST)
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 manage.py test app.tests.PhoneTelemetryEndpointTests --settings=project.test_settings 2>&1 | tail -5
```
Expected: `ImportError` or `404`.

- [ ] **Step 3: Add `get_game_clock_state` to `app/utils.py`**

Append to the bottom of `app/utils.py`:

```python
def get_game_clock_state():
    from datetime import datetime, timezone as dt_timezone, timedelta
    from django.core.cache import cache
    current_year = datetime.now(dt_timezone.utc).year
    games_list = cache.get(f'uf_football_games_{current_year}')
    if not games_list:
        return 'pre'
    now = datetime.now(dt_timezone.utc)
    for game in games_list:
        start_date = game.get('startDate')
        if start_date is None:
            continue
        if not hasattr(start_date, 'tzinfo'):
            continue
        window_start = start_date - timedelta(minutes=30)
        window_end = start_date + timedelta(hours=4)
        if window_start <= now <= window_end:
            return 'live'
    future_games = [
        g for g in games_list
        if g.get('startDate') and hasattr(g['startDate'], 'tzinfo') and g['startDate'] > now
    ]
    return 'pre' if future_games else 'post'
```

- [ ] **Step 4: Add view to `app/views.py`**

Add to imports at top of `views.py`:
```python
from .utils import send_push_notification_next_game, check_game_status, send_notification, get_users_with_push_token, get_game_clock_state
from .serializers import (
    EMASerializer,
    EngagementLogSerializer,
    HeartRateSampleSerializer,
    JITAILogSerializer,
    PhoneTelemetrySerializer,
    StressSampleSerializer,
    TelemetryIngestSerializer,
    UserDataSerializer,
    UserSerializer,
    WearableDeviceSerializer,
)
```

Also import the models not already imported:
```python
from .models import (
    EMA, EngagementLog, HeartRateSample, JITAILog, PhoneTelemetry,
    StressSample, User, UserData, WearableDevice,
)
```

Append the view:

```python
class PhoneTelemetryView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = PhoneTelemetrySerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        event = serializer.save()
        event.game_clock_state = get_game_clock_state()
        event.save(update_fields=['game_clock_state'])
        return Response(PhoneTelemetrySerializer(event).data, status=status.HTTP_201_CREATED)
```

- [ ] **Step 5: Wire URLs in `project/urls.py`**

Add `PhoneTelemetryView` to import. Add to `urlpatterns`:
```python
path('telemetry/phone/', PhoneTelemetryView.as_view(), name='telemetry-phone'),
```

- [ ] **Step 6: Run phone telemetry tests — expect PASS**

```bash
python3 manage.py test app.tests.PhoneTelemetryEndpointTests --settings=project.test_settings 2>&1 | tail -5
```
Expected: `Ran 5 tests in X.XXXs` / `OK`

- [ ] **Step 7: Run full suite**

```bash
python3 manage.py test app --settings=project.test_settings 2>&1 | grep -E "^(OK|FAIL|ERROR|Ran)"
```
Expected: `OK`

- [ ] **Step 8: Commit**

```bash
git add app/utils.py app/views.py project/urls.py app/tests.py
git commit -m "feat: add POST /telemetry/phone/ and get_game_clock_state helper"
```

---

## Task 8: Engagement Log Endpoint

**Files:**
- Modify: `app/views.py`
- Modify: `project/urls.py`
- Modify: `app/tests.py`

**Interfaces:**
- Consumes: `EngagementLog` from `app.models`; `EngagementLogSerializer` from `app.serializers`; `get_game_clock_state` from `app.utils`
- Produces:
  - `POST /telemetry/engagement/` → 201. Server stamps `game_clock_state`. `jitai_log` is optional.

- [ ] **Step 1: Write failing engagement tests**

Append to `app/tests.py`:

```python
# ---------------------------------------------------------------------------
# API: POST /telemetry/engagement/
# ---------------------------------------------------------------------------

@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class EngagementEndpointTests(TestCase):

    def setUp(self):
        self.user = make_user(email='engage@ufl.edu')
        self.client = authenticated_client(self.user)

    @patch('app.views.get_game_clock_state', return_value='live')
    def test_post_creates_engagement_event_and_stamps_game_clock_state(self, mock_gcs):
        from app.models import EngagementLog
        response = self.client.post('/telemetry/engagement/', {
            'user': self.user.user_id,
            'event_type': 'ema_completed',
            'occurred_at': '2026-09-01T15:10:00Z',
        }, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_201_CREATED)
        log = EngagementLog.objects.get(user=self.user)
        self.assertEqual(log.game_clock_state, 'live')
        self.assertIsNone(log.jitai_log)

    @patch('app.views.get_game_clock_state', return_value='live')
    def test_post_with_jitai_log_links_correctly(self, mock_gcs):
        from app.models import EngagementLog
        jitai = JITAILog.objects.create(
            user=self.user, prompt_id='T1', trigger_reason='hr_elevated'
        )
        response = self.client.post('/telemetry/engagement/', {
            'user': self.user.user_id,
            'jitai_log': jitai.id,
            'event_type': 'notification_tapped',
            'occurred_at': '2026-09-01T15:10:00Z',
        }, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_201_CREATED)
        log = EngagementLog.objects.get(user=self.user)
        self.assertEqual(log.jitai_log, jitai)

    def test_post_without_auth_returns_401(self):
        response = APIClient().post('/telemetry/engagement/', {
            'user': self.user.user_id,
            'event_type': 'ema_completed',
            'occurred_at': '2026-09-01T15:10:00Z',
        }, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_401_UNAUTHORIZED)

    @patch('app.views.get_game_clock_state', return_value='pre')
    def test_invalid_event_type_returns_400(self, mock_gcs):
        response = self.client.post('/telemetry/engagement/', {
            'user': self.user.user_id,
            'event_type': 'not_a_real_event',
            'occurred_at': '2026-09-01T15:10:00Z',
        }, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_400_BAD_REQUEST)

    @patch('app.views.get_game_clock_state', return_value='pre')
    def test_client_supplied_game_clock_state_is_ignored(self, mock_gcs):
        from app.models import EngagementLog
        response = self.client.post('/telemetry/engagement/', {
            'user': self.user.user_id,
            'event_type': 'ema_opened',
            'occurred_at': '2026-09-01T15:10:00Z',
            'game_clock_state': 'live',
        }, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_201_CREATED)
        log = EngagementLog.objects.get(user=self.user)
        self.assertEqual(log.game_clock_state, 'pre')
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python3 manage.py test app.tests.EngagementEndpointTests --settings=project.test_settings 2>&1 | tail -5
```
Expected: `404` errors.

- [ ] **Step 3: Add view to `app/views.py`**

Append:

```python
class EngagementLogView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = EngagementLogSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        log = serializer.save()
        log.game_clock_state = get_game_clock_state()
        log.save(update_fields=['game_clock_state'])
        return Response(EngagementLogSerializer(log).data, status=status.HTTP_201_CREATED)
```

- [ ] **Step 4: Wire URL in `project/urls.py`**

Add `EngagementLogView` to import. Add to `urlpatterns`:
```python
path('telemetry/engagement/', EngagementLogView.as_view(), name='telemetry-engagement'),
```

- [ ] **Step 5: Run engagement tests — expect PASS**

```bash
python3 manage.py test app.tests.EngagementEndpointTests --settings=project.test_settings 2>&1 | tail -5
```
Expected: `Ran 5 tests in X.XXXs` / `OK`

- [ ] **Step 6: Run full suite — final check**

```bash
python3 manage.py test app --settings=project.test_settings 2>&1 | grep -E "^(OK|FAIL|ERROR|Ran)"
```
Expected: approximately `Ran 163 tests` / `OK`

- [ ] **Step 7: Commit**

```bash
git add app/views.py project/urls.py app/tests.py
git commit -m "feat: add POST /telemetry/engagement/ endpoint"
```

---

## Self-Review

**Spec coverage check:**

| Requirement | Covered by |
|---|---|
| `SleepSummary` model | Task 1 |
| `PhoneTelemetry` model + IRB validator | Task 1 |
| `EngagementLog` model | Task 1 |
| Migration | Task 1 Step 4 |
| Admin for new models | Task 1 Step 5 |
| `SleepSummarySerializer` | Task 2 |
| `PhoneTelemetrySerializer` + IRB validate_metadata | Task 2 |
| `EngagementLogSerializer` | Task 2 |
| `JITAILogSerializer` ema/observed_mssd/send_prompt | Task 2 |
| `POST /wearable/` | Task 3 |
| `GET /wearable/{user_id}/` | Task 3 |
| `PATCH /wearable/{user_id}/` | Task 3 |
| `POST /ema/` with auto-complete logic | Task 4 |
| `GET /ema/{user_id}/` | Task 4 |
| `POST /jitai/` | Task 5 |
| `GET /jitai/{user_id}/` | Task 5 |
| `GET /telemetry/hr/{user_id}/` + `?limit` | Task 6 |
| `GET /telemetry/stress/{user_id}/` + `?limit` | Task 6 |
| `get_game_clock_state()` utility | Task 7 |
| `POST /telemetry/phone/` + server-stamp game_clock_state | Task 7 |
| `POST /telemetry/engagement/` + server-stamp game_clock_state | Task 8 |
| All new endpoints require `IsAuthenticated` | Tasks 3–8 |
| No message text stored in JITAILog | Enforced by serializer (prompt_id only) |

**Not covered (deferred per architecture):**
- `SleepSummary` REST endpoints — architecture says ingest is ORM-only via Celery; no public REST endpoint defined
- `ingest_wearable_data` and `evaluate_jitai_triggers` Celery tasks — separate plan needed after PI threshold sign-off
- Admin CSV exports and permission groups — separate task pending RA access scope confirmation from Prof. Chang
