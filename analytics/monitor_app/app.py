"""REACT study monitoring, entry page.

Two surfaces sit behind this: the participant grid, which answers "who needs a
phone call today", and the participant timeline, which answers "what actually
happened to this person". Both read the /api/monitor/* endpoints over HTTP and
hold no database credential.
"""

import sys
from pathlib import Path

if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import streamlit as st

from monitor_app import client

st.set_page_config(page_title='REACT · Monitoring', layout='wide')

st.title('REACT study monitoring')
st.caption(f'Reading {client.base_url()}')

st.markdown(
    """
**Participant grid** — the daily RA view. One row per participant ordered by
risk, one column per study day, with slot coverage, wear, prompts delivered and
item completeness on the cell colour. Start here.

**Participant timeline** — one participant, one day per strip. Wear, sync,
check-ins and reminders, decision points, delivered prompts and the volatility
trace, all on a shared clock so they line up. Open it when the grid says
something is wrong and you need to know what.
"""
)

st.divider()

if st.session_state.get('stage3_user_id'):
    st.info(f"Timeline selection carried from the grid: participant "
            f"{st.session_state['stage3_user_id']}.")

with st.expander('What the timeline deliberately does not show'):
    st.markdown(
        """
Response latency and the 30-minute in-window flag are absent on purpose. An EMA
row is only created when a participant submits, and `sent_at` and `responded_at`
are stamped from the same clock read at that moment. Every latency is therefore
about zero and every response is in-window. Charting them would produce a
flawless compliance curve that means nothing at all.

The one real anchor is the gap from a prompt's push to the linked post-prompt
check-in, measured against the two-hour outcome window. It is labelled
**post-prompt response time**, never EMA latency, because it applies only to
prompted check-ins and says nothing about the scheduled ones.
"""
    )
