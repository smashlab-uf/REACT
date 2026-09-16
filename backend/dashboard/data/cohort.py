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

from app.models import JITAILog, User
from django.db.models import Count, F, Q, Sum
from django.utils import timezone as django_timezone

from dashboard.data.config import (
    BENCHMARKS,
    JITAI_COOLDOWN_MINUTES,
    PHASE1_USER_IDS,
    RATE_MIN_PARTICIPANTS,
    RATE_MIN_UNITS,
    RUN_IN_DAYS,
    STUDY_DAYS,
    UNMEASURABLE_BENCHMARKS,
    WAKING_WINDOW_END_HOUR,
    WAKING_WINDOW_START_HOUR,
    randomization_p,
)
from dashboard.data.windows import participant_day_bounds, today_local
from dashboard.models import MetricsCohort, MetricsDaily, MetricsParticipant

SERIES_DAYS = 14
# 95% two-sided normal quantile.
Z = 1.959963984540054
IN_STUDY_PHASES = ('run_in', 'mrt')
# The wear denominator, 08:00-22:00. Constant for this study: America/New_York
# transitions at 02:00, outside the window.
WAKING_MINUTES = (WAKING_WINDOW_END_HOUR - WAKING_WINDOW_START_HOUR) * 60
# Gap-coverage exceeding minute-coverage by this much is burst charging.
WEAR_DIVERGENCE = 0.15
# How far back "active" looks for a scheduled check-in.
ACTIVE_RETENTION_DAYS = 7


def wilson_interval(k, n, z=Z):
    """Wilson score interval for a binomial proportion.

    Preferred over the normal approximation because it stays inside [0, 1] and
    behaves at the small denominators and extreme proportions a feasibility
    study actually produces.
    """
    # Undefined with no denominator, and equally undefined when k falls outside
    # [0, n]: k > n makes p > 1, so p * (1 - p) goes negative and math.sqrt
    # raises. That is not a hypothetical. _gauge is built to surface exactly this
    # contradiction, and production carries 25 prompts sent against 1 eligible
    # decision point, so the gauge that exists to report the problem used to
    # crash the whole cohort recompute on it.
    if not n or not 0 <= k <= n:
        return None, None
    p = k / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return max(0.0, centre - half), min(1.0, centre + half)


def ks_uniform(values):
    """One-sample Kolmogorov-Smirnov test of `values` against Uniform(0, 1).

    Hand-rolled because scipy is not in backend/requirements.txt and the Celery
    worker cannot have it. The statistic is a sort and a max; the p-value is the
    asymptotic Kolmogorov distribution, which is what scipy itself uses for an
    exact-mode fallback and is far more precision than an alarm at p < 0.01
    needs. A test holds it to scipy.stats.kstest, which the analytics
    environment does have.

    Returns (statistic, p_value), or (None, None) when there is nothing to test.
    """
    ordered = sorted(values)
    n = len(ordered)
    if not n:
        return None, None

    statistic = 0.0
    for index, value in enumerate(ordered):
        # Both one-sided deviations: the empirical CDF steps up at each point,
        # so the largest gap can sit on either side of the step.
        statistic = max(statistic, (index + 1) / n - value, value - index / n)

    return statistic, _kolmogorov_sf(math.sqrt(n) * statistic)


def _kolmogorov_sf(x, terms=100):
    """P(K > x) for the limiting Kolmogorov distribution."""
    if x <= 0:
        return 1.0
    total = 0.0
    for k in range(1, terms + 1):
        total += (-1) ** (k - 1) * math.exp(-2 * k * k * x * x)
    return max(0.0, min(1.0, 2 * total))


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

    # The second reading. Naming here is a known trap: the field called
    # wear_valid_pct is (840 - gaps>2h) / 840, which is what the 0.80 benchmark
    # scores, while hr_minutes_valid counts minutes that actually carry a
    # sample. The field called wear_gap_pct is the complementary long-gap
    # fraction and is deliberately not what either tile shows.
    minutes = scored.aggregate(
        minute_total=Sum('hr_minutes_valid'),
        diverged=Count('id', filter=Q(
            wear_valid_pct__gte=F('hr_minutes_valid') / WAKING_MINUTES + WEAR_DIVERGENCE)),
    )
    minute_mean = (
        (minutes['minute_total'] / days / WAKING_MINUTES) if days and minutes['minute_total']
        else None
    )

    return _benchmark(
        'wear', aggregate['met'] or 0, days, aggregate['contributors'] or 0,
        'participant-days meeting the coverage target',
        extra={
            'mean_coverage': (total / days) if days else None,
            'mean_minute_coverage': minute_mean,
            # Gap-coverage far above minute-coverage means the watch was on in
            # short bursts: no single gap ran past two hours, so the benchmark
            # definition flatters a day that carries little actual data.
            'burst_charging_days': minutes['diverged'] or 0,
            'divergence_threshold': WEAR_DIVERGENCE,
        },
    )


