# REACT Schema Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the six-table REACT database schema — extending `User` with Fitbit OAuth2 fields and adding `WearableDevice`, `HeartRateSample`, `ActivitySummary`, `EMA`, and `JITAILog` models with serializers and admin registration.

**Architecture:** All models live in `app/models.py` following the existing single-app pattern. Each model group gets its own Django migration. Serializers follow the one-per-model convention already in `app/serializers.py`. Tests are added to `app/tests.py` using `django.test.TestCase`.

**Tech Stack:** Django 5.1, PostgreSQL, Django REST Framework, Django test runner (`python manage.py test`)

---

## File Map

| File | Change |
|---|---|
| `backend/app/models.py` | Add Fitbit fields to User; add WearableDevice, HeartRateSample, ActivitySummary, EMA, JITAILog |
| `backend/app/serializers.py` | Add serializers for all 5 new models |
| `backend/app/admin.py` | Register all new models |
| `backend/app/tests.py` | Add model and serializer tests |
| `backend/app/migrations/` | Auto-generated — do not edit manually |

---

### Task 1: Extend User with Fitbit token fields

**Files:**
- Modify: `backend/app/models.py`
- Modify: `backend/app/tests.py`

- [ ] **Step 1: Write the failing test**

Add `from django.utils import timezone` to the imports at the top of `backend/app/tests.py`, then add this class at the end of the file:

```python
class UserFitbitFieldTests(TestCase):

    def test_user_stores_fitbit_credentials(self):
        user = make_user()
        user.fitbit_user_id = 'ABCD1234'
        user.fitbit_access_token = 'access_token_value'
        user.fitbit_refresh_token = 'refresh_token_value'
        user.fitbit_token_expires = timezone.now()
        user.save()
        user.refresh_from_db()
        self.assertEqual(user.fitbit_user_id, 'ABCD1234')

    def test_fitbit_fields_are_nullable(self):
        user = make_user()
        self.assertIsNone(user.fitbit_user_id)
        self.assertIsNone(user.fitbit_access_token)
        self.assertIsNone(user.fitbit_refresh_token)
        self.assertIsNone(user.fitbit_token_expires)
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd backend && python manage.py test app.tests.UserFitbitFieldTests
```

Expected: FAIL — `AttributeError: 'User' object has no attribute 'fitbit_user_id'`

- [ ] **Step 3: Add Fitbit fields to the User model**

In `backend/app/models.py`, add these four fields inside the `User` class after the `push_token` field:

```python
fitbit_user_id = models.CharField(max_length=64, blank=True, null=True)
fitbit_access_token = models.TextField(blank=True, null=True)
fitbit_refresh_token = models.TextField(blank=True, null=True)
fitbit_token_expires = models.DateTimeField(blank=True, null=True)
```

- [ ] **Step 4: Generate and apply migration**

```bash
cd backend && python manage.py makemigrations && python manage.py migrate
```

Expected: new migration file created in `app/migrations/` and applied successfully.

- [ ] **Step 5: Run the test to verify it passes**

```bash
cd backend && python manage.py test app.tests.UserFitbitFieldTests
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/models.py backend/app/migrations/ backend/app/tests.py
git commit -m "feat: add Fitbit OAuth2 token fields to User model"
```

---

### Task 2: Add WearableDevice model

**Files:**
- Modify: `backend/app/models.py`
- Modify: `backend/app/tests.py`

- [ ] **Step 1: Write the failing test**

Update the model import line at the top of `tests.py`:

```python
from app.models import User, UserData, NotificationData, WearableDevice
```

Add this class at the end of `tests.py`:

