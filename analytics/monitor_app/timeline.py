"""Timeline payload to frames, one per lane.

Every lane shares one x-encoding, minutes from local midnight, so events line up
vertically across lanes. That alignment is the point of the view: a wear gap
sitting under a flat sync trace is a data-delivery failure, and the same gap
under a live sync trace is genuine non-wear.
"""

import pandas as pd

MINUTES_PER_DAY = 1440

# EngagementLog has no "acted" event. These are the five that exist; anything
# labelled "acted" in a spec means the linked post-prompt check-in completed,
# which is read from the EMA, not from here.
ENGAGEMENT_LABELS = {
    'ema_opened': 'opened',
    'ema_dismissed': 'dismissed',
    'ema_completed': 'completed',
    'notification_tapped': 'tapped',
    'notification_dismissed': 'dismissed',
}

DECISION_OUTCOMES = {
    'missing or insufficient EMA data': 'no data',
    'insufficient within-person history': 'no history',
    'below within-person threshold': 'below threshold',
    'cooldown active': 'cooldown',
    'daily cap reached': 'cap reached',
    'prompt sent': 'eligible',
}


def _minutes(value, day_start):
    if value is None or day_start is None:
        return None
    return (pd.Timestamp(value) - pd.Timestamp(day_start)).total_seconds() / 60


def wear_frame(day):
    """Covered spans and gaps, both as bars on the shared axis."""
    day_start = day['day_start']
    rows = [
        {'kind': 'covered', 'start': span['start'], 'end': span['end'],
         'minutes': span['end'] - span['start'], 'label': ''}
        for span in day['wear']['covered']
    ]
    rows += [
        {'kind': 'gap over threshold' if gap['over_threshold'] else 'gap',
         'start': gap['start'], 'end': gap['end'], 'minutes': gap['minutes'],
         # Only a gap long enough to matter is worth labelling; the rest is noise.
         'label': f"{gap['minutes'] // 60}h{gap['minutes'] % 60:02d}" if gap['over_threshold'] else ''}
        for gap in day['wear']['gaps']
    ]
    frame = pd.DataFrame(rows)
    return frame.assign(local_date=day['local_date'], day_start=day_start)


def sync_frame(day):
    """Step trace of the sync clock, carrying in the value from before midnight.

    Empty when no sync has ever been observed for this participant, which is
    every day before the sync history table was deployed. That renders as a
    blank lane rather than a flat line at zero, because nothing was recorded is
    not the same fact as the device went quiet.
    """
    lane = day['sync']
    if not lane['observed']:
        return pd.DataFrame()

    day_start = day['day_start']
    rows = []
    if lane['carried_in']:
        rows.append({'at': 0, 'source': 'carried in',
                     'last_synced_at': lane['carried_in']['last_synced_at'],
                     'samples_written': lane['carried_in']['samples_written']})
    for advance in lane['advances']:
        rows.append({
            'at': _minutes(advance['observed_at'], day_start),
            'source': advance['source'],
            'last_synced_at': advance['last_synced_at'],
            'samples_written': advance['samples_written'],
        })
    frame = pd.DataFrame(rows)
    # Staleness at each step: how far behind the sync clock had fallen. A rising
    # line is an outage in progress.
    frame['stale_minutes'] = [
        None if row['last_synced_at'] is None
        else max(0.0, row['at'] - _minutes(row['last_synced_at'], day_start))
        for _, row in frame.iterrows()
    ]
    return frame.assign(local_date=day['local_date'])


def _events(day, kind):
    return [event for event in day['events'] if event['kind'] == kind]


def ema_frame(day):
    day_start = day['day_start']
    # Joined on ema_id rather than by position: the two lists are built from
    # separate queries and nothing guarantees they stay in step.
    completeness = {row['ema_id']: row for row in day['completeness']}
    rows = []
    for event in _events(day, 'ema'):
        scored = completeness.get(event['id'], {})
        rows.append({
            'at': _minutes(event['at'], day_start),
            'ema_type': event['ema_type'],
            'status': event['status'],
            'slot': event['slot'],
            'answered': event['answered'],
            'served': event['served'],
            # Fill by completeness, which is what makes a half-finished check-in
            # visible as something other than a submission.
            'completeness': scored.get('completeness'),
            'missing_signal': scored.get('missing_signal', False),
        })
    return pd.DataFrame(rows).assign(local_date=day['local_date']) if rows else pd.DataFrame()


