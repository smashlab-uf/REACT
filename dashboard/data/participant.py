"""Per-participant rollup: where they are in the study, and how they are doing.

Cumulative benchmark rates are aggregated from MetricsDaily rather than from raw
rows, so a definition only ever lives in daily.py. Each rate is stored alongside
its numerator and denominator, because the suppression rule shows counts when a
denominator is thin and the counts have to exist for that to be possible.
"""

from app.models import EMA
from django.utils import timezone as django_timezone

from dashboard.data.config import RUN_IN_DAYS, STUDY_DAYS
from dashboard.data.windows import (
    day1_date,
    elapsed_minutes,
    study_day_for,
    today_local,
    waking_window_minutes,
)
from dashboard.models import Alert, MetricsDaily, MetricsParticipant

# Points per unit of each risk term. Weights are a starting point and are meant
# to be tuned after Phase 1, so they stay here rather than inline at the call.
RISK_WEIGHTS = {
    'ema_stale': 3,
    'low_coverage': 2,
    'sync_stale': 2,
    'low_wear': 1,
    'missing_signal': 1,
    'open_critical': 4,
}
RISK_TRAILING_DAYS = 7
RISK_EMA_STALE_CAP = 5
RISK_MISSING_SIGNAL_CAP = 5
RISK_COVERAGE_FLOOR = 0.5
RISK_WEAR_FLOOR = 0.5
RISK_SYNC_STALE_HOURS = 24
RISK_SCORE_MAX = (
    RISK_WEIGHTS['ema_stale'] * RISK_EMA_STALE_CAP
    + RISK_WEIGHTS['low_coverage'] * RISK_TRAILING_DAYS
    + RISK_WEIGHTS['sync_stale']
    + RISK_WEIGHTS['low_wear'] * RISK_TRAILING_DAYS
    + RISK_WEIGHTS['missing_signal'] * RISK_MISSING_SIGNAL_CAP
    + RISK_WEIGHTS['open_critical']
)

# The score answers "who needs a phone call today", so it is only defined for
# someone the study is currently asking something of. Scoring the other phases
# would put people who have not started, have finished, or have withdrawn at the
# top of the RA's list, since every term reads as maximally bad for them.
RISK_ACTIVE_PHASES = frozenset({'run_in', 'mrt'})


def _rate(numerator, denominator):
    return numerator / denominator if denominator else None


def _trailing_active(daily_rows, local_date):
    rows = sorted(
        (row for row in daily_rows if row.is_active_day and row.local_date <= local_date),
        key=lambda row: row.local_date,
    )
    return rows[-RISK_TRAILING_DAYS:]


def _days_since_scheduled_ema(daily_rows, local_date):
    """Days since the last scheduled check-in, over the whole study, not the
    trailing window. Read from the daily rows rather than from last_ema_at,
    which counts JITAI and post-prompt check-ins as well.
    """
    dates = [
        row.local_date for row in daily_rows
        if row.is_active_day and row.ema_scheduled_n and row.local_date <= local_date
    ]
    if not dates:
        return RISK_EMA_STALE_CAP
    return (local_date - max(dates)).days