```python
class WearableDeviceModelTests(TestCase):

    def test_device_is_linked_to_user(self):
        user = make_user()
        device = WearableDevice.objects.create(
            user=user,
            fitbit_device_id='ABCD1234',
            device_type='tracker',
            device_name='Charge 6',
        )
        self.assertEqual(device.user, user)

    def test_is_active_defaults_to_true(self):
        user = make_user()
        device = WearableDevice.objects.create(
            user=user,
            fitbit_device_id='ABCD1234',
            device_type='tracker',
            device_name='Charge 6',
        )
        self.assertTrue(device.is_active)

    def test_created_at_is_set_automatically(self):
        user = make_user()
        device = WearableDevice.objects.create(
            user=user,
            fitbit_device_id='ABCD1234',
            device_type='tracker',
            device_name='Charge 6',
        )
        self.assertIsNotNone(device.created_at)

    def test_deleting_user_deletes_device(self):
        user = make_user()
        WearableDevice.objects.create(
            user=user,
            fitbit_device_id='ABCD1234',
            device_type='tracker',
            device_name='Charge 6',
        )
        user.delete()
        self.assertEqual(WearableDevice.objects.count(), 0)
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd backend && python manage.py test app.tests.WearableDeviceModelTests
```

Expected: FAIL — `ImportError: cannot import name 'WearableDevice' from 'app.models'`

- [ ] **Step 3: Add the WearableDevice model**

Add to the end of `backend/app/models.py`:

```python
class WearableDevice(models.Model):
    device_id = models.AutoField(primary_key=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    fitbit_device_id = models.CharField(max_length=64)
    device_type = models.CharField(max_length=32)
    device_name = models.CharField(max_length=64)
    last_synced_at = models.DateTimeField(blank=True, null=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.device_name} ({self.user.email})"
```

- [ ] **Step 4: Generate and apply migration**

```bash
cd backend && python manage.py makemigrations && python manage.py migrate
```

Expected: new migration created and applied.

- [ ] **Step 5: Run the test to verify it passes**

```bash
cd backend && python manage.py test app.tests.WearableDeviceModelTests
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/models.py backend/app/migrations/ backend/app/tests.py
git commit -m "feat: add WearableDevice model"
```

---

### Task 3: Add HeartRateSample and ActivitySummary models

**Files:**
- Modify: `backend/app/models.py`
- Modify: `backend/app/tests.py`

- [ ] **Step 1: Write the failing tests**

Update the model import line at the top of `tests.py`:

```python
from app.models import User, UserData, NotificationData, WearableDevice, HeartRateSample, ActivitySummary
```

Add this helper function directly after the existing `make_user()` function:

```python
def make_device(user, fitbit_device_id='DEV001', device_type='tracker', device_name='Charge 6'):
    return WearableDevice.objects.create(
        user=user,
        fitbit_device_id=fitbit_device_id,
        device_type=device_type,
        device_name=device_name,
    )
```

Add these classes at the end of `tests.py`:

```python
class HeartRateSampleModelTests(TestCase):

    def test_sample_links_to_device(self):
        user = make_user()
        device = make_device(user)
        sample = HeartRateSample.objects.create(
            device=device,
            timestamp=timezone.now(),
            bpm=72,
            zone='fat_burn',
        )
        self.assertEqual(sample.device, device)
        self.assertEqual(sample.bpm, 72)
        self.assertEqual(sample.zone, 'fat_burn')

    def test_deleting_device_deletes_samples(self):
        user = make_user()
        device = make_device(user)
        HeartRateSample.objects.create(
            device=device, timestamp=timezone.now(), bpm=80, zone='cardio'
        )
        device.delete()
        self.assertEqual(HeartRateSample.objects.count(), 0)


class ActivitySummaryModelTests(TestCase):

    def test_summary_links_to_device(self):
        user = make_user()
        device = make_device(user)
        summary = ActivitySummary.objects.create(
            device=device,
            date='2026-01-01',
            steps=8000,
            active_minutes=45,
            calories_burned=350,
            distance_km=6.500,
        )
        self.assertEqual(summary.device, device)
        self.assertEqual(summary.steps, 8000)

    def test_duplicate_device_date_raises_error(self):
        from django.db import IntegrityError
        user = make_user()
        device = make_device(user)
        ActivitySummary.objects.create(device=device, date='2026-01-01', steps=8000)
        with self.assertRaises(IntegrityError):
            ActivitySummary.objects.create(device=device, date='2026-01-01', steps=9000)
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && python manage.py test app.tests.HeartRateSampleModelTests app.tests.ActivitySummaryModelTests
```

