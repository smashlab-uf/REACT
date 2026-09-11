"""Raw-table assembly for the participant timeline, one day at a time.

This is the only part of the monitoring layer that reads raw tables, and it does
so for one participant over a handful of days. Everything else reads the
precomputed metric tables, so nothing here should ever be called in a loop over
the cohort.

Wear is returned as runs rather than 1440 per-minute booleans: seven days of
minute bins is ten thousand values per participant to draw a few dozen bars, and
the renderer wants spans anyway.
"""

from app.models import (
    EMA,
    EngagementLog,
    HeartRateSample,
    JITAILog,
    WearableSync,
)
from app.notification_service import _extract_value
from django.db.models import Count, Q

from dashboard.data.config import WEAR_GAP_MIN
from dashboard.data.daily import _askable_sub_item_ids
from dashboard.data.item_bank import load_item_bank
from dashboard.data.windows import (
    elapsed_minutes,
    participant_day_bounds,
    participant_time,
    waking_window_bounds,
)

MINUTES_PER_DAY = 1440


def _runs(minutes):
    """Sorted minute indices to [start, end) spans, end exclusive."""
    spans = []
    for minute in sorted(minutes):
        if spans and minute == spans[-1][1]:
            spans[-1][1] = minute + 1
        else:
            spans.append([minute, minute + 1])
    return [tuple(span) for span in spans]


def _complement(spans, total=MINUTES_PER_DAY):
    gaps = []
    cursor = 0
    for start, end in spans:
        if start > cursor:
            gaps.append((cursor, start))
        cursor = max(cursor, end)
    if cursor < total:
        gaps.append((cursor, total))
    return gaps


def wear_lane(user, local_date):
    """Covered spans and gaps across the local day, in minutes from midnight.

    A minute counts as covered when any sample with bpm > 0 falls in it, which is
    the same bpm > 0 convention the daily wear metric uses. The waking window is
    reported alongside so the renderer can shade it, and each gap carries how
    much of it lands inside that window: only that part belongs to the wear
    denominator, and a gap that straddles the edge must not be counted whole.
    """
    day_start, day_end = participant_day_bounds(local_date)
    window_start, window_end = waking_window_bounds(local_date)
    window = (
        int(elapsed_minutes(day_start, window_start)),
        int(elapsed_minutes(day_start, window_end)),
    )

    timestamps = (
        HeartRateSample.objects
        .filter(user=user, timestamp__gte=day_start, timestamp__lt=day_end, bpm__gt=0)
        .values_list('timestamp', flat=True)
    )
    minutes = {int(elapsed_minutes(day_start, stamp)) for stamp in timestamps}
    covered = _runs(minutes)

    gaps = []
    for start, end in _complement(covered):
        inside = max(0, min(end, window[1]) - max(start, window[0]))
        gaps.append({
            'start': start,
            'end': end,
            'minutes': end - start,
            'waking_minutes': inside,
            # Only a gap inside the waking window counts against wear at all, so
            # a long overnight gap is never flagged as a compliance problem.
            'over_threshold': inside >= WEAR_GAP_MIN,
        })

    return {
        'covered': [{'start': start, 'end': end} for start, end in covered],
        'gaps': gaps,
        'waking_window': {'start': window[0], 'end': window[1]},
        'covered_minutes': len(minutes),
        # None, not zero: no samples at all means the pipeline delivered nothing,
        # which is a different fact from a participant not wearing the watch.
        'has_data': bool(minutes),
    }


def sync_lane(user, local_date):
    """Sync advances during the day, plus the value carried in at midnight.

    Without the carried-in row the step trace has no starting height and a day
    with no advances would render as absent rather than as flat, which is the
    one thing this lane exists to show.
    """
    day_start, day_end = participant_day_bounds(local_date)
    fields = ('observed_at', 'source', 'last_synced_at', 'samples_written')

    carried = (
        WearableSync.objects
        .filter(user=user, observed_at__lt=day_start)
        .order_by('-observed_at')
        .values(*fields)
        .first()
    )
    advances = list(
        WearableSync.objects
        .filter(user=user, observed_at__gte=day_start, observed_at__lt=day_end)
        .order_by('observed_at')
        .values(*fields)
    )
    return {
        'carried_in': carried,
        'advances': advances,
        # Distinguishes "no sync history exists for this participant at all"
        # from "the device went quiet". The first must render blank, the second
        # flat. Today it is always the first: the mobile client is the intended
        # writer and has no wearable code yet, so nothing advances the clock.
        'observed': bool(carried or advances),
    }


def completeness_matrix(emas, bank=None):
    """Per EMA: what was served, what was askable, and what came back.

    Scored with the same _askable_sub_item_ids the daily completeness metric
    uses. If these two ever disagree the grid and the timeline are telling
    different stories about one number, so the gating lives in one place.
    """
    bank = bank or load_item_bank()
    rows = []
    for ema in emas:
        responses = {r.sub_item_id: r for r in ema.item_responses.all()}
        askable = _askable_sub_item_ids(ema, responses, bank)
        # An askable sub-item with no response row is the unanswered case this
        # panel exists to show, so look it up rather than assuming it is there.
        answered = [
            sub_id for sub_id in askable
            if sub_id in responses and _extract_value(responses[sub_id]) is not None
        ]
        rows.append({
            'ema_id': ema.pk,
            'ema_type': ema.ema_type,
            'sent_at': ema.sent_at,
            'status': ema.status,
            'served': list(ema.served_sub_item_ids or []),
            # Empty served means the row predates migration 0044, so askable was
            # inferred from what was answered and completeness is within-item
            # rather than whole-check-in.
            'served_recorded': bool(ema.served_sub_item_ids),
            'askable': askable,
            'answered': answered,
            'completeness': len(answered) / len(askable) if askable else None,
            # B1 and B2 feed calculate_mssd. A check-in without them yields no
            # volatility signal and therefore no decision point, which is a hole
            # in the MRT rather than a data-quality blemish.
            'missing_signal': not {'B1_valence', 'B1_arousal', 'B2_stress'}.issubset(answered),
        })
    return rows