# Reasons the engine records when a decision point was never eligible. Anything
# else means the threshold check passed and the draw should exist.
INELIGIBLE_REASONS = (
    'below within-person threshold',
    'cooldown active',
    'daily cap reached',
    'missing or insufficient EMA data',
    'insufficient within-person history',
)


def _gauge(name, k, n, target=None, extra=None):
    """One MRT integrity gauge. Unlike a benchmark these are never suppressed:
    a randomization audit that hides itself at n=5 is worse than useless, and
    Phase 1 is exactly when you want to catch the engine misbehaving.
    """
    low, high = wilson_interval(k, n)
    entry = {
        'name': name, 'numerator': k, 'denominator': n,
        'value': (k / n) if n else None,
        'wilson_low': low, 'wilson_high': high, 'target': target,
        # A numerator above its denominator is not a thin sample, it is a
        # contradiction: production carries prompts sent on decision points that
        # were never marked eligible, which makes sent_n/eligible_n read 1/0.
        # Rendering that as "no data" would hide the very thing the strip exists
        # to catch.
        'contradiction': k > n,
    }
    if extra:
        entry.update(extra)
    return entry


def _mrt_integrity(users, now):
    """The strip that says whether the trial will be analysable.

    Restricted to weeks 2-5: run-in days carry no intervention, so folding them
    in would dilute every rate here with days the protocol never intended to
    randomize on.
    """
    mrt_days = MetricsDaily.objects.filter(
        user__in=users, is_active_day=True, is_run_in=False,
    )
    totals = mrt_days.aggregate(
        decision_points_n=Sum('decision_points_n'),
        eligible_n=Sum('eligible_n'),
        sent_n=Sum('sent_n'),
        delivered_n=Sum('delivered_n'),
        outcome_captured_n=Sum('outcome_captured_n'),
        cooldown_violations_n=Sum('cooldown_violations_n'),
        ema_scheduled_n=Sum('ema_scheduled_n'),
        cap_hit_days=Count('id', filter=Q(cap_hit=True)),
        participant_days=Count('id'),
    )
    totals = {key: value or 0 for key, value in totals.items()}

    logs = JITAILog.objects.filter(user__in=users, randomization_draw__isnull=False)
    draws = list(logs.values_list('randomization_draw', 'randomization_probability',
                                  'send_prompt'))
    # send_prompt must be exactly the draw landing under the probability. Any row
    # where it is not means the coin flip and what was acted on came apart.
    mismatches = sum(
        1 for draw, probability, sent in draws
        if probability is not None and sent != (draw < probability)
    )
    statistic, p_value = ks_uniform([draw for draw, _, _ in draws])

    # Two readings of "was this eligible" that must agree: the draw exists, and
    # the reason is not one of the ineligible ones. Disagreement is an engine bug.
    audit = JITAILog.objects.filter(user__in=users).aggregate(
        draw_set_but_reason_ineligible=Count('id', filter=Q(
            randomization_draw__isnull=False, trigger_reason__in=INELIGIBLE_REASONS)),
        reason_eligible_but_no_draw=Count('id', filter=Q(
            randomization_draw__isnull=True) & ~Q(trigger_reason__in=INELIGIBLE_REASONS)),
    )

    return {
        'window': 'weeks 2-5 (study_day >= run-in)',
        'eligibility_rate': _gauge(
            'eligibility_rate', totals['eligible_n'], totals['decision_points_n'],
            extra={
                'alarm_low': 0.10, 'alarm_high': 0.35,
                # A decision point only exists when an EMA is submitted, so
                # availability is confounded with compliance. Printed beside the
                # gauge so the confound cannot be read past.
                'decision_points_per_scheduled_ema': (
                    totals['decision_points_n'] / totals['ema_scheduled_n']
                    if totals['ema_scheduled_n'] else None),
                'ema_scheduled_n': totals['ema_scheduled_n'],
                'cross_check_disagreements': sum(audit.values()),
                **audit,
            }),
        'send_rate': _gauge(
            'send_rate', totals['sent_n'], totals['eligible_n'],
            target=randomization_p()),
        'randomization_audit': {
            'name': 'randomization_audit',
            'draws': len(draws),
            'mismatches': mismatches,
            'ks_statistic': statistic,
            'ks_p_value': p_value,
            'alarm_p': 0.01,
        },
        'cap_hit_rate': _gauge(
            'cap_hit_rate', totals['cap_hit_days'], totals['participant_days'],
            extra={'alarm_high': 0.10}),
        'cooldown_violations': {
            'name': 'cooldown_violations',
            'violations': totals['cooldown_violations_n'],
            'cooldown_minutes': JITAI_COOLDOWN_MINUTES,
        },
        'outcome_capture': _gauge(
            'outcome_capture', totals['outcome_captured_n'], totals['delivered_n'],
            extra={'alarm_low': 0.60}),
    }