Expected: FAIL — `ImportError: cannot import name 'HeartRateSample' from 'app.models'`

- [ ] **Step 3: Add HeartRateSample and ActivitySummary to models.py**

Add to the end of `backend/app/models.py`:

```python
class HeartRateSample(models.Model):
    ZONE_CHOICES = [
        ('out_of_range', 'Out of Range'),
        ('fat_burn', 'Fat Burn'),
        ('cardio', 'Cardio'),
        ('peak', 'Peak'),
    ]

    sample_id = models.AutoField(primary_key=True)
    device = models.ForeignKey(WearableDevice, on_delete=models.CASCADE)
    timestamp = models.DateTimeField()
    bpm = models.PositiveSmallIntegerField()
    zone = models.CharField(max_length=20, choices=ZONE_CHOICES)

    def __str__(self):
        return f"{self.bpm} bpm at {self.timestamp}"


class ActivitySummary(models.Model):
    summary_id = models.AutoField(primary_key=True)
    device = models.ForeignKey(WearableDevice, on_delete=models.CASCADE)
    date = models.DateField()
    steps = models.PositiveIntegerField(blank=True, null=True)
    active_minutes = models.PositiveIntegerField(blank=True, null=True)
    calories_burned = models.PositiveIntegerField(blank=True, null=True)
    distance_km = models.DecimalField(max_digits=6, decimal_places=3, blank=True, null=True)

    class Meta:
        unique_together = ('device', 'date')

    def __str__(self):
        return f"Activity for {self.device} on {self.date}"
```

- [ ] **Step 4: Generate and apply migration**

```bash
cd backend && python manage.py makemigrations && python manage.py migrate
```

Expected: new migration created and applied.

- [ ] **Step 5: Run the tests to verify they pass**

```bash
cd backend && python manage.py test app.tests.HeartRateSampleModelTests app.tests.ActivitySummaryModelTests
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/models.py backend/app/migrations/ backend/app/tests.py
git commit -m "feat: add HeartRateSample and ActivitySummary models"
```

---

### Task 4: Add EMA model

**Files:**
- Modify: `backend/app/models.py`
- Modify: `backend/app/tests.py`

- [ ] **Step 1: Write the failing test**

Update the model import line at the top of `tests.py`:

```python
from app.models import User, UserData, NotificationData, WearableDevice, HeartRateSample, ActivitySummary, EMA
```

Add this class at the end of `tests.py`:

```python
class EMAModelTests(TestCase):

    def test_ema_stores_survey_response(self):
        user = make_user()
        ema = EMA.objects.create(
            user=user,
            mood=7,
            energy=5,
            stress=3,
            physical_activity='moderate',
        )
        self.assertEqual(ema.mood, 7)
        self.assertEqual(ema.energy, 5)
        self.assertEqual(ema.stress, 3)
        self.assertEqual(ema.physical_activity, 'moderate')

    def test_timestamp_is_set_automatically(self):
        user = make_user()
        ema = EMA.objects.create(user=user)
        self.assertIsNotNone(ema.timestamp)

    def test_all_fields_are_optional_except_user(self):
        user = make_user()
        ema = EMA.objects.create(user=user)
        self.assertIsNone(ema.mood)
        self.assertIsNone(ema.energy)
        self.assertIsNone(ema.stress)
        self.assertIsNone(ema.physical_activity)
        self.assertIsNone(ema.weight_lbs)
        self.assertIsNone(ema.notes)

    def test_deleting_user_deletes_ema_records(self):
        user = make_user()
        EMA.objects.create(user=user, mood=5)
        user.delete()
        self.assertEqual(EMA.objects.count(), 0)
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd backend && python manage.py test app.tests.EMAModelTests
```

