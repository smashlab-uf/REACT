"""Compose-box telemetry: how participants use the free-text box on the home screen.

Exploratory, not a protocol endpoint. It is the one behavioral signal in the online
domain that is not self-report, so the PI report shows it descriptively.

What the rows mean (mobile/src/telemetry/composeTelemetry.ts):

- A *visit* is the compose box holding focus: session_start on focus, session_end on
  blur, backgrounding or the EMA modal opening. Both share a per-visit session_id.
  Older app builds never send them, so a participant with no session_start on record
  has visits marked unmeasured rather than zero.
- A *draft* is keyed by its own session_id: draft_started on the first keystroke,
  then draft_submitted or draft_deleted. A draft with neither inside 24 hours is
  abandoned; one younger than that is pending and not scored. Abandonment is derived
  here because a draft that loses focus can be resumed.
- keystroke_count and delete_count are character-length deltas (autoCorrect is off,
  so only a paste inflates them); time_on_compose is milliseconds from the first
  keystroke to submit or delete. Only these numeric keys and had_draft are read.

Timing uses occurred_at, the client clock, because the offline queue can hold an
event back for a long time before recorded_at.
"""

import random
from collections import defaultdict
from datetime import datetime, time, timedelta
from statistics import mean, median

from app.models import PhoneTelemetry
from dashboard.data.config import (
    NOTIFICATION_WINDOW_END_HOUR,
    NOTIFICATION_WINDOW_START_HOUR,
    OUTCOME_CHECK_IN_DELAY_MINUTES,
    OUTCOME_WINDOW_HOURS,
    PARTICIPANT_TZ,
    RATE_MIN_PARTICIPANTS,
    RATE_MIN_UNITS,
    STUDY_DAYS,
)
from dashboard.data.windows import elapsed, participant_day_bounds, study_day_for

VISIT_START, VISIT_END = 'session_start', 'session_end'
DRAFT_STARTED, DRAFT_SUBMITTED, DRAFT_DELETED = 'draft_started', 'draft_submitted', 'draft_deleted'
TERMINAL = (DRAFT_SUBMITTED, DRAFT_DELETED)

SUBMITTED, DELETED, ABANDONED, PENDING = 'submitted', 'deleted', 'abandoned', 'pending'
ABANDON_AFTER = timedelta(hours=24)
WINDOW = timedelta(hours=OUTCOME_WINDOW_HOURS)
WINDOW_SPLIT = timedelta(minutes=OUTCOME_CHECK_IN_DELAY_MINUTES)
BLOCK = WINDOW

WINDOW_METRICS = ('opened', 'drafts', 'submitted', 'abandoned', 'keystrokes', 'visit_seconds')
HALF_METRICS = ('opened', 'drafts', 'submitted')
BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_SEED = 20260908


def _number(metadata, key):
    value = (metadata or {}).get(key)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def new_build_users(users):
    return set(
        PhoneTelemetry.objects.filter(user__in=users, event_type=VISIT_START)
        .values_list('user_id', flat=True).distinct()
    )


def compose_drafts(users, start_at, end_at, now):
    started = list(
        PhoneTelemetry.objects
        .filter(user__in=users, event_type=DRAFT_STARTED,
                occurred_at__gte=start_at, occurred_at__lt=end_at)
        .order_by('occurred_at', 'id')
    )
    terminals = defaultdict(list)
    for event in (
        PhoneTelemetry.objects
        .filter(user__in=users, event_type__in=TERMINAL,
                session_id__in={event.session_id for event in started})
        .order_by('occurred_at', 'id')
    ):
        terminals[(event.user_id, event.session_id)].append(event)

    drafts = []
    seen = set()
    for event in started:
        key = (event.user_id, event.session_id)
        if key in seen:
            continue
        seen.add(key)
        deadline = event.occurred_at + ABANDON_AFTER
        terminal = next(
            (t for t in terminals[key] if event.occurred_at <= t.occurred_at <= deadline), None)
        if terminal is not None:
            outcome = SUBMITTED if terminal.event_type == DRAFT_SUBMITTED else DELETED
        else:
            outcome = PENDING if now < deadline else ABANDONED
        metadata = terminal.metadata if terminal is not None else None
        seconds = _number(metadata, 'time_on_compose')
        drafts.append({
            'user_id': event.user_id,
            'session_id': event.session_id,
            'started_at': event.occurred_at,
            'ended_at': terminal.occurred_at if terminal is not None else None,
            'outcome': outcome,
            'keystrokes': _number(metadata, 'keystroke_count'),
            'deletions': _number(metadata, 'delete_count'),
            'seconds': seconds / 1000 if seconds is not None else None,
        })
    return drafts


