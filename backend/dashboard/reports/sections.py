"""One function per section of the PI feasibility and compliance report.

Every function reads study accounts only (RS prefix, dashboard.data.accounts) over
a closed range of Eastern local dates, and returns plain dicts and lists so the
same output can be rendered as HTML or asserted on in a test.

Two sources, chosen per section. Benchmarks and check-in slots come from
MetricsDaily, because wear and slot coverage are defined once in daily.py and
recomputing them here would let the two drift. Decision points, randomization,
outcome windows, cooldown, distress and data quality read the raw tables, because
those checks exist to catch the derived layer being wrong.

Status vocabulary: PASS, FAIL and WATCH for a check with a verdict, SUPPRESSED for
a rate below the publication threshold, NO DATA when the period had nothing to
check, and NOT MEASURED when the schema has no source at all.
"""

from collections import Counter, defaultdict
from datetime import timedelta
from statistics import StatisticsError, correlation, mean, median

from django.db.models import Count, F, Q

from app.distress import BASELINE_SIGNALS, MOMENTARY_SIGNALS
from app.models import (
    EMA, CheckinReminder, DistressFlag, EMAItemResponse, JITAILog, User, WearableDevice,
)
from dashboard.data import accounts
from dashboard.data import compose as compose_data
from dashboard.data import labfront_export
from dashboard.data.cohort import INELIGIBLE_REASONS, ks_uniform, suppress_rate, wilson_interval
from dashboard.data.config import (
    AVAILABILITY_ALARM_HIGH,
    AVAILABILITY_ALARM_LOW,
    AVAILABILITY_PLANNED,
    BENCHMARKS,
    DAILY_PROMPT_CAP,
    GAME_DAY_COMPARISON_START,
    GAME_DAYS,
    JITAI_COOLDOWN_MINUTES,
    N_POWER_TARGET,
    OUTCOME_CHECK_IN_DELAY_MINUTES,
    OUTCOME_WINDOW_HOURS,
)
from dashboard.data.daily import FEEDBACK_TYPE, OUTCOME_TYPES, SCHEDULED_TYPE
from dashboard.data.windows import (
    elapsed_minutes,
    is_run_in,
    participant_day_bounds,
    participant_time,
    study_day_for,
    waking_window_minutes,
)
from dashboard.models import Alert, MetricsDaily, MetricsParticipant

PASS, FAIL, WATCH = 'PASS', 'FAIL', 'WATCH'
SUPPRESSED, NO_DATA, NOT_MEASURED = 'SUPPRESSED', 'NO DATA', 'NOT MEASURED'

IN_STUDY_PHASES = ('run_in', 'mrt')
KS_MIN_DRAWS = 20
KS_ALARM_P = 0.01
WINDOW_TIMING_TOLERANCE_MIN = 1
TIMESTAMP_TOLERANCE = timedelta(seconds=1)
LABFRONT_PLACEHOLDER_PREFIX = 'TEST'
HELPFULNESS_ITEM = 'C0_helpful'
SEEN_RECEIPT, SEEN_RATING_ONLY, SEEN_PENDING, SEEN_NONE = 'receipt', 'rating only', 'pending', 'no evidence'

DISTRESS_GROUPS = (
    ('Self-harm item', ('phq9_self_harm',)),
    ('High-risk alcohol', ('audit_c',)),
    ('Gambling cutoff', ('problem_gambling',)),
    ('Eating disorder screen', ('scoff',)),
)
SIGNAL_LABELS = dict(BASELINE_SIGNALS + MOMENTARY_SIGNALS)
SUPPRESSION_LABELS = dict(JITAILog.SUPPRESSION_CHOICES)
ROUTING_LABELS = {
    '': 'Matched category',
    'closest_fit': 'Alcohol severity override (P020)',
    'category_exhausted': 'Fallback: matched categories exhausted',
    'no_category_matched': 'Fallback: no category matched',
}
LAST_N_EXCLUSION = 5
RETENTION_NOTE = 'Definition pending; currently never-withdrew among participants with a computed rollup.'
AVAILABILITY_NOTE = (
    'As built, available means the MSSD gate passed and no cooldown, daily cap, run-in or '
    'distress safeguard applied. The protocol also lists sleep and stale sync (section 9.3), '
    'which the engine does not check.'
)


def period_bounds(start, end):
    return participant_day_bounds(start)[0], participant_day_bounds(end)[1]


def study_participants():
    return accounts.study_users().filter(enrolled_at__isnull=False)


def labels_for(users):
    return {user.pk: user.study_id for user in users}


def local_date_of(moment):
    return participant_time(moment).date()


def check(name, status, detail=''):
    return {'name': name, 'status': status, 'detail': detail}


def _rate(k, n):
    return k / n if n else None


def _verdict(low, high, target):
    if low is None or target is None:
        return WATCH
    if low >= target:
        return PASS
    if high < target:
        return FAIL
    return WATCH


def rate_entry(name, label, k, n, n_participants, target=None, unit=''):
    value = suppress_rate(k, n, n_participants)
    low, high = wilson_interval(k, n) if value is not None else (None, None)
    if not n:
        status = NO_DATA
    elif value is None:
        status = SUPPRESSED
    else:
        status = _verdict(low, high, target)
    return {
        'name': name, 'label': label, 'unit': unit,
        'numerator': k, 'denominator': n, 'participants': n_participants,
        'value': value, 'wilson_low': low, 'wilson_high': high,
        'target': target, 'status': status,
    }


def _daily_rows(users, start, end):
    return MetricsDaily.objects.filter(
        user__in=users, local_date__gte=start, local_date__lte=end, is_active_day=True,
    )


def metrics_freshness(users):
    latest = MetricsDaily.objects.filter(user__in=users).order_by('-computed_at').first()
    return latest.computed_at if latest else None


# 1.1 -----------------------------------------------------------------------

def participant_status():
    counts = accounts.account_counts()
    study = accounts.study_users()
    phases = Counter(
        MetricsParticipant.objects.filter(user__in=study).values_list('phase', flat=True)
    )
    return {
        'total_accounts': User.objects.count(),
        'study': counts[accounts.STUDY],
        'test': counts[accounts.TEST],
        'unclassified': counts[accounts.UNCLASSIFIED],
        'active': sum(phases[phase] for phase in IN_STUDY_PHASES),
        'not_enrolled': study.filter(enrolled_at__isnull=True).count(),
        'phases': dict(phases),
    }