Expected: FAIL — `ImportError: cannot import name 'EMA' from 'app.models'`

- [ ] **Step 3: Add the EMA model**

Add this import at the top of `backend/app/models.py` (after the existing imports):

```python
from django.core.validators import MinValueValidator, MaxValueValidator
```

Then add to the end of `models.py`:

```python
class EMA(models.Model):
    ACTIVITY_CHOICES = [
        ('none', 'None'),
        ('light', 'Light'),
        ('moderate', 'Moderate'),
        ('vigorous', 'Vigorous'),
    ]

    ema_id = models.AutoField(primary_key=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    timestamp = models.DateTimeField(auto_now_add=True)
    mood = models.PositiveSmallIntegerField(
        blank=True, null=True,
        validators=[MinValueValidator(1), MaxValueValidator(10)],
    )
    energy = models.PositiveSmallIntegerField(
        blank=True, null=True,
        validators=[MinValueValidator(1), MaxValueValidator(10)],
    )
    stress = models.PositiveSmallIntegerField(
        blank=True, null=True,
        validators=[MinValueValidator(1), MaxValueValidator(10)],
    )
    physical_activity = models.CharField(
        max_length=20, choices=ACTIVITY_CHOICES, blank=True, null=True
    )
    weight_lbs = models.DecimalField(max_digits=4, decimal_places=1, blank=True, null=True)
    notes = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"EMA for {self.user.email} at {self.timestamp}"
```

- [ ] **Step 4: Generate and apply migration**

```bash
cd backend && python manage.py makemigrations && python manage.py migrate
```

Expected: new migration created and applied.

- [ ] **Step 5: Run the test to verify it passes**

```bash
cd backend && python manage.py test app.tests.EMAModelTests
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/models.py backend/app/migrations/ backend/app/tests.py
git commit -m "feat: add EMA model"
```

---

### Task 5: Add JITAILog model

**Files:**
- Modify: `backend/app/models.py`
- Modify: `backend/app/tests.py`

- [ ] **Step 1: Write the failing test**

Update the model import line at the top of `tests.py`:

```python
from app.models import User, UserData, NotificationData, WearableDevice, HeartRateSample, ActivitySummary, EMA, JITAILog
```

Add this class at the end of `tests.py`:

```python
class JITAILogModelTests(TestCase):

    def test_jitai_log_stores_intervention(self):
        user = make_user()
        log = JITAILog.objects.create(
            user=user,
            title='Move more!',
            message='You have been inactive for 2 hours.',
            trigger_reason='low_steps',
            volatility_score=0.720,
            threshold_used=0.650,
            prompt_count=3,
        )
        self.assertEqual(log.trigger_reason, 'low_steps')
        self.assertEqual(log.title, 'Move more!')

    def test_prompt_status_defaults_to_sent(self):
        user = make_user()
        log = JITAILog.objects.create(
            user=user,
            title='Move!',
            message='Get up.',
            trigger_reason='low_steps',
        )
        self.assertEqual(log.prompt_status, 'sent')

    def test_opened_at_and_interacted_at_are_nullable(self):
        user = make_user()
        log = JITAILog.objects.create(
            user=user,
            title='Move!',
            message='Get up.',
            trigger_reason='low_steps',
        )
        self.assertIsNone(log.opened_at)
        self.assertIsNone(log.interacted_at)

    def test_timestamp_is_set_automatically(self):
        user = make_user()
        log = JITAILog.objects.create(
            user=user,
            title='Move!',
            message='Get up.',
            trigger_reason='low_steps',
        )
        self.assertIsNotNone(log.timestamp)

    def test_deleting_user_deletes_jitai_logs(self):
        user = make_user()
        JITAILog.objects.create(
            user=user, title='Hi', message='Go.', trigger_reason='low_steps'
        )
        user.delete()
        self.assertEqual(JITAILog.objects.count(), 0)
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
cd backend && python manage.py test app.tests.JITAILogModelTests
```

