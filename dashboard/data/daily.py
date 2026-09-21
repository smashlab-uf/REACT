"""Per-participant, per-study-day metrics, computed straight from the ORM.

Mirrors the definitions in analytics/scripts.py rather than importing them: that
module pulls scipy, statsmodels, matplotlib and seaborn, none of which are in
backend/requirements.txt, so the Celery worker cannot have it. A reconciliation
test holds the two in step.

Two conventions run through every function here. All day and hour boundaries are
Eastern, built by dashboard.data.windows. And structural null is never conflated
with zero: a metric returns None when the thing it measures has no source, and 0
when the source exists and recorded nothing.
"""

import math
from datetime import timedelta
from statistics import median

from app.models import (
    CheckinReminder,
    EMA,
    EngagementLog,
    HeartRateSample,
    JITAILog,
    PhoneTelemetry,
)
from app.notification_service import _extract_value
from django.utils import timezone as django_timezone

from dashboard.data.config import (
    ITEM_BANK_VERSION,
    JITAI_COOLDOWN_MINUTES,
    OUTCOME_WINDOW_HOURS,
    SCHEDULED_CHECK_IN_DAILY_CAP,
    WEAR_GAP_MIN,
)
from dashboard.data.item_bank import load_item_bank
from dashboard.data.windows import (
    elapsed_minutes,
    in_study_range,
    is_run_in,
    participant_day_bounds,
    participant_time,
    scheduled_slot_bounds,
    slot_index_for,
    study_day_for,
    waking_window_bounds,
    waking_window_minutes,
)

SCHEDULED_TYPE = 'scheduled_check_in'
OUTCOME_TYPES = ('post_prompt', 'extra_check_in')
FEEDBACK_TYPE = 'prompt_feedback'
# The three sub-items _evaluate_user requires before an EMA can feed MSSD
# (app/tasks.py SIGNAL_SUB_ITEMS). An EMA missing any of them is skipped by the
# decision engine, which suppresses the successive difference on both sides.
SIGNAL_SUB_ITEM_IDS = ('B1_valence', 'B1_arousal', 'B2_stress')
# C0_behavior_change answers that count as the participant acting on a prompt.
# The other two are 'Nothing different' and 'It did not fit the moment'.
ACTED_CHOICES = ('I paused or waited', 'I changed what I was going to do')


def _p95(values):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)]


def _satisfies(condition, value):
    """Evaluate one EMA_ITEM_BANK depends_on gate against the parent's answer.

    An unanswered parent means the child was never shown, so both forms return
    False for a missing value: "not available" is not "below threshold".
    """
    if 'equals' in condition:
        return value == condition['equals']
    if 'not_equals' in condition:
        return value is not None and value != condition['not_equals']
    return True


def _candidate_sub_items(ema, responses, bank):
    """The sub-items that reached the screen, before depends_on gating.

    EMA.served_sub_item_ids records this exactly, and is the source whenever it
    is present. Rows written before that field existed fall back to inferring
    the set from the items the participant answered something in, which cannot
    see an item they skipped entirely and cannot tell whether a
    schedule_condition held at send time. Under the fallback, completeness is
    within-item rather than whole-check-in, and only B6_plan carries a
    schedule_condition.
    """
    if ema.served_sub_item_ids:
        index = {sub['sub_item_id']: sub
                 for item in bank.values() for sub in item['sub_items']}
        return [(sub_id, index[sub_id]) for sub_id in ema.served_sub_item_ids if sub_id in index]

    candidates = []
    for item_id in {r.item_id for r in responses.values()}:
        item = bank.get(item_id)
        if item is None:
            continue
        for sub in item['sub_items']:
            if 'schedule_condition' in sub and sub['sub_item_id'] not in responses:
                continue
            candidates.append((sub['sub_item_id'], sub))
    return candidates