# 1.2 / 2.3 -----------------------------------------------------------------

def benchmarks(users, start, end):
    rows = list(_daily_rows(users, start, end))

    slot_rows = [row for row in rows if row.slots_expected]
    covered = sum(row.slots_covered or 0 for row in slot_rows)
    expected = sum(row.slots_expected for row in slot_rows)

    prompt_rows = [row for row in rows if row.sent_n]
    captured = sum(row.outcome_captured_n or 0 for row in prompt_rows)
    deliverable = sum(max(0, row.sent_n - (row.delivery_failures_n or 0)) for row in prompt_rows)

    wear_rows = [row for row in rows if row.wear_valid_pct is not None]
    wear_minutes = sum(row.wear_valid_pct * waking_window_minutes(row.local_date) for row in wear_rows)
    waking_minutes = sum(waking_window_minutes(row.local_date) for row in wear_rows)

    retention_rows = list(
        MetricsParticipant.objects
        .filter(user__in=users, active_retention__isnull=False)
        .values_list('active_retention', flat=True)
    )

    def people(subset):
        return len({row.user_id for row in subset})

    return [
        rate_entry('slot_coverage', 'Check-in completion', covered, expected,
                   people(slot_rows), BENCHMARKS['slot_coverage'], 'check-in slots'),
        {**rate_entry('retention', 'Retention', sum(retention_rows), len(retention_rows),
                      len(retention_rows), BENCHMARKS['retention'], 'participants'),
         'provisional': True, 'note': RETENTION_NOTE},
        rate_entry('prompt_response', 'Prompt response', captured, deliverable,
                   people(prompt_rows), BENCHMARKS['prompt_response'], 'prompts'),
        rate_entry('wear', 'Device wear', round(wear_minutes), round(waking_minutes),
                   people(wear_rows), BENCHMARKS['wear'], 'waking minutes'),
    ]


def hair_sample():
    return {
        'name': 'hair_sample', 'label': 'Hair sample success', 'target': 0.90,
        'status': NOT_MEASURED, 'detail': 'No hair-sample table exists; needs an RA sheet import.',
    }


def benchmark_trend(users, start, end):
    days = []
    day = start
    while day <= end:
        days.append({'date': day, 'benchmarks': {
            entry['name']: entry for entry in benchmarks(users, day, day)
        }})
        day += timedelta(days=1)
    return days


# 1.3 -----------------------------------------------------------------------

def checkins_by_participant(users, start, end):
    labels = labels_for(users)
    totals = defaultdict(lambda: {'expected': 0, 'covered': 0, 'reminded_uncovered': 0,
                                  'silent': 0, 'missing_signal': 0, 'days': 0})
    for row in _daily_rows(users, start, end):
        entry = totals[row.user_id]
        entry['days'] += 1
        entry['expected'] += row.slots_expected or 0
        entry['covered'] += row.slots_covered or 0
        entry['reminded_uncovered'] += row.slots_reminded_uncovered or 0
        entry['silent'] += row.slots_silent or 0
        entry['missing_signal'] += row.ema_missing_b1b2_n or 0

    participants = []
    for user_id, study_id in sorted(labels.items(), key=lambda item: item[1]):
        entry = totals.get(user_id)
        if entry is None:
            participants.append({'study_id': study_id, 'expected': None, 'covered': None,
                                 'rate': None, 'notes': ['No active study day in period']})
            continue
        notes = []
        if entry['silent']:
            notes.append(f"{entry['silent']} slot(s) with no reminder and no check-in")
        if entry['reminded_uncovered']:
            notes.append(f"{entry['reminded_uncovered']} reminded slot(s) not answered")
        if entry['missing_signal']:
            notes.append(f"{entry['missing_signal']} check-in(s) missing B1/B2")
        participants.append({
            'study_id': study_id, 'expected': entry['expected'], 'covered': entry['covered'],
            'rate': _rate(entry['covered'], entry['expected']), 'notes': notes,
        })

    expected = sum(p['expected'] or 0 for p in participants)
    covered = sum(p['covered'] or 0 for p in participants)
    return {
        'participants': participants,
        'expected': expected, 'covered': covered, 'rate': _rate(covered, expected),
    }


# Decision rows -------------------------------------------------------------

def decision_rows(users, start, end):
    period_start, period_end = period_bounds(start, end)
    by_user = {user.pk: user for user in users}
    rows = list(
        JITAILog.objects
        .filter(user__in=users, decision_made_at__gte=period_start, decision_made_at__lt=period_end)
        .order_by('decision_made_at', 'id')
    )
    for row in rows:
        day = study_day_for(by_user[row.user_id], local_date_of(row.decision_made_at))
        row.report_run_in = is_run_in(day)
    return rows


def _randomized(rows):
    return [row for row in rows if row.randomization_draw is not None]


ARM_SENT, ARM_RANDOMIZED_OUT = 'A', 'B'
ARM_LABELS = {ARM_SENT: 'Sent', ARM_RANDOMIZED_OUT: 'Randomized out'}


def arm_of(row):
    if row.randomization_draw is None or row.randomization_probability is None:
        return None
    if row.send_prompt:
        return ARM_SENT
    if row.randomization_draw >= row.randomization_probability:
        return ARM_RANDOMIZED_OUT
    return None


def outcome_window_rows(rows):
    return [
        row for row in _randomized(rows)
        if row.decision_point_id and row.decision_point_id.startswith('ema_')
    ]


# 1.4 / 2.4 -----------------------------------------------------------------

