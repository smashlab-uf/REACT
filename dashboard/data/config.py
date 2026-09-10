"""Single source of truth for REACT study constants.

Imported by the Celery tasks, the API views, the monitoring compute layer and the
offline analytics package, so a protocol value is defined exactly once. Only
``app.ema_catalog`` is imported from here, and that module imports nothing, so
this can never cycle even though ``app.tasks`` and ``app.views`` import back the
other way.

Values that are genuinely owned elsewhere are re-exported rather than restated:
the EMA caps and response window belong to the item catalog, which owns its own
item semantics. Everything else is defined here.
"""

import os
from zoneinfo import ZoneInfo

from app.ema_catalog import (
    EMA_RESPONSE_WINDOW_MINUTES,
    POST_PROMPT_CHECK_IN_DAILY_CAP,
    SCHEDULED_CHECK_IN_DAILY_CAP,
)

__all__ = [
    'ARM_RANDOMIZATION_P_DEFAULT',
    'ARM_RANDOMIZATION_P_ENV',
    'BENCHMARKS',
    'CHECKIN_REMINDER_DELAY_MINUTES',
    'DAILY_PROMPT_CAP',
    'EMA_RESPONSE_WINDOW_MINUTES',
    'ITEM_BANK_VERSION',
    'JITAI_COOLDOWN_MINUTES',
    'METRICS_RECOMPUTE_TRAILING_DAYS',
    'MSSD_WINDOW',
    'NOTIFICATION_WINDOW_END_HOUR',
    'NOTIFICATION_WINDOW_START_HOUR',
    'N_TARGET',
    'OUTCOME_WINDOW_HOURS',
    'PARTICIPANT_TZ',
    'PHASE1_USER_IDS',
    'POST_PROMPT_CHECK_IN_DAILY_CAP',
    'RANDOMIZATION_P_DEFAULT',
    'RANDOMIZATION_P_ENV',
    'RATE_MIN_PARTICIPANTS',
    'RATE_MIN_UNITS',
    'RUN_IN_DAYS',
    'SCHEDULED_CHECK_IN_DAILY_CAP',
    'STUDY_DAYS',
    'THRESHOLD_QUANTILE',
    'UNMEASURABLE_BENCHMARKS',
    'WAKING_WINDOW_END_HOUR',
    'WAKING_WINDOW_START_HOUR',
    'WEAR_GAP_MIN',
    'arm_randomization_p',
    'randomization_p',
]


def _int_list_from_env(name):
    raw = os.environ.get(name, '')
    return [int(part) for part in raw.replace(' ', '').split(',') if part]


# Cohort
N_TARGET = 40
PHASE1_USER_IDS = _int_list_from_env('REACT_PHASE1_USER_IDS')

# Study calendar. study_day runs 0..STUDY_DAYS-1; days 0..RUN_IN_DAYS-1 are the
# non-interventional run-in baseline used to establish each participant's
# within-person MSSD threshold (analysis-resources/data-dictionary.md).
STUDY_DAYS = 35
RUN_IN_DAYS = 7

# Hardcoded app-wide: every day and hour boundary in this system is evaluated in
# Eastern, because settings.TIME_ZONE is UTC and a __date lookup would truncate
# in UTC instead.
PARTICIPANT_TZ = ZoneInfo('America/New_York')

# When check-in reminders may fire. Confirmed by Dr. Chang 2026-08-21. Distinct
# from the wear-time waking window below -- do not reuse one for the other.
NOTIFICATION_WINDOW_START_HOUR = 9
NOTIFICATION_WINDOW_END_HOUR = 21
CHECKIN_REMINDER_DELAY_MINUTES = 30

# Wear-time denominator only (analysis-resources/JITAI-analysis-plan.md): 14
# waking hours per day, with a gap counted as non-wear past WEAR_GAP_MIN.
WAKING_WINDOW_START_HOUR = 8
WAKING_WINDOW_END_HOUR = 22
WEAR_GAP_MIN = 120

# Decision engine. All four are PI-confirmed (Resources/TODO.md, "PI Sign-Offs
# Received 2026-07-06") and are passed explicitly into decision_engine rather
# than left to its defaults, so the engine and the monitor cannot drift apart.
THRESHOLD_QUANTILE = 0.80
MSSD_WINDOW = 3
JITAI_COOLDOWN_MINUTES = 60
DAILY_PROMPT_CAP = 4

# Two-stage randomization: whether to send, then which message arm. Read at call
# time rather than import time, because the jitai_demo command and the test suite
# both set these vars after the module is already loaded.
RANDOMIZATION_P_ENV = 'JITAI_RANDOMIZATION_PROBABILITY'
ARM_RANDOMIZATION_P_ENV = 'JITAI_ARM_RANDOMIZATION_PROBABILITY'
RANDOMIZATION_P_DEFAULT = 0.5
ARM_RANDOMIZATION_P_DEFAULT = 0.5


def randomization_p():
    return float(os.environ.get(RANDOMIZATION_P_ENV, RANDOMIZATION_P_DEFAULT))


def arm_randomization_p():
    return float(os.environ.get(ARM_RANDOMIZATION_P_ENV, ARM_RANDOMIZATION_P_DEFAULT))

# How long a delivered JITAI prompt keeps the outcome window open.
OUTCOME_WINDOW_HOURS = 2

# Feasibility benchmarks. 'hair' has no production source: hair_sample is
# documented in the analysis schema but absent from the Django backend, so the
# cohort snapshot carries the column as null rather than reporting a rate.
BENCHMARKS = {
    'slot_coverage': 0.75,
    'prompt_response': 0.60,
    'wear': 0.80,
    'retention': 0.85,
    'hair': 0.90,
}
UNMEASURABLE_BENCHMARKS = frozenset({'hair'})

# Display a rate only once the denominator carries this many participants and
# units; otherwise show raw counts. Phase 1 therefore never shows percentages.
RATE_MIN_PARTICIPANTS = 10
RATE_MIN_UNITS = 30

# Names the frozen EMA_ITEM_BANK export that completeness is scored against, so
# a mid-study item change cannot silently rewrite past completeness.
ITEM_BANK_VERSION = 'v1'

# Trailing local days recomputed on every monitoring run, wide enough to absorb
# Labfront batch lag and late device sync.
METRICS_RECOMPUTE_TRAILING_DAYS = 3

# The two caps are different quantities and nothing else in the repo states both
# in one place: 6 scheduled B1-B8 check-ins per day versus 4 JITAI prompts.
assert SCHEDULED_CHECK_IN_DAILY_CAP != DAILY_PROMPT_CAP
