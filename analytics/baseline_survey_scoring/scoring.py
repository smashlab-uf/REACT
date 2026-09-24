"""Score REACT baseline survey responses against
analytics/analysis-resources/Survey Scoring Codebook.docx.

One score_* function per instrument, built on a small generic engine
(reverse / sum_score / mean_score / count_at_or_above / threshold_flag).
score_participants() runs every instrument over a definitions.py-column
DataFrame (e.g. from qualtrics.load_export()) and returns (scores, alerts).
"""

from __future__ import annotations

import math
from typing import Dict, Optional, Tuple

import numpy as np
import pandas as pd

import definitions as D
from qualtrics import normalize


# Section 1 — generic scoring engine.

def reverse(series: pd.Series, lo, hi) -> pd.Series:
    """Flip a Likert item end for end: lo + hi - x."""
    return lo + hi - series


def complete_mask(frame: pd.DataFrame, columns) -> pd.Series:
    """True where every one of the named items is present."""
    return frame.loc[:, list(columns)].notna().all(axis=1)


def require_complete(frame: pd.DataFrame, columns, values: pd.Series) -> pd.Series:
    """NA out any row missing one of the items the score is built from."""
    return values.where(complete_mask(frame, columns))


def sum_score(frame: pd.DataFrame, columns, reverse_columns=(), lo=None, hi=None) -> pd.Series:
    block = frame.loc[:, list(columns)].copy()
    for column in reverse_columns:
        if column in block.columns:
            block[column] = reverse(block[column], lo, hi)
    return require_complete(frame, columns, block.sum(axis=1, skipna=False))


def mean_score(frame: pd.DataFrame, columns, reverse_columns=(), lo=None, hi=None) -> pd.Series:
    block = frame.loc[:, list(columns)].copy()
    for column in reverse_columns:
        if column in block.columns:
            block[column] = reverse(block[column], lo, hi)
    return require_complete(frame, columns, block.mean(axis=1, skipna=False))


def count_at_or_above(frame: pd.DataFrame, columns, minimum) -> pd.Series:
    """Count items answered at or above a cut. Used by the endorsement screens."""
    block = frame.loc[:, list(columns)]
    return require_complete(frame, columns, (block >= minimum).sum(axis=1))


def threshold_flag(values: pd.Series, cut) -> pd.Series:
    """True at or above the cut, NA where the score itself is NA."""
    return values.map(lambda v: np.nan if pd.isna(v) else bool(v >= cut))


def ensure_columns(frame: pd.DataFrame, columns) -> pd.DataFrame:
    """Add any missing item columns as all-NA so scoring never raises on shape.

    A missing item is a reporting problem, handled loudly by the loader. Here it
    has to become an NA score rather than a KeyError, or one absent column would
    stop all 23 instruments from producing anything.
    """
    out = frame.copy()
    for column in columns:
        if column not in out.columns:
            out[column] = np.nan
    return out


# Section 2 — the instruments.

SSIS_RANGE = (1, 8)
SUPPS_RANGE = (1, 4)
DERS_RANGE = (1, 5)
ERQ_RANGE = (1, 7)
BAQ_RANGE = (1, 7)
BSMAS_RANGE = (1, 5)
PSS_RANGE = (0, 4)
PROMIS_RANGE = (1, 5)
TFEQ_RANGE = (1, 4)
MAIA_RANGE = (0, 5)
DMQ_RANGE = (1, 5)
PGSI_RANGE = (0, 3)
UCLA_RANGE = (1, 3)
EDS_RANGE = (1, 5)
PHQ9_RANGE = (0, 3)
LADDER_RANGE = (1, 10)