def reminder_frame(day):
    day_start = day['day_start']
    rows = [
        {'at': _minutes(event['at'], day_start), 'slot': event['slot']}
        for event in _events(day, 'reminder')
    ]
    return pd.DataFrame(rows).assign(local_date=day['local_date']) if rows else pd.DataFrame()


def decision_frame(day):
    day_start = day['day_start']
    rows = []
    for event in _events(day, 'decision'):
        eligible = event['eligible']
        outcome = DECISION_OUTCOMES.get(event['trigger_reason'], event['trigger_reason'])
        if eligible and not event['send_prompt']:
            outcome = 'eligible, not sent'
        elif eligible and event['send_prompt']:
            outcome = 'eligible, sent'
        rows.append({
            'at': _minutes(event['at'], day_start),
            'outcome': outcome,
            'trigger_reason': event['trigger_reason'],
            'observed_mssd': event['observed_mssd'],
            'threshold': event['threshold_at_decision'],
            'threshold_source': event['threshold_source'],
            'randomization_draw': event['randomization_draw'],
            'decision_point_id': event['decision_point_id'],
        })
    return pd.DataFrame(rows).assign(local_date=day['local_date']) if rows else pd.DataFrame()


def prompt_frame(day):
    """Delivered prompts as bars from push to receipt, so lag reads as length."""
    day_start = day['day_start']
    engagement = {}
    for event in _events(day, 'engagement'):
        if event['jitai_log'] is not None:
            engagement.setdefault(event['jitai_log'], []).append(
                ENGAGEMENT_LABELS.get(event['event_type'], event['event_type']))

    rows = []
    for event in _events(day, 'decision'):
        if event['push_sent_at'] is None:
            continue
        received = _minutes(event['device_received_at'], day_start)
        sent = _minutes(event['push_sent_at'], day_start)
        marks = engagement.get(event['id'], [])
        rows.append({
            'at': sent,
            # A prompt with no receipt gets a zero-length bar, not a bar running
            # to the end of the day: unknown arrival is not slow arrival.
            'received': received,
            'reported': _minutes(event['receipt_reported_at'], day_start),
            'lag_seconds': None if received is None else (received - sent) * 60,
            'delivery_status': event['delivery_status'],
            'delivery_error': event['delivery_error'],
            'platform': event['receipt_platform'],
            'app_state': event['receipt_app_state'],
            'engagement': ', '.join(sorted(set(marks))) or 'none',
        })
    return pd.DataFrame(rows).assign(local_date=day['local_date']) if rows else pd.DataFrame()


def mssd_frame(day):
    day_start = day['day_start']
    rows = [
        {
            'at': _minutes(point['at'], day_start),
            'observed_mssd': point['observed_mssd'],
            'threshold': point['threshold'],
            'threshold_source': point['threshold_source'],
            'unexplained': point['unexplained'],
            'trigger_reason': point['trigger_reason'],
        }
        for point in day['mssd']
    ]
    return pd.DataFrame(rows).assign(local_date=day['local_date']) if rows else pd.DataFrame()


def completeness_grid(day):
    """Long-form EMA by sub-item cells: answered, not answered, not applicable."""
    rows = []
    for entry in day['completeness']:
        applicable = set(entry['askable'])
        answered = set(entry['answered'])
        universe = applicable | set(entry['served'])
        label = f"{pd.Timestamp(entry['sent_at']).strftime('%H:%M')} {entry['ema_type']}"
        for sub_item_id in sorted(universe):
            if sub_item_id in answered:
                state = 'answered'
            elif sub_item_id in applicable:
                state = 'not answered'
            else:
                state = 'not applicable'
            rows.append({'ema': label, 'sub_item_id': sub_item_id, 'state': state,
                         'missing_signal': entry['missing_signal']})
    return pd.DataFrame(rows)


def funnel_frame(payload):
    frame = pd.DataFrame(payload['stages'])
    frame['label'] = frame['stage'].str.replace('_', ' ')
    return frame


def slot_gridlines(day):
    day_start = day['day_start']
    return pd.DataFrame([
        {'at': _minutes(slot['start'], day_start), 'index': slot['index'],
         'covered': slot['covered'], 'reminded': slot['reminded']}
        for slot in day['slots']
    ])
