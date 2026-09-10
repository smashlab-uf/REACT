"""Stage 2: the participant grid.

Audience is the RA, daily, and the question is "who needs a phone call today".
Rows are ordered by risk with the score and its largest term printed alongside,
so the ordering can be argued with rather than trusted.
"""

import altair as alt
import pandas as pd
import streamlit as st

try:
    from monitor_app import client, frame as fr
except ImportError:  # streamlit run monitor_app/app.py puts the file's dir first
    import client
    import frame as fr

PHASES = {'All participants': 'all', 'Phase 1': 'phase1', 'Phase 2': 'phase2'}
CELL = 22
LABEL_WIDTH = 190

SPLIT_COLORS = {'covered': '#1a9850', 'reminded': '#fdae61', 'silent': '#4a4a8a'}

st.set_page_config(page_title='REACT · Participant grid', layout='wide')


def _scale(metric_name, benchmarks, prompt_cap):
    """Colour scale for the selected metric, thresholds fetched not typed."""
    spec = fr.METRICS[metric_name]
    if spec['kind'] == 'ordinal':
        return alt.Scale(domain=[0, prompt_cap], scheme='blues')

    benchmark = benchmarks.get(spec['benchmark']) if spec['benchmark'] else None
    if benchmark is None:
        return alt.Scale(domain=[0, 1], scheme='blues')
    # Diverging about the preregistered benchmark, so "at target" reads as
    # neutral and the eye only catches what is under it.
    return alt.Scale(domain=[0, benchmark, 1], range=['#b2182b', '#f7f7f7', '#1a9850'])


def _heatmap(data, order, x_field, metric_name, benchmarks, prompt_cap):
    """Read-only by design: Streamlit refuses selections on a layered chart, and
    this one layers four marks. The call list above it carries the selection."""
    x_title = 'Study day' if x_field == 'study_day' else 'Calendar date'
    x = (
        alt.X('study_day:O', title=x_title, axis=alt.Axis(labelAngle=0))
        if x_field == 'study_day'
        else alt.X('local_date:T', title=x_title)
    )
    y = alt.Y('label:N', title=None, sort=order,
              axis=alt.Axis(labelLimit=LABEL_WIDTH, labelFontSize=11))
    base = alt.Chart(data).encode(x=x, y=y)

    cells = base.mark_rect(stroke='#ffffff', strokeWidth=0.5).encode(
        color=alt.Color('cell:Q', title=metric_name,
                        scale=_scale(metric_name, benchmarks, prompt_cap),
                        legend=alt.Legend(orient='top')),
        tooltip=[
            alt.Tooltip('participant_id:N', title='Participant'),
            alt.Tooltip('study_day:O', title='Study day'),
            alt.Tooltip('local_date:T', title='Date'),
            alt.Tooltip('cell:Q', title=metric_name, format='.2f'),
            alt.Tooltip('covered:Q', title='Slots covered'),
            alt.Tooltip('reminded:Q', title='Reminded, uncovered'),
            alt.Tooltip('silent:Q', title='Silent (no reminder sent)'),
        ],
    )

    # Altair has no hatch fill. A translucent wash over the run-in days reads
    # more clearly than a hatch would at 40 rows by 35 columns, and it survives
    # the switch to the calendar axis, where run-in is no longer a column range.
    run_in = base.transform_filter(alt.datum.run_in).mark_rect(
        color='#2b2b2b', opacity=0.16)

    # Two overlays that mean opposite things and must not be confused: the dot
    # is an open alert about the participant, the slash is the scheduler having
    # failed to ask them anything.
    dots = base.transform_filter(alt.datum.alert).mark_point(
        shape='circle', size=13, filled=True, color='#111111',
        xOffset=CELL / 2 - 4, yOffset=-CELL / 2 + 4)
    slashes = base.transform_filter(alt.datum.silent > 0).mark_text(
        text='/', color='#4a4a8a', fontSize=15, fontWeight='bold')

    return (
        alt.layer(cells, run_in, dots, slashes)
        .properties(width=alt.Step(CELL), height=alt.Step(CELL))
        .configure_view(strokeWidth=0)
    )


def _split_chart(split, order):
    return (
        alt.Chart(split)
        .mark_bar()
        .encode(
            x=alt.X('slots:Q', title='Slots over the trailing 7 days', stack='zero'),
            y=alt.Y('label:N', title=None, sort=order,
                    axis=alt.Axis(labelLimit=LABEL_WIDTH, labelFontSize=11)),
            color=alt.Color(
                'kind:N', title=None,
                scale=alt.Scale(domain=list(SPLIT_COLORS), range=list(SPLIT_COLORS.values())),
                legend=alt.Legend(orient='top')),
            order=alt.Order('kind:N'),
            tooltip=['participant_id:N', 'kind:N', 'slots:Q'],
        )
        .properties(height=alt.Step(18))
    )


