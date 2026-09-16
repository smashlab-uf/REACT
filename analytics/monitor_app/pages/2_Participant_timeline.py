"""Stage 3: the participant timeline.

One participant, one day per strip, every lane on the same 00:00 to 24:00 local
clock so events line up vertically. That alignment is the whole point: a wear gap
under a flat sync trace is a data-delivery failure, and the same gap under a live
sync trace is genuine non-wear.

Lanes are drawn as separate charts rather than one composed chart. Streamlit
refuses selections on composed charts, and separate charts let each lane keep
its own y-scale.
"""

import sys
from pathlib import Path

if str(Path(__file__).resolve().parents[2]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import altair as alt
import pandas as pd
import streamlit as st

from monitor_app import client, timeline as tlf

st.set_page_config(page_title='REACT · Participant timeline', layout='wide')

HOURS = list(range(0, 25, 3))
LANE_HEIGHT = {'wear': 46, 'sync': 60, 'ema': 60, 'decision': 60, 'prompt': 60, 'mssd': 90}

WEAR_COLORS = {'covered': '#1a9850', 'gap': '#d9d9d9', 'gap over threshold': '#b2182b'}
OUTCOME_COLORS = {
    'no data': '#bdbdbd', 'no history': '#bdbdbd', 'below threshold': '#74add1',
    'cooldown': '#fdae61', 'cap reached': '#f46d43',
    'eligible, not sent': '#8073ac', 'eligible, sent': '#1a9850',
}
STATE_COLORS = {'answered': '#1a9850', 'not answered': '#b2182b',
                'not applicable': '#f0f0f0'}


def _x(title=None):
    return alt.X('at:Q', title=title,
                 scale=alt.Scale(domain=[0, tlf.MINUTES_PER_DAY], nice=False),
                 axis=alt.Axis(values=[h * 60 for h in HOURS],
                               labelExpr='datum.value / 60 + ":00"'))


def _lane_title(text, help_text=None):
    st.markdown(f'**{text}**')
    if help_text:
        st.caption(help_text)


def wear_lane(day):
    frame = tlf.wear_frame(day)
    window = day['wear']['waking_window']
    shade = alt.Chart(pd.DataFrame([window])).mark_rect(
        color='#000000', opacity=0.05).encode(x='start:Q', x2='end:Q')
    bars = alt.Chart(frame).mark_bar(height=18).encode(
        x=_x(), x2='end:Q',
        color=alt.Color('kind:N', title=None,
                        scale=alt.Scale(domain=list(WEAR_COLORS),
                                        range=list(WEAR_COLORS.values())),
                        legend=alt.Legend(orient='top')),
        tooltip=['kind:N', 'minutes:Q'],
    )
    labels = alt.Chart(frame[frame['label'] != '']).mark_text(
        align='center', dy=-14, fontSize=10, color='#b2182b').encode(
        x=alt.X('start:Q'), text='label:N')
    return (shade + bars + labels).properties(height=LANE_HEIGHT['wear'])


def sync_lane(day):
    frame = tlf.sync_frame(day)
    if frame.empty:
        return None
    line = alt.Chart(frame).mark_line(interpolate='step-after', color='#4a4a8a').encode(
        x=_x(), y=alt.Y('stale_minutes:Q', title='min behind'))
    points = alt.Chart(frame).mark_point(size=45, filled=True).encode(
        x='at:Q', y='stale_minutes:Q',
        color=alt.Color('source:N', title=None, legend=alt.Legend(orient='top')),
        tooltip=['source:N', 'last_synced_at:N', 'samples_written:Q'])
    return (line + points).properties(height=LANE_HEIGHT['sync'])


def ema_lane(day):
    slots = tlf.slot_gridlines(day)
    grid = alt.Chart(slots).mark_rule(color='#e0e0e0').encode(x='at:Q')

    emas = tlf.ema_frame(day)
    reminders = tlf.reminder_frame(day)
    layers = [grid]
    if not emas.empty:
        layers.append(alt.Chart(emas).mark_point(size=110, filled=True).encode(
            x=_x(), y=alt.value(18),
            shape=alt.Shape('ema_type:N', title=None, legend=alt.Legend(orient='top')),
            color=alt.Color('completeness:Q', title='completeness',
                            scale=alt.Scale(domain=[0, 1], scheme='greens'),
                            legend=alt.Legend(orient='top')),
            tooltip=['ema_type:N', 'status:N', 'slot:Q', 'answered:Q',
                     'served:Q', 'completeness:Q', 'missing_signal:N']))
    if not reminders.empty:
        layers.append(alt.Chart(reminders).mark_tick(
            thickness=2, size=14, color='#fdae61').encode(
            x='at:Q', y=alt.value(44), tooltip=['slot:Q']))
    return alt.layer(*layers).properties(height=LANE_HEIGHT['ema'])


def decision_lane(day):
    frame = tlf.decision_frame(day)
    if frame.empty:
        return None
    return alt.Chart(frame).mark_point(size=90, filled=True).encode(
        x=_x(), y=alt.Y('outcome:N', title=None, sort=list(OUTCOME_COLORS)),
        color=alt.Color('outcome:N', title=None,
                        scale=alt.Scale(domain=list(OUTCOME_COLORS),
                                        range=list(OUTCOME_COLORS.values())),
                        legend=None),
        tooltip=['outcome:N', 'trigger_reason:N', 'observed_mssd:Q', 'threshold:Q',
                 'threshold_source:N', 'randomization_draw:Q', 'decision_point_id:N'],
    ).properties(height=LANE_HEIGHT['decision'])


def prompt_lane(day):
    frame = tlf.prompt_frame(day)
    if frame.empty:
        return None
    bars = alt.Chart(frame[frame['received'].notna()]).mark_bar(height=8).encode(
        x=_x(), x2='received:Q', color=alt.value('#4a4a8a'),
        tooltip=['delivery_status:N', 'lag_seconds:Q', 'platform:N',
                 'app_state:N', 'engagement:N'])
    marks = alt.Chart(frame).mark_point(size=90, filled=True).encode(
        x='at:Q',
        shape=alt.Shape('engagement:N', title=None, legend=alt.Legend(orient='top')),
        color=alt.Color('delivery_status:N', title=None, legend=alt.Legend(orient='top')),
        tooltip=['delivery_status:N', 'delivery_error:N', 'engagement:N'])
    return (bars + marks).properties(height=LANE_HEIGHT['prompt'])


def mssd_lane(day):
    frame = tlf.mssd_frame(day)
    if frame.empty:
        return None
    observed = alt.Chart(frame).mark_line(
        interpolate='step-after', point=True, color='#2166ac').encode(
        x=_x(), y=alt.Y('observed_mssd:Q', title='MSSD'),
        tooltip=['observed_mssd:Q', 'threshold:Q', 'threshold_source:N', 'trigger_reason:N'])
    # Null threshold breaks the line rather than dropping it to zero: the engine
    # having no threshold yet is a real state, not a value of nothing.
    threshold = alt.Chart(frame[frame['threshold'].notna()]).mark_line(
        interpolate='step-after', strokeDash=[4, 3], color='#b2182b').encode(
        x='at:Q', y='threshold:Q')
    layers = [observed, threshold]
    flagged = frame[frame['unexplained']]
    if not flagged.empty:
        layers.append(alt.Chart(flagged).mark_point(
            shape='triangle-up', size=160, filled=True, color='#d73027').encode(
            x='at:Q', y='observed_mssd:Q'))
    return alt.layer(*layers).properties(height=LANE_HEIGHT['mssd'])


def _chart_or_note(chart, note):
    """Draw the lane, or say why it is empty.

    A statement, never a ternary expression: Streamlit's magic rewrites a bare
    expression into st.write(), and st.altair_chart returns a DeltaGenerator, so
    `a() if c else b()` on its own line dumps the DeltaGenerator docstring into
    the page.
    """
    if chart is None:
        st.caption(note)
    else:
        st.altair_chart(chart)


def render_day(day):
    st.markdown(f"### {day['local_date']} · study day {day['study_day']}")

    _lane_title('Wear', 'Shaded band is the 08:00-22:00 waking window. Only gaps '
                        'inside it count against wear.')
    if day['wear']['has_data']:
        st.altair_chart(wear_lane(day))
    else:
        st.caption('No heart-rate samples for this day. Nothing was delivered, '
                   'which is not the same as the watch not being worn.')

    _lane_title('Sync', 'How far behind the sync clock had fallen. A rising line '
                        'is an outage in progress; a wear gap underneath it is a '
                        'delivery failure, not non-wear.')
    chart = sync_lane(day)
    if chart is not None:
        st.altair_chart(chart)
    else:
        st.caption('No sync history recorded for this participant. Sync history '
                   'only exists from the day WearableSync was deployed.')

    _lane_title('Check-ins and reminders',
                'Gridlines are the six slot boundaries. Ticks below are reminders.')
    st.altair_chart(ema_lane(day))

    _lane_title('Decision points')
    _chart_or_note(decision_lane(day), 'No decisions this day.')

    _lane_title('Delivered prompts', 'Bar length is push-to-receipt lag.')
    _chart_or_note(prompt_lane(day), 'No prompts delivered.')

    _lane_title('Volatility', 'Dashed line is the threshold the engine actually '
                              'compared against. A red triangle is a point above '
                              'threshold that was not made eligible.')
    _chart_or_note(mssd_lane(day), 'No decision points to trace.')

    grid = tlf.completeness_grid(day)
    if not grid.empty:
        _lane_title('Item completeness')
        st.altair_chart(
            alt.Chart(grid).mark_rect(stroke='#ffffff', strokeWidth=1).encode(
                x=alt.X('sub_item_id:N', title=None,
                        axis=alt.Axis(labelAngle=-45, labelFontSize=9)),
                y=alt.Y('ema:N', title=None),
                color=alt.Color('state:N', title=None,
                                scale=alt.Scale(domain=list(STATE_COLORS),
                                                range=list(STATE_COLORS.values())),
                                legend=alt.Legend(orient='top')),
                tooltip=['ema:N', 'sub_item_id:N', 'state:N'],
            ).properties(height=alt.Step(18)))
        if grid['missing_signal'].any():
            st.error('A check-in on this day is missing B1 or B2. Those feed the '
                     'volatility calculation, so it produced no decision point at '
                     'all. That is a hole in the trial, not a data-quality note.')
    st.divider()


def render_funnel(payload):
    frame = tlf.funnel_frame(payload)
    st.altair_chart(
        alt.Chart(frame).mark_bar(color='#4a4a8a').encode(
            x=alt.X('n:Q', title='prompts'),
            y=alt.Y('label:N', title=None, sort=list(frame['label'])),
            tooltip=['label:N', 'n:Q', 'dropped:Q', 'retained:Q'],
        ).properties(height=alt.Step(26)))

    left, middle, right = st.columns(3)
    with left:
        st.caption('By platform')
        st.dataframe(pd.DataFrame(payload['by_platform']), hide_index=True,
                     width='stretch')
    with middle:
        st.caption('By app state')
        st.dataframe(pd.DataFrame(payload['by_app_state']), hide_index=True,
                     width='stretch')
    with right:
        st.caption('Delivery errors')
        errors = pd.DataFrame(payload['errors'])
        if errors.empty:
            st.caption('None recorded.')
        else:
            st.dataframe(errors, hide_index=True, width='stretch')


def main():
    st.title('Participant timeline')

    with st.sidebar:
        st.header('View')
        try:
            roster = client.grid('slots_covered')
        except client.MonitorError as exc:
            st.error(str(exc))
            return
        labels = {row['participant_id']: row['user_id'] for row in roster['rows']}
        if not labels:
            st.warning('No participants yet.')
            return

        carried = st.session_state.get('stage3_user_id')
        names = list(labels)
        index = next((i for i, name in enumerate(names)
                      if labels[name] == carried), 0)
        chosen = st.selectbox('Participant', names, index=index)
        user_id = labels[chosen]

        days = st.slider('Days per screen', 1, 14, 7)
        # An alert deep-link carries the day that offended, which is the day
        # worth looking at rather than today.
        linked = st.session_state.pop('stage3_date', None)
        default_end = pd.Timestamp(linked).date() if linked else pd.Timestamp.now().date()
        end = st.date_input('Ending on', value=default_end)
        if st.button('Refresh now', width='stretch'):
            st.cache_data.clear()

    try:
        payload = client.timeline(user_id, local_date=str(end), days=days)
        funnel = client.funnel(user_id)
    except client.MonitorError as exc:
        st.error(str(exc))
        return

    st.subheader('Delivery funnel')
    st.caption('Whole study, this participant. Each stage is a subset of the one '
               'above it.')
    render_funnel(funnel)
    st.divider()

    for day in payload['days'] if days > 1 else [payload]:
        render_day(day)


if __name__ == '__main__':
    main()