Expected: FAIL — `ImportError: cannot import name 'JITAILog' from 'app.models'`

- [ ] **Step 3: Add the JITAILog model**

Add to the end of `backend/app/models.py`:

```python
class JITAILog(models.Model):
    PROMPT_STATUS_CHOICES = [
        ('sent', 'Sent'),
        ('delivered', 'Delivered'),
        ('opened', 'Opened'),
        ('dismissed', 'Dismissed'),
        ('acted_on', 'Acted On'),
    ]

    log_id = models.AutoField(primary_key=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    timestamp = models.DateTimeField(auto_now_add=True)
    title = models.CharField(max_length=255)
    message = models.TextField()
    trigger_reason = models.CharField(max_length=100)
    volatility_score = models.DecimalField(max_digits=6, decimal_places=3, blank=True, null=True)
    threshold_used = models.DecimalField(max_digits=6, decimal_places=3, blank=True, null=True)
    prompt_status = models.CharField(max_length=20, choices=PROMPT_STATUS_CHOICES, default='sent')
    prompt_count = models.PositiveIntegerField(default=0)
    opened_at = models.DateTimeField(blank=True, null=True)
    interacted_at = models.DateTimeField(blank=True, null=True)

    def __str__(self):
        return f"JITAI for {self.user.email} at {self.timestamp}"
```

- [ ] **Step 4: Generate and apply migration**

```bash
cd backend && python manage.py makemigrations && python manage.py migrate
```

Expected: new migration created and applied.

- [ ] **Step 5: Run the test to verify it passes**

```bash
cd backend && python manage.py test app.tests.JITAILogModelTests
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/models.py backend/app/migrations/ backend/app/tests.py
git commit -m "feat: add JITAILog model"
```

---

### Task 6: Add serializers for new models

**Files:**
- Modify: `backend/app/serializers.py`
- Modify: `backend/app/tests.py`

- [ ] **Step 1: Write the failing tests**

Add this import to `tests.py` (as a new line, separate from the model import):

```python
from app.serializers import (
    UserSerializer, UserDataSerializer, NotificationDataSerializer,
    WearableDeviceSerializer, HeartRateSampleSerializer,
    ActivitySummarySerializer, EMASerializer, JITAILogSerializer,
)
```

Add these classes at the end of `tests.py`:

```python
class WearableDeviceSerializerTests(TestCase):

    def test_serializer_creates_device(self):
        user = make_user()
        data = {
            'user': user.user_id,
            'fitbit_device_id': 'DEV001',
            'device_type': 'tracker',
            'device_name': 'Charge 6',
        }
        serializer = WearableDeviceSerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        device = serializer.save()
        self.assertEqual(device.device_name, 'Charge 6')


class HeartRateSampleSerializerTests(TestCase):

    def test_serializer_creates_sample(self):
        user = make_user()
        device = make_device(user)
        data = {
            'device': device.device_id,
            'timestamp': '2026-01-01T10:00:00Z',
            'bpm': 72,
            'zone': 'fat_burn',
        }
        serializer = HeartRateSampleSerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        sample = serializer.save()
        self.assertEqual(sample.bpm, 72)


class ActivitySummarySerializerTests(TestCase):

    def test_serializer_creates_summary(self):
        user = make_user()
        device = make_device(user)
        data = {
            'device': device.device_id,
            'date': '2026-01-01',
            'steps': 8000,
            'active_minutes': 45,
        }
        serializer = ActivitySummarySerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        summary = serializer.save()
        self.assertEqual(summary.steps, 8000)


class EMASerializerTests(TestCase):

    def test_serializer_creates_ema(self):
        user = make_user()
        data = {
            'user': user.user_id,
            'mood': 7,
            'energy': 5,
            'stress': 3,
            'physical_activity': 'moderate',
        }
        serializer = EMASerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        ema = serializer.save()
        self.assertEqual(ema.mood, 7)


class JITAILogSerializerTests(TestCase):

    def test_serializer_creates_jitai_log(self):
        user = make_user()
        data = {
            'user': user.user_id,
            'title': 'Move more!',
            'message': 'You have been inactive for 2 hours.',
            'trigger_reason': 'low_steps',
            'prompt_count': 1,
        }
        serializer = JITAILogSerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        log = serializer.save()
        self.assertEqual(log.trigger_reason, 'low_steps')
        self.assertEqual(log.prompt_status, 'sent')
```

