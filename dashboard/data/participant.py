"""Per-participant rollup: where they are in the study, and how they are doing.

Cumulative benchmark rates are aggregated from MetricsDaily rather than from raw
rows, so a definition only ever lives in daily.py. Each rate is stored alongside
its numerator and denominator, because the suppression rule shows counts when a
denominator is thin and the counts have to exist for that to be possible.
"""

from app.models import EMA
from django.utils import timezone as django_timezone

from dashboard.data.config import RUN_IN_DAYS, STUDY_DAYS
from dashboard.data.windows import day1_date, study_day_for, today_local, waking_window_minutes
from dashboard.models import Alert, MetricsDaily, MetricsParticipant

# An open alert's contribution to risk_score. Deliberately coarse: two open
# criticals, or one critical plus four warnings, saturate the scale.
RISK_WEIGHTS = {'critical': 40, 'warning': 15}
RISK_SCORE_MAX = 100


def _rate(numerator, denominator):
    return numerator / denominator if denominator else None


def compute_risk_score(user):
    severities = list(
        Alert.objects
        .filter(user=user, resolved_at__isnull=True)
        .values_list('severity', flat=True)
    )
    total = sum(RISK_WEIGHTS.get(severity, 0) for severity in severities)
    return min(RISK_SCORE_MAX, total)


def withdrawal_at(user, existing=None, now=None):
    """The withdrawal proxy for a participant, carried forward once set.

    Split out from compute_participant so the recompute can resolve it before
    computing daily rows, which need it to decide is_active_day. Otherwise a
    withdrawal observed this run would not reach the day rows until the next one.
    """
    now = now or django_timezone.now()
    existing_value = existing.first_seen_not_enrolled_at if existing is not None else None
    if existing_value is not None:
        return existing_value
    if user.enrolled_at is None or user.is_enrolled:
        return None
    study_day_now = study_day_for(user, today_local(now))
    # Unenrolling someone who already reached the last study day is how the
    # study closes out, not a dropout. A mid-study withdrawal that happened
    # before monitoring existed is recovered by the backfill_withdrawals
    # command, which reads the admin audit log.
    if study_day_now is not None and study_day_now >= STUDY_DAYS:
        return None
    return now


def _phase(study_day_now, withdrawn_at):
    # Withdrawal is checked first and is permanent: someone who left on day 10
    # is still withdrawn once day 35 rolls past, not complete.
    if withdrawn_at is not None:
        return 'withdrawn'
    if study_day_now is None or study_day_now < 0:
        return 'pre_enrollment'
    if study_day_now >= STUDY_DAYS:
        return 'complete'
    return 'run_in' if study_day_now < RUN_IN_DAYS else 'mrt'


def _cumulative(rows):
    """Benchmark numerators and denominators over a participant's active days."""
    slots_covered = slots_expected = 0
    captured = sent = 0
    wear_minutes = waking_minutes = 0.0

    for row in rows:
        if row.slots_expected:
            slots_covered += row.slots_covered or 0
            slots_expected += row.slots_expected
        if row.sent_n:
            captured += row.outcome_captured_n or 0
            # Prompts with a documented delivery failure leave the denominator
            # and are reported separately, per JITAI-analysis-plan.md. Note this
            # is not the same as delivered_n: a prompt can reach the phone and
            # never have its receipt reported, and dropping those would
            # understate the denominator and inflate the rate.
            sent += max(0, row.sent_n - (row.delivery_failures_n or 0))
        if row.wear_valid_pct is not None:
            total = waking_window_minutes(row.local_date)
            wear_minutes += row.wear_valid_pct * total
            waking_minutes += total

    return {
        'slot_coverage_num': slots_covered,
        'slot_coverage_den': slots_expected,
        'slot_coverage_rate': _rate(slots_covered, slots_expected),
        # A prompt counts as responded to when the outcome-window check-in is
        # completed, not when the notification is tapped. Confirmed 2026-09-10.
        # notification_tapped is still tracked per-day as prompt_opened_n, but
        # it is not this benchmark.
        'prompt_response_num': captured,
        'prompt_response_den': sent,
        'prompt_response_rate': _rate(captured, sent),
        'wear_num': round(wear_minutes),
        'wear_den': round(waking_minutes),
        'wear_rate': _rate(wear_minutes, waking_minutes),
    }


def compute_participant(user, existing=None, daily_rows=None, now=None):
    """One participant's MetricsParticipant fields.

    `existing` is that participant's current row, if any. It carries
    first_seen_not_enrolled_at forward: the field records the first run that
    observed is_enrolled=False, so it must never be recomputed from scratch.
    """
    now = now or django_timezone.now()
    study_day_now = study_day_for(user, today_local(now))
    withdrawn_at = withdrawal_at(user, existing, now)

    if daily_rows is None:
        daily_rows = list(MetricsDaily.objects.filter(user=user, is_active_day=True))

    last_ema = (
        EMA.objects.filter(user=user).order_by('-sent_at').values_list('sent_at', flat=True).first()
    )
    device = getattr(user, 'wearabledevice', None)

    payload = {
        'enrolled_at': user.enrolled_at,
        'day1_date': day1_date(user),
        'study_day_now': study_day_now,
        'phase': _phase(study_day_now, withdrawn_at),
        'is_enrolled_snapshot': user.is_enrolled,
        'first_seen_not_enrolled_at': withdrawn_at,
        'last_ema_at': last_ema,
        'last_sync_at': device.last_synced_at if device is not None else None,
        # Retention is "never withdrew", not "responded recently": missing
        # prompts is not dropout (analysis-resources/JITAI-analysis-plan.md).
        'active_retention': None if user.enrolled_at is None else withdrawn_at is None,
        'risk_score': compute_risk_score(user),
    }
    payload.update(_cumulative(daily_rows))
    return payload


def refresh_risk_scores(users=None):
    """Re-score open alerts into MetricsParticipant.

    Called after evaluate_alerts() so a score reflects the alerts from this run
    rather than the previous one.
    """
    queryset = MetricsParticipant.objects.select_related('user')
    if users is not None:
        queryset = queryset.filter(user__in=users)

    updated = []
    for row in queryset:
        score = compute_risk_score(row.user)
        if row.risk_score != score:
            row.risk_score = score
            updated.append(row)
    if updated:
        MetricsParticipant.objects.bulk_update(updated, ['risk_score'])
    return len(updated)
