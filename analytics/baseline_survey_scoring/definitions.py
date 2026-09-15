"""Column names for REACT baseline scoring. These are not the
Qualtrics QID names. The Qualtrics columns in the export get renamed
to these names in qualtrics.py.
"""

from __future__ import annotations


def numbered(prefix: str, count: int) -> tuple[str, ...]:
    """Create names such as ex_1 through ex_10."""
    return tuple(
        f"{prefix}_{number}"
        for number in range(1, count + 1)
    )


PARTICIPANT_ID = "participant_id"


# A1. Demographics
# Not scored, only preserved. GENDER is the exception: the AUDIT-C staff-alert
# threshold is 4 for Man and 3 for everyone else, so scoring reads this column.
AGE = "age"
GENDER = "gender"
RACE_ETHNICITY = "race_ethnicity"
YEAR_IN_SCHOOL = "year_in_school"
MAJOR = "major"
LIVING_SITUATION = "living_situation"
HEIGHT = "height"
WEIGHT = "weight"

DEMOGRAPHICS = (
    AGE,
    GENDER,
    RACE_ETHNICITY,
    YEAR_IN_SCHOOL,
    MAJOR,
    LIVING_SITUATION,
    HEIGHT,
    WEIGHT,
)

MACARTHUR = (
    "macarthur_community",
    "macarthur_us",
)


# A2. Sport Spectator Identification Scale (SSIS)
SSIS = numbered("ssis", 7)


# A3. Digital habits
## These are preserved but do not currently have a composite.
DIGITAL_HABITS = numbered("digital_habits", 3)


# A4. SUPPS-P
SUPPS_NEGATIVE_URGENCY = numbered(
    "supps_negative_urgency",
    4,
)

SUPPS_LACK_PERSEVERANCE = numbered(
    "supps_lack_perseverance",
    4,
)

SUPPS_LACK_PREMEDITATION = numbered(
    "supps_lack_premeditation",
    4,
)

SUPPS_SENSATION_SEEKING = numbered(
    "supps_sensation_seeking",
    4,
)

SUPPS_POSITIVE_URGENCY = numbered(
    "supps_positive_urgency",
    4,
)

SUPPS = (
    SUPPS_NEGATIVE_URGENCY
    + SUPPS_LACK_PERSEVERANCE
    + SUPPS_LACK_PREMEDITATION
    + SUPPS_SENSATION_SEEKING
    + SUPPS_POSITIVE_URGENCY
)

# Negative urgency, sensation seeking, and positive urgency
# are reverse-coded; reverse-coded in scoring.py's score_supps.
SUPPS_REVERSE = (
    SUPPS_NEGATIVE_URGENCY
    + SUPPS_SENSATION_SEEKING
    + SUPPS_POSITIVE_URGENCY
)


# A5. DERS-16
DERS_CLARITY = numbered(
    "ders_clarity",
    2,
)

DERS_GOALS = numbered(
    "ders_goals",
    3,
)

DERS_IMPULSE = numbered(
    "ders_impulse",
    3,
)

DERS_STRATEGIES = numbered(
    "ders_strategies",
    5,
)

DERS_NONACCEPTANCE = numbered(
    "ders_nonacceptance",
    3,
)

DERS = (
    DERS_CLARITY
    + DERS_GOALS
    + DERS_IMPULSE
    + DERS_STRATEGIES
    + DERS_NONACCEPTANCE
)


# A5a. ERQ
ERQ_REAPPRAISAL = numbered(
    "erq_reappraisal",
    6,
)

ERQ_SUPPRESSION = numbered(
    "erq_suppression",
    4,
)

ERQ = (
    ERQ_REAPPRAISAL
    + ERQ_SUPPRESSION
)


# A6. Brief Aggression Questionnaire
BAQ_PHYSICAL = numbered(
    "baq_physical",
    3,
)

BAQ_ANGER = numbered(
    "baq_anger",
    3,
)

BAQ_VERBAL = numbered(
    "baq_verbal",
    3,
)

BAQ_HOSTILITY = numbered(
    "baq_hostility",
    3,
)

BAQ = (
    BAQ_PHYSICAL
    + BAQ_ANGER
    + BAQ_VERBAL
    + BAQ_HOSTILITY
)