def _askable_sub_item_ids(ema, responses, bank):
    """Which sub-items this check-in could have been answered on.

    Served ids still need depends_on gating applied: EMANextView sends every
    dependent sub-item and the mobile client hides the ones whose parent answer
    does not open them, so a served id is not automatically an asked question.

    views._filter_conditional_sub_items is deliberately not reused: it needs a
    satisfied-conditions set computed from *now*, which cannot be reconstructed
    for a past check-in, and it does not handle depends_on at all.
    """
    values = {sub_id: _extract_value(r) for sub_id, r in responses.items()}
    askable = []
    for sub_id, sub in _candidate_sub_items(ema, responses, bank):
        depends_on = sub.get('depends_on')
        if depends_on and not _satisfies(depends_on, values.get(depends_on['sub_item_id'])):
            continue
        askable.append(sub_id)
    return askable


def _ema_metrics(user, local_date, day_start, day_end, slots):
    emas = list(
        EMA.objects
        .filter(user=user, sent_at__gte=day_start, sent_at__lt=day_end)
        .prefetch_related('item_responses')
        .order_by('sent_at')
    )
    completed = [e for e in emas if e.status == 'completed']
    scheduled = [e for e in completed if e.ema_type == SCHEDULED_TYPE]
    outcome = [e for e in completed if e.ema_type in OUTCOME_TYPES]
    feedback = [e for e in completed if e.ema_type == FEEDBACK_TYPE]

    covered = {}
    for ema in scheduled:
        index = slot_index_for(ema.sent_at, slots=slots)
        if index is not None:
            covered.setdefault(index, ema.sent_at)

    reminders = list(
        CheckinReminder.objects
        .filter(user=user, sent_at__gte=day_start, sent_at__lt=day_end)
        .values_list('daily_count_at_send', 'sent_at')
    )
    reminded = {}
    for index, sent_at in reminders:
        if 0 <= index < SCHEDULED_CHECK_IN_DAILY_CAP:
            reminded.setdefault(index, sent_at)

    reminded_uncovered = set(reminded) - set(covered)
    # At most one reminder exists per slot, so this can only ever be 0 or 1.
    # data-dictionary.md describes a count that can run high, which the
    # one-reminder-per-slot rule makes impossible.
    nudged = [
        1 if index in reminded and reminded[index] <= ema_sent_at else 0
        for index, ema_sent_at in covered.items()
    ]

    bank = load_item_bank()
    scored = scheduled + outcome
    completeness = []
    missing_signal = 0
    for ema in scored:
        responses = {r.sub_item_id: r for r in ema.item_responses.all()}
        if not all(sub_id in responses for sub_id in SIGNAL_SUB_ITEM_IDS):
            missing_signal += 1
        askable = _askable_sub_item_ids(ema, responses, bank)
        if askable:
            answered = sum(1 for sub_id in askable if sub_id in responses)
            completeness.append(answered / len(askable))

    return {
        'ema_scheduled_n': len(scheduled),
        'ema_post_prompt_n': len(outcome),
        'ema_jitai_n': len(feedback),
        'slots_expected': SCHEDULED_CHECK_IN_DAILY_CAP,
        'slots_covered': len(covered),
        'slots_reminded_uncovered': len(reminded_uncovered),
        'slots_silent': SCHEDULED_CHECK_IN_DAILY_CAP - len(covered) - len(reminded_uncovered),
        'reminders_sent': len(reminders),
        'reminders_per_checkin_median': median(nudged) if nudged else None,
        'completeness_mean': sum(completeness) / len(completeness) if completeness else None,
        'ema_missing_b1b2_n': missing_signal,
    }


