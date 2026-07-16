from django.db import models
from django.contrib.auth.hashers import make_password, check_password as django_check_password
from django.core.validators import MinValueValidator, MaxValueValidator
from django.core.exceptions import ValidationError
from django.utils import timezone


class User(models.Model):
    user_id = models.AutoField(primary_key=True)
    email = models.EmailField(unique=True)
    first_name = models.CharField(max_length=100, default="")
    last_name = models.CharField(max_length=100, default="")
    birthdate = models.DateField()
    gender = models.CharField(max_length=10, choices=[('male', 'Male'), ('female', 'Female'), ('other', 'Other')])
    password = models.CharField(max_length=128, blank=True, null=True)
    push_token = models.CharField(max_length=128, blank=True, null=True)
    is_enrolled = models.BooleanField(default=False)
    enrolled_at = models.DateTimeField(null=True, blank=True)

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['user_id', 'first_name', 'last_name', 'birthdate', 'gender', 'password']

    def __str__(self):
        return f"User ID: {self.user_id}, Email: {self.email}"

    def set_password(self, raw_password: str):
        self.password = make_password(raw_password)

    def check_password(self, raw_password: str) -> bool:
        if not self.password:
            return False
        return django_check_password(raw_password, self.password)


class WearableDevice(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE)
    labfront_participant_id = models.CharField(max_length=64, unique=True)
    is_active = models.BooleanField(default=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.labfront_participant_id} ({self.user.email})"


class HeartRateSample(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    timestamp = models.DateTimeField(db_index=True)
    bpm = models.PositiveSmallIntegerField()
    source = models.CharField(max_length=32, default='garmin_labfront')

    class Meta:
        ordering = ['-timestamp']
        indexes = [models.Index(fields=['user', 'timestamp'])]

    def __str__(self):
        return f"{self.bpm} bpm at {self.timestamp}"


class StressSample(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    timestamp = models.DateTimeField(db_index=True)
    stress_score = models.PositiveSmallIntegerField()
    source = models.CharField(max_length=32, default='garmin_labfront')

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
        ('pending', 'Pending'),
        ('delivered', 'Delivered'),
        ('opened', 'Opened'),
        ('interacted', 'Interacted'),
        ('failed', 'Failed'),
        ('not_sent', 'Not Sent'),
    ]
    DELIVERY_STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('not_sent', 'Not Sent'),
        ('accepted_by_expo', 'Accepted by Expo'),
        ('received_on_device', 'Received on Device'),
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
    decision_point_id = models.CharField(max_length=64, unique=True, null=True, blank=True)
    randomization_probability = models.FloatField(null=True, blank=True)
    randomization_draw = models.FloatField(null=True, blank=True)
    send_prompt = models.BooleanField(default=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='pending')
    decision_made_at = models.DateTimeField(default=timezone.now, db_index=True)
    push_sent_at = models.DateTimeField(null=True, blank=True, db_index=True)
    device_received_at = models.DateTimeField(null=True, blank=True, db_index=True)
    receipt_reported_at = models.DateTimeField(null=True, blank=True, db_index=True)
    delivery_status = models.CharField(
        max_length=32,
        choices=DELIVERY_STATUS_CHOICES,
        default='pending',
        db_index=True,
    )
    delivery_error = models.TextField(blank=True, default='')
    receipt_platform = models.CharField(max_length=16, blank=True, default='')
    receipt_app_state = models.CharField(max_length=32, blank=True, default='')

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

    class Meta:
        ordering = ['-occurred_at']
        indexes = [models.Index(fields=['user', 'occurred_at'])]

    def __str__(self):
        return f"{self.event_type} for {self.user.email} at {self.occurred_at}"


