"""Eastern-time primitives shared by the monitoring layer and the Celery tasks.

Every day and hour boundary in REACT is evaluated in America/New_York. A Django
`__date` lookup truncates at the database level using the active timezone, which
is UTC here, so it silently ignores the Eastern conversion. Day bounds are
therefore always built here as aware datetimes and used with __gte/__lt.

study_day is 0-indexed, so study_day 0 is the protocol's "Day 1". Days 0 through
RUN_IN_DAYS-1 are the non-interventional run-in baseline.
"""

from datetime import datetime, time, timedelta, timezone as dt_timezone

from django.utils import timezone as django_timezone

from dashboard.data.config import (
    METRICS_RECOMPUTE_TRAILING_DAYS,
    NOTIFICATION_WINDOW_END_HOUR,
    NOTIFICATION_WINDOW_START_HOUR,
    PARTICIPANT_TZ,
    RUN_IN_DAYS,
    SCHEDULED_CHECK_IN_DAILY_CAP,
    STUDY_DAYS,
    WAKING_WINDOW_END_HOUR,
    WAKING_WINDOW_START_HOUR,
)


def participant_time(moment):
    return moment.astimezone(PARTICIPANT_TZ)


def elapsed(start, end):
    """Real elapsed time between two aware datetimes.

    Subtracting two datetimes that share a tzinfo object skips the UTC
    conversion and returns the wall-clock difference instead, so a plain
    `end - start` across the November DST fallback reports 24 hours for a day
    that really lasts 25. Converting both to UTC first is what makes any
    duration that can span a transition come out right.
    """
    return end.astimezone(dt_timezone.utc) - start.astimezone(dt_timezone.utc)


def elapsed_minutes(start, end):
    return elapsed(start, end).total_seconds() / 60


def today_local(now=None):
    return participant_time(now or django_timezone.now()).date()


def _at(local_date, hour):
    return datetime.combine(local_date, time(hour), tzinfo=PARTICIPANT_TZ)


def participant_day_bounds(local_date):
    """[midnight, next midnight) Eastern, as aware datetimes.

    The end is the next calendar midnight rather than start + 24h, so the
    25-hour day at the November DST transition is bounded correctly.
    """
    start = datetime.combine(local_date, time.min, tzinfo=PARTICIPANT_TZ)
    end = datetime.combine(local_date + timedelta(days=1), time.min, tzinfo=PARTICIPANT_TZ)
    return start, end


def waking_window_bounds(local_date):
    """The wear-time denominator window, 08:00-22:00 Eastern.

    Deliberately not the notification window: reminders fire 09:00-21:00. The
    two are different concepts and must not be substituted for each other.
    """
    return _at(local_date, WAKING_WINDOW_START_HOUR), _at(local_date, WAKING_WINDOW_END_HOUR)


def waking_window_minutes(local_date):
    """The wear-time denominator, in real minutes. 840 for every day of this
    study: America/New_York transitions at 02:00, outside the waking window.
    """
    start, end = waking_window_bounds(local_date)
    return elapsed_minutes(start, end)


def scheduled_slot_bounds(local_date):
    """The day's fixed check-in slots, evenly spaced across the notification
    window (6 slots across 9am-9pm land 2 hours apart, per Dr. Chang
    2026-08-21). CheckinReminder.daily_count_at_send is an index into this list.

    Slot length is deliberately wall-clock, matching what send_checkin_reminders
    has always done. It makes no difference in this timezone, where transitions
    fall at 02:00, well outside the notification window.
    """
    window_start = _at(local_date, NOTIFICATION_WINDOW_START_HOUR)
    window_end = _at(local_date, NOTIFICATION_WINDOW_END_HOUR)
    slot_length = (window_end - window_start) / SCHEDULED_CHECK_IN_DAILY_CAP
    return [
        (window_start + i * slot_length, window_start + (i + 1) * slot_length)
        for i in range(SCHEDULED_CHECK_IN_DAILY_CAP)
    ]


def slot_index_for(moment, local_date=None):
    """Which check-in slot a timestamp falls in, or None if outside the window."""
    local = participant_time(moment)
    slots = scheduled_slot_bounds(local_date if local_date is not None else local.date())
    for index, (start, end) in enumerate(slots):
        if start <= local < end:
            return index
    return None


def day1_date(user):
    if user.enrolled_at is None:
        return None
    return participant_time(user.enrolled_at).date()


def study_day_for(user, local_date):
    anchor = day1_date(user)
    if anchor is None:
        return None
    return (local_date - anchor).days


def is_run_in(study_day):
    return study_day is not None and 0 <= study_day < RUN_IN_DAYS


def in_study_range(study_day):
    return study_day is not None and 0 <= study_day < STUDY_DAYS


def local_dates_for(user, n_trailing=METRICS_RECOMPUTE_TRAILING_DAYS, now=None):
    """Trailing local dates to recompute for one participant, oldest first, each
    paired with its study_day and clipped to the study window.

    Empty for a participant who was never enrolled, whose window has not opened,
    or who is past the last study day. Days outside the window get no row at all,
    which is what renders them blank rather than zero.
    """
    end = today_local(now)
    pairs = []
    for offset in range(n_trailing - 1, -1, -1):
        local_date = end - timedelta(days=offset)
        study_day = study_day_for(user, local_date)
        if in_study_range(study_day):
            pairs.append((local_date, study_day))
    return pairs