def _mrt_metrics(user, day_start, day_end, run_in_day):
    logs = list(
        JITAILog.objects
        .filter(user=user, decision_made_at__gte=day_start, decision_made_at__lt=day_end)
        .order_by('decision_made_at')
    )
    sent = [log for log in logs if log.send_prompt]
    # A sent prompt whose push never left has no push_sent_at; its decision time
    # is still the moment the cooldown should be measured from.
    sent_times = sorted(log.push_sent_at or log.decision_made_at for log in sent)
    gaps = [
        elapsed_minutes(earlier, later)
        for earlier, later in zip(sent_times[:-1], sent_times[1:])
    ]

    metrics = {
        'decision_points_n': len(logs),
        # The randomization draw is only taken once eligibility has passed, so a
        # non-null draw is the eligibility marker (app/tasks.py).
        'eligible_n': sum(1 for log in logs if log.randomization_draw is not None),
        'sent_n': len(sent),
        'delivered_n': sum(1 for log in logs if log.device_received_at is not None),
        'cap_hit': any(log.trigger_reason == 'daily cap reached' for log in logs),
        'min_gap_min': round(min(gaps)) if gaps else None,
        'cooldown_violations_n': sum(1 for gap in gaps if gap < JITAI_COOLDOWN_MINUTES),
        # _evaluate_user gates run-in days, so this should be zero for any row
        # written after the gate; a non-zero count is a regression.
        'runin_violation_n': len(sent) if run_in_day else 0,
        'delivery_failures_n': sum(1 for log in logs if log.delivery_status == 'failed'),
    }
    return metrics, sent


def _engagement_metrics(user, sent_logs):
    sent_ids = [log.id for log in sent_logs]
    if not sent_ids:
        return {
            'prompt_opened_n': 0, 'prompt_acted_n': 0,
            'prompt_dismissed_n': 0, 'outcome_captured_n': 0,
        }

    # ENGAGEMENT_EVENT_TYPES has no prompt_opened/prompt_acted/prompt_dismissed
    # despite data-dictionary.md 2.9 naming them; these are the real events.
    events = list(
        EngagementLog.objects
        .filter(user=user, jitai_log_id__in=sent_ids)
        .values_list('event_type', flat=True)
    )

    linked = list(
        EMA.objects
        .filter(source_jitai_log_id__in=sent_ids, status='completed')
        .prefetch_related('item_responses')
    )

    acted = 0
    for ema in linked:
        if ema.ema_type != FEEDBACK_TYPE:
            continue
        for response in ema.item_responses.all():
            if response.sub_item_id == 'C0_behavior_change' and response.value_choice in ACTED_CHOICES:
                acted += 1

    outcome_window = timedelta(hours=OUTCOME_WINDOW_HOURS)
    by_log = {}
    for ema in linked:
        if ema.ema_type in OUTCOME_TYPES:
            by_log.setdefault(ema.source_jitai_log_id, []).append(ema.sent_at)
    captured = sum(
        1 for log in sent_logs
        if log.push_sent_at is not None and any(
            log.push_sent_at <= sent_at <= log.push_sent_at + outcome_window
            for sent_at in by_log.get(log.id, [])
        )
    )

    return {
        'prompt_opened_n': events.count('notification_tapped'),
        'prompt_acted_n': acted,
        'prompt_dismissed_n': events.count('notification_dismissed'),
        'outcome_captured_n': captured,
    }


def _wear_metrics(user, local_date, device):
    window_start, window_end = waking_window_bounds(local_date)
    samples = list(
        HeartRateSample.objects
        .filter(user=user, timestamp__gte=window_start, timestamp__lt=window_end, bpm__gt=0)
        .order_by('timestamp')
        .values_list('timestamp', flat=True)
    )

    # Garmin exports carry no is_worn flag, so non-wear is inferred from missing
    # timestamps. That makes "device never delivered data" and "watch was off
    # all day" indistinguishable from the rows alone. A participant whose device
    # has never synced is reported as no data rather than as 0% wear.
    if not samples and (device is None or device.last_synced_at is None):
        return {
            'wear_valid_pct': None, 'wear_gap_pct': None,
            'gaps_gt2h_n': None, 'max_gap_min': None, 'hr_minutes_valid': None,
        }

    # Window edges are included so a morning-only wear day is not mistaken for
    # full coverage, matching identify_wear_gaps in analytics/scripts.py.
    edges = [window_start] + samples + [window_end]
    gaps = [
        elapsed_minutes(earlier, later)
        for earlier, later in zip(edges[:-1], edges[1:])
    ]
    long_gaps = [gap for gap in gaps if gap > WEAR_GAP_MIN]

    total = waking_window_minutes(local_date)
    gap_minutes = sum(long_gaps)
    wear_minutes = max(0.0, total - gap_minutes)
    minutes_seen = {
        participant_time(ts).replace(second=0, microsecond=0) for ts in samples
    }

    return {
        'wear_valid_pct': wear_minutes / total,
        'wear_gap_pct': min(1.0, gap_minutes / total),
        'gaps_gt2h_n': len(long_gaps),
        'max_gap_min': round(max(gaps)) if gaps else None,
        'hr_minutes_valid': len(minutes_seen),
    }