def compose_visits(users, start_at, end_at):
    starts = list(
        PhoneTelemetry.objects
        .filter(user__in=users, event_type=VISIT_START,
                occurred_at__gte=start_at, occurred_at__lt=end_at)
        .order_by('occurred_at', 'id')
    )
    ends = {}
    for event in (
        PhoneTelemetry.objects
        .filter(user__in=users, event_type=VISIT_END,
                session_id__in={event.session_id for event in starts})
        .order_by('occurred_at', 'id')
    ):
        ends.setdefault((event.user_id, event.session_id), event)

    visits = []
    for event in starts:
        end = ends.get((event.user_id, event.session_id))
        seconds = None
        if end is not None and end.occurred_at >= event.occurred_at:
            seconds = elapsed(event.occurred_at, end.occurred_at).total_seconds()
        visits.append({
            'user_id': event.user_id,
            'opened_at': event.occurred_at,
            'seconds': seconds,
            'closed': end is not None,
            'had_draft_at_close': bool((end.metadata or {}).get('had_draft')) if end else None,
        })
    return visits


def _median(values):
    values = [value for value in values if value is not None]
    return median(values) if values else None


def compose_by_participant(users, start_at, end_at, now):
    on_new_build = new_build_users(users)
    drafts = compose_drafts(users, start_at, end_at, now)
    visits = compose_visits(users, start_at, end_at)

    drafts_by_user = defaultdict(list)
    for draft in drafts:
        drafts_by_user[draft['user_id']].append(draft)
    visits_by_user = defaultdict(list)
    for visit in visits:
        visits_by_user[visit['user_id']].append(visit)

    rows = []
    for user in users:
        own = drafts_by_user[user.pk]
        counts = {outcome: sum(1 for d in own if d['outcome'] == outcome)
                  for outcome in (SUBMITTED, DELETED, ABANDONED, PENDING)}
        scored = len(own) - counts[PENDING]
        finished = [d for d in own if d['outcome'] in (SUBMITTED, DELETED)]
        keystrokes = sum(d['keystrokes'] or 0 for d in finished)
        deletions = sum(d['deletions'] or 0 for d in finished)
        measured = user.pk in on_new_build
        own_visits = visits_by_user[user.pk]
        rows.append({
            'user_id': user.pk,
            'study_id': user.study_id,
            'visits_measured': measured,
            'opens': len(own_visits) if measured else None,
            'visit_seconds_median': _median(v['seconds'] for v in own_visits) if measured else None,
            'left_with_draft': (
                sum(1 for v in own_visits if v['had_draft_at_close']) if measured else None),
            'drafts': len(own),
            **counts,
            'submit_rate': counts[SUBMITTED] / scored if scored else None,
            'keystrokes_median': _median(d['keystrokes'] for d in finished),
            'deletions_median': _median(d['deletions'] for d in finished),
            'seconds_median': _median(d['seconds'] for d in finished),
            'deletion_ratio': deletions / keystrokes if keystrokes else None,
        })
    return rows


def _empty_metrics(measured):
    metrics = {name: 0 for name in WINDOW_METRICS}
    if not measured:
        metrics['opened'] = None
        metrics['visit_seconds'] = None
    for half in ('early', 'late'):
        for name in HALF_METRICS:
            metrics[f'{half}_{name}'] = None if (name == 'opened' and not measured) else 0
    return metrics


def _add(metrics, name, value, half=None):
    if metrics[name] is not None:
        metrics[name] += value
    if half is not None and name in HALF_METRICS and metrics[f'{half}_{name}'] is not None:
        metrics[f'{half}_{name}'] += value


def _finalise(metrics):
    for key in ('opened', 'early_opened', 'late_opened'):
        if metrics[key] is not None:
            metrics[key] = 1 if metrics[key] else 0
    return metrics


def _bucket_events(users, start_at, end_at, now):
    drafts = compose_drafts(users, start_at, end_at, now)
    visits = compose_visits(users, start_at, end_at)
    submitted = list(
        PhoneTelemetry.objects
        .filter(user__in=users, event_type=DRAFT_SUBMITTED,
                occurred_at__gte=start_at, occurred_at__lt=end_at)
        .values_list('user_id', 'occurred_at')
    )
    events = defaultdict(list)
    for visit in visits:
        events[visit['user_id']].append((visit['opened_at'], 'visit', visit))
    for draft in drafts:
        events[draft['user_id']].append((draft['started_at'], 'draft', draft))
    for user_id, moment in submitted:
        events[user_id].append((moment, 'submit', None))
    return events


def _apply(metrics, kind, payload, half):
    if kind == 'visit':
        _add(metrics, 'opened', 1, half)
        if metrics['visit_seconds'] is not None and payload['seconds'] is not None:
            metrics['visit_seconds'] += payload['seconds']
    elif kind == 'draft':
        _add(metrics, 'drafts', 1, half)
        if payload['outcome'] == ABANDONED:
            _add(metrics, 'abandoned', 1)
        if payload['outcome'] in (SUBMITTED, DELETED) and payload['keystrokes'] is not None:
            _add(metrics, 'keystrokes', payload['keystrokes'])
    else:
        _add(metrics, 'submitted', 1, half)


