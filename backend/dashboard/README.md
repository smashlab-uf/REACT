# `dashboard` app

Read-only feasibility and integrity monitoring for REACT, served at
`/api/monitor/*`. This app lives at **`backend/dashboard/`**, beside `app` and
`project`, so the `web`, `worker` and `beat` processes import it with no
`sys.path` help — all three run with `backend/` as their working directory.
Its Django app label is `dashboard`, which is what keeps the `dashboard_*`
table names stable; do not change the `INSTALLED_APPS` entry to a dotted path.

It is not a general study-data browser (that's still Django Admin); it exists
to answer one question every day of the field period: *which participants need
a phone call, and is the pipeline itself healthy.*

```
backend/dashboard/
  data/                # pure compute layer, no views, no Celery
    config.py           # single source of truth for every study constant
    windows.py           # Eastern-time day/slot boundaries shared with app/tasks.py
    daily.py             # per-participant-per-day metrics -> MetricsDaily
    participant.py       # per-participant rollup + risk score -> MetricsParticipant
    cohort.py             # cohort-wide benchmarks + MRT integrity -> MetricsCohort
    alerts.py             # fire/update/resolve rules -> Alert
    timeline.py            # the one raw-table reader, for the timeline endpoint
    item_bank.py            # frozen EMA item bank, for completeness scoring
  models.py            # the four derived tables (see below)
  serializers.py       # DRF serializers for the four tables
  views.py              # /api/monitor/* APIViews
  tasks.py               # recompute_metrics(), shared by Celery and the mgmt command
  management/commands/
    recompute_metrics.py     # runs recompute_metrics() synchronously
    backfill_thresholds.py    # replays build_decision_frame for JITAILog.threshold_at_decision
    backfill_withdrawals.py    # backdates first_seen_not_enrolled_at from django_admin_log
    dump_item_bank.py           # freezes EMA_ITEM_BANK to a versioned JSON file
```

The Django app here computes and serves the metrics. The **Streamlit
prototype** that visualizes them lives separately, under
`analytics/monitor_app/` — see [Streamlit prototype](#streamlit-prototype-analyticsmonitor_app)
below.

---

## The compute pipeline

`dashboard.tasks.recompute_metrics()` is the single entry point, run every
600 s by `dashboard.tasks.recompute_monitoring_metrics` (the Celery Beat task)
and on demand by `python manage.py recompute_metrics`. For every enrolled
participant it:

1. Resolves `withdrawal_at` (needed before daily rows, since `is_active_day`
   depends on it).
2. Upserts one `MetricsDaily` row per study day in the requested window
   (`data/daily.py:compute_daily`), keyed on `(user, study_day)`.
3. Upserts that participant's `MetricsParticipant` rollup
   (`data/participant.py:compute_participant`).
4. Writes one `MetricsCohort` snapshot per phase filter
   (`data/cohort.py:compute_cohort`).
5. Runs `data/alerts.py:evaluate_alerts` to open, update, or resolve `Alert`
   rows.
6. Re-scores every participant's `risk_score` (after alerts, so a score
   reflects this run rather than the last one).
7. Prunes `MetricsCohort` snapshots older than
   `METRICS_COHORT_RETENTION_DAYS`.

The default window is a **trailing 3-day recompute**
(`METRICS_RECOMPUTE_TRAILING_DAYS`), sized to absorb Labfront batch lag without
rereading the whole study every 10 minutes. `--all` (every study day 0 to
today) is for the initial fill or after a metric definition changes; `--from-day`
reprocesses everything from a given study day forward.

One participant's bad data cannot stop the run: `recompute_metrics` catches
and logs per-user exceptions the same way `evaluate_jitai_triggers` isolates
its per-user work, and still writes the cohort snapshot and alerts for
everyone else.

`data/daily.py` mirrors — rather than imports — the metric definitions in
`analytics/scripts.py`, because that module pulls scipy/statsmodels/matplotlib/
seaborn, none of which are in `backend/requirements.txt`, so the Celery worker
can't have them. `analytics/reconcile_monitoring.py` is what keeps the two
implementations in step; run it after changing any metric definition.

### Three invariants worth preserving in any change

- **Structural null is not zero.** A participant-day outside the active range
  (pre-enrollment, past the last study day, after withdrawal) is either absent
  or `is_active_day=False` with every metric `NULL`. A day inside the range
  with no activity is `0`. Every metric column on `MetricsDaily` is nullable
  for exactly this reason, and the grid endpoint (`MonitorGridView`) preserves
  the distinction all the way to the wire.
- **Rates are suppressed on thin denominators.** A rate on `MetricsCohort` or
  `MetricsParticipant` is only emitted above `RATE_MIN_PARTICIPANTS` (10) and
  `RATE_MIN_UNITS` (30); below that the payload carries raw counts and a null
  rate. Phase 1 (n≈5) is therefore always counts, never percentages.
- **A benchmark with no source is marked unmeasurable**, never reported as
  zero (`UNMEASURABLE_BENCHMARKS` in `data/config.py`).

`backend/dashboard/data/config.py` is the single authority for every protocol
constant — the notification window, the daily caps, the JITAI cooldown, the
threshold quantile, the randomization probabilities, the benchmarks, the
timezone. `app/tasks.py`, `app/views.py` and `analytics/scripts.py` all import
from it. Never reintroduce a literal for any of these values elsewhere.

---

## The `dashboard_*` tables

Four tables, all defined in `backend/dashboard/models.py`. They hold no collected
data — everything in them is derived from `app`'s tables and can be dropped
and rebuilt in full with `manage.py recompute_metrics --all`.

### `MetricsDaily`

One row per participant per study day (`unique(user, study_day)`), recomputed
on the trailing window described above. This is the densest table and backs
the Stage 2 heatmap directly.

Grouped fields:

| Group | Fields | Notes |
|---|---|---|
| Identity | `user`, `study_day`, `local_date`, `is_run_in`, `is_active_day`, `item_bank_version` | `item_bank_version` records which frozen `EMA_ITEM_BANK` export `completeness_mean` was scored against, so a mid-study item edit can't silently rewrite past completeness. |
| EMA counts | `ema_scheduled_n`, `ema_jitai_n`, `ema_post_prompt_n` | Split by `EMA.ema_type`. |
| Check-in slots | `slots_expected`, `slots_covered`, `slots_reminded_uncovered`, `slots_silent` | The compliance denominator is the day's fixed six slots, not EMA rows — EMA rows only exist once a participant submits. `covered` and `reminded_uncovered` are disjoint; `silent` means the participant was never even reminded (a scheduler failure, not non-compliance). |
| Reminders | `reminders_sent`, `reminders_per_checkin_median` | |
| Completeness | `completeness_mean`, `ema_missing_b1b2_n` | Answered-over-askable, 0–1; askable is reconstructed against the frozen item bank because the served sub-item set isn't persisted per row until `served_sub_item_ids` is populated for that EMA. Missing B1/B2 breaks the MSSD calculation for that submission. |
| JITAI decisions | `decision_points_n`, `eligible_n`, `sent_n`, `delivered_n`, `cap_hit`, `min_gap_min`, `cooldown_violations_n`, `runin_violation_n` | `runin_violation_n` is expected to be non-zero until `evaluate_jitai_triggers` gets a run-in gate — a known engine gap, deliberately surfaced rather than patched around here. |
| Engagement | `prompt_opened_n`, `prompt_acted_n`, `prompt_dismissed_n`, `outcome_captured_n` | From `EngagementLog`. |
| Wear / sync | `wear_valid_pct`, `wear_gap_pct`, `gaps_gt2h_n`, `max_gap_min`, `hr_minutes_valid`, `last_sync_age_h_eod`, `clock_skew_p95_ms` | Wear percentages are fractions of the 840-minute waking window (08:00–22:00 Eastern). `clock_skew_p95_ms` is signed: a device clock running ahead of the server is a real, diagnostic condition, not an error. |
| Delivery | `delivery_failures_n` | |

### `MetricsParticipant`

One row per participant (`OneToOne` on `user`) — the rollup driving the Stage 2
right rail and the row ordering on the grid.

- `phase` — one of `pre_enrollment`, `run_in`, `mrt`, `complete`, `withdrawn`.
- `first_seen_not_enrolled_at` — a withdrawal proxy: the first `computed_at` at
  which a recompute observed `is_enrolled=False` for a previously-enrolled
  participant. Resolution equals the polling interval; `backfill_withdrawals`
  backdates it once from `django_admin_log` for withdrawals that predate
  monitoring.
- `risk_score` / `risk_components` — see [Risk score](#risk-score) below.
- `slot_coverage_rate` / `_num` / `_den`, `prompt_response_rate` / `_num` /
  `_den`, `wear_rate` / `_num` / `_den` — cumulative benchmark rates, each
  stored with its numerator and denominator so a suppressed rate can still
  render as raw counts.

### `MetricsCohort`

One row per compute run per `phase_filter` (`all`, `phase1`, `phase2`) —
append-only, pruned after `METRICS_COHORT_RETENTION_DAYS`. Backs Stage 1.

- `benchmarks` — one JSON document: `{name: {value, wilson_low, wilson_high,
  numerator, denominator, suppressed, measurable, ...}}`. Held as a single
  document because the row is always read whole, never one benchmark at a
  time.
- `series_14d` — pre-aggregated daily series, so a 60-second poll never
  re-aggregates `MetricsDaily` itself. **Every rate in it goes through the same
  suppression as the headline figure.** A sparkline is a sequence of rates, so
  publishing one from five participants while withholding the tile above it
  would let a trend in through the back door. Raw counts stay alongside, so the
  tile can still print them.
- `integrity` — the MRT strip: eligibility, send rate, randomization audit,
  cap-hit, cooldown, outcome capture. Restricted to **weeks 2–5**
  (`is_run_in=False`), because run-in days carry no intervention and folding
  them in would dilute every rate with days the protocol never meant to
  randomize on. Unlike benchmarks these gauges are **never suppressed**: Phase 1
  is exactly when you want to catch the engine misbehaving.
- `funnel` — enrollment stages. `consented` carries `measurable: false` because
  nothing in the schema records consent; `User` has `enrolled_at` and
  `is_enrolled` and nothing before them, and reporting enrollment as consent
  would invent a number.
- `decision_points_n`, `eligible_n`, `sent_n`, `delivered_n`,
  `cooldown_violations_n`, `runin_violations_n`, `cap_hit_days`,
  `delivery_failures_n` — cohort-wide counts over *all* active days, kept
  separate from `integrity` which is weeks 2–5 only.

Two things in the cohort layer are worth knowing about:

- **The KS test is hand-rolled** (`data/cohort.py:ks_uniform`). scipy is not in
  `backend/requirements.txt` and the Celery worker cannot have it, so the
  statistic and the asymptotic p-value are computed directly and held to
  `scipy.stats.kstest` by a test, since scipy *is* available in the analytics
  environment. Agreement is exact on the statistic and ~1e-15 on the p-value.
- **The wear field names are a trap.** `wear_valid_pct` is
  `(840 − gaps>2h) / 840`, which is what the 0.80 benchmark scores;
  `wear_gap_pct` is the complementary long-gap fraction and is *not* what either
  wear tile shows. Minute-level coverage is `hr_minutes_valid / 840`. A day high
  on the first and low on the last is a watch worn in short bursts, flagged as
  `burst_charging_days`.

### `Alert`

Open/resolved lifecycle rows, one per firing condition, written by
`data/alerts.py:evaluate_alerts`.

- `user` is nullable — a cohort-level alert (e.g. "pipeline hasn't run") has no
  user. An alert about the *system* is cohort-scoped on purpose: `runin_violation`
  and `sync_stale`-with-no-writer are facts about the engine and the pipeline, and
  raising them per participant put an identical open critical on all 40, which
  flattened `risk_score` and buried the alerts view. The per-participant detail
  stays in `MetricsDaily`.
- `severity` is a three-tier ladder, `warning` < `high` < `critical`, ordered by
  what is at stake rather than how long it has been true. **critical**: trial
  integrity is already compromised or the whole pipeline is down. **high**: one
  participant's data is being lost now and will keep being lost until someone
  acts. **warning**: worth watching, not worth a call today. `SEVERITY_RANK`
  orders a response (sorting on the column puts `critical` before `warning` only
  by accident of spelling); `ACTIONABLE_SEVERITIES` is what `risk_score` reads,
  so both `critical` and `high` move it.
- A rule whose signal has **no writer** emits one cohort alert saying so, never
  one alert per participant. `sync_stale` works this way today: nothing writes
  `last_synced_at`, so measuring staleness from enrollment would turn everyone
  critical 72 h in, permanently.
- Two partial unique constraints (`uniq_open_alert_per_user_rule`,
  `uniq_open_cohort_alert_per_rule`) make re-firing an already-open alert a
  no-op instead of a duplicate row; two constraints are needed because SQL
  treats `NULL` user values as distinct from each other.
- `payload` carries whatever the rule needs to explain itself (thresholds
  crossed, counts observed).

### Risk score

Computed once in `data/participant.py`, not duplicated in any view, so the
grid's row order and the API always agree. Six weighted terms
(`RISK_WEIGHTS`) over the trailing seven *active* days, capped at 47, with
each term's point contribution stored in `risk_components` so the ordering is
arguable rather than opaque. Weights are a starting point meant to be tuned
after Phase 1.

`risk_score` is **null, not zero**, for anyone the study isn't currently
asking anything of — pre-enrollment, complete, or withdrawn — since every term
would otherwise read as maximally bad for them and they'd dominate the top of
the call list.

---

## `/api/monitor/*` endpoints (`views.py`)

All gated by `IsAdminUserOrDashboardAPIKey` (staff session or
`X-Dashboard-API-Key`), not `IsAuthenticated`.

| Endpoint | Reads | Purpose |
|---|---|---|
| `GET /api/monitor/cohort` | `MetricsCohort` | Latest snapshot for a `?phase=` filter: benchmarks, Wilson bounds, 14-day series. Returns `{"available": false, ...}` rather than a 404 or an empty 200 when nothing's been computed yet. |
| `GET /api/monitor/grid` | `MetricsParticipant`, `MetricsDaily`, `Alert` | One row per participant, one cell per study day, for a `?metric=` in `MetricDaily`'s numeric fields. Every overlay (run-in shading, alert dot, silent-slot slash) travels with the same response so the client never fetches per toggle. |
| `GET /api/monitor/participant/{id}` | `MetricsParticipant`, `MetricsDaily`, `Alert` | One participant's full rollup, every daily row, and open alerts. |
| `GET /api/monitor/participant/{id}/timeline` | `EMA`, `CheckinReminder`, `JITAILog`, `EngagementLog`, `HeartRateSample`, `WearableSync` (raw tables) | The **one** endpoint that reads raw tables rather than the derived ones, and only for one participant, bounded to `?days=1..14`. Assembles wear/sync/decision/engagement lanes for the Stage 3 timeline. |
| `GET /api/monitor/participant/{id}/funnel` | `JITAILog`, `EngagementLog` (via `data/timeline.py`) | One participant's whole-study delivery waterfall, aggregated in the DB rather than looped per day. |
| `GET /api/monitor/alerts` | `Alert` | Open alerts with severity counts, optional `?severity=` filter. |

---

## Streamlit prototype (`analytics/monitor_app/`)

A local-only Streamlit multipage app that visualizes the `/api/monitor/*`
endpoints above over plain HTTP. It holds **no database credential and no
Django import** — everything it draws is something the API can already serve,
so it doubles as a live check on those endpoints. Nothing deploys it; it lives
under `analytics/`, separate from this Django app.

```
analytics/monitor_app/
  app.py                          # entry page + shared config (base URL, API key)
  client.py                        # thin wrapper over the six endpoints, cached 60s
  frame.py                          # /api/monitor/grid JSON -> one participant-day DataFrame
  timeline.py                        # /api/monitor/participant/{id}/timeline JSON -> one frame per lane
  pages/1_Participant_grid.py         # Stage 2: the daily "who needs a call" view
  pages/2_Participant_timeline.py      # Stage 3: one participant, one day per strip
  pages/0_Cohort_board.py              # Stage 1: the PI's weekly one-screen board
```

Run it with the backend up and its metric tables already computed at least
once (`python manage.py recompute_metrics --all`), then from `analytics/`:

```bash
export MONITOR_BASE_URL=http://localhost:8000
export DASHBOARD_API_KEY=...   # must equal the backend's DASHBOARD_API_KEY — never export API_KEY here
streamlit run monitor_app/app.py
```

**Page 1 — Participant grid (Stage 2).** A heatmap, one row per participant
ordered by `risk_score` descending, columns = study day 0–34 (with a toggle to
calendar date, the only way to see a cohort-wide outage — a Celery failure
hits everyone on the same calendar date, not the same study day). The
selected metric is colored against its `BENCHMARKS` value fetched live from
`/api/monitor/cohort`; overlays drawn on every metric are a blank cell
(outside the participant's active window), a shaded cell (run-in baseline,
days 0–6), a dot (an open alert fired that day), and a slash (a slot went
silent). Below the grid, a three-way split — `covered` / `reminded` / `silent`
— classifies every check-in slot over the trailing week; `silent` is a
scheduler failure (dead token, outcome-window guard, Celery down), never
non-compliance, and nobody should be called about it. The right rail shows
the selected participant's phase, study day, risk breakdown, cumulative
benchmark rates with raw counts, sync/EMA freshness, and open alerts.

**Page 2 — Participant timeline (Stage 3).** One participant, one day per
strip, every lane sharing the same 00:00–24:00 local clock so events line up
vertically — the point being that a wear gap under a flat sync trace is a
data-delivery failure, while the same gap under a live sync trace is genuine
non-wear. Lanes: wear (minute coverage from HR samples, 2h+ gaps labelled),
sync (how far behind the sync clock had fallen), check-ins (six-slot grid
filled by completeness, reminders as ticks), decision points (one mark per
JITAI decision, by outcome), delivered prompts (push→receipt as bar length),
and volatility (observed MSSD against the threshold actually compared
against). Below the strips: the whole-study delivery funnel and the per-day
item-completeness matrix (a check-in missing B1/B2 is called out in red,
since it produces no decision point at all — a hole in the trial, not a
data-quality note).

Two things the timeline is deliberately honest about:

- **Nothing writes the sync clock yet, so the lane is blank.**
  `WearableDevice.last_synced_at` is a single mutable column with no audit
  trail, which is why `WearableSync` exists. The mobile client is the intended
  writer, through `PATCH /wearable/{id}/`, but the app has no wearable code and
  `ingest_wearable_data` is still a stub, so nothing advances it in production.
  Days with no history render as a blank lane, never a flat line at zero.
- **A threshold is either recorded or replayed.** `JITAILog.threshold_source`
  is `engine` when the live decision wrote what it compared against, and
  `reconstructed` when `backfill_thresholds` replayed it. The marker accusing
  the engine of missing an eligible decision point fires only on `engine`
  rows — a replayed number can disagree for reasons that aren't the engine's
  fault.

Deliberately omitted: EMA response latency and the 30-minute in-window flag
(an EMA row only exists once submitted, so `sent_at`/`responded_at` would draw
a flawless, meaningless compliance curve). The one real latency shown is
**post-prompt response time** — push to the linked post-prompt check-in,
against the 2-hour outcome window — never called "EMA latency" since it only
covers prompted check-ins.

Full detail (every overlay, every lane, every suppression rule) is documented
in `analytics/monitor_app/README.md`; this section is the pointer from the
Django side that computes what it renders.

---

## Management commands

```bash
python manage.py recompute_metrics                 # trailing window (what the periodic task does)
python manage.py recompute_metrics --all            # every study day — initial fill / after a metric definition change
python manage.py recompute_metrics --user 12 --days 2

python manage.py backfill_withdrawals --dry-run     # backdate first_seen_not_enrolled_at from django_admin_log
python manage.py backfill_thresholds --dry-run      # fill JITAILog.threshold_at_decision on pre-migration-0046 rows
python manage.py dump_item_bank --check             # fails if EMA_ITEM_BANK drifted from the frozen JSON
```

## Tests

```bash
python manage.py test dashboard --settings=project.test_settings
```
