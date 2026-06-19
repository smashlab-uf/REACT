from django.db import models
from django.contrib.auth.hashers import make_password, check_password as django_check_password
from django.core.validators import MinValueValidator, MaxValueValidator


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


class NotificationData(models.Model):
    notification_id = models.AutoField(primary_key=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    notification_title = models.CharField(max_length=255, default="Default Title")
    notification_message = models.CharField(max_length=255)
    timestamp = models.DateTimeField(auto_now_add=True)
    read_status = models.BooleanField(default=False)

    def __str__(self):
        return f"Notification for {self.user.email} at {self.timestamp}"


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
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default='delivered')

    class Meta:
        ordering = ['-triggered_at']

    def __str__(self):
        return f"JITAI for {self.user.email} at {self.triggered_at}"