- [ ] **Step 2: Run the tests to verify they fail**

```bash
cd backend && python manage.py test app.tests.WearableDeviceSerializerTests app.tests.HeartRateSampleSerializerTests app.tests.ActivitySummarySerializerTests app.tests.EMASerializerTests app.tests.JITAILogSerializerTests
```

Expected: FAIL — `ImportError: cannot import name 'WearableDeviceSerializer' from 'app.serializers'`

- [ ] **Step 3: Add serializers to serializers.py**

Update the model import line at the top of `backend/app/serializers.py`:

```python
from .models import UserData, User, NotificationData, WearableDevice, HeartRateSample, ActivitySummary, EMA, JITAILog
```

Add to the end of `serializers.py`:

```python
class WearableDeviceSerializer(serializers.ModelSerializer):
    class Meta:
        model = WearableDevice
        fields = '__all__'
        read_only_fields = ('device_id', 'created_at')


class HeartRateSampleSerializer(serializers.ModelSerializer):
    class Meta:
        model = HeartRateSample
        fields = '__all__'
        read_only_fields = ('sample_id',)


class ActivitySummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = ActivitySummary
        fields = '__all__'
        read_only_fields = ('summary_id',)


class EMASerializer(serializers.ModelSerializer):
    class Meta:
        model = EMA
        fields = '__all__'
        read_only_fields = ('ema_id', 'timestamp')


class JITAILogSerializer(serializers.ModelSerializer):
    class Meta:
        model = JITAILog
        fields = '__all__'
        read_only_fields = ('log_id', 'timestamp')
```

- [ ] **Step 4: Run the tests to verify they pass**

```bash
cd backend && python manage.py test app.tests.WearableDeviceSerializerTests app.tests.HeartRateSampleSerializerTests app.tests.ActivitySummarySerializerTests app.tests.EMASerializerTests app.tests.JITAILogSerializerTests
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/serializers.py backend/app/tests.py
git commit -m "feat: add serializers for WearableDevice, HeartRateSample, ActivitySummary, EMA, JITAILog"
```

---

### Task 7: Register new models in Django admin

**Files:**
- Modify: `backend/app/admin.py`

- [ ] **Step 1: Update admin.py**

Replace the full contents of `backend/app/admin.py` with:

```python
from django.contrib import admin
from .models import (
    User, UserData, NotificationData,
    WearableDevice, HeartRateSample, ActivitySummary, EMA, JITAILog,
)

admin.site.register(User)
admin.site.register(UserData)
admin.site.register(NotificationData)
admin.site.register(WearableDevice)
admin.site.register(HeartRateSample)
admin.site.register(ActivitySummary)
admin.site.register(EMA)
admin.site.register(JITAILog)
```

- [ ] **Step 2: Verify the full test suite passes**

```bash
cd backend && python manage.py test app
```

Expected: all tests PASS with no regressions.

- [ ] **Step 3: Commit**

```bash
git add backend/app/admin.py
git commit -m "feat: register REACT models in Django admin"
```
