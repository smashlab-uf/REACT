# Monitoring prototype (Stages 2 and 3)

Two Streamlit pages over the monitoring API:

- **Participant grid** — the daily RA view: who needs a phone call today.
- **Participant timeline** — one participant, one day per strip: what actually
  happened to this person.

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

## The timeline

One participant, one day per strip, every lane on the same 00:00 to 24:00 local
clock so events line up vertically. That alignment is the point: **a wear gap
under a flat sync trace is a data-delivery failure, and the same gap under a live
sync trace is genuine non-wear.**

| Lane | Reads |
|---|---|
| Wear | minute coverage from heart-rate samples, gaps over 2 h labelled, waking window shaded |
| Sync | how far behind the sync clock had fallen, stepped, coloured by ingestion or client |
| Check-ins | submissions on the six-slot grid, filled by completeness, reminders as ticks beneath |
| Decision points | one mark per decision, by outcome: below threshold, cooldown, cap, eligible sent or not |
| Delivered prompts | push to receipt as bar length, so delivery lag reads as distance |
| Volatility | observed MSSD against the threshold the engine actually compared it to |

Below the strips sit the whole-study delivery funnel and, per day, the item
completeness matrix. A check-in missing B1 or B2 is called out in red: those feed
the volatility calculation, so a submission without them produces no decision
point at all. That is a hole in the trial, not a data-quality note.

### Two things the timeline is honest about

**Nothing writes the sync clock yet, so the lane is blank.** The mobile client is
the intended writer, through `PATCH /wearable/{id}/`. It does not call it: the app
has no wearable code at all. `ingest_wearable_data` is still a stub. So
`last_synced_at` has no writer in production and `WearableSync` stays empty.

That absence is reported as unmeasurable rather than as failure, which matters
more than it sounds. `sync_stale` measures from `last_sync_at or enrolled_at`, so
scoring it anyway would turn every participant warning at 24 hours and critical
at 72, permanently, and pin an identical 4 points of risk on all forty. Instead
one cohort alert says sync reporting is not implemented, no participant alert
fires, and the risk score's sync term contributes nothing until a writer lands.

**A threshold is either recorded or replayed, and the difference matters.**
`JITAILog.threshold_source` is `engine` when the engine wrote what it compared
against, and `reconstructed` when `backfill_thresholds` replayed it. The red
marker accusing the engine of missing an eligible decision point fires only on
`engine` rows: a replayed number can disagree for reasons that are not the
engine's fault, so it is never grounds for the accusation.

## What the timeline deliberately omits

Response latency and the 30-minute in-window flag. An EMA row is only created
when a participant submits, and `sent_at` and `responded_at` are stamped from the
same clock read at that moment, so every latency is about zero and every response
is in-window. Charting them would draw a flawless compliance curve that means
nothing.

The one real anchor is push to linked post-prompt check-in, measured against the
two-hour outcome window. It is labelled **post-prompt response time**, never EMA
latency, because it covers only prompted check-ins and says nothing about
scheduled ones.

## Files

| File | Role |
|---|---|
| `app.py` | entry page and shared config |
| `pages/1_Participant_grid.py` | Stage 2 |
| `pages/2_Participant_timeline.py` | Stage 3 |
| `client.py` | the endpoints, cached for 60 s |
| `frame.py` | grid JSON to one participant-day frame |
| `timeline.py` | timeline JSON to one frame per lane |
