"""Stage 1: the cohort board.

Audience is the PI at a weekly meeting. One screen, no scrolling, and the whole
thing has to survive being screenshotted into a slide. That last constraint is
what shapes it: every number a reader needs is printed, never hovered, and a
suppressed rate shows its raw counts rather than a blank.

The benchmark tiles say whether the study is hitting its targets. The integrity
strip below them says whether the trial will be analysable at all, which is the
more important question and the one nothing else on the dashboard asks.
"""

import sys
from pathlib import Path

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import altair as alt
import pandas as pd
import streamlit as st

from monitor_app import client, cohort as cf

st.set_page_config(page_title='REACT · Cohort board', layout='wide')

PHASES = {'All participants': 'all', 'Phase 1': 'phase1', 'Phase 2': 'phase2'}
SPARK_HEIGHT = 54
OK = '#1a9850'
ALARM = '#b2182b'


def _sparkline(frame, target=None, step=False):
    if frame.empty:
        return None
    mark = alt.Chart(frame).mark_line(interpolate='step-after' if step else 'linear',
                                      color='#4a4a8a', strokeWidth=2)
    layers = [mark.encode(
        x=alt.X('date:T', axis=None),
        y=alt.Y('value:Q', axis=None, scale=alt.Scale(domain=[0, 1])),
    )]
    if target is not None:
        layers.append(
            alt.Chart(pd.DataFrame([{'t': target}]))
            .mark_rule(color='#999999', strokeDash=[3, 3]).encode(y='t:Q'))
    return alt.layer(*layers).properties(height=SPARK_HEIGHT).configure_view(strokeWidth=0)


def _tile(column, row, series):
    with column:
        st.markdown(f"**{row['label']}**")

        if row['state'] == 'unmeasurable':
            st.markdown('### —')
            st.caption('No source for this benchmark yet.')
            return
        if row['state'] == 'suppressed':
            # Phase 1 is five participants, so every rate suppresses. Showing
            # counts is the honest fallback; a percentage from five people would
            # look authoritative and mean nothing.
            st.markdown(f"### {row['counts'].split(' ')[0]}")
            st.caption(f"counts only · {row['counts']}")
            st.caption(f"target {row['target']:.0%}" if row['target'] else '')
            return

        st.markdown(f"### {cf.pct(row['value'])}")
        st.caption(f"95% CI {row['interval']}")
        st.caption(row['counts'])
        if row['target'] is not None:
            delta = row['value'] - row['target']
            arrow = '▲' if delta >= 0 else '▼'
            st.caption(f"target {row['target']:.0%} · {arrow} {abs(delta):.0%}")
        if row['participant_mean'] is not None:
            # The pooled figure weights each participant by how many days they
            # contributed; the benchmark is stated about participants.
            st.caption(f"participant mean {row['participant_mean']:.0%}")

        chart = _sparkline(series, row['target'], step=row['key'] == 'retention')
        if chart is not None:
            st.altair_chart(chart)


def _wear_extras(entry):
    minute = entry.get('mean_minute_coverage')
    burst = entry.get('burst_charging_days') or 0
    line = f"minute coverage {minute:.0%}" if minute is not None else 'minute coverage —'
    if burst:
        line += f" · {burst} burst-charging days"
    st.caption(line)


def _retention_extras(entry):
    active = entry.get('active') or {}
    if active.get('numerator') is None:
        return
    st.caption(f"active last 7d {active['numerator']}/{active['denominator']}")


def _funnel(snapshot):
    st.markdown('**Enrollment**')
    frame = cf.funnel_frame(snapshot)
    if frame.empty:
        st.caption('No participants yet.')
        return
    st.altair_chart(
        alt.Chart(frame).mark_bar(color='#4a4a8a').encode(
            x=alt.X('n:Q', title=None),
            y=alt.Y('stage:N', title=None, sort=list(frame['stage'])),
        ).properties(height=alt.Step(22)))
    missing = cf.unmeasurable_stages(snapshot)
    if missing:
        st.caption(f"{', '.join(missing)}: no source in the schema")


def _integrity(snapshot):
    st.markdown('**MRT integrity** · weeks 2-5')
    rows = cf.gauge_frame(snapshot)
    if not rows:
        st.caption('Not computed yet.')
        return
    for column, row in zip(st.columns(len(rows)), rows):
        with column:
            colour = ALARM if row['alarming'] else OK
            st.markdown(
                f"<div style='font-size:0.8rem;color:#555'>{row['label']}</div>"
                f"<div style='font-size:1.5rem;font-weight:600;color:{colour}'>"
                f"{row['display']}</div>",
                unsafe_allow_html=True)
            if row['counts']:
                st.caption(row['counts'])
            if row['note']:
                st.caption(row['note'])

    gauge = (snapshot.get('integrity') or {}).get('eligibility_rate') or {}
    ratio = gauge.get('decision_points_per_scheduled_ema')
    if ratio is not None:
        st.caption(
            f"Decision points per scheduled check-in: {ratio:.2f}. A decision "
            f"point only exists once an EMA is submitted, so availability is "
            f"confounded with compliance and the eligibility gauge cannot be "
            f"read as a property of the engine alone.")


def _alert_feed(payload):
    st.markdown(f"**Alerts** · {payload.get('open', 0)} open")
    frame = cf.alert_frame(payload)
    if frame.empty:
        st.caption('None open.')
        return
    for row in frame.itertuples():
        colour = {'critical': ALARM, 'high': '#e08214'}.get(row.severity, '#888888')
        st.markdown(
            f"<span style='color:{colour};font-weight:600'>{row.severity}</span> "
            f"{row.rule}<br><span style='font-size:0.8rem;color:#666'>"
            f"{row.who} · {row.date}</span>",
            unsafe_allow_html=True)
        if row.user_id and st.button('open timeline', key=f'a{row.Index}'):
            st.session_state['stage3_user_id'] = int(row.user_id)
            st.session_state['stage3_date'] = row.date
            st.switch_page('pages/2_Participant_timeline.py')


def main():
    st.title('Cohort board')

    with st.sidebar:
        phase_label = st.selectbox('Cohort', list(PHASES))
        if st.button('Refresh now', width='stretch'):
            st.cache_data.clear()
        st.caption(f'Reading {client.base_url()}')

    phase = PHASES[phase_label]
    try:
        snapshot = client.cohort(phase)
        alerts = client.alerts()
    except client.MonitorError as exc:
        st.error(str(exc))
        return

    if not snapshot.get('available', True):
        st.warning(snapshot.get('detail', 'No snapshot yet.'))
        return

    st.caption(
        f"{snapshot['n_participants']} enrolled · {snapshot['n_active']} in study · "
        f"as of {pd.Timestamp(snapshot['as_of']).tz_convert('America/New_York'):%b %d %H:%M} ET")

    board, rail = st.columns([5, 1], gap='medium')

    with board:
        rows = cf.benchmark_frame(snapshot)
        for column, row in zip(st.columns(len(rows)), rows):
            field = cf.SERIES_FIELD.get(row['key'])
            series = cf.series_frame(snapshot, field) if field else pd.DataFrame()
            _tile(column, row, series)
            with column:
                if row['key'] == 'wear':
                    _wear_extras(row['entry'])
                if row['key'] == 'retention':
                    _retention_extras(row['entry'])

        st.divider()
        _integrity(snapshot)
        st.divider()
        _funnel(snapshot)

    with rail:
        _alert_feed(alerts)


if __name__ == '__main__':
    main()