def decision_points(rows, n_days=1):
    mrt_rows = [row for row in rows if not row.report_run_in]
    randomized = _randomized(rows)
    suppressed = [row for row in rows if row.suppression_reason]
    passed_gate = [row for row in rows if row.randomization_draw is not None or row.suppression_reason]
    mrt_randomized = _randomized(mrt_rows)
    unavailable = Counter(
        SUPPRESSION_LABELS.get(row.suppression_reason, row.suppression_reason)
        if row.suppression_reason else row.trigger_reason
        for row in rows if row.randomization_draw is None
    )

    availability = _rate(len(mrt_randomized), len(mrt_rows))
    if availability is None:
        availability_status = NO_DATA
    elif AVAILABILITY_ALARM_LOW <= availability <= AVAILABILITY_ALARM_HIGH:
        availability_status = PASS
    else:
        availability_status = WATCH

    counts = {
        'evaluated': len(rows),
        'available': len(randomized),
        'passed_mssd_gate': len(passed_gate),
        'sent': sum(1 for row in rows if row.send_prompt),
        'not_sent': sum(1 for row in randomized if not row.send_prompt),
        'suppressed': len(suppressed),
    }
    return {
        **counts,
        'daily_average': {key: value / n_days for key, value in counts.items()} if n_days else None,
        'suppressed_by_reason': dict(Counter(row.suppression_reason for row in suppressed)),
        'mrt_evaluated': len(mrt_rows),
        'mrt_available': len(mrt_randomized),
        'unavailable_by_reason': dict(unavailable.most_common()),
        'availability_rate': availability,
        'availability_note': AVAILABILITY_NOTE,
        'availability_status': availability_status,
        'availability_planned': AVAILABILITY_PLANNED,
        'alarm_band': (AVAILABILITY_ALARM_LOW, AVAILABILITY_ALARM_HIGH),
    }


# 1.5 -----------------------------------------------------------------------

def randomization_integrity(rows, include_ks=False):
    randomized = _randomized(rows)

    threshold_disagreement = sum(
        1 for row in rows
        if (row.randomization_draw is not None and row.trigger_reason in INELIGIBLE_REASONS)
        or (row.randomization_draw is None and not row.suppression_reason
            and row.trigger_reason not in INELIGIBLE_REASONS)
    )

    send_mismatch = 0
    for row in randomized:
        if row.randomization_probability is None:
            continue
        should_send = row.randomization_draw < row.randomization_probability
        if row.send_prompt and not should_send:
            send_mismatch += 1
        elif should_send and not row.send_prompt and row.prompt_id:
            send_mismatch += 1

    probability_missing = sum(1 for row in randomized if row.randomization_probability is None)
    probability_on_suppressed = sum(
        1 for row in rows if row.suppression_reason and row.randomization_probability is not None)
    pool_missing = sum(1 for row in randomized if row.eligible_prompt_ids is None)
    sent = [row for row in rows if row.send_prompt]
    arm_missing = sum(
        1 for row in sent if not row.message_arm or row.arm_randomization_draw is None)
    log_incomplete = sum(
        1 for row in rows if not row.decision_point_id or not row.trigger_reason)

    checks = [
        check('Eligibility logic operating',
               _pass_if(threshold_disagreement == 0, rows),
               f'{threshold_disagreement} row(s) where the draw and trigger_reason disagree'),
        check('Randomization executed',
               _pass_if(send_mismatch == 0, randomized),
               f'{len(randomized)} draw(s), {send_mismatch} send decision(s) not matching draw < p'),
        check('Randomization probability logged',
               _pass_if(probability_missing == 0 and probability_on_suppressed == 0, rows),
               f'{probability_missing} draw(s) without p; '
               f'{probability_on_suppressed} suppressed row(s) carrying p'),
        check('Message pool recorded',
               _pass_if(pool_missing == 0 and arm_missing == 0, randomized),
               f'{pool_missing} randomized row(s) without a pool; '
               f'{arm_missing} sent prompt(s) without a message arm draw'),
        check('Decision logs complete',
               _pass_if(log_incomplete == 0, rows),
               f'{log_incomplete} row(s) missing decision_point_id or trigger_reason'),
    ]

    if include_ks:
        draws = [row.randomization_draw for row in randomized]
        statistic, p_value = ks_uniform(draws)
        if len(draws) < KS_MIN_DRAWS:
            status = NO_DATA
            ks_detail = f'{len(draws)} draw(s); needs {KS_MIN_DRAWS}'
        else:
            status = FAIL if p_value < KS_ALARM_P else PASS
            ks_detail = f'D = {statistic:.3f}, p = {p_value:.3f} over {len(draws)} draws'
        checks.append(check('Draws uniform on [0, 1] (KS)', status, ks_detail))

    return checks


def _pass_if(ok, population):
    if not population:
        return NO_DATA
    return PASS if ok else FAIL


# 1.5b Message eligibility ---------------------------------------------------

def _randomized_to_send(rows):
    return [
        row for row in _randomized(rows)
        if row.randomization_probability is not None
        and row.randomization_draw < row.randomization_probability
    ]


def _recent_sent_prompts(rows):
    history = defaultdict(list)
    for user_id, pk, made_at, prompt_id in (
        JITAILog.objects
        .filter(user_id__in={row.user_id for row in rows}, send_prompt=True)
        .exclude(prompt_id='')
        .order_by('decision_made_at', 'id')
        .values_list('user_id', 'id', 'decision_made_at', 'prompt_id')
    ):
        history[user_id].append((made_at, pk, prompt_id))

    recent = {}
    for row in rows:
        earlier = [
            prompt_id for made_at, pk, prompt_id in history[row.user_id]
            if (made_at, pk) < (row.decision_made_at, row.pk)
        ]
        recent[row.pk] = earlier[-LAST_N_EXCLUSION:]
    return recent