# PROMIS Sleep Disturbance 4a short-form conversion table, transcribed from the
# image embedded in the codebook. Raw is the sum of the four items after items 1
# and 2 are reversed, so its domain is 4 to 20 and nothing else.
PROMIS_CONVERSION = {
    4: (32.0, 5.2), 5: (37.5, 4.0), 6: (41.1, 3.7), 7: (43.8, 3.5),
    8: (46.2, 3.5), 9: (48.4, 3.4), 10: (50.5, 3.4), 11: (52.4, 3.4),
    12: (54.3, 3.4), 13: (56.1, 3.4), 14: (57.9, 3.3), 15: (59.8, 3.3),
    16: (61.7, 3.3), 17: (63.8, 3.4), 18: (66.0, 3.4), 19: (68.8, 3.7),
    20: (73.3, 4.6),
}

# [(raw - min) / range] * 100, per subscale. Cognitive Restraint has 6 items so
# raw runs 6 to 24, Uncontrolled Eating 9 items so 9 to 36, Emotional Eating
# 3 items so 3 to 12.
TFEQ_TRANSFORM = {
    "cognitive_restraint": (6, 18),
    "uncontrolled_eating": (9, 27),
    "emotional_eating": (3, 9),
}

# Items 1 to 3 count Sometimes and above; items 4 to 6 only Often and above.
ASRS_CUTS = {0: 2, 1: 2, 2: 2, 3: 3, 4: 3, 5: 3}
ASRS_POSITIVE_AT = 4

# The codebook is explicit that the more sensitive cut is deliberate for anyone
# who does not specify a gender, so an absent gender takes 3 and is still
# screened. It must never skip the check.
AUDIT_C_CUT_MAN = 4
AUDIT_C_CUT_OTHER = 3

SCOFF_POSITIVE_AT = 2
HUNGER_POSITIVE_AT = 1   # Sometimes true (1) or Often true (2)

# Section 11 safety signals, added for the JITAI distress override
# (backend/app/distress.py). Not from the codebook, which gives PHQ-9 no
# severity bands at all: these are the instrument's own published scoring
# (Kroenke, Spitzer & Williams, 2001). phq9_9 is "Thoughts that you would be
# better off dead or of hurting yourself in some way" -- confirmed against
# analysis-resources/Survey Scoring Codebook.docx, item 9 of 9, same order.
PHQ9_SELF_HARM_ITEM = D.PHQ9[-1]
PHQ9_MODERATELY_SEVERE_AT = 15  # 15-19 moderately severe, 20-27 severe

# Not a new threshold: pgsi_band() below already treats 8+ as "problem
# gambling". This just exposes that existing cutoff as a boolean flag.
PGSI_PROBLEM_GAMBLING_AT = 8


def score_ssis(frame: pd.DataFrame) -> Dict[str, pd.Series]:
    lo, hi = SSIS_RANGE
    # The codebook fixes the 1-to-8 response scale but never says sum or mean.
    # Both are emitted; the comparison against Gabby settles which she computed.
    return {
        "ssis_sum": sum_score(frame, D.SSIS),
        "ssis_mean": mean_score(frame, D.SSIS),
    }


def score_supps(frame: pd.DataFrame) -> Dict[str, pd.Series]:
    """Five subscale means on 1-4, after reversing the items worded toward impulsivity.

    The scale runs 1 = strongly agree to 4 = strongly disagree, so agreement is
    LOW. An item like "When I am upset I often act without thinking" therefore
    has to be reversed for a high score to mean more impulsive, while "I finish
    what I start" already scores high on disagreement and is left alone.

    CONFLICT: the superseded xlsx marks the opposite eight items as reversed.
    definitions.py and the docx agree with each other and with the logic above.
    Four subscale directions ride on this; Prof. Chang or Gabby should rule.
    """
    lo, hi = SUPPS_RANGE
    subscales = {
        "supps_negative_urgency": D.SUPPS_NEGATIVE_URGENCY,
        "supps_lack_perseverance": D.SUPPS_LACK_PERSEVERANCE,
        "supps_lack_premeditation": D.SUPPS_LACK_PREMEDITATION,
        "supps_sensation_seeking": D.SUPPS_SENSATION_SEEKING,
        "supps_positive_urgency": D.SUPPS_POSITIVE_URGENCY,
    }
    out = {}
    for name, columns in subscales.items():
        reversed_here = [c for c in columns if c in D.SUPPS_REVERSE]
        out[f"{name}_mean"] = mean_score(frame, columns, reversed_here, lo, hi)
    return out


