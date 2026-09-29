"""
Single source of truth for REACT study constants.

Imported by the Celery tasks, the API views, the monitoring compute layer and the
offline analytics package, so a protocol value is defined exactly once. Only
``app.ema_catalog`` is imported from here, and that module imports nothing, so
this can never cycle even though ``app.tasks`` and ``app.views`` import back the
other way.
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
    'DISTRESS_B1_AFFECT_CEILING',
    'DISTRESS_B1_VALENCE_FLOOR',
    'DISTRESS_B2_STRESS_CEILING',
    'DISTRESS_MOMENTARY_PAUSE_HOURS',
    'EMA_RESPONSE_WINDOW_MINUTES',
    'HRV_BASELINE_WINDOW',
    'HRV_BBI_MAX_MS',
    'HRV_BBI_MIN_CONFIDENCE',
    'HRV_BBI_MIN_MS',
    'HRV_HIGH_CUTOFF',
    'HRV_LOW_CUTOFF',
    'HRV_MIN_BEATS',
    'HRV_WINDOW_SECONDS',
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

STUDY_DAYS = 35
RUN_IN_DAYS = 7 

PARTICIPANT_TZ = ZoneInfo('America/New_York') # EST

NOTIFICATION_WINDOW_START_HOUR = 9
NOTIFICATION_WINDOW_END_HOUR = 21
CHECKIN_REMINDER_DELAY_MINUTES = 30

WAKING_WINDOW_START_HOUR = 8
WAKING_WINDOW_END_HOUR = 22
WEAR_GAP_MIN = 120

THRESHOLD_QUANTILE = 0.80
MSSD_WINDOW = 3
JITAI_COOLDOWN_MINUTES = 60
DAILY_PROMPT_CAP = 4

DISTRESS_MOMENTARY_PAUSE_HOURS = 24

# Momentary distress override cutoffs, confirmed by Dr. Chang 2026-09-22: exact
# scale-endpoint values, not thresholds. Separate from ROUTING_TRIGGER_RULES in
# app/ema_catalog.py:174-230 (coping-message topic selection only) -- do not
# confuse the two. Routing uses inequalities (B1_valence <= 3, B2_stress >= 5,
# B1_affect_sad/anxious >= 4); this uses exact equality deliberately:
# EMAAnswerSerializer.validate already rejects any value outside an item's
# min/max with a 400, so an out-of-range value can never reach storage and an
# `==` cutoff can never be defeated the way an inequality could.
DISTRESS_B1_VALENCE_FLOOR = 1      # B1_valence is 1-7; 1 is the floor.
DISTRESS_B2_STRESS_CEILING = 7     # B2_stress is 1-7; 7 is the ceiling.
DISTRESS_B1_AFFECT_CEILING = 5     # B1_affect_sad / B1_affect_anxious are 1-5;
                                    # both share this ceiling.

# HRV (RMSSD over Garmin beat-to-beat intervals). PROVISIONAL: every value below
# is a numeric decision threshold and none has PI sign-off yet, so HRV is only
# recorded on JITAILog — it does not gate send_prompt. See the HRV section of
# analytics/analysis-resources/data-dictionary.md for how this 5-minute ratio
# classifier differs from the nightly Garmin hrv_status convention.
HRV_WINDOW_SECONDS = 300
HRV_MIN_BEATS = 5
HRV_LOW_CUTOFF = 0.8
HRV_HIGH_CUTOFF = 1.2
HRV_BASELINE_WINDOW = 288
HRV_BBI_MIN_MS = 300
HRV_BBI_MAX_MS = 2000
HRV_BBI_MIN_CONFIDENCE = 1

RANDOMIZATION_P_ENV = 'JITAI_RANDOMIZATION_PROBABILITY'
ARM_RANDOMIZATION_P_ENV = 'JITAI_ARM_RANDOMIZATION_PROBABILITY'
RANDOMIZATION_P_DEFAULT = 0.5
ARM_RANDOMIZATION_P_DEFAULT = 0.5


def randomization_p():
    return float(os.environ.get(RANDOMIZATION_P_ENV, RANDOMIZATION_P_DEFAULT))


def arm_randomization_p():
    return float(os.environ.get(ARM_RANDOMIZATION_P_ENV, ARM_RANDOMIZATION_P_DEFAULT))

OUTCOME_WINDOW_HOURS = 2


BENCHMARKS = {
    'slot_coverage': 0.75,
    'prompt_response': 0.70,
    'wear': 0.80,
    'retention': 0.85,
}


UNMEASURABLE_BENCHMARKS = frozenset()

RATE_MIN_PARTICIPANTS = 10
RATE_MIN_UNITS = 30

ITEM_BANK_VERSION = 'v1'

METRICS_RECOMPUTE_TRAILING_DAYS = 3
METRICS_COHORT_RETENTION_DAYS = 30

# The two caps are different quantities and nothing else in the repo states both
# in one place: 6 scheduled B1-B8 check-ins per day versus 4 JITAI prompts.
assert SCHEDULED_CHECK_IN_DAILY_CAP != DAILY_PROMPT_CAP
