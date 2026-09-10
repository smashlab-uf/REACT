"""Grid JSON to one tidy frame, so every chart reads the same rows.

One row per participant-day. Days the participant never lived carry NaN in every
column and are dropped before charting: a missing mark is what makes a
structural blank look different from a zero-valued cell, and that distinction is
the whole reason the metric tables are nullable.
"""

import pandas as pd

# Cell value per metric, and the scale it is drawn on. Thresholds are not here:
# the coverage and wear benchmarks come from /api/monitor/cohort and the prompt
# cap comes from the grid payload, so no protocol constant is typed into the
# prototype.
METRICS = {
    'Slot coverage': {
        'field': 'slots_covered',
        'kind': 'rate',
        'denominator': 'slots_expected',
        'benchmark': 'slot_coverage',
        'format': '.0%',
    },
    'Wear': {
        'field': 'wear_valid_pct',
        'kind': 'rate',
        'denominator': None,
        'benchmark': 'wear',
        'format': '.0%',
    },
    'Prompts delivered': {
        'field': 'delivered_n',
        'kind': 'ordinal',
        'denominator': None,
        'benchmark': None,
        'format': 'd',
    },
    'Item completeness': {
        'field': 'completeness_mean',
        'kind': 'rate',
        'denominator': None,
        'benchmark': None,
        'format': '.0%',
    },
}

OVERLAY_KEYS = ('local_dates', 'run_in', 'covered', 'reminded', 'silent', 'alert')


def top_term(components):
    """The single largest contributor to a risk score, for the "why" column."""
    if not components:
        return ''
    term, points = max(components.items(), key=lambda item: item[1])
    return term.replace('_', ' ') if points else ''


def to_frame(payload):
    """A participant-day frame from one /api/monitor/grid response."""
    records = []
    for row in payload['rows']:
        components = row.get('risk_components') or {}
        for day in payload['study_days']:
            records.append({
                'user_id': row['user_id'],
                'participant_id': row['participant_id'],
                'phase': row['phase'],
                'study_day_now': row['study_day_now'],
                'risk_score': row['risk_score'],
                'risk_why': top_term(components),
                'study_day': day,
                'value': row['values'][day],
                'local_date': row['local_dates'][day],
                'run_in': row['run_in'][day],
                'covered': row['covered'][day],
                'reminded': row['reminded'][day],
                'silent': row['silent'][day],
                'alert': row['alert'][day],
            })

    frame = pd.DataFrame.from_records(records)
    frame['local_date'] = pd.to_datetime(frame['local_date'])
    # A day outside the window has no value on any array, so this one test
    # separates "never lived" from "lived and did nothing".
    frame['lived'] = frame['local_date'].notna()
    return frame


def participant_order(frame):
    """Participants by risk descending, unscored last.

    A null risk score means the study is not currently asking anything of that
    participant, so they sort below everyone who is in the field rather than
    above them, which is where a naive descending sort on NaN would put them.
    """
    ranked = (
        frame[['user_id', 'participant_id', 'risk_score', 'risk_why', 'phase',
               'study_day_now']]
        .drop_duplicates('user_id')
        .assign(_unscored=lambda f: f['risk_score'].isna())
        .sort_values(['_unscored', 'risk_score', 'participant_id'],
                     ascending=[True, False, True])
        .reset_index(drop=True)
    )
    return ranked.drop(columns='_unscored')


def call_list(frame, alert_counts):
    """The RA's ordered call list: who, how bad, and why, one row each.

    Row order is identical to the heatmap's, so the two read together, and the
    row index is what the selection maps back through.
    """
    ranked = participant_order(frame)
    silent_days = (
        frame[frame['lived'] & (frame['silent'] > 0)]
        .groupby('user_id')['silent'].size()
    )
    return pd.DataFrame({
        'Participant': ranked['participant_id'],
        'Day': ranked['study_day_now'].astype('Int64'),
        'Phase': ranked['phase'],
        'Risk': ranked['risk_score'].astype('Int64'),
        'Why': ranked['risk_why'],
        'Alerts': ranked['user_id'].map(alert_counts).fillna(0).astype(int),
        'Silent days': ranked['user_id'].map(silent_days).fillna(0).astype(int),
    })


def row_label(participant_id, risk_score, risk_why):
    if risk_score is None or pd.isna(risk_score):
        return f'{participant_id}  ·  —'
    why = f'  ({risk_why})' if risk_why else ''
    return f'{participant_id}  ·  {int(risk_score)}{why}'


def with_labels(frame):
    """Attach the axis label each row is drawn against."""
    labels = {
        row.user_id: row_label(row.participant_id, row.risk_score, row.risk_why)
        for row in participant_order(frame).itertuples()
    }
    return frame.assign(label=frame['user_id'].map(labels)), [
        labels[user_id] for user_id in participant_order(frame)['user_id']
    ]


def cell_value(frame, metric_name, payload):
    """The number a cell is coloured by, as a 0-1 rate or an ordinal count."""
    spec = METRICS[metric_name]
    values = frame['value'].astype('Float64')
    if spec['kind'] == 'rate' and spec['denominator'] == 'slots_expected':
        # slots_covered arrives as a count; the grid draws it against the day's
        # own expected slots rather than a hardcoded six.
        denominator = frame['covered'] + frame['reminded'] + frame['silent']
        values = values / denominator.replace(0, pd.NA)
    return frame.assign(cell=values.astype(float))


def trailing_split(frame, days=7):
    """Long-form covered/reminded/silent counts over the trailing week."""
    lived = frame[frame['lived']]
    if lived.empty:
        return lived.assign(kind=None, slots=None)

    cutoff = lived['local_date'].max() - pd.Timedelta(days=days - 1)
    window = lived[lived['local_date'] >= cutoff]
    return (
        window
        .melt(id_vars=['user_id', 'participant_id', 'label'],
              value_vars=['covered', 'reminded', 'silent'],
              var_name='kind', value_name='slots')
        .groupby(['user_id', 'participant_id', 'label', 'kind'], as_index=False)['slots']
        .sum()
    )