def _pipeline_metrics(user, day_start, day_end, device, now):
    last_sync_age = None
    if device is not None and device.last_synced_at is not None:
        # For a day still in progress, measure to now rather than to a midnight
        # that has not happened yet, which would inflate the age.
        reference = min(day_end, now)
        last_sync_age = elapsed_minutes(device.last_synced_at, reference) / 60

    skews = [
        elapsed_minutes(occurred_at, recorded_at) * 60_000
        for occurred_at, recorded_at in (
            list(EngagementLog.objects
                 .filter(user=user, occurred_at__gte=day_start, occurred_at__lt=day_end)
                 .values_list('occurred_at', 'recorded_at'))
            + list(PhoneTelemetry.objects
                   .filter(user=user, occurred_at__gte=day_start, occurred_at__lt=day_end)
                   .values_list('occurred_at', 'recorded_at'))
        )
    ]
    p95 = _p95(skews)

    return {
        'last_sync_age_h_eod': last_sync_age,
        'clock_skew_p95_ms': round(p95) if p95 is not None else None,
    }


def _blank_metrics():
    return {field: None for field in METRIC_FIELDS}


def compute_daily(user, local_date, study_day=None, withdrawn_at=None, now=None):
    """Metrics for one participant-day, as a dict of MetricsDaily fields.

    `withdrawn_at` is the withdrawal proxy from MetricsParticipant. The day a
    participant withdraws still counts as active, so the coverage they did
    record that day is kept rather than discarded; only the days after it go
    blank.
    """
    now = now or django_timezone.now()
    if study_day is None:
        study_day = study_day_for(user, local_date)

    day_start, day_end = participant_day_bounds(local_date)
    run_in_day = is_run_in(study_day)
    active = in_study_range(study_day) and (withdrawn_at is None or withdrawn_at >= day_start)

    payload = {
        'study_day': study_day,
        'local_date': local_date,
        'is_run_in': run_in_day,
        'is_active_day': active,
        'item_bank_version': ITEM_BANK_VERSION,
    }

    if not active:
        payload.update(_blank_metrics())
        return payload

    device = getattr(user, 'wearabledevice', None)
    slots = scheduled_slot_bounds(local_date)
    mrt, sent_logs = _mrt_metrics(user, day_start, day_end, run_in_day)

    payload.update(_ema_metrics(user, local_date, day_start, day_end, slots))
    payload.update(mrt)
    payload.update(_engagement_metrics(user, sent_logs))
    payload.update(_wear_metrics(user, local_date, device))
    payload.update(_pipeline_metrics(user, day_start, day_end, device, now))
    return payload


METRIC_FIELDS = (
    'ema_scheduled_n', 'ema_jitai_n', 'ema_post_prompt_n',
    'slots_expected', 'slots_covered', 'slots_reminded_uncovered', 'slots_silent',
    'reminders_sent', 'reminders_per_checkin_median',
    'completeness_mean', 'ema_missing_b1b2_n',
    'decision_points_n', 'eligible_n', 'sent_n', 'delivered_n', 'cap_hit',
    'min_gap_min', 'cooldown_violations_n', 'runin_violation_n',
    'prompt_opened_n', 'prompt_acted_n', 'prompt_dismissed_n', 'outcome_captured_n',
    'wear_valid_pct', 'wear_gap_pct', 'gaps_gt2h_n', 'max_gap_min', 'hr_minutes_valid',
    'last_sync_age_h_eod', 'clock_skew_p95_ms', 'delivery_failures_n',
)