def message_eligibility(rows):
    to_send = _randomized_to_send(rows)
    sent = [row for row in to_send if row.send_prompt]
    coping = [row for row in sent if row.message_arm == 'coping']
    no_message = [row for row in to_send if not row.send_prompt and not row.prompt_id]

    outside_pool = [row for row in sent if row.prompt_id not in (row.eligible_prompt_ids or [])]
    recent = _recent_sent_prompts(coping)
    repeats = [row for row in coping if row.prompt_id in recent[row.pk]]
    pool_sizes = [len(row.eligible_prompt_ids or []) for row in sent]

    checks = [
        check('Prompt drawn from its eligible pool', _pass_if(not outside_pool, sent),
              f'{len(outside_pool)} of {len(sent)} sent prompt(s) not in the recorded pool'),
        check(f'Last-{LAST_N_EXCLUSION} exclusion honored (coping)', _pass_if(not repeats, coping),
              f'{len(repeats)} of {len(coping)} coping prompt(s) repeated one of the previous '
              f'{LAST_N_EXCLUSION} sent'),
        check('Message available when randomized to send', _watch_if(not no_message, to_send),
              f'{len(no_message)} of {len(to_send)} decision(s) randomized to send had an empty pool'),
    ]
    return {
        'checks': checks,
        'randomized_to_send': len(to_send),
        'sent': len(sent),
        'arms': dict(Counter(row.message_arm or 'none' for row in sent)),
        'routing': dict(Counter(
            ROUTING_LABELS.get(row.fallback_reason, row.fallback_reason) for row in coping)),
        'categories': dict(Counter(row.category_drawn for row in coping if row.category_drawn)),
        'pool_size_median': median(pool_sizes) if pool_sizes else None,
        'pool_size_min': min(pool_sizes) if pool_sizes else None,
    }


# 1.6 -----------------------------------------------------------------------

def outcome_windows(rows, now):
    delay = timedelta(minutes=OUTCOME_CHECK_IN_DELAY_MINUTES)
    length = timedelta(hours=OUTCOME_WINDOW_HOURS)
    windows = outcome_window_rows(rows)
    ids = [row.pk for row in windows]

    reminders = Counter(
        CheckinReminder.objects.filter(jitai_log_id__in=ids).values_list('jitai_log_id', flat=True)
    )
    outcome_emas = defaultdict(list)
    for ema in EMA.objects.filter(
        source_jitai_log_id__in=ids, ema_type__in=OUTCOME_TYPES, status='completed',
    ):
        outcome_emas[ema.source_jitai_log_id].append(ema)

    arms = {}
    timing_mismatch = early = extra_reminders = 0
    for arm in (ARM_SENT, ARM_RANDOMIZED_OUT):
        subset = [row for row in windows if arm_of(row) == arm]
        closed = [row for row in subset if row.decision_made_at + length <= now]
        stats = {'label': ARM_LABELS[arm], 'windows': len(subset),
                 'closed': len(closed), 'open': len(subset) - len(closed),
                 'reminded': 0, 'checked_in': 0, 'captured': 0, 'invalid_early': 0}
        for row in closed:
            opens_at = row.decision_made_at + delay
            closes_at = row.decision_made_at + length
            if reminders[row.pk]:
                stats['reminded'] += 1
            extra_reminders += max(0, reminders[row.pk] - 1)
            linked = outcome_emas.get(row.pk, [])
            if linked:
                stats['checked_in'] += 1
            answered = [ema.responded_at or ema.sent_at for ema in linked]
            if any(opens_at <= moment <= closes_at for moment in answered):
                stats['captured'] += 1
            if any(moment < opens_at for moment in answered):
                stats['invalid_early'] += 1
            for ema in linked:
                if ema.outcome_window_start is None or ema.outcome_window_end is None:
                    continue
                if (abs(elapsed_minutes(row.decision_made_at, ema.outcome_window_start)) > WINDOW_TIMING_TOLERANCE_MIN
                        or abs(elapsed_minutes(closes_at, ema.outcome_window_end)) > WINDOW_TIMING_TOLERANCE_MIN):
                    timing_mismatch += 1
        early += stats['invalid_early']
        stats['missing'] = stats['closed'] - stats['captured']
        stats['capture_rate'] = _rate(stats['captured'], stats['closed'])
        arms[arm] = stats

    closed_total = arms['A']['closed'] + arms['B']['closed']
    population = closed_total or None
    checks = [
        check('Outcome window opened',
               _watch_if(arms['A']['closed'] > 0 and arms['B']['closed'] > 0, population),
               f"{arms['A']['closed']} closed window(s) after a sent prompt, "
               f"{arms['B']['closed']} after a randomized-out decision"),
        check('First post-decision check-in occurred',
               _watch_if(arms['A']['checked_in'] + arms['B']['checked_in'] > 0, population),
               f"{arms['A']['checked_in'] + arms['B']['checked_in']} of {closed_total} window(s) "
               'had a linked outcome check-in'),
        check('Check-in within 2-hour window',
               _pass_if(early == 0 and timing_mismatch == 0, population),
               f'{early} answered before the +{OUTCOME_CHECK_IN_DELAY_MINUTES} min opening; '
               f'{timing_mismatch} with stored window bounds not equal to decision time and +{OUTCOME_WINDOW_HOURS} h'),
        check('Reminder delivered',
               _pass_if(extra_reminders == 0, population),
               f"{arms['A']['reminded'] + arms['B']['reminded']} window(s) reminded; "
               f'{extra_reminders} duplicate reminder(s)'),
        check('Outcome captured',
               _watch_if(arms['A']['captured'] + arms['B']['captured'] > 0, population),
               f"{arms['A']['captured'] + arms['B']['captured']} of {closed_total} closed window(s)"),
    ]
    return {
        'arms': arms, 'checks': checks,
        'assigned_no_message': sum(1 for row in windows if arm_of(row) is None),
    }


def _watch_if(ok, population):
    if not population:
        return NO_DATA
    return PASS if ok else WATCH


# 1.6b Prompt seen ----------------------------------------------------------

def _rating_times(sent):
    rated = {}
    responses = (
        EMAItemResponse.objects
        .filter(sub_item_id=HELPFULNESS_ITEM, ema__status='completed',
                ema__ema_type=FEEDBACK_TYPE, ema__source_jitai_log_id__in=[row.pk for row in sent])
        .values_list('ema__source_jitai_log_id', 'ema__responded_at', 'ema__sent_at')
    )
    for log_id, responded_at, sent_at in responses:
        moment = responded_at or sent_at
        if log_id not in rated or moment < rated[log_id]:
            rated[log_id] = moment
    return rated


