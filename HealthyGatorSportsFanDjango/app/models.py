from django.db import models
from django.contrib.auth.hashers import make_password, check_password as django_check_password
from django.core.validators import MinValueValidator, MaxValueValidator
from django.core.exceptions import ValidationError


class User(models.Model):
    user_id = models.AutoField(primary_key=True)
    email = models.EmailField(unique=True)
    first_name = models.CharField(max_length=100, default="")
    last_name = models.CharField(max_length=100, default="")
    birthdate = models.DateField()
    gender = models.CharField(max_length=10, choices=[('male', 'Male'), ('female', 'Female'), ('other', 'Other')])
    height_feet = models.CharField(max_length=10, default="")
    height_inches = models.CharField(max_length=10, default="")
    goal_weight = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    goal_to_lose_weight = models.BooleanField(default=False)
    goal_to_feel_better = models.BooleanField(default=False)
    password = models.CharField(max_length=128, blank=True, null=True)
    push_token = models.CharField(max_length=128, blank=True, null=True)
    is_enrolled = models.BooleanField(default=False)
    enrolled_at = models.DateTimeField(null=True, blank=True)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['user_id', 'first_name', 'last_name', 'birthdate', 'gender', 'height_feet', 'height_inches', 'goal_weight', 'goal_to_lose_weight', 'goal_to_feel_better', 'password']

    def __str__(self):
        return f"User ID: {self.user_id}, Email: {self.email}"

    def set_password(self, raw_password: str):
        self.password = make_password(raw_password)

    def check_password(self, raw_password: str) -> bool:
        if not self.password:
            return False
        return django_check_password(raw_password, self.password)


class UserData(models.Model):
    data_id = models.AutoField(primary_key=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    timestamp = models.DateTimeField(auto_now_add=True)
    goal_type = models.CharField(max_length=20, choices=[('loseWeight', 'Lose Weight'), ('feelBetter', 'Feel Better'), ('both', 'Both')])
    weight_value = models.DecimalField(max_digits=4, decimal_places=1, null=True, blank=True)
    feel_better_value = models.IntegerField(null=True, blank=True)

    def __str__(self):
        return f"Data for {self.user.email} at {self.timestamp}"


class WearableDevice(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    fitabase_participant_id = models.CharField(max_length=64, unique=True)
    device_name = models.CharField(max_length=100, blank=True, null=True)
    is_active = models.BooleanField(default=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.fitabase_participant_id} ({self.user.email})"


class HeartRateSample(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    timestamp = models.DateTimeField(db_index=True)
    bpm = models.PositiveSmallIntegerField()
    source = models.CharField(max_length=32, default='garmin_fitabase')

    class Meta:
        ordering = ['-timestamp']
        indexes = [models.Index(fields=['user', 'timestamp'])]

    def __str__(self):
        return f"{self.bpm} bpm at {self.timestamp}"


class StressSample(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    timestamp = models.DateTimeField(db_index=True)
    stress_score = models.PositiveSmallIntegerField()
    source = models.CharField(max_length=32, default='garmin_fitabase')

    class Meta:
        ordering = ['-timestamp']
        indexes = [models.Index(fields=['user', 'timestamp'])]

    def __str__(self):
        return f"Stress {self.stress_score} at {self.timestamp}"



class EMA(models.Model):
    STATUS_CHOICES = [('pending', 'Pending'), ('completed', 'Completed'), ('expired', 'Expired')]

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    prompt_id = models.CharField(max_length=64)
    sent_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='pending')
    mood = models.PositiveSmallIntegerField(
        null=True, blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(7)],
    )
    stress = models.PositiveSmallIntegerField(
        null=True, blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(7)],
    )
    energy = models.PositiveSmallIntegerField(
        null=True, blank=True,
        validators=[MinValueValidator(1), MaxValueValidator(7)],
    )

    class Meta:
        ordering = ['-sent_at']

    def __str__(self):
        return f"EMA for {self.user.email} at {self.sent_at}"


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


PHONE_EVENT_TYPES = (
    ('draft_started', 'Draft Started'),
    ('draft_deleted', 'Draft Deleted'),
    ('draft_submitted', 'Draft Submitted'),
    ('session_start', 'Session Start'),
    ('session_end', 'Session End'),
)

ENGAGEMENT_EVENT_TYPES = (
    ('ema_opened', 'EMA Opened'),
    ('ema_dismissed', 'EMA Dismissed'),
    ('ema_completed', 'EMA Completed'),
    ('notification_tapped', 'Notification Tapped'),
    ('notification_dismissed', 'Notification Dismissed'),
)

GAME_CLOCK_STATES = (
    ('pre', 'Pre-Game'),
    ('live', 'Live'),
    ('post', 'Post-Game'),
)


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


class JITAILog(models.Model):
    STATUS_CHOICES = [
        ('delivered', 'Delivered'),
        ('opened', 'Opened'),
        ('interacted', 'Interacted'),
        ('failed', 'Failed'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    prompt_id = models.CharField(max_length=64)
    triggered_at = models.DateTimeField(auto_now_add=True)
    trigger_reason = models.CharField(max_length=128)
    hr_at_trigger = models.PositiveSmallIntegerField(null=True, blank=True)
    stress_at_trigger = models.PositiveSmallIntegerField(null=True, blank=True)
    ema = models.ForeignKey(EMA, on_delete=models.SET_NULL, null=True, blank=True)
    observed_mssd = models.FloatField(null=True, blank=True)
    send_prompt = models.BooleanField(default=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='delivered')

    class Meta:
        ordering = ['-triggered_at']

    def __str__(self):
        return f"JITAI for {self.user.email} at {self.triggered_at}"


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
