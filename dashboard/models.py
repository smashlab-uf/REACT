from django.db import models
from django.db.models import Q, UniqueConstraint
from django.utils import timezone


class MetricsDaily(models.Model):
    """One row per participant per study day, recomputed on a trailing window.

    Structural null and zero are different facts and are never collapsed. A day
    outside the participant's active range (before enrollment, past the last
    study day, or after withdrawal) is either absent or carries
    is_active_day=False with every metric NULL. A day inside the range with no
    activity carries zeros. Every metric column is therefore nullable, including
    ones that look like they could never be missing.
    """

    user = models.ForeignKey(
        'app.User', on_delete=models.CASCADE, related_name='metrics_daily',
    )
    study_day = models.SmallIntegerField()
    local_date = models.DateField(db_index=True)
    is_run_in = models.BooleanField()
    is_active_day = models.BooleanField()
    computed_at = models.DateTimeField(auto_now=True)
    # Which frozen EMA_ITEM_BANK export completeness_mean was scored against, so
    # a mid-study item change cannot silently rewrite past completeness.
    item_bank_version = models.CharField(max_length=16)

    ema_scheduled_n = models.PositiveSmallIntegerField(null=True, blank=True)
    ema_jitai_n = models.PositiveSmallIntegerField(null=True, blank=True)
    ema_post_prompt_n = models.PositiveSmallIntegerField(null=True, blank=True)

    # The check-in compliance denominator. EMA rows only exist once a
    # participant submits, so the day's fixed slots -- not EMA rows -- are what
    # "we asked" is counted against. covered and reminded_uncovered are disjoint.
    slots_expected = models.PositiveSmallIntegerField(null=True, blank=True)
    slots_covered = models.PositiveSmallIntegerField(null=True, blank=True)
    slots_reminded_uncovered = models.PositiveSmallIntegerField(null=True, blank=True)
    slots_silent = models.PositiveSmallIntegerField(null=True, blank=True)

    reminders_sent = models.PositiveSmallIntegerField(null=True, blank=True)
    reminders_per_checkin_median = models.FloatField(null=True, blank=True)

    # Answered over askable, 0-1. Askable is reconstructed from the frozen item
    # bank because the served sub-item set is never persisted on the EMA row.
    completeness_mean = models.FloatField(null=True, blank=True)
    ema_missing_b1b2_n = models.PositiveSmallIntegerField(null=True, blank=True)

    decision_points_n = models.PositiveSmallIntegerField(null=True, blank=True)
    eligible_n = models.PositiveSmallIntegerField(null=True, blank=True)
    sent_n = models.PositiveSmallIntegerField(null=True, blank=True)
    delivered_n = models.PositiveSmallIntegerField(null=True, blank=True)
    cap_hit = models.BooleanField(null=True, blank=True)
    min_gap_min = models.IntegerField(null=True, blank=True)
    cooldown_violations_n = models.PositiveSmallIntegerField(null=True, blank=True)
    # Prompts sent during the run-in baseline. evaluate_jitai_triggers has no
    # run-in gate, so this is expected to be non-zero until one is added.
    runin_violation_n = models.PositiveSmallIntegerField(null=True, blank=True)

    prompt_opened_n = models.PositiveSmallIntegerField(null=True, blank=True)
    prompt_acted_n = models.PositiveSmallIntegerField(null=True, blank=True)
    prompt_dismissed_n = models.PositiveSmallIntegerField(null=True, blank=True)
    outcome_captured_n = models.PositiveSmallIntegerField(null=True, blank=True)

    # Fractions of the 840-minute waking window, 0-1.
    wear_valid_pct = models.FloatField(null=True, blank=True)
    wear_gap_pct = models.FloatField(null=True, blank=True)
    gaps_gt2h_n = models.PositiveSmallIntegerField(null=True, blank=True)
    max_gap_min = models.IntegerField(null=True, blank=True)
    hr_minutes_valid = models.PositiveSmallIntegerField(null=True, blank=True)

    last_sync_age_h_eod = models.FloatField(null=True, blank=True)
    # Signed: a device clock running ahead of the server puts occurred_at after
    # recorded_at, which is a real and diagnostic condition, not an error.
    clock_skew_p95_ms = models.IntegerField(null=True, blank=True)
    delivery_failures_n = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        ordering = ['user', 'study_day']
        constraints = [
            UniqueConstraint(fields=['user', 'study_day'], name='uniq_metrics_daily_user_day'),
        ]
        indexes = [models.Index(fields=['user', 'local_date'])]

    def __str__(self):
        return f"Day {self.study_day} for user {self.user_id} ({self.local_date})"