def prompt_seen(rows, now, labels):
    sent = [row for row in rows if row.send_prompt]
    rated = _rating_times(sent)
    cutoff = timedelta(hours=OUTCOME_WINDOW_HOURS)

    prompts = []
    for row in sent:
        sent_at = row.push_sent_at or row.decision_made_at
        evidence = [moment for moment in (row.device_received_at, rated.get(row.pk)) if moment]
        seen_at = min(evidence) if evidence else None
        if row.device_received_at:
            status = SEEN_RECEIPT
        elif row.pk in rated:
            status = SEEN_RATING_ONLY
        elif now - sent_at < cutoff:
            status = SEEN_PENDING
        else:
            status = SEEN_NONE
        prompts.append({
            'study_id': labels.get(row.user_id), 'sent_at': sent_at, 'status': status,
            'seen_at': seen_at,
            'minutes_to_seen': elapsed_minutes(sent_at, seen_at) if seen_at else None,
            'hours_since_sent': elapsed_minutes(sent_at, now) / 60,
            'delivery_status': row.delivery_status,
        })

    counts = Counter(prompt['status'] for prompt in prompts)
    to_seen = [prompt['minutes_to_seen'] for prompt in prompts if prompt['minutes_to_seen'] is not None]
    unseen = [prompt for prompt in prompts if prompt['status'] == SEEN_NONE]
    rating_only = [prompt for prompt in prompts if prompt['status'] == SEEN_RATING_ONLY]
    checks = [
        check('Sent prompts seen', _watch_if(not unseen, prompts),
              f'{len(unseen)} of {len(prompts)} sent prompt(s) with no device receipt and no '
              f'helpfulness rating after {OUTCOME_WINDOW_HOURS} h'),
        check('Device receipts reported', _watch_if(not rating_only, prompts),
              f'{len(rating_only)} prompt(s) rated by the participant but never receipted by the device'),
    ]
    return {
        'sent': len(prompts),
        'counts': {status: counts.get(status, 0)
                   for status in (SEEN_RECEIPT, SEEN_RATING_ONLY, SEEN_PENDING, SEEN_NONE)},
        'median_minutes_to_seen': median(to_seen) if to_seen else None,
        'seen_n': len(to_seen),
        'flagged': sorted(unseen + rating_only, key=lambda prompt: prompt['sent_at']),
        'cutoff_hours': OUTCOME_WINDOW_HOURS,
        'checks': checks,
    }


# 1.7 -----------------------------------------------------------------------

def cooldown(rows, labels):
    sent_by_user = defaultdict(list)
    for row in rows:
        if row.send_prompt:
            sent_by_user[row.user_id].append(row.push_sent_at or row.decision_made_at)

    violations = []
    gaps = []
    cap_days = []
    for user_id, times in sent_by_user.items():
        times.sort()
        for earlier, later in zip(times[:-1], times[1:]):
            gap = elapsed_minutes(earlier, later)
            gaps.append(gap)
            if gap < JITAI_COOLDOWN_MINUTES:
                violations.append({'study_id': labels.get(user_id), 'at': later,
                                   'gap_min': round(gap, 1)})
        per_day = Counter(local_date_of(moment) for moment in times)
        cap_days.extend(
            {'study_id': labels.get(user_id), 'date': day, 'sent': n}
            for day, n in per_day.items() if n > DAILY_PROMPT_CAP
        )

    sent = sum(len(times) for times in sent_by_user.values())
    return {
        'sent': sent,
        'violations': violations,
        'closest_gap_min': round(min(gaps)) if gaps else None,
        'cooldown_minutes': JITAI_COOLDOWN_MINUTES,
        'status': (PASS if not violations else FAIL) if sent else NO_DATA,
        'cap_exceeded': cap_days,
        'cap_status': (PASS if not cap_days else FAIL) if sent else NO_DATA,
        'daily_cap': DAILY_PROMPT_CAP,
    }


# 1.8 -----------------------------------------------------------------------

def distress(users, rows, start, end, now):
    period_start, period_end = period_bounds(start, end)
    flags = list(DistressFlag.objects.filter(
        user__in=users, raised_at__gte=period_start, raised_at__lt=period_end))
    open_baseline = list(DistressFlag.objects.filter(
        user__in=users, source='baseline', contact_documented_at__isnull=True))
    suppressed = [row for row in rows if row.suppression_reason.startswith('distress_')]

    signal_counts = Counter(code for flag in flags for code in flag.signals)
    undocumented = {code for flag in open_baseline for code in flag.signals}
    labels = labels_for(users)
    momentary_review = [
        {'study_id': labels.get(flag.user_id), 'raised_at': participant_time(flag.raised_at),
         'signals': [SIGNAL_LABELS.get(code, code) for code in flag.signals]}
        for flag in sorted(flags, key=lambda flag: flag.raised_at) if flag.source == 'momentary'
    ]

    grouped = set()
    breakdown = []
    for label, codes in DISTRESS_GROUPS:
        grouped.update(codes)
        breakdown.append({
            'label': label, 'count': sum(signal_counts[code] for code in codes),
            'follow_up': any(code in undocumented for code in codes),
        })
    other = {code: n for code, n in signal_counts.items() if code not in grouped}
    breakdown.append({
        'label': 'Other distress signal', 'count': sum(other.values()),
        'follow_up': any(code in undocumented for code in undocumented - grouped),
        'codes': {SIGNAL_LABELS.get(code, code): n for code, n in sorted(other.items())},
    })

    return {
        'override_events': len(flags),
        'by_source': dict(Counter(flag.source for flag in flags)),
        'resource_cards': None,
        'resource_cards_status': NOT_MEASURED,
        'randomization_suppressed': len(suppressed),
        'suppressed_by_reason': dict(Counter(row.suppression_reason for row in suppressed)),
        'active_now': sum(1 for flag in DistressFlag.objects.filter(user__in=users) if flag.is_active(now)),
        'breakdown': breakdown,
        'follow_up_open': len(open_baseline),
        'momentary_review': momentary_review,
    }


# 1.9 -----------------------------------------------------------------------