def compose_after_decisions(users, windows, now):
    """Compose use in the two hours after each decision in `windows`.

    `windows` is a list of (decision_id, user_id, decision_made_at, arm). An event
    goes to the most recent window that contains it, so overlapping windows never
    count the same event twice. A window still open at `now` is returned with
    closed=False and is not scored.
    """
    if not windows:
        return []
    on_new_build = new_build_users(users)
    start_at = min(window[2] for window in windows)
    end_at = max(window[2] for window in windows) + WINDOW
    events = _bucket_events(users, start_at, end_at, now)

    by_user = defaultdict(list)
    for window in sorted(windows, key=lambda window: window[2]):
        by_user[window[1]].append(window)

    scored = []
    for user_id, own in by_user.items():
        measured = user_id in on_new_build
        metrics = {window[0]: _empty_metrics(measured) for window in own}
        for moment, kind, payload in events[user_id]:
            containing = [w for w in own if w[2] <= moment < w[2] + WINDOW]
            if not containing:
                continue
            window = containing[-1]
            half = 'early' if moment < window[2] + WINDOW_SPLIT else 'late'
            _apply(metrics[window[0]], kind, payload, half)
        for decision_id, _, made_at, arm in own:
            scored.append({
                'decision_id': decision_id, 'user_id': user_id, 'arm': arm,
                'decision_made_at': made_at, 'closed': made_at + WINDOW <= now,
                **_finalise(metrics[decision_id]),
            })
    return scored


def background_blocks(users, windows, start, end, now):
    """The same metrics over 2-hour blocks of the 9-21 Eastern day that overlap no
    decision window: what compose use looks like when nothing was just decided."""
    on_new_build = new_build_users(users)
    period_start = participant_day_bounds(start)[0]
    period_end = participant_day_bounds(end)[1]
    events = _bucket_events(users, period_start, period_end, now)
    windows_by_user = defaultdict(list)
    for _, user_id, made_at, _ in windows:
        windows_by_user[user_id].append((made_at, made_at + WINDOW))

    blocks = []
    day = start
    while day <= end:
        for user in users:
            study_day = study_day_for(user, day)
            if study_day is None or not 0 <= study_day < STUDY_DAYS:
                continue
            hour = NOTIFICATION_WINDOW_START_HOUR
            while hour + OUTCOME_WINDOW_HOURS <= NOTIFICATION_WINDOW_END_HOUR:
                block_start = datetime.combine(day, time(hour), tzinfo=PARTICIPANT_TZ)
                block_end = block_start + BLOCK
                hour += OUTCOME_WINDOW_HOURS
                if block_end > now or any(
                        w_start < block_end and block_start < w_end
                        for w_start, w_end in windows_by_user[user.pk]):
                    continue
                metrics = _empty_metrics(user.pk in on_new_build)
                for moment, kind, payload in events[user.pk]:
                    if block_start <= moment < block_end:
                        half = 'early' if moment < block_start + WINDOW_SPLIT else 'late'
                        _apply(metrics, kind, payload, half)
                blocks.append({'user_id': user.pk, 'arm': 'background', **_finalise(metrics)})
        day += timedelta(days=1)
    return blocks


def _person_means(units, metric):
    per_person = defaultdict(list)
    for unit in units:
        if unit[metric] is not None:
            per_person[unit['user_id']].append(unit[metric])
    return {user_id: mean(values) for user_id, values in per_person.items()}


def _bootstrap_ci(differences, seed=BOOTSTRAP_SEED):
    rng = random.Random(seed)
    n = len(differences)
    resampled = sorted(
        mean(rng.choice(differences) for _ in range(n)) for _ in range(BOOTSTRAP_RESAMPLES))
    return (resampled[int(0.025 * BOOTSTRAP_RESAMPLES)],
            resampled[int(0.975 * BOOTSTRAP_RESAMPLES) - 1])


def compare_arms(scored, background=(), with_ci=False):
    closed = [unit for unit in scored if unit['closed']]
    arms = {arm: [unit for unit in closed if unit['arm'] == arm] for arm in ('A', 'B')}
    metrics = WINDOW_METRICS + tuple(
        f'{half}_{name}' for half in ('early', 'late') for name in HALF_METRICS)

    result = []
    for metric in metrics:
        sent = _person_means(arms['A'], metric)
        held = _person_means(arms['B'], metric)
        reference = _person_means(background, metric)
        paired = sorted(set(sent) & set(held))
        differences = [sent[user_id] - held[user_id] for user_id in paired]
        units = sum(1 for arm in arms.values() for unit in arm if unit[metric] is not None)
        publishable = len(paired) >= RATE_MIN_PARTICIPANTS and units >= RATE_MIN_UNITS
        ci = _bootstrap_ci(differences) if with_ci and publishable else (None, None)
        result.append({
            'metric': metric,
            'sent_mean': mean(sent.values()) if sent else None,
            'randomized_out_mean': mean(held.values()) if held else None,
            'background_mean': mean(reference.values()) if reference else None,
            'paired_participants': len(paired),
            'mean_difference': mean(differences) if differences else None,
            'difference_by_participant': dict(zip(paired, differences)),
            'ci_low': ci[0], 'ci_high': ci[1],
            'suppressed': not publishable,
        })
    return {
        'windows': {arm: len(units) for arm, units in arms.items()},
        'open_windows': sum(1 for unit in scored if not unit['closed']),
        'background_blocks': len(background),
        'metrics': result,
    }