# Each stage adds a condition to the one before it, so the counts cannot rise as
# you go down. Filtering each stage independently looks equivalent and is not:
# production carries 20 prompts with a device receipt but only 4 with a
# push_sent_at, which makes an independent "device_received" larger than the
# "push_sent" above it and the waterfall nonsense. Rows that skip a stage are
# real and are reported separately as anomalies rather than being averaged away.
_SENT = Q(send_prompt=True)
_PUSHED = _SENT & Q(push_sent_at__isnull=False)
_RECEIVED = _PUSHED & Q(device_received_at__isnull=False)
_REPORTED = _RECEIVED & Q(receipt_reported_at__isnull=False)

FUNNEL_STAGES = (
    ('eligible_sent', _SENT),
    ('push_sent', _PUSHED),
    ('device_received', _RECEIVED),
    ('receipt_reported', _REPORTED),
)

FUNNEL_ANOMALIES = (
    # A receipt without a push timestamp, or engagement on a prompt that never
    # reported one. Both mean a stage was skipped rather than failed, which is a
    # pipeline problem and not a participant one.
    ('received_without_push', Q(send_prompt=True, device_received_at__isnull=False,
                                push_sent_at__isnull=True)),
    ('reported_without_receipt', Q(send_prompt=True, receipt_reported_at__isnull=False,
                                   device_received_at__isnull=True)),
)


def delivery_funnel(user):
    """Whole-study delivery waterfall for one participant."""
    logs = JITAILog.objects.filter(user=user)
    counts = logs.aggregate(**{
        name: Count('id', filter=condition)
        for name, condition in FUNNEL_STAGES + FUNNEL_ANOMALIES
    })
    engaged_ids = set(
        EngagementLog.objects
        .filter(user=user, jitai_log__isnull=False)
        .values_list('jitai_log_id', flat=True)
    )
    reported_ids = set(logs.filter(_REPORTED).values_list('id', flat=True))
    engaged = len(engaged_ids & reported_ids)
    sent_ids = set(logs.filter(_SENT).values_list('id', flat=True))
    engaged_without_receipt = len((engaged_ids & sent_ids) - reported_ids)

    stages, previous = [], None
    for name, _ in FUNNEL_STAGES:
        value = counts[name]
        stages.append({
            'stage': name,
            'n': value,
            'dropped': None if previous is None else previous - value,
            'retained': None if not previous else value / previous,
        })
        previous = value
    stages.append({
        'stage': 'engaged',
        'n': engaged,
        'dropped': None if previous is None else previous - engaged,
        'retained': None if not previous else engaged / previous,
    })

    delivered = logs.filter(send_prompt=True, device_received_at__isnull=False)
    return {
        'stages': stages,
        # Prompts that skipped a stage rather than failing at one. Kept out of
        # the waterfall so it stays a waterfall, and reported here so they are
        # not silently dropped: each one is a prompt the pipeline mis-recorded.
        'anomalies': {
            'received_without_push': counts['received_without_push'],
            'reported_without_receipt': counts['reported_without_receipt'],
            'engaged_without_receipt': engaged_without_receipt,
        },
        'by_platform': list(
            delivered.values('receipt_platform').annotate(n=Count('id')).order_by('-n')
        ),
        'by_app_state': list(
            delivered.values('receipt_app_state').annotate(n=Count('id')).order_by('-n')
        ),
        'errors': list(
            logs.exclude(delivery_error='')
            .values('delivery_error').annotate(n=Count('id')).order_by('-n')
        ),
    }


def mssd_lane(user, local_date):
    """Decision points with the threshold each was actually compared against.

    A point above its threshold that was not marked eligible is an engine
    defect, but only when threshold_source is 'engine': a reconstructed value
    can disagree with the engine for reasons that are not the engine's fault, so
    it is never grounds for the accusation.
    """
    day_start, day_end = participant_day_bounds(local_date)
    points = []
    for log in (
        JITAILog.objects
        .filter(user=user, decision_made_at__gte=day_start, decision_made_at__lt=day_end)
        .order_by('decision_made_at')
    ):
        above = (
            log.observed_mssd is not None
            and log.threshold_at_decision is not None
            and log.observed_mssd > log.threshold_at_decision
        )
        points.append({
            'at': log.decision_made_at,
            'decision_point_id': log.decision_point_id,
            'observed_mssd': log.observed_mssd,
            'threshold': log.threshold_at_decision,
            'threshold_source': log.threshold_source or None,
            'trigger_reason': log.trigger_reason,
            'eligible': log.randomization_draw is not None,
            'send_prompt': log.send_prompt,
            'unexplained': bool(
                above and log.randomization_draw is None
                and log.threshold_source == 'engine'
            ),
        })
    return points


def local_minutes(moment, local_date):
    """Minutes from local midnight, for putting an event on the shared x-axis."""
    if moment is None:
        return None
    day_start, _ = participant_day_bounds(local_date)
    return elapsed_minutes(day_start, moment)
