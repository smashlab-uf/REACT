from rest_framework import serializers

from dashboard.data.windows import participant_time
from dashboard.models import Alert, MetricsCohort, MetricsDaily, MetricsParticipant


class MetricsDailySerializer(serializers.ModelSerializer):
    class Meta:
        model = MetricsDaily
        fields = [
            'id', 'user', 'study_day', 'local_date', 'is_run_in', 'is_active_day',
            'computed_at', 'item_bank_version',
            'ema_scheduled_n', 'ema_jitai_n', 'ema_post_prompt_n',
            'slots_expected', 'slots_covered', 'slots_reminded_uncovered', 'slots_silent',
            'reminders_sent', 'reminders_per_checkin_median',
            'completeness_mean', 'ema_missing_b1b2_n',
            'decision_points_n', 'eligible_n', 'sent_n', 'delivered_n', 'cap_hit',
            'min_gap_min', 'cooldown_violations_n', 'runin_violation_n',
            'prompt_opened_n', 'prompt_acted_n', 'prompt_dismissed_n', 'outcome_captured_n',
            'wear_valid_pct', 'wear_gap_pct', 'gaps_gt2h_n', 'max_gap_min', 'hr_minutes_valid',
            'last_sync_age_h_eod', 'clock_skew_p95_ms', 'delivery_failures_n',
        ]
        read_only_fields = ('id', 'user', 'computed_at')


class MetricsParticipantSerializer(serializers.ModelSerializer):
    participant_id = serializers.SerializerMethodField()

    class Meta:
        model = MetricsParticipant
        fields = [
            'id', 'user', 'participant_id', 'computed_at',
            'enrolled_at', 'day1_date', 'study_day_now', 'phase',
            'is_enrolled_snapshot', 'first_seen_not_enrolled_at',
            'last_ema_at', 'last_sync_at', 'active_retention',
            'risk_score', 'risk_components',
            'slot_coverage_rate', 'slot_coverage_num', 'slot_coverage_den',
            'prompt_response_rate', 'prompt_response_num', 'prompt_response_den',
            'wear_rate', 'wear_num', 'wear_den',
        ]
        read_only_fields = ('id', 'user', 'computed_at')

    def get_participant_id(self, obj):
        return participant_label(obj.user)


class MetricsCohortSerializer(serializers.ModelSerializer):
    class Meta:
        model = MetricsCohort
        fields = [
            'id', 'as_of', 'phase_filter', 'n_participants', 'n_active',
            'benchmarks', 'series_14d', 'integrity', 'funnel',
            'decision_points_n', 'eligible_n', 'sent_n', 'delivered_n',
            'cooldown_violations_n', 'runin_violations_n', 'cap_hit_days',
            'delivery_failures_n',
        ]
        read_only_fields = ('id', 'as_of')


class AlertSerializer(serializers.ModelSerializer):
    participant_id = serializers.SerializerMethodField()
    scope = serializers.SerializerMethodField()
    link_date = serializers.SerializerMethodField()

    class Meta:
        model = Alert
        fields = [
            'id', 'user', 'participant_id', 'scope', 'rule_id', 'severity',
            'fired_at', 'resolved_at', 'payload', 'link_date',
        ]
        read_only_fields = ('id', 'fired_at')

    def get_participant_id(self, obj):
        return participant_label(obj.user) if obj.user_id else None

    def get_scope(self, obj):
        return 'participant' if obj.user_id else 'cohort'

    def get_link_date(self, obj):
        """The local day the timeline should open on.

        Rules that know which days offended say so in their payload, and that
        day is what someone wants to look at. Everything else falls back to when
        the alert fired, which is at least the right neighbourhood. Derived here
        so every reader gets the same answer.
        """
        payload = obj.payload or {}
        days = payload.get('days')
        if isinstance(days, dict) and days:
            return sorted(days)[0]
        if isinstance(days, list) and days:
            return sorted(days)[0]
        for key in ('day', 'local_date'):
            if payload.get(key):
                return payload[key]
        return participant_time(obj.fired_at).date().isoformat()


def participant_label(user):
    """The Labfront id when there is one, else the user_id.

    Matches what the existing /dashboard/participants/ endpoint reports, so both
    surfaces name a participant the same way.
    """
    if user is None:
        return None
    device = getattr(user, 'wearabledevice', None)
    return device.labfront_participant_id if device is not None else str(user.pk)