def score_ders(frame: pd.DataFrame) -> Dict[str, pd.Series]:
    subscales = {
        "ders_clarity": D.DERS_CLARITY,
        "ders_goals": D.DERS_GOALS,
        "ders_impulse": D.DERS_IMPULSE,
        "ders_strategies": D.DERS_STRATEGIES,
        "ders_nonacceptance": D.DERS_NONACCEPTANCE,
    }
    out = {f"{name}_sum": sum_score(frame, columns) for name, columns in subscales.items()}
    out["ders_total"] = sum_score(frame, D.DERS)
    return out


def score_erq(frame: pd.DataFrame) -> Dict[str, pd.Series]:
    return {
        "erq_reappraisal_mean": mean_score(frame, D.ERQ_REAPPRAISAL),
        "erq_suppression_mean": mean_score(frame, D.ERQ_SUPPRESSION),
    }


def score_baq(frame: pd.DataFrame) -> Dict[str, pd.Series]:
    lo, hi = BAQ_RANGE
    reverse_cols = list(D.BAQ_REVERSE)
    subscales = {
        "baq_physical": D.BAQ_PHYSICAL,
        "baq_anger": D.BAQ_ANGER,
        "baq_verbal": D.BAQ_VERBAL,
        "baq_hostility": D.BAQ_HOSTILITY,
    }
    out = {}
    for name, columns in subscales.items():
        here = [c for c in columns if c in reverse_cols]
        out[f"{name}_sum"] = sum_score(frame, columns, here, lo, hi)
    out["baq_total"] = sum_score(frame, D.BAQ, reverse_cols, lo, hi)
    return out


def score_bsmas(frame: pd.DataFrame) -> Dict[str, pd.Series]:
    return {"bsmas_total": sum_score(frame, D.BSMAS)}


def score_pss(frame: pd.DataFrame) -> Dict[str, pd.Series]:
    lo, hi = PSS_RANGE
    return {"pss_total": sum_score(frame, D.PSS, D.PSS_REVERSE, lo, hi)}


def score_promis(frame: pd.DataFrame) -> Dict[str, pd.Series]:
    """Reverse items 1 and 2, sum to 4-20, then the published conversion table.

    The docx prints items 1 and 2 with their values already flipped while also
    listing them as reverse-scored. Reversing those printed values would flip
    them twice, so the loader stores what the participant saw and the single
    reversal happens here. The raw sum is asserted into 4-20, which is the
    table's whole domain, so a double reversal cannot pass unnoticed.
    """
    lo, hi = PROMIS_RANGE
    raw = sum_score(frame, D.PROMIS_SLEEP, D.PROMIS_SLEEP_REVERSE, lo, hi)
    scored = raw.dropna()
    if len(scored) and not scored.between(4, 20).all():
        bad = sorted(scored[~scored.between(4, 20)].unique())
        raise ValueError(f"PROMIS raw sum outside the 4-20 conversion domain: {bad}")
    return {
        "promis_sleep_raw": raw,
        "promis_sleep_t": raw.map(lambda v: np.nan if pd.isna(v) else PROMIS_CONVERSION[int(v)][0]),
        "promis_sleep_se": raw.map(lambda v: np.nan if pd.isna(v) else PROMIS_CONVERSION[int(v)][1]),
    }


def recode_tfeq_ladder(series: pd.Series) -> pd.Series:
    """The 1-to-8 restraint item onto the common 1-4 scale: 1-2, 3-4, 5-6, 7-8."""
    def one(value):
        if pd.isna(value):
            return np.nan
        if not 1 <= value <= 8:
            return np.nan
        return math.ceil(value / 2)
    return series.map(one)


