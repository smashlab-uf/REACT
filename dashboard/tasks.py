import logging
from datetime import timedelta

from app.models import User
from celery import shared_task
from django.utils import timezone as django_timezone

from dashboard.data.alerts import evaluate_alerts
from dashboard.data.cohort import compute_cohort
from dashboard.data.config import (
    METRICS_COHORT_RETENTION_DAYS,
    METRICS_RECOMPUTE_TRAILING_DAYS,
    STUDY_DAYS,
)
from dashboard.data.daily import compute_daily
from dashboard.data.participant import compute_participant, refresh_risk_scores, withdrawal_at
from dashboard.data.windows import day1_date, local_dates_for, today_local
from dashboard.models import MetricsCohort, MetricsDaily, MetricsParticipant

logger = logging.getLogger(__name__)

PHASE_FILTERS = (MetricsCohort.PHASE_ALL, MetricsCohort.PHASE_1, MetricsCohort.PHASE_2)


def _dates_for(user, days, from_day, all_days, now):
    if not all_days and from_day is None:
        return local_dates_for(user, days or METRICS_RECOMPUTE_TRAILING_DAYS, now)

    anchor = day1_date(user)
    if anchor is None:
        return []
    today = today_local(now)
    first = 0 if all_days else max(0, from_day)
    pairs = []
    for study_day in range(first, STUDY_DAYS):
        local_date = anchor + timedelta(days=study_day)
        if local_date > today:
            break
        pairs.append((local_date, study_day))
    return pairs


def recompute_for_user(user, existing=None, days=None, from_day=None, all_days=False, now=None):
    """Upsert one participant's daily rows, then their rollup. Returns row count."""
    now = now or django_timezone.now()
    if existing is None:
        existing = MetricsParticipant.objects.filter(user=user).first()

    # Resolved before the day rows, because is_active_day depends on it.
    withdrawn_at = withdrawal_at(user, existing, now)

    written = 0
    for local_date, study_day in _dates_for(user, days, from_day, all_days, now):
        payload = compute_daily(user, local_date, study_day=study_day,
                                withdrawn_at=withdrawn_at, now=now)
        payload.pop('study_day')
        MetricsDaily.objects.update_or_create(
            user=user, study_day=study_day, defaults=payload,
        )
        written += 1

    rollup = compute_participant(user, existing=existing, now=now)
    MetricsParticipant.objects.update_or_create(user=user, defaults=rollup)
    return written


def recompute_metrics(users=None, days=None, from_day=None, all_days=False, now=None):
    """Refresh the whole monitoring layer. Shared by the Celery task and the
    recompute_metrics management command.
    """
    now = now or django_timezone.now()
    if users is None:
        users = User.objects.filter(enrolled_at__isnull=False)
    users = list(users)

    existing_rows = {
        row.user_id: row for row in MetricsParticipant.objects.filter(user__in=users)
    }

    participants = rows = failed = 0
    for user in users:
        try:
            rows += recompute_for_user(
                user, existing=existing_rows.get(user.pk),
                days=days, from_day=from_day, all_days=all_days, now=now,
            )
            participants += 1
        except Exception:
            # One participant's bad data must not stop the cohort from updating,
            # matching how evaluate_jitai_triggers isolates its per-user work.
            failed += 1
            logger.exception('recompute_metrics failed for user_id=%s', user.pk)

    for phase_filter in PHASE_FILTERS:
        MetricsCohort.objects.create(**compute_cohort(phase_filter, now=now))

    opened, updated, resolved = evaluate_alerts(now=now)
    # After the alerts, so a score reflects this run rather than the last one.
    rescored = refresh_risk_scores()

    cutoff = now - timedelta(days=METRICS_COHORT_RETENTION_DAYS)
    pruned, _ = MetricsCohort.objects.filter(as_of__lt=cutoff).delete()

    return {
        'participants': participants, 'daily_rows': rows, 'failed': failed,
        'cohorts': len(PHASE_FILTERS), 'alerts_opened': opened,
        'alerts_updated': updated, 'alerts_resolved': resolved,
        'rescored': rescored, 'cohort_rows_pruned': pruned,
    }


@shared_task
def recompute_monitoring_metrics():
    summary = recompute_metrics()
    logger.info('recompute_monitoring_metrics: %s', summary)
    return summary