def compute_risk_score(user, phase=None, daily_rows=None, last_sync_at=None, now=None):
    """The transparent risk score that orders the participant grid.

    Returns (score, components). Components hold points rather than raw counts,
    so the "why" column an RA reads sums to the score without arithmetic.

    Every term is null-safe by design: a day whose metric is NULL is outside the
    participant's window or was never measured, and contributes nothing. Counting
    it as a failure would collapse structural null into zero, which is the one
    thing this layer exists not to do.
    """
    now = now or django_timezone.now()
    local_date = today_local(now)

    if phase is None:
        phase = _phase(study_day_for(user, local_date), withdrawal_at(user, None, now))
    if phase not in RISK_ACTIVE_PHASES:
        return None, None

    if daily_rows is None:
        daily_rows = list(MetricsDaily.objects.filter(user=user, is_active_day=True))
    if last_sync_at is None:
        device = getattr(user, 'wearabledevice', None)
        last_sync_at = device.last_synced_at if device is not None else None

    trailing = _trailing_active(daily_rows, local_date)

    low_coverage = sum(
        1 for row in trailing
        if row.slots_expected and (row.slots_covered or 0) / row.slots_expected < RISK_COVERAGE_FLOOR
    )
    low_wear = sum(
        1 for row in trailing
        if row.wear_valid_pct is not None and row.wear_valid_pct < RISK_WEAR_FLOOR
    )
    missing_signal = min(
        RISK_MISSING_SIGNAL_CAP,
        sum(row.ema_missing_b1b2_n or 0 for row in trailing),
    )
    # Never synced counts as stale. A participant with no device row at all is a
    # different problem, caught by the no_wearable_data cohort alert, but from
    # the RA's side both are still a call worth making.
    sync_stale = (
        last_sync_at is None
        or elapsed_minutes(last_sync_at, now) / 60 > RISK_SYNC_STALE_HOURS
    )
    # The spec names severity=high, which no alert can carry: Alert.SEVERITY_CHOICES
    # is critical/warning. Scored against critical, otherwise the term is dead.
    open_critical = Alert.objects.filter(
        user=user, resolved_at__isnull=True, severity='critical',
    ).exists()

    counts = {
        'ema_stale': min(RISK_EMA_STALE_CAP, _days_since_scheduled_ema(daily_rows, local_date)),
        'low_coverage': low_coverage,
        'sync_stale': int(sync_stale),
        'low_wear': low_wear,
        'missing_signal': missing_signal,
        'open_critical': int(open_critical),
    }
    components = {term: count * RISK_WEIGHTS[term] for term, count in counts.items()}
    return sum(components.values()), components


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
    last_sync_at = device.last_synced_at if device is not None else None
    phase = _phase(study_day_now, withdrawn_at)
    risk_score, risk_components = compute_risk_score(
        user, phase=phase, daily_rows=daily_rows, last_sync_at=last_sync_at, now=now)

    payload = {
        'enrolled_at': user.enrolled_at,
        'day1_date': day1_date(user),
        'study_day_now': study_day_now,
        'phase': phase,
        'is_enrolled_snapshot': user.is_enrolled,
        'first_seen_not_enrolled_at': withdrawn_at,
        'last_ema_at': last_ema,
        'last_sync_at': last_sync_at,
        # Retention is "never withdrew", not "responded recently": missing
        # prompts is not dropout (analysis-resources/JITAI-analysis-plan.md).
        'active_retention': None if user.enrolled_at is None else withdrawn_at is None,
        'risk_score': risk_score,
        'risk_components': risk_components,
    }
    payload.update(_cumulative(daily_rows))
    return payload


def refresh_risk_scores(users=None, now=None):
    """Re-score into MetricsParticipant after the alert pass.

    Called after evaluate_alerts() so a score reflects the alerts from this run
    rather than the previous one. Only the open-critical term can have moved
    since compute_participant ran, but the score is recomputed whole rather than
    patched, so there is one definition of it and not two.
    """
    now = now or django_timezone.now()
    queryset = MetricsParticipant.objects.select_related('user', 'user__wearabledevice')
    if users is not None:
        queryset = queryset.filter(user__in=users)
    rows = list(queryset)

    daily_by_user = {}
    for daily in MetricsDaily.objects.filter(
        user__in=[row.user_id for row in rows], is_active_day=True,
    ):
        daily_by_user.setdefault(daily.user_id, []).append(daily)

    updated = []
    for row in rows:
        score, components = compute_risk_score(
            row.user, phase=row.phase, daily_rows=daily_by_user.get(row.user_id, []),
            last_sync_at=row.last_sync_at, now=now)
        if (row.risk_score, row.risk_components) != (score, components):
            row.risk_score = score
            row.risk_components = components
            updated.append(row)
    if updated:
        MetricsParticipant.objects.bulk_update(updated, ['risk_score', 'risk_components'])
    return len(updated)