def score_tfeq(frame: pd.DataFrame) -> Dict[str, pd.Series]:
    """Three subscale sums, then each transformed onto 0-100.

    The sixth Cognitive Restraint item is answered 1 to 8 and is recoded onto
    1 to 4 first, otherwise it contributes twice the weight of its five
    neighbours and pushes the raw sum past the subscale maximum.
    """
    working = frame.copy()
    working[D.TFEQ_RESTRAINT_1_TO_8] = recode_tfeq_ladder(working[D.TFEQ_RESTRAINT_1_TO_8])
    subscales = {
        "cognitive_restraint": D.TFEQ_COGNITIVE_RESTRAINT,
        "uncontrolled_eating": D.TFEQ_UNCONTROLLED_EATING,
        "emotional_eating": D.TFEQ_EMOTIONAL_EATING,
    }
    out = {}
    for name, columns in subscales.items():
        raw = sum_score(working, columns)
        minimum, span = TFEQ_TRANSFORM[name]
        out[f"tfeq_{name}_raw"] = raw
        out[f"tfeq_{name}_0_100"] = (raw - minimum) / span * 100.0
    return out


def score_maia(frame: pd.DataFrame) -> Dict[str, pd.Series]:
    return {
        "maia_noticing_mean": mean_score(frame, D.MAIA_NOTICING),
        "maia_body_listening_mean": mean_score(frame, D.MAIA_BODY_LISTENING),
    }


def score_audit_c(frame: pd.DataFrame) -> Dict[str, pd.Series]:
    """Total 0-12, then the gender-conditional staff-alert flag."""
    total = sum_score(frame, D.AUDIT_C)
    gender = frame[D.GENDER] if D.GENDER in frame.columns else pd.Series(np.nan, index=frame.index)

    def cut_for(value):
        if value is None or (isinstance(value, float) and pd.isna(value)):
            return AUDIT_C_CUT_OTHER
        return AUDIT_C_CUT_MAN if normalize(value) == "man" else AUDIT_C_CUT_OTHER

    cuts = gender.map(cut_for)
    flag = pd.Series(
        [np.nan if pd.isna(score) else bool(score >= cut)
         for score, cut in zip(total, cuts)],
        index=frame.index,
    )
    return {"audit_c_total": total, "audit_c_cut": cuts, "audit_c_alert": flag}


def score_dmq(frame: pd.DataFrame) -> Dict[str, pd.Series]:
    return {
        "dmq_enhancement_sum": sum_score(frame, D.DMQ_ENHANCEMENT),
        "dmq_social_sum": sum_score(frame, D.DMQ_SOCIAL),
        "dmq_conformity_sum": sum_score(frame, D.DMQ_CONFORMITY),
        "dmq_coping_sum": sum_score(frame, D.DMQ_COPING),
    }


def score_byaacq(frame: pd.DataFrame) -> Dict[str, pd.Series]:
    return {"byaacq_total": sum_score(frame, D.BYAACQ)}


def pgsi_band(score) -> Optional[str]:
    if pd.isna(score):
        return None
    if score == 0:
        return "non-problem"
    if score <= 4:
        return "low risk"
    if score <= 7:
        return "moderate risk"
    return "problem gambling"


def score_pgsi(frame: pd.DataFrame) -> Dict[str, pd.Series]:
    total = sum_score(frame, D.PGSI)
    return {
        "pgsi_total": total,
        "pgsi_band": total.map(pgsi_band),
        "pgsi_problem_gambling": threshold_flag(total, PGSI_PROBLEM_GAMBLING_AT),
    }