def _participant_mean(rows, field):
    """Mean of a per-participant rate, ignoring participants with no denominator.

    Different from the pooled figure and deliberately shown beside it: pooling
    weights each participant by how many days they contributed, so someone who
    lasted two days counts a fifteenth of someone who lasted thirty. The
    benchmark is stated about participants, so both readings belong on the tile.
    """
    values = [getattr(row, field) for row in rows if getattr(row, field) is not None]
    return (sum(values) / len(values)) if values else None


def _active_retention(users, now):
    """Participants with a scheduled check-in in the trailing 7 days, over those
    currently inside the study window.

    The formal retention number cannot move until Day 35, because it asks only
    whether someone withdrew. This one moves the week someone goes quiet, which
    is the point of putting it on the same tile.
    """
    today = today_local(now)
    in_window = [
        row for row in MetricsParticipant.objects.filter(user__in=users)
        if row.phase in IN_STUDY_PHASES
    ]
    if not in_window:
        return _benchmark('retention', 0, 0, 0, 'participants active in the last 7 days')

    active = set(
        MetricsDaily.objects
        .filter(
            user__in=[row.user_id for row in in_window],
            is_active_day=True,
            local_date__gt=today - timedelta(days=ACTIVE_RETENTION_DAYS),
            local_date__lte=today,
            ema_scheduled_n__gt=0,
        )
        .values_list('user_id', flat=True)
    )
    return _benchmark(
        'retention', len(active), len(in_window), len(in_window),
        'participants active in the last 7 days',
    )


def _funnel(rows):
    """Enrollment funnel from the phases already computed.

    'Consented' is not here because nothing records it: User carries enrolled_at
    and is_enrolled and nothing before them. Reporting enrollment as consent
    would invent a number, so the stage is marked as having no source instead.
    """
    return [
        {'stage': 'consented', 'n': None, 'measurable': False,
         'detail': 'No consent field exists in the schema.'},
        {'stage': 'started day 1', 'n': len(rows), 'measurable': True},
        {'stage': 'active today', 'measurable': True,
         'n': sum(1 for row in rows if row.phase in IN_STUDY_PHASES)},
        {'stage': 'completed day 34', 'measurable': True,
         'n': sum(1 for row in rows if row.phase == 'complete')},
        {'stage': 'withdrew', 'measurable': True,
         'n': sum(1 for row in rows if row.phase == 'withdrawn')},
    ]


def _platform_series(users, now):
    """Delivery counts per local day per platform, for the tile 2 sparkline.

    iOS and Android push behaviour diverge, and a platform-specific collapse is
    invisible in the pooled number. Straight from JITAILog because MetricsDaily
    has no platform dimension; one grouped query over one cohort.
    """
    end = today_local(now)
    start, _ = participant_day_bounds(end - timedelta(days=SERIES_DAYS - 1))
    rows = (
        JITAILog.objects
        .filter(user__in=users, send_prompt=True, push_sent_at__gte=start)
        .values('receipt_platform')
        .annotate(
            sent=Count('id'),
            received=Count('id', filter=Q(device_received_at__isnull=False)),
        )
        .order_by('receipt_platform')
    )
    return [
        {'platform': row['receipt_platform'] or 'unreported',
         'sent': row['sent'], 'received': row['received']}
        for row in rows
    ]


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
        entry = {key: value for key, value in row.items() if key != 'local_date'}
        entry['date'] = row['local_date'].isoformat()
        # Every rate in the series goes through the same suppression as the
        # headline figure. A sparkline is a sequence of rates, so publishing one
        # from five participants while withholding the tile above it would let a
        # trend in through the back door.
        participants = row['participants']
        entry['slot_coverage'] = suppress_rate(
            row['slots_covered'] or 0, row['slots_expected'] or 0, participants)
        entry['prompt_response'] = suppress_rate(
            row['outcome_captured_n'] or 0, row['delivered_n'] or 0, participants)
        entry['wear_pass_rate'] = suppress_rate(
            row['wear_days_met'] or 0, row['wear_days_scored'] or 0, participants)
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
    # only a withdrawal is (analytics/analysis-resources/JITAI-analysis-plan.md).
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
                # A proxy for the 75% check-in benchmark, not the benchmark
                # itself: the tile says so on its face rather than in a footnote.
                extra={
                    'proxy': True,
                    'participant_mean': _participant_mean(rows, 'slot_coverage_rate'),
                },
            ),
            'prompt_response': _benchmark(
                'prompt_response', prompt_k, prompt_n, prompt_contributors,
                'prompts sent, less documented delivery failures',
                extra={
                    'participant_mean': _participant_mean(rows, 'prompt_response_rate'),
                    'by_platform': _platform_series(users, now),
                },
            ),
            'wear': _wear_benchmark(users),
            'retention': _benchmark(
                'retention', retained, len(began), len(began), 'participants',
                extra={'active': _active_retention(users, now)},
            ),
        },
        'funnel': _funnel(rows),
        'integrity': _mrt_integrity(users, now),
        'series_14d': _series(users, now),
        **{key: value or 0 for key, value in integrity.items()},
    }
