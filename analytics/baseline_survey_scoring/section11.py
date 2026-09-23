"""Maps scored baseline rows to the Section 11 distress-signal codes that
backend/app/distress.py expects (BASELINE_SIGNALS). Consumed by
backend/app/management/commands/import_baseline_distress_flags.py, which
maps each flagged row to a User and writes a DistressFlag.

free_text_risk is deliberately excluded: definitions.py has no free-text
column, so this pipeline has nothing to read for it, and judging a
disclosure is a staff call in any case. Route it manually, the way it
already is.
"""

from __future__ import annotations

from typing import Dict, List

import pandas as pd

# code (as stored in DistressFlag.signals / app.distress.BASELINE_SIGNALS) ->
# the scores column that is True when it fires.
SIGNAL_COLUMNS: Dict[str, str] = {
    "phq9_self_harm": "phq9_self_harm_positive",
    "phq9_severity": "phq9_severity_alert",
    "scoff": "scoff_positive",
    "audit_c": "audit_c_alert",
    "problem_gambling": "pgsi_problem_gambling",
    "food_insecurity": "hunger_positive",
}


def row_signals(row: pd.Series) -> List[str]:
    return [code for code, column in SIGNAL_COLUMNS.items() if row.get(column) is True]


def section11_signals(scores: pd.DataFrame) -> pd.Series:
    """One list of signal codes per row of `scores` (score_participants()[0]).

    Empty list where nothing is positive or the columns are missing.
    """
    missing = [c for c in SIGNAL_COLUMNS.values() if c not in scores.columns]
    if missing:
        raise ValueError(f"scores is missing columns section11_signals needs: {missing}")
    return scores.apply(row_signals, axis=1)