def data_quality(users, rows, start, end):
    period_start, period_end = period_bounds(start, end)
    emas = EMA.objects.filter(user__in=users, sent_at__gte=period_start, sent_at__lt=period_end)

    out_of_order = sum(
        1 for row in rows
        if (row.push_sent_at and row.push_sent_at < row.decision_made_at - TIMESTAMP_TOLERANCE)
        or (row.device_received_at and row.push_sent_at
            and row.device_received_at < row.push_sent_at - TIMESTAMP_TOLERANCE)
    )
    ema_out_of_order = emas.filter(responded_at__lt=F('sent_at') - TIMESTAMP_TOLERANCE).count()
    skew = [
        value for value in _daily_rows(users, start, end).values_list('clock_skew_p95_ms', flat=True)
        if value is not None
    ]

    unlinked = emas.filter(
        ema_type__in=OUTCOME_TYPES + (FEEDBACK_TYPE,), source_jitai_log__isnull=True).count()

    duplicate_emas = (
        JITAILog.objects.filter(pk__in=[row.pk for row in rows], ema__isnull=False)
        .values('ema_id').annotate(n=Count('id')).filter(n__gt=1).count()
    )

    logged_emas = set(
        JITAILog.objects.filter(user__in=users, ema__isnull=False).values_list('ema_id', flat=True))
    by_user = {user.pk: user for user in users}
    missing_logs = 0
    for ema in emas.filter(ema_type=SCHEDULED_TYPE, status='completed'):
        day = study_day_for(by_user[ema.user_id], local_date_of(ema.sent_at))
        if ema.pk not in logged_emas and day is not None and day >= 0:
            missing_logs += 1

    unlabelled = accounts.unclassified_users().filter(enrolled_at__isnull=False)

    return [
        check('Timestamp consistency', PASS if not (out_of_order or ema_out_of_order) else WATCH,
               f'{out_of_order} decision(s) and {ema_out_of_order} check-in(s) with timestamps out of '
               'order' + (f'; worst daily p95 clock skew {max(skew, key=abs)} ms' if skew else '')),
        check('Check-in linked to decision point', PASS if not unlinked else WATCH,
               f'{unlinked} outcome or feedback check-in(s) with no source decision'),
        check('Duplicate records', PASS if not duplicate_emas else WATCH,
               f'{duplicate_emas} check-in(s) with more than one decision row'),
        check('Missing decision logs', PASS if not missing_logs else WATCH,
               f'{missing_logs} completed scheduled check-in(s) with no decision row'),
        check('Missing participant IDs', PASS if not unlabelled.exists() else WATCH,
               'Enrolled accounts without an RS/ST study_id: '
               + (', '.join(f'user {pk}' for pk in unlabelled.values_list('pk', flat=True)) or 'none')),
        labfront_crosswalk(users),
        push_tokens(users),
    ]


def push_tokens(users):
    missing = sorted(user.study_id for user in users if not (user.push_token or '').strip())
    return check('Push token on file', WATCH if missing else (PASS if users else NO_DATA),
                 ('No push token, so no reminders or prompts can reach: ' + ', '.join(missing))
                 if missing else f'{len(users)} participant(s) checked')


def _normalised(code):
    return (code or '').strip().upper()


def labfront_crosswalk(users):
    devices = {
        device.user_id: device.labfront_participant_id
        for device in WearableDevice.objects.filter(user__in=users)
    }
    problems = {'no device': [], 'placeholder ID': [], 'different ID': []}
    for user in sorted(users, key=lambda user: user.study_id or ''):
        labfront = _normalised(devices.get(user.pk))
        if user.pk not in devices or not labfront:
            problems['no device'].append(user.study_id)
        elif labfront.startswith(LABFRONT_PLACEHOLDER_PREFIX):
            problems['placeholder ID'].append(f'{user.study_id} ({devices[user.pk]})')
        elif labfront != _normalised(user.study_id):
            problems['different ID'].append(f'{user.study_id} ({devices[user.pk]})')
    found = {kind: ids for kind, ids in problems.items() if ids}
    detail = '; '.join(f"{kind}: {', '.join(ids)}" for kind, ids in found.items())
    return check('Labfront ID matches study ID', WATCH if found else (PASS if users else NO_DATA),
                 detail or f'{len(users)} participant(s) checked')


# 1.10 / 2.5 ----------------------------------------------------------------

def issues(users, check_groups, labels):
    open_alerts = Alert.objects.filter(resolved_at__isnull=True).filter(
        Q(user__isnull=True) | Q(user__in=users))
    entries = [
        {'description': alert.rule_id.replace('_', ' '),
         'scope': labels.get(alert.user_id, 'cohort') if alert.user_id else 'cohort',
         'severity': alert.severity, 'source': 'alert', 'rank': Alert.SEVERITY_RANK[alert.severity]}
        for alert in open_alerts
    ]
    for section, checks in check_groups:
        for check in checks:
            if check['status'] in (FAIL, WATCH):
                severity = 'critical' if check['status'] == FAIL else 'warning'
                entries.append({
                    'description': f"{check['name']}: {check['detail']}", 'scope': section,
                    'severity': severity, 'source': 'check', 'rank': Alert.SEVERITY_RANK[severity],
                })
    entries.sort(key=lambda entry: (entry['rank'], entry['scope'], entry['description']))
    return entries


# Compose telemetry (exploratory) -------------------------------------------

COMPOSE_NOTE = (
    'Exploratory, not a protocol endpoint. An open is the compose box gaining focus and is '
    'measured only from the app build that sends it. A draft starts on the first keystroke and '
    'is abandoned when neither submitted nor deleted within 24 hours. Keystroke and deletion '
    'counts are character-length changes, so a paste counts by its length.'
)


def compose(users, rows, start, end, now, with_ci=False):
    labels = labels_for(users)
    period_start, period_end = period_bounds(start, end)
    windows = [
        (row.pk, row.user_id, row.decision_made_at, arm_of(row))
        for row in outcome_window_rows(rows) if arm_of(row) is not None
    ]
    scored = compose_data.compose_after_decisions(users, windows, now)
    background = compose_data.background_blocks(users, windows, start, end, now)
    comparison = compose_data.compare_arms(scored, background, with_ci=with_ci)
    for metric in comparison['metrics']:
        metric['difference_by_participant'] = {
            labels.get(user_id): value
            for user_id, value in metric['difference_by_participant'].items()
        }
    return {
        'note': COMPOSE_NOTE,
        'participants': compose_data.compose_by_participant(users, period_start, period_end, now),
        'comparison': comparison,
        'excluded_no_message': sum(1 for row in outcome_window_rows(rows) if arm_of(row) is None),
    }


