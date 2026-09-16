"""Cohort snapshot to frames, for the Stage 1 board.

Everything here assumes the board is read from a screenshot, so nothing is
allowed to live in a tooltip. Values, denominators and intervals all come back
as printable text.
"""

import pandas as pd

TILES = [
    ('slot_coverage', 'Slot coverage (proxy)'),
    ('prompt_response', 'Prompt response'),
    ('wear', 'Wear coverage'),
    ('retention', 'Retention'),
]

GAUGES = [
    ('eligibility_rate', 'Eligibility'),
    ('send_rate', 'Send rate'),
    ('randomization_audit', 'Randomization'),
    ('cap_hit_rate', 'Cap hit'),
    ('cooldown_violations', 'Cooldown'),
    ('outcome_capture', 'Outcome capture'),
]


def pct(value, places=0):
    return '—' if value is None else f'{value:.{places}%}'


def counts(entry):
    """The denominator line printed under every tile value.

    Printed, not hovered: a screenshot of this board has to carry enough for a PI
    to argue with the number.
    """
    numerator, denominator = entry.get('numerator'), entry.get('denominator')
    if numerator is None or denominator is None:
        return 'no source'
    return f"{numerator} / {denominator} {entry.get('unit', '')}".strip()


def interval(entry):
    low, high = entry.get('wilson_low'), entry.get('wilson_high')
    if low is None or high is None:
        return '—'
    return f'{low:.0%} to {high:.0%}'


def tile_state(entry):
    """What the tile is able to say, which is not always a rate.

    Three distinct answers, and collapsing any two of them would lie: the
    benchmark has no source at all, the denominator is too thin to publish a
    rate from, or there is a number.
    """
    if not entry.get('measurable', True):
        return 'unmeasurable'
    if entry.get('suppressed'):
        return 'suppressed'
    return 'value'


def benchmark_frame(snapshot):
    rows = []
    for key, label in TILES:
        entry = (snapshot.get('benchmarks') or {}).get(key)
        if entry is None:
            continue
        rows.append({
            'key': key, 'label': label, 'state': tile_state(entry),
            'value': entry.get('value'), 'target': entry.get('target'),
            'low': entry.get('wilson_low'), 'high': entry.get('wilson_high'),
            'counts': counts(entry), 'interval': interval(entry),
            'participant_mean': entry.get('participant_mean'),
            'entry': entry,
        })
    return rows


def series_frame(snapshot, field):
    rows = snapshot.get('series_14d') or []
    frame = pd.DataFrame(rows)
    if frame.empty or field not in frame:
        return pd.DataFrame()
    frame = frame[['date', field]].copy()
    frame['date'] = pd.to_datetime(frame['date'])
    return frame.rename(columns={field: 'value'}).dropna(subset=['value'])


# Every tile's sparkline reads a field the data layer has already passed through
# the suppression rule, so a trend cannot get published from a denominator too
# thin to publish the headline rate from.
SERIES_FIELD = {
    'slot_coverage': 'slot_coverage',
    'prompt_response': 'prompt_response',
    'wear': 'wear_pass_rate',
    'retention': None,
}


def funnel_frame(snapshot):
    rows = [row for row in (snapshot.get('funnel') or []) if row.get('measurable')]
    return pd.DataFrame(rows)


def unmeasurable_stages(snapshot):
    return [row['stage'] for row in (snapshot.get('funnel') or [])
            if not row.get('measurable')]


def _alarm(gauge):
    """Whether a gauge is outside the band it is supposed to sit in.

    Returns (is_alarming, note). The note is printed, so a PI reading a
    screenshot can see what the gauge was compared against.
    """
    name = gauge.get('name')

    if name == 'randomization_audit':
        if gauge.get('mismatches'):
            return True, f"{gauge['mismatches']} draws contradict send_prompt"
        p_value = gauge.get('ks_p_value')
        if p_value is not None and p_value < gauge.get('alarm_p', 0.01):
            return True, f'draws not uniform, p={p_value:.3g}'
        return False, ('no draws yet' if not gauge.get('draws')
                       else f"{gauge['draws']} draws, p={p_value:.2g}")

    if name == 'cooldown_violations':
        violations = gauge.get('violations') or 0
        return bool(violations), (
            f"{violations} under {gauge.get('cooldown_minutes')} min")

    value = gauge.get('value')
    if value is None:
        return False, 'no denominator yet'

    low, high = gauge.get('alarm_low'), gauge.get('alarm_high')
    if name == 'send_rate':
        # Alarms when the interval excludes the target, not when the point
        # estimate misses it: at these denominators the point estimate misses
        # constantly and means nothing.
        target = gauge.get('target')
        if target is not None and gauge.get('wilson_low') is not None:
            excluded = not (gauge['wilson_low'] <= target <= gauge['wilson_high'])
            return excluded, f'target {target:.0%}, CI {interval(gauge)}'
    if low is not None and value < low:
        return True, f'below {low:.0%}'
    if high is not None and value > high:
        return True, f'above {high:.0%}'
    band = ' to '.join(f'{edge:.0%}' for edge in (low, high) if edge is not None)
    return False, (f'in band {band}' if band else '')


def gauge_frame(snapshot):
    integrity = snapshot.get('integrity') or {}
    rows = []
    for key, label in GAUGES:
        gauge = integrity.get(key)
        if gauge is None:
            continue
        alarming, note = _alarm(gauge)
        if key == 'randomization_audit':
            display = 'FAIL' if alarming else 'OK'
        elif key == 'cooldown_violations':
            display = str(gauge.get('violations') or 0)
        else:
            display = pct(gauge.get('value'))
        rows.append({
            'key': key, 'label': label, 'display': display,
            'alarming': alarming, 'note': note,
            'counts': counts(gauge) if 'denominator' in gauge else '',
        })
    return rows


def alert_frame(payload):
    alerts = payload.get('alerts') or []
    if not alerts:
        return pd.DataFrame()
    frame = pd.DataFrame([{
        'severity': alert['severity'],
        'rule': alert['rule_id'].replace('_', ' '),
        'who': alert['participant_id'] or 'cohort',
        'user_id': alert['user'],
        'date': alert['link_date'],
        'fired_at': alert['fired_at'],
    } for alert in alerts])
    return frame.sort_values('fired_at', ascending=False)
