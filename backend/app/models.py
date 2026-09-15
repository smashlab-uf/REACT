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

    def save(self, *args, **kwargs):
        """Stamp enrolled_at the first time a participant becomes enrolled.

        Nothing used to set this field: it is exposed on the serializer and
        nowhere else, so ticking the box in Django Admin left it NULL. The whole
        monitoring layer counts study days from it, so a participant without one
        has no day 0, resolves to no phase, and is excluded from every cohort
        filter and every metric.

        Only ever set, never moved: re-saving an enrolled participant, or
        unenrolling and re-enrolling them, must not shift their day 0 and
        renumber the study days of data already collected.
        """
        if self.is_enrolled and self.enrolled_at is None:
            self.enrolled_at = timezone.now()
            update_fields = kwargs.get('update_fields')
            if update_fields is not None and 'enrolled_at' not in update_fields:
                kwargs['update_fields'] = list(update_fields) + ['enrolled_at']
        super().save(*args, **kwargs)

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


SYNC_SOURCES = (
    ('ingest', 'Labfront ingestion'),
    ('client', 'Mobile client'),
)


class WearableSync(models.Model):
    """Append-only history of a device's sync clock advancing.

    WearableDevice.last_synced_at is a single mutable column, so it answers
    "how stale now" and nothing about when data actually arrived. Without this
    table a sync outage is indistinguishable from genuine non-wear, which is the
    one distinction the monitoring timeline exists to draw.

    Rows are written only when the value moves forward, so a flat stretch is a
    real outage rather than an artefact of the polling interval.

    The mobile client is the intended writer, through PATCH /wearable/{id}/, and
    `source` records that. It does not call it yet -- the app has no wearable
    code at all -- and ingest_wearable_data is still a stub, so nothing writes
    the sync clock in production today and this table stays empty until one of
    them lands. Empty is reported as unmeasurable, never as a device gone quiet.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='wearable_syncs')
    observed_at = models.DateTimeField(db_index=True)
    source = models.CharField(max_length=16, choices=SYNC_SOURCES)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    samples_written = models.PositiveIntegerField(null=True, blank=True)

    class Meta:
        ordering = ['-observed_at']
        indexes = [models.Index(fields=['user', 'observed_at'])]

    def __str__(self):
        return f"sync for user {self.user_id} at {self.observed_at} ({self.source})"


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
    STATUS_CHOICES = [
        ('pending', 'Pending'), ('completed', 'Completed'), ('expired', 'Expired'),
        ('dismissed', 'Dismissed'),
    ]
    EMA_TYPE_CHOICES = [
        ('scheduled_check_in', 'Scheduled Check-in'),
        ('post_prompt', 'Post-prompt Outcome Window'),
        ('extra_check_in', 'Extra Check-in'),
        ('prompt_feedback', 'Prompt Feedback'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    prompt_id = models.CharField(max_length=64)
    sent_at = models.DateTimeField(auto_now_add=True)
    responded_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='pending')
    ema_type = models.CharField(max_length=32, choices=EMA_TYPE_CHOICES, default='scheduled_check_in')
    source_jitai_log = models.ForeignKey('JITAILog', on_delete=models.SET_NULL, null=True, blank=True, related_name='ema_prompts')
    outcome_window_start = models.DateTimeField(null=True, blank=True)
    outcome_window_end = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    # Which sub-items this check-in actually put on screen, after the B4-B7
    # rotation and the schedule_condition filter. Without it, "how many
    # questions was this person asked" is unrecoverable, and item completeness
    # can only be inferred from what they happened to answer. NULL on rows
    # written before this field existed.
    served_sub_item_ids = models.JSONField(null=True, blank=True)
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


class EMAItemResponse(models.Model):
    RESPONSE_TYPE_CHOICES = [
        ('likert', 'Likert'),
        ('single_choice', 'Single choice'),
        ('multi_choice', 'Multiple choice'),
        ('number', 'Number'),
        ('yes_no', 'Yes/No'),
    ]

    ema = models.ForeignKey(EMA, on_delete=models.CASCADE, related_name='item_responses')
    item_id = models.CharField(max_length=8)
    sub_item_id = models.CharField(max_length=32)
    response_type = models.CharField(max_length=16, choices=RESPONSE_TYPE_CHOICES)
    value_numeric = models.IntegerField(null=True, blank=True)
    value_choice = models.CharField(max_length=64, null=True, blank=True)
    value_choices = models.JSONField(null=True, blank=True)

    class Meta:
        unique_together = ('ema', 'sub_item_id')
        ordering = ['sub_item_id']

    def __str__(self):
        return f"{self.sub_item_id} for EMA {self.ema_id}"


class CheckinReminder(models.Model):
    """A record that a 'time to check in' push was sent — distinct from EMA,
    which only gets a row once the participant actually responds. Lets staff
    tell 'we prompted them and they didn't respond' apart from 'we never
    prompted them at all'."""

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    sent_at = models.DateTimeField(auto_now_add=True)
    # Which of the day's fixed check-in slots (0-indexed) this reminder was
    # for — one reminder per slot, at most, per Dr. Chang 2026-08-21.
    daily_count_at_send = models.PositiveSmallIntegerField()

    class Meta:
        ordering = ['-sent_at']

    def __str__(self):
        return f"Reminder for {self.user.email} at {self.sent_at}"


