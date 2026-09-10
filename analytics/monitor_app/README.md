# Participant grid (Stage 2)

A Streamlit prototype of the daily RA view: who needs a phone call today.

It reads the `/api/monitor/*` endpoints over HTTP and holds no database
credential and no Django import. Anything it renders is something the API can
already serve, so it doubles as a check on those endpoints.

## Running it

The backend has to be up, and its metric tables have to have been computed at
least once.

```bash
# terminal 1, from backend/
python manage.py recompute_metrics --all
python manage.py runserver 0.0.0.0:8000

# terminal 2, from analytics/
export MONITOR_BASE_URL=http://localhost:8000
export DASHBOARD_API_KEY=...          # must equal the backend's DASHBOARD_API_KEY
streamlit run monitor_app/app.py
```

`MONITOR_BASE_URL` defaults to `http://localhost:8000`. Both variables are read
from the environment only. Do not put the key in a file in this directory, do
not pass it on a command line, and do not commit it: a live `DASHBOARD_API_KEY`
is already sitting in this repo's history at
`1fdc6f3^:analytics/REACT-dashboard/README.md` and still needs rotating.

**`DASHBOARD_API_KEY` is the only key this needs. Do not export `API_KEY`.**
`/api/monitor/*` is reachable with the dashboard key alone, through
`APIKeyMiddleware.DASHBOARD_PREFIXES`. Exporting `API_KEY` switches the
middleware on for every other route, which used to break the dress rehearsal's
delivery receipts silently and fail about 97 backend tests on a healthy tree.
The test runner now blanks both keys during a test run, but the shell is still
the wrong place to leave `API_KEY` sitting.

## What it shows

**The heatmap.** One row per participant, ordered by risk score descending, with
the score and its largest term printed in the row label. Columns are study day 0
to 34 so participants at different calendar dates line up. The axis toggle
switches to calendar date, which is the only way to see a cohort-wide outage: a
Celery failure hits everyone on the same date, not the same study day.

Four metrics colour the cells. Slot coverage and wear diverge about their
preregistered benchmarks, which are fetched from `/api/monitor/cohort` rather
than typed here. The prompt cap comes from the grid payload. No protocol
constant lives in this package; `dashboard/data/config.py` remains the authority.

**Overlays**, drawn on every metric:

| Mark | Meaning |
|---|---|
| blank cell | outside the participant's window: before enrollment, past day 34, or after withdrawal |
| shaded cell | run-in baseline, days 0 to 6, where no prompts are expected |
| dot | an open alert that fired on that day |
| slash | at least one slot went silent |

A blank cell and a zero-valued cell are different facts and the layer never
collapses them. That is why a day outside the window draws no mark at all rather
than a dark one.

**The three-way split** under the grid classifies every slot over the trailing
week as covered, reminded, or silent.

- `covered` — at least one scheduled check-in landed in the slot.
- `reminded` — no check-in, but a reminder was sent. This is non-response.
- `silent` — no check-in and no reminder. **The participant was never asked.**

Silent is a scheduler failure, not non-compliance, and nobody should be called
about it. It means a null `push_token`, an intervention outcome window already
open when the reminder was due (the "one buzz at a time" guard in
`_maybe_send_reminder`), or Celery not running.

Reminder counts are useless as a rate denominator, because one reminder fires
per slot and only where the slot is still uncovered, so a compliant participant
accrues almost none. This classification is the only defensible use of
`CheckinReminder`.

**The right rail** shows the selected participant's phase, study day, days
remaining, risk breakdown, cumulative benchmark rates with their raw counts,
sync and EMA freshness, and open alerts.

## Risk score

Computed in `dashboard/data/participant.py`, not here, so the row order and the
API agree. Six weighted terms over the trailing seven active days, maximum 47,
with every term's point contribution stored alongside the total so the ordering
can be argued with. Weights are a starting point and are meant to be tuned after
Phase 1.

The score is null for anyone the study is not currently asking something of:
pre-enrollment, complete, or withdrawn. Those rows sort last rather than first,
because every term reads as maximally bad for a participant who is not in the
field.

## Files

| File | Role |
|---|---|
| `client.py` | the five endpoints, cached for 60 s |
| `frame.py` | grid JSON to one participant-day frame that every chart reads |
| `app.py` | layout and charts |