def _right_rail(user_id, study_days):
    detail = client.participant(user_id)
    rollup = detail['participant'] or {}
    st.subheader(detail['participant_id'])

    day_now = rollup.get('study_day_now')
    phase = rollup.get('phase') or '—'
    if day_now is None:
        st.caption(phase)
    else:
        st.caption(f'{phase} · study day {day_now} of {study_days}'
                   f' · {max(0, study_days - 1 - day_now)} remaining')

    st.metric('Risk score', rollup.get('risk_score') if rollup.get('risk_score') is not None else '—')
    components = rollup.get('risk_components') or {}
    if components:
        st.dataframe(
            pd.DataFrame(
                [{'term': term.replace('_', ' '), 'points': points}
                 for term, points in sorted(components.items(), key=lambda i: -i[1])]
            ),
            hide_index=True, width='stretch',
        )

    st.write('**Cumulative**')
    st.dataframe(pd.DataFrame([
        {'metric': 'Slot coverage',
         'value': _pct(rollup.get('slot_coverage_rate')),
         'counts': _counts(rollup, 'slot_coverage')},
        {'metric': 'Prompt response',
         'value': _pct(rollup.get('prompt_response_rate')),
         'counts': _counts(rollup, 'prompt_response')},
        {'metric': 'Wear',
         'value': _pct(rollup.get('wear_rate')),
         'counts': _counts(rollup, 'wear')},
    ]), hide_index=True, width='stretch')

    delivered = sum(day.get('delivered_n') or 0 for day in detail['daily'])
    st.write('**To date**')
    st.write(f'Prompts delivered: {delivered}')
    st.write(f"Last sync: {_age(rollup.get('last_sync_at'))}")
    st.write(f"Last EMA: {_age(rollup.get('last_ema_at'))}")

    open_alerts = detail['alerts']
    st.write(f"**Open alerts ({len(open_alerts)})**")
    for alert in open_alerts:
        st.write(f"`{alert['severity']}` {alert['rule_id']}")

    if st.button('Open participant timeline', width='stretch'):
        st.session_state['stage3_user_id'] = user_id
        st.info('Stage 3 is not built yet. Selection stored for it.')


def _age(timestamp):
    """How stale, then when. An RA acts on the age; the timestamp is the proof."""
    if not timestamp:
        return 'never'
    moment = pd.to_datetime(timestamp, utc=True)
    hours = (pd.Timestamp.now(tz='UTC') - moment).total_seconds() / 3600
    local = moment.tz_convert('America/New_York')
    scale = f'{hours:.0f} h' if hours >= 1 else f'{hours * 60:.0f} min'
    return f"{scale} ago · {local.strftime('%b %d %H:%M')} ET"


def _pct(value):
    return '—' if value is None else f'{value:.0%}'


def _counts(rollup, prefix):
    numerator, denominator = rollup.get(f'{prefix}_num'), rollup.get(f'{prefix}_den')
    if numerator is None or denominator is None:
        return '—'
    return f'{numerator} / {denominator}'


def main():
    st.title('Participant grid')

    with st.sidebar:
        st.header('View')
        phase_label = st.selectbox('Cohort', list(PHASES))
        metric_name = st.radio('Cell colour', list(fr.METRICS))
        axis_label = st.radio(
            'X axis', ['Study day', 'Calendar date'],
            help='Calendar date is what shows a cohort-wide outage. A Celery '
                 'failure hits everyone on the same date, not the same study day.')
        if st.button('Refresh now', width='stretch'):
            st.cache_data.clear()
        st.caption(f'Reading {client.base_url()}')

    phase = PHASES[phase_label]
    spec = fr.METRICS[metric_name]

    try:
        payload = client.grid(spec['field'], phase)
        snapshot = client.cohort(phase)
        open_alerts = client.alerts()
    except client.MonitorError as exc:
        st.error(str(exc))
        return

    if not payload['rows']:
        st.warning('No participants in this cohort yet.')
        return

    study_days = len(payload['study_days'])
    benchmarks = {
        name: entry.get('target')
        for name, entry in (snapshot.get('benchmarks') or {}).items()
        if isinstance(entry, dict) and entry.get('target') is not None
    }

    grid_frame = fr.to_frame(payload)
    grid_frame, order = fr.with_labels(grid_frame)
    grid_frame = fr.cell_value(grid_frame, metric_name, payload)
    drawable = grid_frame[grid_frame['lived']]

    x_field = 'study_day' if axis_label == 'Study day' else 'local_date'
    # Cohort alerts carry a null user and simply fail to match any participant.
    alert_counts = pd.Series(
        [alert['user'] for alert in open_alerts['alerts']], dtype='object',
    ).value_counts()

    left, right = st.columns([4, 1], gap='medium')

    with left:
        st.subheader('Call list')
        st.caption('Ordered by risk. Select a row to inspect that participant.')
        ranked = fr.participant_order(grid_frame)
        event = st.dataframe(
            fr.call_list(grid_frame, alert_counts),
            hide_index=True, width='stretch',
            on_select='rerun', selection_mode='single-row', key='calls',
        )

        st.subheader(metric_name)
        st.caption(
            'Blank means outside the participant window. Shaded columns are the '
            'run-in baseline, where no prompts are expected. A dot is an open '
            'alert. A slash is a day where at least one slot went silent.')
        st.altair_chart(
            _heatmap(drawable, order, x_field, metric_name, benchmarks,
                     payload['daily_prompt_cap']))

        st.subheader('Where the empty slots went')
        st.caption(
            'Silent is a scheduler failure, not non-response: no reminder was '
            'sent, so the participant was never asked. Causes are a missing push '
            'token, an intervention outcome window already open, or Celery not '
            'running. Do not call anyone about a silent slot.')
        split = fr.trailing_split(drawable)
        if not split.empty:
            st.altair_chart(_split_chart(split, order))

    with right:
        selected = _selected_user(event, ranked)
        if selected is None:
            st.info('Select a row in the call list to inspect a participant.')
        else:
            _right_rail(selected, study_days)


def _selected_user(event, ranked):
    """Map the selected call-list row back to a user.

    The call list is built from `ranked` in order, so the selected row index is
    a positional index into it.
    """
    rows = ((event or {}).get('selection') or {}).get('rows') or []
    if not rows:
        return None
    position = rows[0]
    if position >= len(ranked):
        return None
    return int(ranked.iloc[position]['user_id'])


if __name__ == '__main__':
    main()