class MetricsParticipant(models.Model):
    PHASE_CHOICES = [
        ('pre_enrollment', 'Pre-enrollment'),
        ('run_in', 'Run-in'),
        ('mrt', 'Micro-randomized'),
        ('complete', 'Complete'),
        ('withdrawn', 'Withdrawn'),
    ]

    user = models.OneToOneField(
        'app.User', on_delete=models.CASCADE, related_name='metrics_participant',
    )
    computed_at = models.DateTimeField(auto_now=True)

    enrolled_at = models.DateTimeField(null=True, blank=True)
    day1_date = models.DateField(null=True, blank=True)
    study_day_now = models.SmallIntegerField(null=True, blank=True)
    phase = models.CharField(max_length=16, choices=PHASE_CHOICES)
    is_enrolled_snapshot = models.BooleanField()
    # Withdrawal proxy: the first computed_at at which a recompute observed
    # is_enrolled=False for a participant who had been enrolled. Resolution
    # equals the polling interval; earlier changes are backfilled once from
    # django_admin_log.
    first_seen_not_enrolled_at = models.DateTimeField(null=True, blank=True)

    last_ema_at = models.DateTimeField(null=True, blank=True)
    last_sync_at = models.DateTimeField(null=True, blank=True)
    active_retention = models.BooleanField(null=True, blank=True)
    risk_score = models.PositiveSmallIntegerField(null=True, blank=True)

    # Cumulative benchmark rates, each stored with its numerator and denominator
    # so a suppressed rate can still be shown as raw counts.
    slot_coverage_rate = models.FloatField(null=True, blank=True)
    slot_coverage_num = models.IntegerField(null=True, blank=True)
    slot_coverage_den = models.IntegerField(null=True, blank=True)
    prompt_response_rate = models.FloatField(null=True, blank=True)
    prompt_response_num = models.IntegerField(null=True, blank=True)
    prompt_response_den = models.IntegerField(null=True, blank=True)
    wear_rate = models.FloatField(null=True, blank=True)
    wear_num = models.IntegerField(null=True, blank=True)
    wear_den = models.IntegerField(null=True, blank=True)

    class Meta:
        ordering = ['user']

    def __str__(self):
        return f"Metrics for user {self.user_id} ({self.phase})"


class MetricsCohort(models.Model):
    PHASE_ALL = 'all'
    PHASE_1 = 'phase1'
    PHASE_2 = 'phase2'
    PHASE_FILTER_CHOICES = [
        (PHASE_ALL, 'All participants'),
        (PHASE_1, 'Phase 1'),
        (PHASE_2, 'Phase 2'),
    ]

    as_of = models.DateTimeField(default=timezone.now, db_index=True)
    phase_filter = models.CharField(max_length=16, choices=PHASE_FILTER_CHOICES)
    n_participants = models.IntegerField()
    n_active = models.IntegerField()

    # {name: {value, wilson_low, wilson_high, numerator, denominator,
    #         suppressed, measurable}}. Held as one document rather than 25
    #  columns because the row is always read whole; the 'hair' entry is always
    #  measurable=false, since hair_sample has no production table.
    benchmarks = models.JSONField(null=True, blank=True)
    # Pre-aggregated daily series so a 60-second poll never re-aggregates
    # MetricsDaily.
    series_14d = models.JSONField(null=True, blank=True)

    decision_points_n = models.IntegerField(null=True, blank=True)
    eligible_n = models.IntegerField(null=True, blank=True)
    sent_n = models.IntegerField(null=True, blank=True)
    delivered_n = models.IntegerField(null=True, blank=True)
    cooldown_violations_n = models.IntegerField(null=True, blank=True)
    runin_violations_n = models.IntegerField(null=True, blank=True)
    cap_hit_days = models.IntegerField(null=True, blank=True)
    delivery_failures_n = models.IntegerField(null=True, blank=True)

    class Meta:
        ordering = ['-as_of']
        indexes = [models.Index(fields=['phase_filter', '-as_of'])]

    def __str__(self):
        return f"Cohort {self.phase_filter} at {self.as_of}"


class Alert(models.Model):
    SEVERITY_CHOICES = [
        ('critical', 'Critical'),
        ('warning', 'Warning'),
    ]

    user = models.ForeignKey(
        'app.User', on_delete=models.CASCADE, null=True, blank=True, related_name='alerts',
    )
    rule_id = models.CharField(max_length=64, db_index=True)
    severity = models.CharField(max_length=16, choices=SEVERITY_CHOICES)
    fired_at = models.DateTimeField(default=timezone.now, db_index=True)
    resolved_at = models.DateTimeField(null=True, blank=True, db_index=True)
    payload = models.JSONField(null=True, blank=True)

    class Meta:
        ordering = ['-fired_at']
        constraints = [
            # Re-firing an already-open alert is a no-op rather than a duplicate
            # row. Two constraints are needed because a cohort-level alert has a
            # NULL user, and SQL treats NULLs as distinct from each other.
            UniqueConstraint(
                fields=['user', 'rule_id'],
                condition=Q(resolved_at__isnull=True),
                name='uniq_open_alert_per_user_rule',
            ),
            UniqueConstraint(
                fields=['rule_id'],
                condition=Q(resolved_at__isnull=True, user__isnull=True),
                name='uniq_open_cohort_alert_per_rule',
            ),
        ]

    def __str__(self):
        scope = f"user {self.user_id}" if self.user_id else "cohort"
        return f"{self.rule_id} [{self.severity}] for {scope}"
