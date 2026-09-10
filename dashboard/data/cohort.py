"""Cohort snapshot: the feasibility benchmarks and the MRT integrity counts.

Written once per compute run per phase filter, so Stage 1 reads a single row
instead of re-aggregating MetricsDaily on every poll.

Two rules are enforced here rather than left to the front end. A rate is only
emitted once its denominator carries enough participants and enough units;
below that the entry carries raw counts and a null value, so a percentage
computed from five people can never reach a chart. And a benchmark named in
UNMEASURABLE_BENCHMARKS is marked unmeasurable rather than reported as zero.
"""

import math
from datetime import timedelta

from app.models import User
from django.db.models import Count, Q, Sum
from django.utils import timezone as django_timezone

from dashboard.data.config import (
    BENCHMARKS,
    PHASE1_USER_IDS,
    RATE_MIN_PARTICIPANTS,
    RATE_MIN_UNITS,
    UNMEASURABLE_BENCHMARKS,
)
from dashboard.data.windows import today_local
from dashboard.models import MetricsCohort, MetricsDaily, MetricsParticipant

SERIES_DAYS = 14
# 95% two-sided normal quantile.
Z = 1.959963984540054
IN_STUDY_PHASES = ('run_in', 'mrt')


def wilson_interval(k, n, z=Z):
    """Wilson score interval for a binomial proportion.

    Preferred over the normal approximation because it stays inside [0, 1] and
    behaves at the small denominators and extreme proportions a feasibility
    study actually produces.
    """
    if not n:
        return None, None
    p = k / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return max(0.0, centre - half), min(1.0, centre + half)


def suppress_rate(k, n, n_participants):
    """The proportion, or None when the denominator is too thin to publish.

    Phase 1 has five participants, so every rate there comes back None and the
    caller shows counts instead.
    """
    if n_participants < RATE_MIN_PARTICIPANTS or n < RATE_MIN_UNITS:
        return None
    return k / n if n else None


def _benchmark(name, k, n, n_participants, unit, extra=None):
    if name in UNMEASURABLE_BENCHMARKS:
        return {
            'measurable': False, 'suppressed': False, 'unit': unit,
            'target': BENCHMARKS.get(name),
            'value': None, 'wilson_low': None, 'wilson_high': None,
            'numerator': None, 'denominator': None, 'participants': 0,
        }

    value = suppress_rate(k, n, n_participants)
    low, high = wilson_interval(k, n) if value is not None else (None, None)
    entry = {
        'measurable': True,
        'suppressed': value is None,
        'unit': unit,
        'target': BENCHMARKS.get(name),
        'value': value,
        'wilson_low': low,
        'wilson_high': high,
        'numerator': k,
        'denominator': n,
        'participants': n_participants,
    }
    if extra:
        entry.update(extra)
    return entry


def participants_for(phase_filter):
    """The participants a phase filter selects. Never-enrolled users are out.

    Phase 1 is a pinned list of user_ids from the environment; phase 2 is
    everyone else. With the list unset, phase 1 is empty and phase 2 is the
    whole cohort.
    """
    queryset = User.objects.filter(enrolled_at__isnull=False)
    if phase_filter == MetricsCohort.PHASE_1:
        return queryset.filter(user_id__in=PHASE1_USER_IDS)
    if phase_filter == MetricsCohort.PHASE_2:
        return queryset.exclude(user_id__in=PHASE1_USER_IDS)
    return queryset


def _sum_pairs(rows, num_field, den_field):
    k = n = contributors = 0
    for row in rows:
        denominator = getattr(row, den_field) or 0
        if denominator:
            k += getattr(row, num_field) or 0
            n += denominator
            contributors += 1
    return k, n, contributors