# "I am an even-tempered person."
BAQ_REVERSE = (
    "baq_anger_1",
)


# A7. Bergen Social Media Addiction Scale
BSMAS = numbered("bsmas", 6)


# A8. Perceived Stress Scale
PSS = numbered("pss", 10)

PSS_REVERSE = (
    "pss_4",
    "pss_5",
    "pss_7",
    "pss_8",
)


# A9. PROMIS Sleep Disturbance 4a
PROMIS_SLEEP = numbered(
    "promis_sleep",
    4,
)

# "My sleep quality was" and "My sleep was refreshing" are worded toward good
# sleep, so they are reversed to make high mean more disturbance. Store the raw
# 1-5 the participant saw and reverse once here; the codebook prints these two
# items with their values already flipped, and reversing those would flip twice.
PROMIS_SLEEP_REVERSE = (
    "promis_sleep_1",
    "promis_sleep_2",
)


# A10. TFEQ-R18
TFEQ_COGNITIVE_RESTRAINT = numbered(
    "tfeq_cognitive_restraint",
    6,
)

TFEQ_UNCONTROLLED_EATING = numbered(
    "tfeq_uncontrolled_eating",
    9,
)

TFEQ_EMOTIONAL_EATING = numbered(
    "tfeq_emotional_eating",
    3,
)

TFEQ = (
    TFEQ_COGNITIVE_RESTRAINT
    + TFEQ_UNCONTROLLED_EATING
    + TFEQ_EMOTIONAL_EATING
)

# This is the one item originally answered from 1 to 8. A single column name,
# not a tuple: the parentheses it used to carry had no trailing comma, so it was
# a plain string that iterated character by character.
TFEQ_RESTRAINT_1_TO_8 = "tfeq_cognitive_restraint_6"


# A11. MAIA-2
# Codebook currently specifies Tier-1 scoring for
# Noticing and Body Listening.

MAIA_NOTICING = numbered(
    "maia_noticing",
    4,
)

MAIA_BODY_LISTENING = numbered(
    "maia_body_listening",
    3,
)

MAIA_TIER1 = (
    MAIA_NOTICING
    + MAIA_BODY_LISTENING
)


# A12. Alcohol
AUDIT_C = numbered(
    "audit_c",
    3,
)

DMQ_ENHANCEMENT = numbered(
    "dmq_enhancement",
    3,
)

DMQ_SOCIAL = numbered(
    "dmq_social",
    3,
)

DMQ_CONFORMITY = numbered(
    "dmq_conformity",
    3,
)

DMQ_COPING = numbered(
    "dmq_coping",
    3,
)

DMQ_R_SF = (
    DMQ_ENHANCEMENT
    + DMQ_SOCIAL
    + DMQ_CONFORMITY
    + DMQ_COPING
)

BYAACQ = numbered(
    "byaacq",
    24,
)


# A13. Sports betting / PGSI
PGSI = numbered(
    "pgsi",
    9,
)


# A14. Health and context
HUNGER_VITAL_SIGN = numbered(
    "hunger_vital_sign",
    2,
)

ASRS = numbered(
    "asrs",
    6,
)

UCLA3 = numbered(
    "ucla3",
    3,
)

EDS = numbered(
    "eds",
    5,
)

RMEQ = numbered(
    "rmeq",
    5,
)


# A15. Sensitive block
PHQ9 = numbered(
    "phq9",
    9,
)

SCOFF = numbered(
    "scoff",
    5,
)

ACE = numbered(
    "ace",
    10,
)


# All expected scoring columns
ALL_SCORING_COLUMNS = (
    MACARTHUR
    + SSIS
    + SUPPS
    + DERS
    + ERQ
    + BAQ
    + BSMAS
    + PSS
    + PROMIS_SLEEP
    + TFEQ
    + MAIA_TIER1
    + AUDIT_C
    + DMQ_R_SF
    + BYAACQ
    + PGSI
    + HUNGER_VITAL_SIGN
    + ASRS
    + UCLA3
    + EDS
    + RMEQ
    + PHQ9
    + SCOFF
    + ACE
)


# Carried through to the output untouched. The codebook scores neither.
ALL_PRESERVED_COLUMNS = (
    DEMOGRAPHICS
    + DIGITAL_HABITS
)