def numbered(str, int) -> tuple[str, ...]:
    """Create names such as pss10_1 through pss10_10 using the str as the prefix and int as count."""

    return tuple(
        f"{prefix}_{number}"
        for number in range(1, count + 1)
    )


# A2: Sport spectator identification
SSIS = numbered("ssis", 7)


# A4: SUPPS-P
SUPPS_NEGATIVE_URGENCY = numbered(
    "supps_negative_urgency", 4
)
SUPPS_LACK_PERSEVERANCE = numbered(
    "supps_lack_perseverance", 4
)
SUPPS_LACK_PREMEDITATION = numbered(
    "supps_lack_premeditation", 4
)
SUPPS_SENSATION_SEEKING = numbered(
    "supps_sensation_seeking", 4
)
SUPPS_POSITIVE_URGENCY = numbered(
    "supps_positive_urgency", 4
)

SUPPS = (
    SUPPS_NEGATIVE_URGENCY
    + SUPPS_LACK_PERSEVERANCE
    + SUPPS_LACK_PREMEDITATION
    + SUPPS_SENSATION_SEEKING
    + SUPPS_POSITIVE_URGENCY
)

SUPPS_REVERSE = (
    SUPPS_LACK_PERSEVERANCE
    + SUPPS_LACK_PREMEDITATION
)


# A5: DERS-16
DERS_CLARITY = ("ders_1", "ders_2")
DERS_GOALS = ("ders_3", "ders_4", "ders_5")
DERS_IMPULSE = ("ders_6", "ders_7", "ders_8")

DERS_STRATEGIES = tuple(
    f"ders_{number}"
    for number in range(9, 14)
)

DERS_NONACCEPTANCE = tuple(
    f"ders_{number}"
    for number in range(14, 17)
)

DERS = (
    DERS_CLARITY
    + DERS_GOALS
    + DERS_IMPULSE
    + DERS_STRATEGIES
    + DERS_NONACCEPTANCE
)


# A5a: ERQ
ERQ_REAPPRAISAL = numbered("erq_reappraisal", 6)
ERQ_SUPPRESSION = numbered("erq_suppression", 4)
ERQ = ERQ_REAPPRAISAL + ERQ_SUPPRESSION


# A6: BAQ
BAQ_PHYSICAL = numbered("baq_physical", 3)
BAQ_ANGER = numbered("baq_anger", 3)
BAQ_VERBAL = numbered("baq_verbal", 3)
BAQ_HOSTILITY = numbered("baq_hostility", 3)

BAQ = (
    BAQ_PHYSICAL
    + BAQ_ANGER
    + BAQ_VERBAL
    + BAQ_HOSTILITY
)

BAQ_REVERSE = ("baq_anger_1",)


# A7-A9
BSMAS = numbered("bsmas", 6)

PSS10 = numbered("pss10", 10)
PSS10_REVERSE = (
    "pss10_4",
    "pss10_5",
    "pss10_7",
    "pss10_8",
)

PROMIS_SLEEP = numbered("promis_sleep", 4)
PROMIS_SLEEP_REVERSE = (
    "promis_sleep_1",
    "promis_sleep_2",
)


# A10: TFEQ-R18
TFEQ_RESTRAINT = numbered("tfeq_restraint", 6)
TFEQ_UNCONTROLLED = numbered("tfeq_uncontrolled", 9)
TFEQ_EMOTIONAL = numbered("tfeq_emotional", 3)

TFEQ = (
    TFEQ_RESTRAINT
    + TFEQ_UNCONTROLLED
    + TFEQ_EMOTIONAL
)


# A11: Selected MAIA-2 subscales
MAIA_NOTICING = numbered("maia_noticing", 4)
MAIA_BODY_LISTENING = numbered(
    "maia_body_listening", 3
)

MAIA = MAIA_NOTICING + MAIA_BODY_LISTENING


# A12: Alcohol
AUDIT_C = numbered("audit_c", 3)

DMQ_ENHANCEMENT = numbered("dmq_enhancement", 3)
DMQ_SOCIAL = numbered("dmq_social", 3)
DMQ_CONFORMITY = numbered("dmq_conformity", 3)
DMQ_COPING = numbered("dmq_coping", 3)

DMQ = (
    DMQ_ENHANCEMENT
    + DMQ_SOCIAL
    + DMQ_CONFORMITY
    + DMQ_COPING
)

BYAACQ = numbered("byaacq", 24)


# A13: Gambling
PGSI = numbered("pgsi", 9)


# A14: Health and context
HUNGER_VITAL_SIGN = ("hvs_1", "hvs_2")
ASRS = numbered("asrs", 6)
UCLA = numbered("ucla", 3)

EVERYDAY_DISCRIMINATION = numbered(
    "discrimination", 5
)

RMEQ = numbered("rmeq", 5)


# A15: Sensitive block
PHQ9 = numbered("phq9", 9)
SCOFF = numbered("scoff", 5)
ACE = numbered("ace", 10)


ALL_SCORED_ITEMS = (
    SSIS
    + SUPPS
    + DERS
    + ERQ
    + BAQ
    + BSMAS
    + PSS10
    + PROMIS_SLEEP
    + TFEQ
    + MAIA
    + AUDIT_C
    + DMQ
    + BYAACQ
    + PGSI
    + HUNGER_VITAL_SIGN
    + ASRS
    + UCLA
    + EVERYDAY_DISCRIMINATION
    + RMEQ
    + PHQ9
    + SCOFF
    + ACE
)


# The following fields are retained but are not combined into scores:
PASSTHROUGH_COLUMNS = (
    "participant_id",
    "age_group",
    "gender_identity",
    "race_ethnicity",
    "school_year",
    "major",
    "living_situation",
    "height",
    "weight",
    "status_ladder_community",
    "status_ladder_us",
    "social_media_time",
    "upset_posting_frequency",
    "posting_regret_frequency",
    "sports_betting_past_year",
    "sports_betting_frequency",
    "sports_betting_types",
    "sports_betting_monthly_amount",
    "appetite_weight_medication",
    "cannabis_frequency",
    "nicotine_frequency",
    "discrimination_attribution",
    "currently_menstruating",
    "days_since_period_started",
    "cycle_regularity",
    "phq9_difficulty",
)