def score_hunger(frame: pd.DataFrame) -> Dict[str, pd.Series]:
    """Positive when either item is Often true or Sometimes true.

    Screened, never summed. The two items are not a scale and adding them would
    let two Sometimes outweigh one Often, which is not what the instrument says.
    """
    endorsed = count_at_or_above(frame, D.HUNGER_VITAL_SIGN, HUNGER_POSITIVE_AT)
    return {
        "hunger_endorsed_n": endorsed,
        "hunger_positive": threshold_flag(endorsed, 1),
    }


def score_asrs(frame: pd.DataFrame) -> Dict[str, pd.Series]:
    """Item-specific cuts, count 0-6, positive at 4 or more.

    Items 1 to 3 count Sometimes, Often or Very often; items 4 to 6 only Often
    or Very often. A single cut across all six is the classic way to get this
    wrong, so each item carries its own.
    """
    block = frame.loc[:, list(D.ASRS)]
    counted = pd.DataFrame(
        {column: block[column] >= ASRS_CUTS[index]
         for index, column in enumerate(D.ASRS)},
        index=frame.index,
    )
    total = require_complete(frame, D.ASRS, counted.sum(axis=1))
    return {
        "asrs_count": total,
        "asrs_positive": threshold_flag(total, ASRS_POSITIVE_AT),
    }


def score_ucla(frame: pd.DataFrame) -> Dict[str, pd.Series]:
    return {"ucla3_total": sum_score(frame, D.UCLA3)}


def score_eds(frame: pd.DataFrame) -> Dict[str, pd.Series]:
    return {"eds_total": sum_score(frame, D.EDS)}


def score_rmeq(frame: pd.DataFrame) -> Dict[str, pd.Series]:
    return {"rmeq_total": sum_score(frame, D.RMEQ)}


def score_phq9(frame: pd.DataFrame) -> Dict[str, pd.Series]:
    """Sum of all nine items, 0-27, plus the two Section 11 safety flags.

    The codebook's scoring line says "the sum of all four items". That is a
    leftover from when this instrument was PHQ-4 in the older xlsx; its own item
    list here has nine. Reported, not implemented.

    phq9_self_harm_positive is a nonzero response to the self-harm item alone,
    never summed with anything else. phq9_severity_alert is the total at or
    above the moderately-severe band. Both feed the distress override and stay
    separate from the "resource handout" ALERT_RULES below.
    """
    total = sum_score(frame, D.PHQ9)
    return {
        "phq9_total": total,
        "phq9_self_harm_positive": threshold_flag(frame[PHQ9_SELF_HARM_ITEM], 1),
        "phq9_severity_alert": threshold_flag(total, PHQ9_MODERATELY_SEVERE_AT),
    }


def score_scoff(frame: pd.DataFrame) -> Dict[str, pd.Series]:
    total = sum_score(frame, D.SCOFF)
    return {
        "scoff_total": total,
        "scoff_positive": threshold_flag(total, SCOFF_POSITIVE_AT),
    }


def score_ace(frame: pd.DataFrame) -> Dict[str, pd.Series]:
    return {"ace_total": sum_score(frame, D.ACE)}


def score_macarthur(frame: pd.DataFrame) -> Dict[str, pd.Series]:
    return {
        "macarthur_community": frame[D.MACARTHUR[0]],
        "macarthur_us": frame[D.MACARTHUR[1]],
    }


SCORERS = [
    score_ssis, score_supps, score_ders, score_erq, score_baq, score_bsmas,
    score_pss, score_promis, score_tfeq, score_maia, score_audit_c, score_dmq,
    score_byaacq, score_pgsi, score_hunger, score_asrs, score_ucla, score_eds,
    score_rmeq, score_phq9, score_scoff, score_ace, score_macarthur,
]


# Section 3 — assembly.

ALERT_RULES = [
    ("audit_c_alert", "AUDIT-C", "audit_c_total",
     "Hazardous alcohol use screen positive"),
    ("hunger_positive", "Hunger Vital Sign", "hunger_endorsed_n",
     "Food insecurity screen positive"),
    ("asrs_positive", "ASRS v1.1 Part A", "asrs_count",
     "ADHD symptom screen positive"),
    ("scoff_positive", "SCOFF", "scoff_total",
     "Eating disorder screen positive, triggers the resource handout"),
]