def _wear_benchmark(users):
    """Wear scored per participant-day, which is the unit that makes it binomial.

    A minutes-over-minutes ratio is the natural coverage figure but its 840
    "trials" per day are not independent, so a Wilson interval on it would be
    meaningless. The day-level pass rate carries the interval; the mean coverage
    rides along as context.
    """
    scored = MetricsDaily.objects.filter(
        user__in=users, is_active_day=True, wear_valid_pct__isnull=False,
    )
    aggregate = scored.aggregate(
        days=Count('id'),
        met=Count('id', filter=Q(wear_valid_pct__gte=BENCHMARKS['wear'])),
        contributors=Count('user', distinct=True),
        mean_coverage=Sum('wear_valid_pct'),
    )
    days = aggregate['days'] or 0
    total = aggregate['mean_coverage']
    return _benchmark(
        'wear', aggregate['met'] or 0, days, aggregate['contributors'] or 0,
        'participant-days meeting the coverage target',
        extra={'mean_coverage': (total / days) if days else None},
    )


def _series(users, now):
    end = today_local(now)
    start = end - timedelta(days=SERIES_DAYS - 1)
    rows = (
        MetricsDaily.objects
        .filter(user__in=users, is_active_day=True, local_date__gte=start, local_date__lte=end)
        .values('local_date')
        .annotate(
            participants=Count('user', distinct=True),
            slots_covered=Sum('slots_covered'),
            slots_expected=Sum('slots_expected'),
            ema_scheduled_n=Sum('ema_scheduled_n'),
            sent_n=Sum('sent_n'),
            delivered_n=Sum('delivered_n'),
            outcome_captured_n=Sum('outcome_captured_n'),
            delivery_failures_n=Sum('delivery_failures_n'),
            wear_days_scored=Count('id', filter=Q(wear_valid_pct__isnull=False)),
            wear_days_met=Count('id', filter=Q(wear_valid_pct__gte=BENCHMARKS['wear'])),
        )
        .order_by('local_date')
    )

    series = []
    for row in rows:
        covered = row['slots_covered'] or 0
        expected = row['slots_expected'] or 0
        entry = {key: value for key, value in row.items() if key != 'local_date'}
        entry['date'] = row['local_date'].isoformat()
        entry['slot_coverage'] = suppress_rate(covered, expected, row['participants'])
        series.append(entry)
    return series


def compute_cohort(phase_filter, now=None):
    now = now or django_timezone.now()
    users = list(participants_for(phase_filter))
    rows = list(MetricsParticipant.objects.filter(user__in=users))

    slot_k, slot_n, slot_contributors = _sum_pairs(rows, 'slot_coverage_num', 'slot_coverage_den')
    prompt_k, prompt_n, prompt_contributors = _sum_pairs(
        rows, 'prompt_response_num', 'prompt_response_den'
    )
    # Retention is one trial per participant: missing prompts is not dropout,
    # only a withdrawal is (analysis-resources/JITAI-analysis-plan.md).
    began = [row for row in rows if row.active_retention is not None]
    retained = sum(1 for row in began if row.active_retention)

    integrity = MetricsDaily.objects.filter(user__in=users, is_active_day=True).aggregate(
        decision_points_n=Sum('decision_points_n'),
        eligible_n=Sum('eligible_n'),
        sent_n=Sum('sent_n'),
        delivered_n=Sum('delivered_n'),
        cooldown_violations_n=Sum('cooldown_violations_n'),
        runin_violations_n=Sum('runin_violation_n'),
        delivery_failures_n=Sum('delivery_failures_n'),
        cap_hit_days=Count('id', filter=Q(cap_hit=True)),
    )

    return {
        'as_of': now,
        'phase_filter': phase_filter,
        'n_participants': len(users),
        'n_active': sum(1 for row in rows if row.phase in IN_STUDY_PHASES),
        'benchmarks': {
            'slot_coverage': _benchmark(
                'slot_coverage', slot_k, slot_n, slot_contributors, 'check-in slots',
            ),
            'prompt_response': _benchmark(
                'prompt_response', prompt_k, prompt_n, prompt_contributors,
                'prompts sent, less documented delivery failures',
            ),
            'wear': _wear_benchmark(users),
            'retention': _benchmark(
                'retention', retained, len(began), len(began), 'participants',
            ),
        },
        'series_14d': _series(users, now),
        **{key: value or 0 for key, value in integrity.items()},
    }