# 2.2 -----------------------------------------------------------------------

def participant_flow():
    status = participant_status()
    phases = status['phases']
    stages = [
        ('Screened', None), ('Eligible', None), ('Consented', None),
        ('Enrolled', status['study'] - status['not_enrolled']),
        ('Active', status['active']),
        ('Withdrawn', phases.get('withdrawn', 0)),
        ('Completed', phases.get('complete', 0)),
    ]
    targeted = {'Consented', 'Enrolled', 'Active', 'Completed'}
    return [
        {'stage': stage, 'n': n, 'measurable': n is not None,
         'target': N_POWER_TARGET if stage in targeted else None,
         'pct_of_target': (n / N_POWER_TARGET) if n is not None and stage in targeted else None}
        for stage, n in stages
    ]


# Scheduled check-in answers ---------------------------------------------------

def _scheduled_checkins(users, start, end):
    period_start, period_end = period_bounds(start, end)
    return list(
        EMA.objects
        .filter(user__in=users, ema_type=SCHEDULED_TYPE, status='completed',
                sent_at__gte=period_start, sent_at__lt=period_end)
        .order_by('sent_at', 'id')
    )


def _answers(emas, items):
    answers = defaultdict(dict)
    for ema_id, sub_item_id, value in (
        EMAItemResponse.objects
        .filter(ema__in=emas, sub_item_id__in=items, value_numeric__isnull=False)
        .values_list('ema_id', 'sub_item_id', 'value_numeric')
    ):
        answers[ema_id][sub_item_id] = value
    return answers


def _mean_or_none(values):
    return mean(values) if values else None


# 2.6 Game days vs non-game days ----------------------------------------------

GAME_DAY_ITEMS = (
    ('B1_valence', 'Valence (1–7, higher is more positive)'),
    ('B1_arousal', 'Arousal (1–7, higher is more excited)'),
    ('B2_stress', 'Stress (1–7)'),
    ('B1_affect_anxious', 'Anxious or on edge (1–5)'),
    ('B1_affect_angry', 'Angry or frustrated (1–5)'),
    ('B1_affect_sad', 'Sad or down (1–5)'),
    ('B1_affect_excited', 'Excited or pumped up (1–5)'),
    ('B1_affect_happy', 'Happy or content (1–5)'),
    ('B1_affect_bored', 'Bored (1–5)'),
)
GAME_DAY_PARTICIPANT_ITEMS = ('B1_valence', 'B2_stress')
GAME_DAY_NOTE = (
    'Cumulative from the Friday of homecoming weekend through the end of this report, not just this week, '
    'since a week holds at most one game. Game days are the football dates listed in config.GAME_DAYS. '
    'Answers come from completed scheduled check-ins only, so prompt-triggered outcome check-ins cannot tilt '
    'the split. Means are per participant first, then averaged. The difference is the mean of each '
    "participant's game-day minus non-game-day mean, over participants with check-ins on both. Descriptive: "
    'no confidence interval or p-value.'
)


def _completion(rows, name, label):
    slot_rows = [row for row in rows if row.slots_expected]
    return rate_entry(
        name, label,
        sum(row.slots_covered or 0 for row in slot_rows), sum(row.slots_expected for row in slot_rows),
        len({row.user_id for row in slot_rows}), BENCHMARKS['slot_coverage'], 'check-in slots',
    )


def game_days(users, end):
    start = GAME_DAY_COMPARISON_START
    base = {
        'start': start, 'end': end, 'note': GAME_DAY_NOTE,
        'game_dates': [], 'completion': [], 'items': [], 'participants': [],
    }
    if end < start:
        return {**base, 'status': NO_DATA,
                'detail': f'The comparison begins {start:%Y-%m-%d}, the Friday of homecoming weekend.'}

    game_dates = sorted(day for day in GAME_DAYS if start <= day <= end)
    if not game_dates:
        return {**base, 'status': NO_DATA,
                'detail': f'No game day between {start:%Y-%m-%d} and {end:%Y-%m-%d} in config.GAME_DAYS.'}
    is_game = set(game_dates).__contains__

    daily = list(_daily_rows(users, start, end))
    completion = [
        _completion([row for row in daily if is_game(row.local_date)], 'game', 'Game days'),
        _completion([row for row in daily if not is_game(row.local_date)], 'other', 'Non-game days'),
    ]

    emas = _scheduled_checkins(users, start, end)
    answers = _answers(emas, [item for item, _ in GAME_DAY_ITEMS])
    values = defaultdict(lambda: {True: [], False: []})
    checkins = defaultdict(lambda: {True: 0, False: 0})
    for ema in emas:
        game = is_game(local_date_of(ema.sent_at))
        checkins[ema.user_id][game] += 1
        for item, value in answers.get(ema.pk, {}).items():
            values[(ema.user_id, item)][game].append(value)

    labels = labels_for(users)
    ordered = sorted(labels.items(), key=lambda item: item[1] or '')
    items = []
    for item, label in GAME_DAY_ITEMS:
        game_means, other_means, differences = [], [], {}
        for user_id, study_id in ordered:
            game_mean = _mean_or_none(values[(user_id, item)][True])
            other_mean = _mean_or_none(values[(user_id, item)][False])
            if game_mean is not None:
                game_means.append(game_mean)
            if other_mean is not None:
                other_means.append(other_mean)
            differences[study_id] = (
                game_mean - other_mean if game_mean is not None and other_mean is not None else None)
        paired = [value for value in differences.values() if value is not None]
        items.append({
            'item': item, 'label': label,
            'game_n': sum(len(values[(user_id, item)][True]) for user_id, _ in ordered),
            'other_n': sum(len(values[(user_id, item)][False]) for user_id, _ in ordered),
            'game_mean': _mean_or_none(game_means), 'other_mean': _mean_or_none(other_means),
            'mean_difference': _mean_or_none(paired), 'paired_participants': len(paired),
            'difference_by_participant': differences,
        })

    by_item = {entry['item']: entry for entry in items}
    participants = [
        {'study_id': study_id,
         'game_checkins': checkins[user_id][True], 'other_checkins': checkins[user_id][False],
         'differences': {item: by_item[item]['difference_by_participant'][study_id]
                         for item in GAME_DAY_PARTICIPANT_ITEMS}}
        for user_id, study_id in ordered
    ]
    return {
        **base, 'status': None, 'detail': '',
        'game_dates': game_dates, 'completion': completion, 'items': items, 'participants': participants,
    }