class EventDay(models.Model):
    date = models.DateField(unique=True)
    sport = models.CharField(max_length=64, blank=True, default='')
    description = models.CharField(max_length=128, blank=True, default='')

    class Meta:
        ordering = ['-date']

    def __str__(self):
        return f"{self.date} ({self.sport or 'event'})"


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
    # What observed_mssd was actually compared against. The engine computes this
    # as user_threshold on every decision and then discarded it, so nothing
    # recorded why a decision point went the way it did. threshold_source keeps
    # an engine-written value distinguishable from one replayed offline: a
    # dashboard that accuses the engine of missing an eligible point must not be
    # able to do so on a number a reimplementation produced.
    threshold_at_decision = models.FloatField(null=True, blank=True)
    threshold_source = models.CharField(max_length=16, blank=True, default='')
    decision_point_id = models.CharField(max_length=64, unique=True, null=True, blank=True)
    randomization_probability = models.FloatField(null=True, blank=True)
    randomization_draw = models.FloatField(null=True, blank=True)
    MESSAGE_ARM_CHOICES = [('coping', 'Coping Message'), ('control', 'Active Control')]
    # Second-stage randomization, confirmed by Dr. Chang 2026-08-25: only
    # drawn when the first-stage send decision (randomization_draw < p)
    # resulted in a send. 0.5/0.5 coping-vs-control, logged separately from
    # the send draw so the two effects (message sent vs. content of message)
    # can be analyzed independently.
    message_arm = models.CharField(max_length=16, choices=MESSAGE_ARM_CHOICES, null=True, blank=True)
    arm_randomization_probability = models.FloatField(null=True, blank=True)
    arm_randomization_draw = models.FloatField(null=True, blank=True)
    send_prompt = models.BooleanField(default=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='pending')
    decision_made_at = models.DateTimeField(default=timezone.now, db_index=True)
    push_sent_at = models.DateTimeField(null=True, blank=True, db_index=True)
    device_received_at = models.DateTimeField(null=True, blank=True, db_index=True)
    receipt_reported_at = models.DateTimeField(null=True, blank=True, db_index=True)
    receipt_event_id = models.CharField(max_length=64, unique=True, null=True, blank=True, db_index=True)
    delivery_status = models.CharField(
        max_length=32,
        choices=DELIVERY_STATUS_CHOICES,
        default='pending',
        db_index=True,
    )
    delivery_error = models.TextField(blank=True, default='')
    receipt_platform = models.CharField(max_length=16, blank=True, default='')
    receipt_app_state = models.CharField(max_length=32, blank=True, default='')
    trigger_signal = models.CharField(max_length=32, null=True, blank=True)
    ema_mood = models.PositiveSmallIntegerField(null=True, blank=True)
    ema_stress = models.PositiveSmallIntegerField(null=True, blank=True)
    ema_energy = models.PositiveSmallIntegerField(null=True, blank=True)
    eligible_prompt_ids = models.JSONField(null=True, blank=True)
    # Routing logging, confirmed by Dr. Chang 2026-09-07 — every field here
    # needs to be recorded at every decision point, coping arm or not, so the
    # routing behavior is fully auditable. evaluated_items covers both "what
    # value was checked" and "was the item available" in one structure: a
    # missing/null value means the item wasn't part of that check-in's
    # rotation, not that it was checked and found low.
    evaluated_items = models.JSONField(null=True, blank=True)
    matched_categories = models.JSONField(null=True, blank=True)
    category_drawn = models.CharField(max_length=64, null=True, blank=True)
    fallback_reason = models.CharField(max_length=128, blank=True, default='')

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




def record_sync(user, last_synced_at, source, samples_written=None):
    """Append a WearableSync row when a device's sync clock moves forward.

    A no-op when the value has not advanced, so a flat stretch in the timeline's
    sync lane is a real outage and not an artefact of how often we poll. Returns
    the row it wrote, or None.
    """
    if last_synced_at is None:
        return None
    latest = (
        WearableSync.objects
        .filter(user=user)
        .order_by('-observed_at')
        .values_list('last_synced_at', flat=True)
        .first()
    )
    if latest is not None and last_synced_at <= latest:
        return None
    return WearableSync.objects.create(
        user=user,
        observed_at=timezone.now(),
        source=source,
        last_synced_at=last_synced_at,
        samples_written=samples_written,
    )