def score_participants(frame: pd.DataFrame) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """Run every instrument. Returns (scores, alerts)."""
    prepared = ensure_columns(frame, list(D.ALL_SCORING_COLUMNS) + [D.GENDER])

    identifier = (prepared[D.PARTICIPANT_ID] if D.PARTICIPANT_ID in prepared.columns
                  else pd.Series(prepared.index, index=prepared.index, name=D.PARTICIPANT_ID))
    scores = pd.DataFrame({D.PARTICIPANT_ID: identifier.values}, index=prepared.index)

    for column in D.ALL_PRESERVED_COLUMNS:
        if column in prepared.columns:
            scores[column] = prepared[column].values

    for scorer in SCORERS:
        for name, values in scorer(prepared).items():
            scores[name] = values.values if hasattr(values, "values") else values

    alert_rows = []
    for flag_column, instrument, score_column, message in ALERT_RULES:
        if flag_column not in scores.columns:
            continue
        for position, flagged in enumerate(scores[flag_column]):
            if flagged is True:
                alert_rows.append({
                    D.PARTICIPANT_ID: scores[D.PARTICIPANT_ID].iloc[position],
                    "instrument": instrument,
                    "score": scores[score_column].iloc[position],
                    "alert": message,
                })
    alerts = pd.DataFrame(
        alert_rows,
        columns=[D.PARTICIPANT_ID, "instrument", "score", "alert"],
    )
    return scores, alerts


SCORE_RANGES = {
    "ssis_sum": (7, 56), "ssis_mean": (1, 8),
    "supps_negative_urgency_mean": (1, 4), "supps_lack_perseverance_mean": (1, 4),
    "supps_lack_premeditation_mean": (1, 4), "supps_sensation_seeking_mean": (1, 4),
    "supps_positive_urgency_mean": (1, 4),
    "ders_total": (16, 80), "ders_clarity_sum": (2, 10), "ders_goals_sum": (3, 15),
    "ders_impulse_sum": (3, 15), "ders_strategies_sum": (5, 25),
    "ders_nonacceptance_sum": (3, 15),
    "erq_reappraisal_mean": (1, 7), "erq_suppression_mean": (1, 7),
    "baq_total": (12, 84), "baq_physical_sum": (3, 21), "baq_anger_sum": (3, 21),
    "baq_verbal_sum": (3, 21), "baq_hostility_sum": (3, 21),
    "bsmas_total": (6, 30), "pss_total": (0, 40),
    "promis_sleep_raw": (4, 20), "promis_sleep_t": (32.0, 73.3),
    "tfeq_cognitive_restraint_0_100": (0, 100),
    "tfeq_uncontrolled_eating_0_100": (0, 100),
    "tfeq_emotional_eating_0_100": (0, 100),
    "maia_noticing_mean": (0, 5), "maia_body_listening_mean": (0, 5),
    "audit_c_total": (0, 12),
    "dmq_enhancement_sum": (3, 15), "dmq_social_sum": (3, 15),
    "dmq_conformity_sum": (3, 15), "dmq_coping_sum": (3, 15),
    "byaacq_total": (0, 24), "pgsi_total": (0, 27),
    "hunger_endorsed_n": (0, 2), "asrs_count": (0, 6),
    "ucla3_total": (3, 9), "eds_total": (5, 25), "rmeq_total": (4, 25),
    "phq9_total": (0, 27), "scoff_total": (0, 5), "ace_total": (0, 10),
    "pgsi_problem_gambling": (0, 1), "phq9_self_harm_positive": (0, 1),
    "phq9_severity_alert": (0, 1),
    "macarthur_community": (1, 10), "macarthur_us": (1, 10),
}