# 2.7 Garmin signals at check-in ----------------------------------------------

GARMIN_ALIGN_WINDOW_MIN = 30
GARMIN_MIN_PAIRS = 10
GARMIN_ALIGNED, GARMIN_NO_SIGNAL = 'aligned', 'no signal in window'
GARMIN_OUTSIDE, GARMIN_NO_FOLDER = 'outside export', 'no export folder'
GARMIN_STATUSES = (GARMIN_ALIGNED, GARMIN_NO_SIGNAL, GARMIN_OUTSIDE, GARMIN_NO_FOLDER)
GARMIN_NOTE = (
    'Read from a Labfront export file, not the live database: wearable ingest is not running. Heart rate is '
    "Garmin Connect's 15-second series and stress its 3-minute score (0–100); stress codes -1 and -2 (too little "
    'data, too much motion) are dropped. Each completed scheduled check-in gets the mean of the samples in the '
    f'{GARMIN_ALIGN_WINDOW_MIN} minutes before it was answered. A check-in whose window falls outside the export '
    'is not counted as missing. The correlation pairs self-reported stress with Garmin stress within one '
    f'participant, needs {GARMIN_MIN_PAIRS} aligned check-ins, and is exploratory.'
)
GARMIN_NOT_SUPPLIED = 'No Labfront export supplied (build_report --labfront-export DIR); wearable ingest is not live.'


def _ranks(values):
    order = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def spearman(xs, ys):
    if len(xs) < 2:
        return None
    try:
        return correlation(_ranks(xs), _ranks(ys))
    except StatisticsError:
        return None


def _align(ema, window, folder, coverage, heart_rate, stress):
    moment = ema.responded_at or ema.sent_at
    row = {'at': moment, 'hr_mean': None, 'hr_n': 0, 'stress_mean': None, 'stress_n': 0}
    if folder is None:
        return {**row, 'status': GARMIN_NO_FOLDER}
    if coverage is None or moment < coverage[0] or moment - window > coverage[1]:
        return {**row, 'status': GARMIN_OUTSIDE}
    hr_values = labfront_export.values_between(heart_rate, moment - window, moment)
    stress_values = labfront_export.values_between(stress, moment - window, moment)
    return {
        **row,
        'hr_mean': _mean_or_none(hr_values), 'hr_n': len(hr_values),
        'stress_mean': _mean_or_none(stress_values), 'stress_n': len(stress_values),
        'status': GARMIN_ALIGNED if hr_values or stress_values else GARMIN_NO_SIGNAL,
    }


def garmin_at_checkins(users, start, end, export_dir):
    base = {'note': GARMIN_NOTE, 'window_minutes': GARMIN_ALIGN_WINDOW_MIN, 'min_pairs': GARMIN_MIN_PAIRS,
            'statuses': GARMIN_STATUSES, 'participants': [], 'checkins': [], 'counts': {},
            'coverage_end': None, 'unmatched_folders': []}
    if export_dir is None:
        return {**base, 'status': NOT_MEASURED, 'detail': GARMIN_NOT_SUPPLIED}

    folders = labfront_export.participant_folders(export_dir)
    labels = labels_for(users)
    emas = _scheduled_checkins(users, start, end)
    answers = _answers(emas, ('B1_valence', 'B2_stress'))
    by_user = defaultdict(list)
    for ema in emas:
        by_user[ema.user_id].append(ema)

    window = timedelta(minutes=GARMIN_ALIGN_WINDOW_MIN)
    checkins, participants, coverage_ends = [], [], []
    for user_id, study_id in sorted(labels.items(), key=lambda item: item[1] or ''):
        folder = folders.get(_normalised(study_id))
        coverage = labfront_export.coverage(folder) if folder else None
        if coverage:
            coverage_ends.append(coverage[1])
        rows = []
        if by_user[user_id] and coverage:
            heart_rate = labfront_export.read_stream(folder, labfront_export.HEART_RATE, start, end)
            stress = labfront_export.read_stream(folder, labfront_export.STRESS, start, end)
        else:
            heart_rate = stress = ([], [])
        for ema in by_user[user_id]:
            row = _align(ema, window, folder, coverage, heart_rate, stress)
            row.update({'study_id': study_id,
                        'valence': answers.get(ema.pk, {}).get('B1_valence'),
                        'self_stress': answers.get(ema.pk, {}).get('B2_stress')})
            rows.append(row)
        checkins.extend(rows)

        aligned = [row for row in rows if row['status'] == GARMIN_ALIGNED]
        pairs = [(row['self_stress'], row['stress_mean']) for row in aligned
                 if row['self_stress'] is not None and row['stress_mean'] is not None]
        hr = [row['hr_mean'] for row in aligned if row['hr_mean'] is not None]
        garmin_stress = [row['stress_mean'] for row in aligned if row['stress_mean'] is not None]
        participants.append({
            'study_id': study_id, 'in_export': folder is not None, 'coverage': coverage,
            'checkins': len(rows),
            'counts': {status: sum(1 for row in rows if row['status'] == status) for status in GARMIN_STATUSES},
            'hr_median': median(hr) if hr else None,
            'stress_median': median(garmin_stress) if garmin_stress else None,
            'pairs': len(pairs),
            'spearman': spearman(*zip(*pairs)) if len(pairs) >= GARMIN_MIN_PAIRS else None,
        })

    matched = {_normalised(study_id) for study_id in labels.values()}
    return {
        **base,
        'status': None if checkins else NO_DATA,
        'detail': '' if checkins else 'No completed scheduled check-ins in this period.',
        'participants': participants,
        'checkins': sorted(checkins, key=lambda row: (row['at'], row['study_id'] or '')),
        'counts': {status: sum(1 for row in checkins if row['status'] == status) for status in GARMIN_STATUSES},
        'coverage_end': max(coverage_ends) if coverage_ends else None,
        'unmatched_folders': sorted(set(folders) - matched),
    }
