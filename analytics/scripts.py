from __future__ import annotations

import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
import scipy.stats as stats
import matplotlib.pyplot as plt
import seaborn as sns


# DATA LOADING
# Source tables: app_user, app_ema, app_emaitemresponse, app_jitailog,
#               app_heartratesample, app_stresssample, app_engagementlog,
#               app_phonetelemetry, app_wearabledevice
# Column names follow analysis-resources/data-dictionary.md and
# Resources/react_schema.csv. Loaders bind to the live/seeded REACT database
# through the Django ORM (see _ensure_django below); the pure-DataFrame
# analysis functions further down never touch the database.


_DJANGO_READY = False


def _ensure_django() -> None:
    """
    Bootstrap the Django ORM so loaders can query app.models.

    Idempotent: safe to call from every loader. Adds backend/ to sys.path,
    points DJANGO_SETTINGS_MODULE at project.settings, and calls
    django.setup() exactly once per process. Import-time cost is paid lazily
    so `import scripts` works without a database (the compute_* functions
    below operate purely on DataFrames).

    Example:
        _ensure_django()            # first call configures Django
        _ensure_django()            # subsequent calls are no-ops
    Source of data:
        backend/project/settings.py (reads DATABASE_URL / SECRET_KEY from env).
    """
    global _DJANGO_READY
    if _DJANGO_READY:
        return

    import os
    import sys
    import django

    backend_dir = Path(__file__).resolve().parent.parent / "backend"
    if str(backend_dir) not in sys.path:
        sys.path.insert(0, str(backend_dir))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "project.settings")
    django.setup()
    _DJANGO_READY = True


def _to_utc(df: pd.DataFrame, columns: List[str]) -> pd.DataFrame:
    """
    Coerce the named columns to timezone-aware UTC pandas timestamps.

    Example:
        _to_utc(df, ["sent_at"])   # df["sent_at"] becomes tz-aware UTC
    Source of data:
        Any DataFrame built from ORM .values(); Django returns tz-aware
        datetimes when USE_TZ is enabled, this just normalizes to UTC.
    """
    for column in columns:
        if column in df.columns:
            df[column] = pd.to_datetime(df[column], utc=True, errors="coerce")
    return df


def _filter_dates(
    queryset,
    field: str,
    start_date: Optional[datetime.date],
    end_date: Optional[datetime.date],
):
    """
    Apply inclusive start/end date bounds to a queryset on a datetime field.

    Example:
        qs = _filter_dates(EMA.objects.all(), "sent_at", d1, d2)
    Source of data:
        A Django queryset; `field` is the datetime column to bound.
    """
    if start_date is not None:
        queryset = queryset.filter(**{f"{field}__date__gte": start_date})
    if end_date is not None:
        queryset = queryset.filter(**{f"{field}__date__lte": end_date})
    return queryset


def load_users(user_id: Optional[int] = None) -> pd.DataFrame:
    """
    Purpose:
        Load participant records from app_user. Needed by retention, dropout,
        dosage, and biomarker-rate functions that take a user_df.

    Inputs:
        user_id - int or None. If None, returns all participants.

    Outputs:
        pd.DataFrame with columns:
            user_id, email, first_name, last_name, birthdate, gender,
            is_enrolled, enrolled_at.
        One row per participant. enrolled_at is timezone-aware (UTC).

    Example:
        load_users(user_id=42)  ->  1-row DataFrame for participant 42
    Source of data:
        app_user (Django model app.models.User).
    """
    _ensure_django()
    from app.models import User

    queryset = User.objects.all()
    if user_id is not None:
        queryset = queryset.filter(user_id=user_id)

    df = pd.DataFrame(
        queryset.values(
            "user_id", "email", "first_name", "last_name", "birthdate",
            "gender", "is_enrolled", "enrolled_at",
        )
    )
    return _to_utc(df, ["enrolled_at"])


def load_ema(
    user_id: Optional[int] = None,
    start_date: Optional[datetime.date] = None,
    end_date: Optional[datetime.date] = None,
) -> pd.DataFrame:
    """
    Purpose:
        Load EMA prompt records from app_ema into a DataFrame, filtered by
        participant and/or date range.

    Inputs:
        user_id    - int or None. If None, returns records for all participants.
        start_date - datetime.date or None. Inclusive lower bound on sent_at.
        end_date   - datetime.date or None. Inclusive upper bound on sent_at.

    Outputs:
        pd.DataFrame with columns matching app_ema:
            id, user_id, prompt_id, sent_at, responded_at, status, ema_type,
            source_jitai_log_id, outcome_window_start, outcome_window_end,
            expires_at, mood, stress, energy.
        One row per EMA prompt. Timestamps are timezone-aware (UTC).

    Example:
        load_ema(user_id=42, start_date=date(2026,9,1))
        ->  DataFrame of participant 42's prompts sent on/after 2026-09-01
    Source of data:
        app_ema (Django model app.models.EMA). mood/stress/energy are the
        top-level summary Likert items (1-7); the JITAI trigger signal itself
        lives in app_emaitemresponse (see load_ema_item_responses).
    """
    _ensure_django()
    from app.models import EMA

    queryset = _filter_dates(EMA.objects.all(), "sent_at", start_date, end_date)
    if user_id is not None:
        queryset = queryset.filter(user_id=user_id)

    df = pd.DataFrame(
        queryset.values(
            "id", "user_id", "prompt_id", "sent_at", "responded_at", "status",
            "ema_type", "source_jitai_log_id", "outcome_window_start",
            "outcome_window_end", "expires_at", "mood", "stress", "energy",
        )
    )
    return _to_utc(
        df,
        ["sent_at", "responded_at", "outcome_window_start",
         "outcome_window_end", "expires_at"],
    )


def load_jitai_log(
    user_id: Optional[int] = None,
    start_date: Optional[datetime.date] = None,
    end_date: Optional[datetime.date] = None,
) -> pd.DataFrame:
    """
    Purpose:
        Load JITAI decision-point records from app_jitailog into a DataFrame.
        Covers all decision columns, trigger context, and delivery funnel columns.

    Inputs:
        user_id    - int or None. If None, returns records for all participants.
        start_date - datetime.date or None. Inclusive lower bound on triggered_at.
        end_date   - datetime.date or None. Inclusive upper bound on triggered_at.

    Outputs:
        pd.DataFrame with columns matching app_jitailog:
            id, user_id, prompt_id, triggered_at, decision_point_id,
            decision_made_at, trigger_reason, trigger_signal, observed_mssd,
            randomization_probability, randomization_draw, send_prompt,
            eligible_prompt_ids, hr_at_trigger, stress_at_trigger, ema_id,
            ema_mood, ema_stress, ema_energy, status, delivery_status,
            push_sent_at, device_received_at, receipt_reported_at,
            receipt_platform, receipt_app_state, delivery_error.
        One row per decision point. Timestamps are timezone-aware (UTC).

    Example:
        load_jitai_log(user_id=42)  ->  DataFrame of every decision point for 42
    Source of data:
        app_jitailog (Django model app.models.JITAILog).
    """
    _ensure_django()
    from app.models import JITAILog

    queryset = _filter_dates(
        JITAILog.objects.all(), "triggered_at", start_date, end_date
    )
    if user_id is not None:
        queryset = queryset.filter(user_id=user_id)

    df = pd.DataFrame(
        queryset.values(
            "id", "user_id", "prompt_id", "triggered_at", "decision_point_id",
            "decision_made_at", "trigger_reason", "trigger_signal",
            "observed_mssd", "randomization_probability", "randomization_draw",
            "send_prompt", "eligible_prompt_ids", "hr_at_trigger",
            "stress_at_trigger", "ema_id", "ema_mood", "ema_stress",
            "ema_energy", "status", "delivery_status", "push_sent_at",
            "device_received_at", "receipt_reported_at", "receipt_platform",
            "receipt_app_state", "delivery_error",
        )
    )
    return _to_utc(
        df,
        ["triggered_at", "decision_made_at", "push_sent_at",
         "device_received_at", "receipt_reported_at"],
    )


def load_heart_rate(
    user_id: Optional[int] = None,
    start_date: Optional[datetime.date] = None,
    end_date: Optional[datetime.date] = None,
) -> pd.DataFrame:
    """
    Purpose:
        Load heart-rate samples from app_heartratesample into a DataFrame.
        Cadence is ~15 seconds from Labfront; subject to 2-5 min sync lag.

    Inputs:
        user_id    - int or None. If None, returns records for all participants.
        start_date - datetime.date or None. Inclusive lower bound on timestamp.
        end_date   - datetime.date or None. Inclusive upper bound on timestamp.

    Outputs:
        pd.DataFrame with columns: id, user_id, timestamp, bpm, source.
        bpm = 0 or NaN is treated as inferred non-wear (no explicit wear flag).
        Timestamps are timezone-aware (UTC).

    Example:
        load_heart_rate(user_id=42)  ->  DataFrame of 42's bpm samples
    Source of data:
        app_heartratesample (Django model app.models.HeartRateSample).
    """
    _ensure_django()
    from app.models import HeartRateSample

    queryset = _filter_dates(
        HeartRateSample.objects.all(), "timestamp", start_date, end_date
    )
    if user_id is not None:
        queryset = queryset.filter(user_id=user_id)

    df = pd.DataFrame(
        queryset.values("id", "user_id", "timestamp", "bpm", "source")
    )
    return _to_utc(df, ["timestamp"])


def load_stress_samples(
    user_id: Optional[int] = None,
    start_date: Optional[datetime.date] = None,
    end_date: Optional[datetime.date] = None,
) -> pd.DataFrame:
    """
    Purpose:
        Load Garmin stress-score samples from app_stresssample into a DataFrame.
        Garmin stress (0-100) is a black-box HRV-derived metric, NOT a function
        of raw heart rate.

    Inputs:
        user_id    - int or None. If None, returns records for all participants.
        start_date - datetime.date or None. Inclusive lower bound on timestamp.
        end_date   - datetime.date or None. Inclusive upper bound on timestamp.

    Outputs:
        pd.DataFrame with columns: id, user_id, timestamp, stress_score, source.
        Timestamps are timezone-aware (UTC).

    Example:
        load_stress_samples(user_id=42)  ->  DataFrame of 42's stress scores
    Source of data:
        app_stresssample (Django model app.models.StressSample).
    """
    _ensure_django()
    from app.models import StressSample

    queryset = _filter_dates(
        StressSample.objects.all(), "timestamp", start_date, end_date
    )
    if user_id is not None:
        queryset = queryset.filter(user_id=user_id)

    df = pd.DataFrame(
        queryset.values("id", "user_id", "timestamp", "stress_score", "source")
    )
    return _to_utc(df, ["timestamp"])


def load_engagement_log(
    user_id: Optional[int] = None,
    start_date: Optional[datetime.date] = None,
    end_date: Optional[datetime.date] = None,
) -> pd.DataFrame:
    """
    Purpose:
        Load user engagement events (EMA opened/dismissed/completed,
        notification tapped/dismissed) from app_engagementlog.

    Inputs:
        user_id    - int or None. If None, returns records for all participants.
        start_date - datetime.date or None. Inclusive lower bound on occurred_at.
        end_date   - datetime.date or None. Inclusive upper bound on occurred_at.

    Outputs:
        pd.DataFrame with columns:
            id, user_id, jitai_log_id, event_type, occurred_at, recorded_at.
        One row per engagement event. Timestamps are timezone-aware (UTC).

    Example:
        load_engagement_log(user_id=42)  ->  DataFrame of 42's tap/open events
    Source of data:
        app_engagementlog (Django model app.models.EngagementLog).
    """
    _ensure_django()
    from app.models import EngagementLog

    queryset = _filter_dates(
        EngagementLog.objects.all(), "occurred_at", start_date, end_date
    )
    if user_id is not None:
        queryset = queryset.filter(user_id=user_id)

    df = pd.DataFrame(
        queryset.values(
            "id", "user_id", "jitai_log_id", "event_type",
            "occurred_at", "recorded_at",
        )
    )
    return _to_utc(df, ["occurred_at", "recorded_at"])


def load_phone_telemetry(
    user_id: Optional[int] = None,
    start_date: Optional[datetime.date] = None,
    end_date: Optional[datetime.date] = None,
) -> pd.DataFrame:
    """
    Purpose:
        Load raw app-usage events from app_phonetelemetry for diagnostics and
        latency analysis.

    Inputs:
        user_id    - int or None. If None, returns records for all participants.
        start_date - datetime.date or None. Inclusive lower bound on occurred_at.
        end_date   - datetime.date or None. Inclusive upper bound on occurred_at.

    Outputs:
        pd.DataFrame with columns:
            id, user_id, session_id, event_type, occurred_at, recorded_at,
            screen_name, latency_ms, metadata.
        One row per event. Timestamps are timezone-aware (UTC).

    Example:
        load_phone_telemetry(user_id=42)  ->  DataFrame of 42's app events
    Source of data:
        app_phonetelemetry (Django model app.models.PhoneTelemetry).
    """
    _ensure_django()
    from app.models import PhoneTelemetry

    queryset = _filter_dates(
        PhoneTelemetry.objects.all(), "occurred_at", start_date, end_date
    )
    if user_id is not None:
        queryset = queryset.filter(user_id=user_id)

    df = pd.DataFrame(
        queryset.values(
            "id", "user_id", "session_id", "event_type", "occurred_at",
            "recorded_at", "screen_name", "latency_ms", "metadata",
        )
    )
    return _to_utc(df, ["occurred_at", "recorded_at"])


def load_wearable_devices() -> pd.DataFrame:
    """
    Purpose:
        Load all wearable device records from app_wearabledevice (one per
        participant; OneToOne with user).

    Inputs:
        None.

    Outputs:
        pd.DataFrame with columns:
            id, user_id, labfront_participant_id, is_active, last_synced_at.
        One row per enrolled participant who has registered a device.

    Example:
        load_wearable_devices()  ->  DataFrame, one row per registered device
    Source of data:
        app_wearabledevice (Django model app.models.WearableDevice).
    """
    _ensure_django()
    from app.models import WearableDevice

    df = pd.DataFrame(
        WearableDevice.objects.values(
            "id", "user_id", "labfront_participant_id",
            "is_active", "last_synced_at",
        )
    )
    return _to_utc(df, ["last_synced_at"])


def load_ema_item_responses(
    user_id: Optional[int] = None,
    start_date: Optional[datetime.date] = None,
    end_date: Optional[datetime.date] = None,
) -> pd.DataFrame:
    """
    Purpose:
        Load per-item EMA responses from app_emaitemresponse. Required to
        reconstruct the exact MSSD trigger signal used by the decision engine,
        which reads value_numeric where item_id = 'B1' (energy) and item_id =
        'B2' (stress). Filtering on item_id and pivoting on sub_item_id gives
        the per-prompt EMA series that calculate_mssd() consumed.

    Inputs:
        user_id    - int or None. If None, returns records for all participants.
        start_date - datetime.date or None. Inclusive lower bound on the
                     parent EMA's sent_at timestamp.
        end_date   - datetime.date or None. Inclusive upper bound on the
                     parent EMA's sent_at timestamp.

    Outputs:
        pd.DataFrame with columns matching app_emaitemresponse:
            id, ema_id, item_id, sub_item_id, response_type,
            value_numeric, value_choice, value_choices.
        Also includes user_id and sent_at joined from the parent app_ema row
        so callers can group and sort by participant and prompt time without a
        second join.
        One row per (ema_id, sub_item_id). Timestamps are timezone-aware (UTC).

    Usage example (reconstruct B1 energy series for MSSD audit):
        item_df = load_ema_item_responses(user_id=42)
        b1 = item_df[item_df['item_id'] == 'B1'].sort_values('sent_at')
        mssd_series = b1['value_numeric']

    Example:
        load_ema_item_responses(user_id=42)
        ->  DataFrame, one row per answered sub-item, with user_id + sent_at
    Source of data:
        app_emaitemresponse joined to app_ema (Django model
        app.models.EMAItemResponse; parent fields via ema__user_id/ema__sent_at).
    """
    _ensure_django()
    from app.models import EMAItemResponse

    queryset = _filter_dates(
        EMAItemResponse.objects.all(), "ema__sent_at", start_date, end_date
    )
    if user_id is not None:
        queryset = queryset.filter(ema__user_id=user_id)

    df = pd.DataFrame(
        queryset.values(
            "id", "ema_id", "item_id", "sub_item_id", "response_type",
            "value_numeric", "value_choice", "value_choices",
            "ema__user_id", "ema__sent_at",
        )
    )
    if not df.empty:
        df = df.rename(
            columns={"ema__user_id": "user_id", "ema__sent_at": "sent_at"}
        )
    return _to_utc(df, ["sent_at"])


#  EMA FEASIBILITY METRICS
# Preregistered feasibility benchmark: 75% completed check-in rate.
# Analytic requirement for AR(1) parameter recovery: 80% (reported separately).


def compute_ema_response_rate(ema_df: pd.DataFrame) -> pd.DataFrame:
    """
    Purpose:
        Compute completed EMA check-in rate per participant and overall.
        A prompt is 'completed' when all required items are submitted within
        the 60-minute response window (status = 'completed').
        Delivery failures are excluded from the denominator.

    Inputs:
        ema_df - pd.DataFrame from load_ema(). Must contain columns:
                 user_id, status, sent_at, responded_at.

    Outputs:
        pd.DataFrame with columns:
            user_id, delivered, completed, response_rate_pct.
        Last row is the cohort-level aggregate (user_id = 'ALL').
        response_rate_pct = completed / delivered * 100.
        Benchmark: >= 75%.

    Example:
        rows: u1 [completed, completed, expired], u2 [completed]
        ->  u1: delivered=3 completed=2 rate=66.7; u2: 1/1=100; ALL: 3/4=75.0
    Source of data:
        app_ema.status (via load_ema()).
    """
    if ema_df is None or ema_df.empty:
        return pd.DataFrame(
            columns=["user_id", "delivered", "completed", "response_rate_pct"]
        )

    df = ema_df.copy()
    df["_completed"] = df["status"].eq("completed")

    grouped = df.groupby("user_id").agg(
        delivered=("status", "size"),
        completed=("_completed", "sum"),
    ).reset_index()
    grouped["response_rate_pct"] = (
        100.0 * grouped["completed"] / grouped["delivered"]
    )

    total = pd.DataFrame([{
        "user_id": "ALL",
        "delivered": int(df.shape[0]),
        "completed": int(df["_completed"].sum()),
        "response_rate_pct": 100.0 * df["_completed"].sum() / df.shape[0],
    }])

    return pd.concat([grouped, total], ignore_index=True)


def flag_in_window_completions(
    ema_df: pd.DataFrame,
    window_minutes: int = 60,
) -> pd.Series:
    """
    Purpose:
        Identify which EMA rows were responded to within the required time
        window. Used to separate in-window completions from late responses.

    Inputs:
        ema_df         - pd.DataFrame from load_ema(). Must contain columns:
                         sent_at, responded_at.
        window_minutes - int. Response window in minutes (default 60).

    Outputs:
        pd.Series of bool, same index as ema_df.
        TRUE  = responded_at - sent_at <= window_minutes AND responded_at is
                not null.
        FALSE = unanswered, expired, or responded outside window.

    Example:
        sent 10:00, responded 10:30  ->  True   (30 <= 60)
        sent 10:00, responded 11:30  ->  False  (90 > 60)
        sent 10:00, responded NaT    ->  False
    Source of data:
        app_ema.sent_at / responded_at (via load_ema()).
    """
    if ema_df is None or ema_df.empty:
        return pd.Series([], dtype=bool)

    latency = (
        pd.to_datetime(ema_df["responded_at"], utc=True, errors="coerce")
        - pd.to_datetime(ema_df["sent_at"], utc=True, errors="coerce")
    ).dt.total_seconds() / 60.0

    in_window = latency.le(window_minutes) & latency.ge(0)
    return in_window.fillna(False).astype(bool)


def compute_response_latency(ema_df: pd.DataFrame) -> pd.Series:
    """
    Purpose:
        Compute response latency (time from prompt delivery to submission) in
        minutes for each EMA row. Returns NaN for unanswered prompts.

    Inputs:
        ema_df - pd.DataFrame from load_ema(). Must contain columns:
                 sent_at, responded_at.

    Outputs:
        pd.Series of float (minutes), same index as ema_df.
        NaN for rows where responded_at is null.
        Latency > 60 min indicates an out-of-window response.

    Example:
        sent 10:00, responded 10:12  ->  12.0
        sent 10:00, responded NaT    ->  NaN
    Source of data:
        app_ema.sent_at / responded_at (via load_ema()).
    """
    if ema_df is None or ema_df.empty:
        return pd.Series([], dtype=float)

    latency = (
        pd.to_datetime(ema_df["responded_at"], utc=True, errors="coerce")
        - pd.to_datetime(ema_df["sent_at"], utc=True, errors="coerce")
    ).dt.total_seconds() / 60.0
    return latency


def compute_item_missingness(ema_df: pd.DataFrame) -> pd.DataFrame:
    """
    Purpose:
        Compute the per-item (mood, stress, energy) missingness rate across all
        delivered EMA prompts, broken down by participant. These are the
        top-level denormalized fields on app_ema.

        For per-B-item (B1-B8) missingness matching the analysis plan, use a
        separate function operating on load_ema_item_responses() instead.

    Inputs:
        ema_df - pd.DataFrame from load_ema(). Must contain columns:
                 user_id, mood, stress, energy.

    Outputs:
        pd.DataFrame with columns:
            user_id, item, missing_count, delivered_count, missing_pct.
        One row per (user_id, item) combination.
        Last block of rows is the cohort-level aggregate (user_id = 'ALL').

    Example:
        u1 has 3 prompts, mood null on 1  ->  (u1, mood): 1/3 = 33.3%
    Source of data:
        app_ema.mood / stress / energy (via load_ema()).
    """
    items = ["mood", "stress", "energy"]
    if ema_df is None or ema_df.empty:
        return pd.DataFrame(
            columns=["user_id", "item", "missing_count",
                     "delivered_count", "missing_pct"]
        )

    records = []

    def _rows_for(label, frame):
        for item in items:
            missing = int(frame[item].isna().sum())
            delivered = int(frame.shape[0])
            records.append({
                "user_id": label,
                "item": item,
                "missing_count": missing,
                "delivered_count": delivered,
                "missing_pct": 100.0 * missing / delivered if delivered else np.nan,
            })

    for user_id, frame in ema_df.groupby("user_id"):
        _rows_for(user_id, frame)
    _rows_for("ALL", ema_df)

    return pd.DataFrame.from_records(records)


# WITHIN-PERSON MSSD
# Core JITAI trigger signal: mean of squared successive differences over
# consecutive answered EMA items.
# Missingness suppression rule: a missing EMA suppresses the difference for
# both the missing row AND the immediately following answered prompt.

def compute_mssd(ema_series: pd.Series) -> float:
    """
    Purpose:
        Compute the Mean of Squared Successive Differences (MSSD) from a
        time-ordered sequence of Likert EMA responses. Applies the missingness
        suppression rule: NaN at position t suppresses both the (t-1, t) and
        (t, t+1) differences.

    Inputs:
        ema_series - pd.Series of float. Time-ordered Likert responses (1-7).
                     NaN encodes a missed / unanswered prompt.

    Outputs:
        float. MSSD over all valid consecutive answered pairs.
        Returns NaN if fewer than 2 valid consecutive pairs exist.

    Example:
        compute_mssd(pd.Series([4, 5, 3, np.nan, 6])) -> 2.5
        (diffs 1, -2 kept; the NaN suppresses the two diffs touching it;
         mean of 1^2 and 2^2 = 2.5)
    Source of data:
        app_emaitemresponse.value_numeric where item_id in {B1, B2}
        (via load_ema_item_responses()). Matches mssd_validation.empirical_mssd.
    """
    values = np.asarray(ema_series, dtype=float)
    diffs = np.diff(values)
    diffs = diffs[~np.isnan(diffs)]

    if diffs.size == 0:
        return float("nan")
    return float(np.mean(diffs ** 2))


def compute_run_in_mssd(ema_df: pd.DataFrame) -> pd.DataFrame:
    """
    Purpose:
        Compute within-person MSSD using only Week 1 (run-in baseline) EMA
        responses. Week 1 = enrolled_at through enrolled_at + 6 days.
        No JITAI randomization occurs during this window; data are used solely
        to calibrate individual thresholds for Weeks 2-5.

    Inputs:
        ema_df - pd.DataFrame from load_ema_item_responses() filtered to
                 item_id = 'B1' (energy) or 'B2' (stress), joined with user
                 enrolled_at. Must contain columns: user_id, sent_at,
                 value_numeric, enrolled_at.
                 Do NOT use mood from load_ema() - the decision engine reads
                 B1/B2 from app_emaitemresponse, not EMA.mood.

    Outputs:
        pd.DataFrame with columns: user_id, run_in_mssd.
        One row per participant. NaN if fewer than 2 valid pairs in Week 1.

    Example:
        participant answered B1 [4,5,3,6] during days 1-6  ->  run_in_mssd row
    Source of data:
        app_emaitemresponse.value_numeric (B1/B2) + app_user.enrolled_at.
    """
    columns = ["user_id", "run_in_mssd"]
    if ema_df is None or ema_df.empty:
        return pd.DataFrame(columns=columns)

    df = ema_df.copy()
    df["sent_at"] = pd.to_datetime(df["sent_at"], utc=True, errors="coerce")
    df["enrolled_at"] = pd.to_datetime(
        df["enrolled_at"], utc=True, errors="coerce"
    )
    run_in_end = df["enrolled_at"] + pd.Timedelta(days=7)
    week1 = df[(df["sent_at"] >= df["enrolled_at"]) & (df["sent_at"] < run_in_end)]

    records = []
    for user_id, frame in week1.groupby("user_id"):
        series = frame.sort_values("sent_at")["value_numeric"]
        records.append({"user_id": user_id, "run_in_mssd": compute_mssd(series)})

    return pd.DataFrame.from_records(records, columns=columns)


def compute_within_person_threshold(
    ema_df: pd.DataFrame,
    quantile: float = 0.80,
) -> pd.Series:
    """
    Purpose:
        Compute each participant's within-person MSSD eligibility threshold as
        an expanding quantile over their EMA history. An expanding window
        (shifted forward by one observation) is used so the threshold at time t
        is estimated only from data before t, preventing look-ahead bias.

    Inputs:
        ema_df   - pd.DataFrame from load_ema_item_responses() filtered to
                   item_id = 'B1' (energy) or 'B2' (stress). Must contain
                   columns: user_id, sent_at, value_numeric.
                   Do NOT use mood from load_ema() - the live decision engine
                   reads B1/B2 from app_emaitemresponse to compute observed_mssd
                   stored in JITAILog. Using mood would produce threshold values
                   that disagree with what the engine actually computed.
        quantile - float in (0, 1). Threshold percentile (default 0.80,
                   PI sign-off 2026-07-06).

    Outputs:
        pd.Series of float, indexed by ema_df index.
        Value at row i = quantile of MSSD distribution computed from all
        answered EMA pairs before row i for that participant.

    Note on MSSD formulation: the live engine uses a rolling(window=3) MSSD
    rather than cumulative MSSD over all answered pairs. For an exact audit
    against JITAILog.observed_mssd, match the rolling formulation. For the
    overall feasibility report, cumulative MSSD is acceptable and aligns with
    the construct-validity harness in mssd_validation.py.

    Example:
        per-prompt squared diffs [., 1, 4, 1, 9] -> expanding 80th-pct, shift(1)
        so each row's threshold only sees strictly earlier squared diffs.
    Source of data:
        app_emaitemresponse.value_numeric (B1/B2). Mirrors
        decision_engine.add_within_person_threshold (expanding, shifted).
    """
    if ema_df is None or ema_df.empty:
        return pd.Series([], dtype=float)

    df = ema_df.copy()
    df["sent_at"] = pd.to_datetime(df["sent_at"], utc=True, errors="coerce")
    df = df.sort_values(["user_id", "sent_at"])

    squared_diff = (
        df.groupby("user_id")["value_numeric"].diff() ** 2
    )

    threshold = (
        squared_diff.groupby(df["user_id"])
        .transform(
            lambda s: s.expanding(min_periods=3).quantile(quantile).shift(1)
        )
    )

    return threshold.reindex(ema_df.index)


def compute_expected_mssd(sigma: float, rho: float) -> float:
    """
    Purpose:
        Compute the theoretical expected MSSD for an AR(1) process with given
        parameters. Used as the ground-truth benchmark for synthetic data
        validation.

    Inputs:
        sigma - float. Stationary standard deviation of the AR(1) process.
        rho   - float. Lag-1 autocorrelation in (-1, 1).

    Outputs:
        float. Expected MSSD = 2 * sigma^2 * (1 - rho).

    Example:
        compute_expected_mssd(1.0, 0.5) -> 1.0
    Source of data:
        Latent AR(1) parameters from the synthetic generator (Derived).
    """
    return 2.0 * float(sigma) ** 2 * (1.0 - float(rho))


def compute_ar1_parameters(ema_series: pd.Series) -> Tuple[float, float]:
    """
    Purpose:
        Estimate AR(1) parameters (rho_hat, sigma_hat) from an observed EMA
        time series. Parameter recovery degrades below ~80% response rate;
        this is an analytic requirement distinct from the 75% feasibility
        benchmark and should be reported as a design recommendation for the
        full trial.

    Inputs:
        ema_series - pd.Series of float. Time-ordered Likert responses (1-7).
                     NaN encodes missed prompts; only answered pairs are used.

    Outputs:
        Tuple[float, float]: (rho_hat, sigma_hat).
            rho_hat   - estimated lag-1 autocorrelation.
            sigma_hat - estimated residual standard deviation (more robust
                        than rho_hat at low response rates).
        Both are NaN if fewer than 3 answered observations exist.

    Example:
        compute_ar1_parameters(pd.Series([4,5,4,6,5,4]))  ->  (rho_hat, sigma_hat)
    Source of data:
        app_emaitemresponse.value_numeric (B1/B2). Mirrors
        mssd_validation.recover_ar1_params.
    """
    z = np.asarray(ema_series, dtype=float)
    answered = z[~np.isnan(z)]

    if answered.size < 3:
        return float("nan"), float("nan")

    mean = answered.mean()
    sigma_hat = float(answered.std(ddof=1))

    a = z[:-1]
    b = z[1:]
    pair = ~np.isnan(a) & ~np.isnan(b)
    a = a[pair] - mean
    b = b[pair] - mean

    denom = np.sum(a ** 2)
    if a.size < 2 or denom <= 0:
        return float("nan"), sigma_hat

    rho_hat = float(np.sum(a * b) / denom)
    return rho_hat, sigma_hat


# MSSD FORMULATION TAXONOMY (see Tien_Le_MSSD_v1_0.ipynb)
#   * Base MSSD  (compute_mssd)        : mean squared successive difference over
#                                        answered adjacent pairs. This is what the
#                                        decision engine ROLLS (window=3) to produce
#                                        JITAILog.observed_mssd, the real-time
#                                        trigger signal. For an exact per-decision
#                                        audit, read observed_mssd straight from
#                                        the JITAILog (do not recompute offline).
#   * LT-MSSD    (compute_lt_mssd)     : long-term / day-to-day instability =
#                                        (1/(J-1)) * sum (xbar_{j+1} - xbar_j)^2,
#                                        where xbar_j is a participant's DAILY MEAN.
#                                        This is the OVERALL study-level volatility
#                                        descriptor reported per participant/cohort.


def compute_lt_mssd(ema_df: pd.DataFrame) -> pd.DataFrame:
    """
    Purpose:
        Compute Long-Term MSSD (LT-MSSD) per participant: the day-to-day
        instability of daily-mean EMA scores. Unlike base MSSD (which the
        engine rolls for the real-time trigger), LT-MSSD collapses each study
        day to its mean first, then takes squared successive differences
        between consecutive daily means:

            LT-MSSD = (1 / (J-1)) * sum_{j=1}^{J-1} (xbar_{j+1} - xbar_j)^2

    Inputs:
        ema_df - pd.DataFrame of the per-prompt trigger signal. Must contain
                 columns: user_id, sent_at, value_numeric. Typically the
                 composite B1/B2 series from _prep_trigger_signal().

    Outputs:
        pd.DataFrame with columns: user_id, lt_mssd.
        One row per participant. NaN if fewer than 2 answered days.

    Example:
        daily means [3.0, 3.5, 2.0]  ->  ((0.5^2)+(1.5^2))/2 = 1.25
    Source of data:
        app_emaitemresponse.value_numeric (B1/B2), via load_ema_item_responses()
        collapsed to daily means. Matches lt_mssd() in Tien_Le_MSSD_v1_0.ipynb.
    """
    columns = ["user_id", "lt_mssd"]
    if ema_df is None or ema_df.empty:
        return pd.DataFrame(columns=columns)

    df = ema_df.copy()
    df["sent_at"] = pd.to_datetime(df["sent_at"], utc=True, errors="coerce")
    df["day"] = df["sent_at"].dt.date

    records = []
    for user_id, frame in df.groupby("user_id"):
        daily_means = (
            frame.dropna(subset=["value_numeric"])
            .groupby("day")["value_numeric"].mean()
            .sort_index()
        )
        records.append({"user_id": user_id, "lt_mssd": compute_mssd(daily_means)})

    return pd.DataFrame.from_records(records, columns=columns)


def _prep_trigger_signal(
    item_df: pd.DataFrame,
    user_df: Optional[pd.DataFrame] = None,
) -> pd.DataFrame:
    """
    Collapse per-item B1/B2 responses into one composite per-prompt series.

    The decision engine's trigger signal is built from B1 (energy) and B2
    (stress). To avoid interleaving two different constructs into one MSSD
    series, this collapses each prompt to a single composite value =
    mean of the available B1/B2 value_numeric for that prompt. enrolled_at is
    merged from user_df when supplied (needed by compute_run_in_mssd).

    Example:
        prompt with B1=5, B2=3  ->  one row value_numeric=4.0
    Source of data:
        app_emaitemresponse (B1/B2) + app_user.enrolled_at.
    """
    columns = ["user_id", "ema_id", "sent_at", "value_numeric", "enrolled_at"]
    if item_df is None or item_df.empty:
        return pd.DataFrame(columns=columns)

    b1b2 = item_df[item_df["item_id"].isin(["B1", "B2"])].copy()
    collapsed = (
        b1b2.groupby(["user_id", "ema_id", "sent_at"], as_index=False)
        ["value_numeric"].mean()
    )

    if user_df is not None and not user_df.empty:
        collapsed = collapsed.merge(
            user_df[["user_id", "enrolled_at"]], on="user_id", how="left"
        )
    else:
        collapsed["enrolled_at"] = pd.NaT

    return collapsed


# WEARABLE / WEAR TIME
# No explicit wear flag in Garmin exports; non-wear is inferred.
# Waking window: 8:00 AM - 10:00 PM local time (14 h/day).
# Benchmark: >= 8 h wear / day, >= 5 days / week.

def flag_non_wear(hr_df: pd.DataFrame) -> pd.Series:
    """
    Purpose:
        Infer non-wear periods from heart-rate samples. A sample is flagged as
        non-wear if bpm is 0 or null. Garmin exports carry no explicit is_worn
        flag; this is the primary inference method.

    Inputs:
        hr_df - pd.DataFrame from load_heart_rate(). Must contain column: bpm.

    Outputs:
        pd.Series of bool, same index as hr_df.
        TRUE = inferred non-wear (bpm is 0 or NaN).

    Example:
        bpm [62, 0, NaN, 71]  ->  [False, True, True, False]
    Source of data:
        app_heartratesample.bpm (via load_heart_rate()).
    """
    if hr_df is None or hr_df.empty:
        return pd.Series([], dtype=bool)

    bpm = pd.to_numeric(hr_df["bpm"], errors="coerce")
    return (bpm.isna() | bpm.eq(0)).astype(bool)


def identify_wear_gaps(
    hr_df: pd.DataFrame,
    gap_threshold_minutes: int = 120,
) -> pd.DataFrame:
    """
    Purpose:
        Identify contiguous stretches of missing or non-wear HR data during
        waking hours (8:00 AM - 10:00 PM) that exceed the gap threshold.
        A gap > 2 hours is treated as a non-wear episode for wear-time
        calculation.

    Inputs:
        hr_df                 - pd.DataFrame from load_heart_rate(). Must
                                contain columns: user_id, timestamp, bpm.
        gap_threshold_minutes - int. Minimum gap duration to flag (default 120).

    Outputs:
        pd.DataFrame with columns:
            user_id, date, gap_start, gap_end, gap_duration_minutes.
        One row per identified wear gap during waking hours.

    Example:
        worn 09:00 then next worn sample 12:30  ->  one 210-min gap row
    Note:
        Gaps are measured within each waking day AND against the window edges:
        the span from 08:00 to the first worn sample and from the last worn
        sample to 22:00 count as gaps too, so a morning-only wear day is not
        mistaken for full coverage.
    Source of data:
        app_heartratesample.timestamp / bpm (via load_heart_rate()).
    """
    columns = ["user_id", "date", "gap_start", "gap_end", "gap_duration_minutes"]
    if hr_df is None or hr_df.empty:
        return pd.DataFrame(columns=columns)

    df = hr_df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    df = df[~flag_non_wear(df).values]
    df = df.dropna(subset=["timestamp"])

    hour = df["timestamp"].dt.hour
    df = df[(hour >= 8) & (hour < 22)]
    df["_date"] = df["timestamp"].dt.date

    records = []
    for (user_id, date), frame in df.groupby(["user_id", "_date"]):
        times = list(frame.sort_values("timestamp")["timestamp"])
        window_start = pd.Timestamp(f"{date}T08:00:00", tz="UTC")
        window_end = pd.Timestamp(f"{date}T22:00:00", tz="UTC")
        # boundaries let leading/trailing non-wear count as gaps
        edges = [window_start] + times + [window_end]
        for prev_time, curr_time in zip(edges[:-1], edges[1:]):
            gap = (curr_time - prev_time).total_seconds() / 60.0
            if gap > gap_threshold_minutes:
                records.append({
                    "user_id": user_id,
                    "date": date,
                    "gap_start": prev_time,
                    "gap_end": curr_time,
                    "gap_duration_minutes": float(gap),
                })

    return pd.DataFrame.from_records(records, columns=columns)


def compute_wear_time(hr_df: pd.DataFrame) -> pd.DataFrame:
    """
    Purpose:
        Compute daily wearable coverage percentage for each participant over the
        standardized waking window (8:00 AM - 10:00 PM, 14 h/day).
        Non-wear = gaps > 2 consecutive hours. Benchmark: >= 8 h/day,
        >= 5 days/week.

    Inputs:
        hr_df - pd.DataFrame from load_heart_rate(). Must contain columns:
                user_id, timestamp, bpm.

    Outputs:
        pd.DataFrame with columns:
            user_id, date, wear_minutes, wear_pct.
        wear_pct = (840 - gap_minutes) / 840 * 100 (840 min = 14 h).
        One row per (user_id, date).

    Example:
        one 210-min waking gap on a day  ->  wear_minutes=630, wear_pct=75.0
    Source of data:
        app_heartratesample.timestamp / bpm (via load_heart_rate()).
    """
    columns = ["user_id", "date", "wear_minutes", "wear_pct"]
    if hr_df is None or hr_df.empty:
        return pd.DataFrame(columns=columns)

    waking_minutes = 14 * 60
    df = hr_df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    worn = df[~flag_non_wear(df).values].dropna(subset=["timestamp"])
    hour = worn["timestamp"].dt.hour
    worn = worn[(hour >= 8) & (hour < 22)]

    gaps = identify_wear_gaps(hr_df)
    gap_by_day = (
        gaps.groupby(["user_id", "date"])["gap_duration_minutes"].sum()
        if not gaps.empty
        else pd.Series(dtype=float)
    )

    day_index = (
        worn.assign(date=worn["timestamp"].dt.date)
        .groupby(["user_id", "date"]).size().index
    )

    records = []
    for user_id, date in day_index:
        gap_minutes = float(gap_by_day.get((user_id, date), 0.0))
        wear_minutes = max(0.0, waking_minutes - gap_minutes)
        records.append({
            "user_id": user_id,
            "date": date,
            "wear_minutes": wear_minutes,
            "wear_pct": 100.0 * wear_minutes / waking_minutes,
        })

    return pd.DataFrame.from_records(records, columns=columns)


def flag_stale_sync(
    wearable_df: pd.DataFrame,
    threshold_hours: int = 24,
) -> pd.Series:
    """
    Purpose:
        Flag wearable device records whose last sync is older than the threshold.
        Stale syncs indicate the participant may not be wearing the device or
        the Labfront connection has lapsed. Note the inherent 2-5 min sync lag
        from Labfront processing is separate from this staleness check.

    Inputs:
        wearable_df     - pd.DataFrame from load_wearable_devices(). Must
                          contain column: last_synced_at.
        threshold_hours - int. Hours before a sync is considered stale
                          (default 24).

    Outputs:
        pd.Series of bool, same index as wearable_df.
        TRUE = last_synced_at is more than threshold_hours ago or is null.

    Example:
        last_synced_at 30h ago  ->  True;  2h ago  ->  False;  NaT  ->  True
    Source of data:
        app_wearabledevice.last_synced_at (via load_wearable_devices()).
    """
    if wearable_df is None or wearable_df.empty:
        return pd.Series([], dtype=bool)

    last_sync = pd.to_datetime(
        wearable_df["last_synced_at"], utc=True, errors="coerce"
    )
    cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=threshold_hours)
    return (last_sync.isna() | last_sync.lt(cutoff)).astype(bool)


# JITAI DECISION AUDIT
# Two-stage decision: (1) eligibility - observed_mssd >= within-person
# threshold; (2) randomization - randomization_draw < randomization_probability.
# These stages must not be conflated in any report.
# PI sign-offs (2026-07-06): p = 0.50, daily cap = 4, cooldown = 60 min.


def audit_decision_stages(jitai_df: pd.DataFrame) -> pd.DataFrame:
    """
    Purpose:
        Break down JITAI decision-point records by stage: total evaluated,
        eligible (MSSD threshold met), randomized-to-send, actually sent, and
        not sent. Surfaces the two-stage decision pipeline for reporting.

    Inputs:
        jitai_df - pd.DataFrame from load_jitai_log(). Must contain columns:
                   user_id, observed_mssd, randomization_draw,
                   randomization_probability, send_prompt, trigger_reason.

    Outputs:
        pd.DataFrame with columns:
            user_id, total_decision_points, eligible, randomized_to_send,
            sent, not_sent, pct_sent_of_eligible.
        One row per participant, plus a cohort-aggregate row (user_id = 'ALL').

    Example:
        10 decision points, 6 eligible, 3 sent  ->  pct_sent_of_eligible=50.0
    Source of data:
        app_jitailog (via load_jitai_log()). Eligibility inferred from a
        non-null randomization_draw (only eligible points are randomized).
    """
    columns = ["user_id", "total_decision_points", "eligible",
               "randomized_to_send", "sent", "not_sent", "pct_sent_of_eligible"]
    if jitai_df is None or jitai_df.empty:
        return pd.DataFrame(columns=columns)

    df = jitai_df.copy()
    df["_eligible"] = df["randomization_draw"].notna()
    df["_sent"] = df["send_prompt"].fillna(False).astype(bool)
    df["_randomized_to_send"] = (
        df["_eligible"]
        & df["randomization_draw"].lt(df["randomization_probability"])
    )

    def _summary(label, frame):
        eligible = int(frame["_eligible"].sum())
        sent = int(frame["_sent"].sum())
        return {
            "user_id": label,
            "total_decision_points": int(frame.shape[0]),
            "eligible": eligible,
            "randomized_to_send": int(frame["_randomized_to_send"].sum()),
            "sent": sent,
            "not_sent": int(frame.shape[0] - sent),
            "pct_sent_of_eligible": 100.0 * sent / eligible if eligible else np.nan,
        }

    records = [_summary(user_id, frame) for user_id, frame in df.groupby("user_id")]
    records.append(_summary("ALL", df))
    return pd.DataFrame.from_records(records, columns=columns)


def check_cooldown_compliance(
    jitai_df: pd.DataFrame,
    cooldown_minutes: int = 60,
) -> pd.DataFrame:
    """
    Purpose:
        Identify JITAI decision points where the gap between consecutive sent
        prompts for the same participant is shorter than the required cooldown.
        Violations indicate a decision-engine logic error.

    Inputs:
        jitai_df         - pd.DataFrame from load_jitai_log(). Must contain
                           columns: user_id, triggered_at, send_prompt.
        cooldown_minutes - int. Minimum required gap in minutes (default 60).

    Outputs:
        pd.DataFrame with columns:
            user_id, jitai_log_id, triggered_at, previous_triggered_at,
            gap_minutes, violation.
        Only rows where send_prompt = TRUE are included.
        violation = TRUE if gap_minutes < cooldown_minutes.

    Example:
        two sent prompts 40 min apart  ->  gap_minutes=40, violation=True
    Source of data:
        app_jitailog.triggered_at / send_prompt (via load_jitai_log()).
    """
    columns = ["user_id", "jitai_log_id", "triggered_at",
               "previous_triggered_at", "gap_minutes", "violation"]
    if jitai_df is None or jitai_df.empty:
        return pd.DataFrame(columns=columns)

    df = jitai_df.copy()
    df["triggered_at"] = pd.to_datetime(
        df["triggered_at"], utc=True, errors="coerce"
    )
    sent = df[df["send_prompt"].fillna(False).astype(bool)].sort_values(
        ["user_id", "triggered_at"]
    )

    records = []
    for user_id, frame in sent.groupby("user_id"):
        prev_times = frame["triggered_at"].shift(1)
        gaps = (frame["triggered_at"] - prev_times).dt.total_seconds() / 60.0
        for row_id, triggered, prev_time, gap in zip(
            frame["id"], frame["triggered_at"], prev_times, gaps
        ):
            if pd.isna(gap):
                continue
            records.append({
                "user_id": user_id,
                "jitai_log_id": row_id,
                "triggered_at": triggered,
                "previous_triggered_at": prev_time,
                "gap_minutes": float(gap),
                "violation": bool(gap < cooldown_minutes),
            })

    return pd.DataFrame.from_records(records, columns=columns)


def check_daily_cap_compliance(
    jitai_df: pd.DataFrame,
    daily_cap: int = 4,
) -> pd.DataFrame:
    """
    Purpose:
        Identify participant-days where the number of sent JITAI prompts
        exceeds the hard daily cap. Active intervention days (Weeks 2-5) only;
        Week 1 run-in is excluded.

    Inputs:
        jitai_df  - pd.DataFrame from load_jitai_log(). Must contain columns:
                    user_id, triggered_at, send_prompt.
        daily_cap - int. Maximum allowed prompts per day (default 4).

    Outputs:
        pd.DataFrame with columns:
            user_id, date, prompts_sent, exceeds_cap.
        One row per (user_id, date). exceeds_cap = TRUE if prompts_sent > daily_cap.

    Example:
        5 prompts sent to u1 on 2026-09-10  ->  exceeds_cap=True
    Source of data:
        app_jitailog.triggered_at / send_prompt (via load_jitai_log()).
    """
    columns = ["user_id", "date", "prompts_sent", "exceeds_cap"]
    if jitai_df is None or jitai_df.empty:
        return pd.DataFrame(columns=columns)

    df = jitai_df.copy()
    df["triggered_at"] = pd.to_datetime(
        df["triggered_at"], utc=True, errors="coerce"
    )
    sent = df[df["send_prompt"].fillna(False).astype(bool)].copy()
    sent["date"] = sent["triggered_at"].dt.date

    grouped = (
        sent.groupby(["user_id", "date"]).size()
        .reset_index(name="prompts_sent")
    )
    grouped["exceeds_cap"] = grouped["prompts_sent"] > daily_cap
    return grouped[columns]


def compute_intervention_dosage(jitai_df: pd.DataFrame) -> pd.DataFrame:
    """
    Purpose:
        Compute the number of JITAI prompts sent per participant per active
        study day (Weeks 2-5 only; Week 1 run-in excluded from denominator).
        Expected delivery: ~2-3 prompts per eligible day; hard cap = 4.

    Inputs:
        jitai_df - pd.DataFrame from load_jitai_log() joined with user
                   enrolled_at. Must contain columns: user_id, triggered_at,
                   send_prompt, enrolled_at.

    Outputs:
        pd.DataFrame with columns:
            user_id, date, prompts_sent, is_active_phase.
        One row per (user_id, date). is_active_phase = FALSE for Week 1.

    Example:
        prompt on enrollment day+2  ->  is_active_phase=False (still Week 1)
        prompt on enrollment day+9  ->  is_active_phase=True
    Source of data:
        app_jitailog.triggered_at / send_prompt + app_user.enrolled_at.
    """
    columns = ["user_id", "date", "prompts_sent", "is_active_phase"]
    if jitai_df is None or jitai_df.empty:
        return pd.DataFrame(columns=columns)

    df = jitai_df.copy()
    df["triggered_at"] = pd.to_datetime(
        df["triggered_at"], utc=True, errors="coerce"
    )
    df["enrolled_at"] = pd.to_datetime(
        df["enrolled_at"], utc=True, errors="coerce"
    )
    sent = df[df["send_prompt"].fillna(False).astype(bool)].copy()
    sent["date"] = sent["triggered_at"].dt.date

    grouped = (
        sent.groupby(["user_id", "date", "enrolled_at"]).size()
        .reset_index(name="prompts_sent")
    )
    active_start = grouped["enrolled_at"] + pd.Timedelta(days=7)
    grouped["is_active_phase"] = pd.to_datetime(
        grouped["date"], utc=True
    ).ge(active_start)
    return grouped[columns]


# PUSH DELIVERY FUNNEL
# Conversion chain: push_sent_at -> device_received_at -> receipt_reported_at
# -> engagement event (ema_opened / notification_tapped).

def compute_delivery_funnel(jitai_df: pd.DataFrame) -> pd.DataFrame:
    """
    Purpose:
        Compute the push delivery funnel: count and percentage of sent prompts
        that reach each stage. Used to diagnose loss at any stage of the
        push-notification pipeline.

    Inputs:
        jitai_df - pd.DataFrame from load_jitai_log(). Must contain columns:
                   user_id, send_prompt, push_sent_at, device_received_at,
                   receipt_reported_at, delivery_status.

    Outputs:
        pd.DataFrame with columns:
            stage, count, pct_of_sent.
        Stages in order: sent, accepted_by_expo, received_on_device,
        receipt_reported. Includes a cohort-level summary row.

    Example:
        100 sent, 95 push, 90 received, 80 reported
        ->  received_on_device: count=90, pct_of_sent=90.0
    Source of data:
        app_jitailog send_prompt + funnel timestamps / delivery_status.
    """
    columns = ["stage", "count", "pct_of_sent"]
    if jitai_df is None or jitai_df.empty:
        return pd.DataFrame(columns=columns)

    df = jitai_df.copy()
    sent = df[df["send_prompt"].fillna(False).astype(bool)]
    sent_count = int(sent.shape[0])

    accepted = int(sent["push_sent_at"].notna().sum())
    received = int(sent["device_received_at"].notna().sum())
    reported = int(sent["receipt_reported_at"].notna().sum())

    def _pct(count):
        return 100.0 * count / sent_count if sent_count else np.nan

    records = [
        {"stage": "sent", "count": sent_count, "pct_of_sent": _pct(sent_count)},
        {"stage": "accepted_by_expo", "count": accepted, "pct_of_sent": _pct(accepted)},
        {"stage": "received_on_device", "count": received, "pct_of_sent": _pct(received)},
        {"stage": "receipt_reported", "count": reported, "pct_of_sent": _pct(reported)},
    ]
    return pd.DataFrame.from_records(records, columns=columns)


def compute_push_latency(jitai_df: pd.DataFrame) -> pd.DataFrame:
    """
    Purpose:
        Compute the latency in seconds at each stage transition of the push
        delivery pipeline per JITAI record.

    Inputs:
        jitai_df - pd.DataFrame from load_jitai_log(). Must contain columns:
                   id, user_id, push_sent_at, device_received_at,
                   receipt_reported_at.

    Outputs:
        pd.DataFrame with columns:
            id, user_id, sent_to_received_sec, received_to_reported_sec,
            total_pipeline_sec.
        NaN for stages where a timestamp is missing.

    Example:
        push 10:00:00, received 10:00:03, reported 10:00:05
        ->  sent_to_received_sec=3, received_to_reported_sec=2, total=5
    Source of data:
        app_jitailog push_sent_at / device_received_at / receipt_reported_at.
    """
    columns = ["id", "user_id", "sent_to_received_sec",
               "received_to_reported_sec", "total_pipeline_sec"]
    if jitai_df is None or jitai_df.empty:
        return pd.DataFrame(columns=columns)

    df = jitai_df.copy()
    for column in ["push_sent_at", "device_received_at", "receipt_reported_at"]:
        df[column] = pd.to_datetime(df[column], utc=True, errors="coerce")

    result = pd.DataFrame({
        "id": df["id"],
        "user_id": df["user_id"],
        "sent_to_received_sec": (
            df["device_received_at"] - df["push_sent_at"]
        ).dt.total_seconds(),
        "received_to_reported_sec": (
            df["receipt_reported_at"] - df["device_received_at"]
        ).dt.total_seconds(),
        "total_pipeline_sec": (
            df["receipt_reported_at"] - df["push_sent_at"]
        ).dt.total_seconds(),
    })
    return result[columns]


# ENGAGEMENT ANALYSIS
# Source: app_engagementlog joined to app_jitailog.
# =============================================================================


def compute_engagement_rates(
    engagement_df: pd.DataFrame,
    jitai_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Purpose:
        Compute per-participant rates of opening, acting on, and dismissing
        delivered JITAI prompts. Denominator = prompts where send_prompt = TRUE
        and delivery_status = 'received_on_device'.

    Inputs:
        engagement_df - pd.DataFrame from load_engagement_log(). Must contain
                        columns: user_id, jitai_log_id, event_type.
        jitai_df      - pd.DataFrame from load_jitai_log(). Must contain
                        columns: id, user_id, send_prompt, delivery_status.

    Outputs:
        pd.DataFrame with columns:
            user_id, delivered, opened, acted, dismissed,
            open_rate_pct, act_rate_pct, dismiss_rate_pct.
        One row per participant, plus a cohort-aggregate row (user_id = 'ALL').

    Example:
        u1 delivered=10, opened=6  ->  open_rate_pct=60.0
    Source of data:
        app_engagementlog.event_type + app_jitailog delivery_status.
    """
    columns = ["user_id", "delivered", "opened", "acted", "dismissed",
               "open_rate_pct", "act_rate_pct", "dismiss_rate_pct"]
    if jitai_df is None or jitai_df.empty:
        return pd.DataFrame(columns=columns)

    delivered = jitai_df[
        jitai_df["send_prompt"].fillna(False).astype(bool)
        & jitai_df["delivery_status"].eq("received_on_device")
    ]
    delivered_ids = set(delivered["id"])

    engagement = (
        engagement_df[engagement_df["jitai_log_id"].isin(delivered_ids)]
        if engagement_df is not None and not engagement_df.empty
        else pd.DataFrame(columns=["user_id", "jitai_log_id", "event_type"])
    )

    open_events = {"ema_opened", "notification_tapped"}
    act_events = {"ema_completed"}
    dismiss_events = {"ema_dismissed", "notification_dismissed"}

    def _summary(label, deliv_frame, eng_frame):
        delivered_count = int(deliv_frame.shape[0])
        opened = int(eng_frame["event_type"].isin(open_events).sum())
        acted = int(eng_frame["event_type"].isin(act_events).sum())
        dismissed = int(eng_frame["event_type"].isin(dismiss_events).sum())

        def _pct(count):
            return 100.0 * count / delivered_count if delivered_count else np.nan

        return {
            "user_id": label,
            "delivered": delivered_count,
            "opened": opened,
            "acted": acted,
            "dismissed": dismissed,
            "open_rate_pct": _pct(opened),
            "act_rate_pct": _pct(acted),
            "dismiss_rate_pct": _pct(dismissed),
        }

    records = []
    for user_id, deliv_frame in delivered.groupby("user_id"):
        eng_frame = engagement[engagement["user_id"] == user_id]
        records.append(_summary(user_id, deliv_frame, eng_frame))
    records.append(_summary("ALL", delivered, engagement))

    return pd.DataFrame.from_records(records, columns=columns)


def compute_open_latency(
    engagement_df: pd.DataFrame,
    jitai_df: pd.DataFrame,
) -> pd.Series:
    """
    Purpose:
        Compute time (in minutes) from push delivery (push_sent_at) to the
        participant opening the prompt (ema_opened event). Measures
        participant responsiveness to delivered interventions.

    Inputs:
        engagement_df - pd.DataFrame from load_engagement_log(). Must contain
                        columns: jitai_log_id, event_type, occurred_at.
                        Filtered to event_type = 'ema_opened'.
        jitai_df      - pd.DataFrame from load_jitai_log(). Must contain
                        columns: id, push_sent_at.

    Outputs:
        pd.Series of float (minutes), indexed by jitai_log_id.
        NaN where no ema_opened event is recorded.

    Example:
        push 10:00, opened 10:07  ->  7.0 (indexed by that jitai_log_id)
    Source of data:
        app_engagementlog.occurred_at (ema_opened) + app_jitailog.push_sent_at.
    """
    if (engagement_df is None or engagement_df.empty
            or jitai_df is None or jitai_df.empty):
        return pd.Series(dtype=float)

    opens = engagement_df[engagement_df["event_type"] == "ema_opened"].copy()
    opens["occurred_at"] = pd.to_datetime(
        opens["occurred_at"], utc=True, errors="coerce"
    )
    first_open = opens.groupby("jitai_log_id")["occurred_at"].min()

    push = jitai_df.set_index("id")["push_sent_at"]
    push = pd.to_datetime(push, utc=True, errors="coerce")

    latency = (first_open - push.reindex(first_open.index)).dt.total_seconds() / 60.0
    latency.index.name = "jitai_log_id"
    return latency


# PARTICIPANT RETENTION
# Benchmark: enrolled continuously Day 1 through Day 35.
# Missing prompts != dropout; run-in (Week 1) and active phase reported separately.


def compute_retention(user_df: pd.DataFrame) -> Dict:
    """
    Purpose:
        Compute participant retention rate overall and split by study phase
        (run-in Week 1 vs. active Weeks 2-5). Missing EMA prompts are not
        counted as dropout unless there is a formal withdrawal record.

    Inputs:
        user_df - pd.DataFrame of user records. Must contain columns:
                  user_id, is_enrolled, enrolled_at.

    Outputs:
        dict with keys:
            overall_n, retained_n, overall_retention_pct,
            run_in_retained_n, run_in_retention_pct,
            active_retained_n, active_retention_pct.

    Example:
        20 began, 18 still enrolled  ->  overall_retention_pct=90.0
    Source of data:
        app_user.is_enrolled / enrolled_at (via load_users()).
    """
    if user_df is None or user_df.empty:
        return {
            "overall_n": 0, "retained_n": 0, "overall_retention_pct": np.nan,
            "run_in_retained_n": 0, "run_in_retention_pct": np.nan,
            "active_retained_n": 0, "active_retention_pct": np.nan,
        }

    df = user_df.copy()
    df["enrolled_at"] = pd.to_datetime(
        df["enrolled_at"], utc=True, errors="coerce"
    )
    began = df[df["enrolled_at"].notna()]
    overall_n = int(began.shape[0])
    enrolled = began["is_enrolled"].fillna(False).astype(bool)
    retained_n = int(enrolled.sum())

    now = pd.Timestamp.now(tz="UTC")
    past_run_in = began["enrolled_at"] + pd.Timedelta(days=7) <= now
    past_active = began["enrolled_at"] + pd.Timedelta(days=35) <= now

    run_in_n = int((enrolled & past_run_in).sum())
    active_n = int((enrolled & past_active).sum())

    def _pct(count):
        return 100.0 * count / overall_n if overall_n else np.nan

    return {
        "overall_n": overall_n,
        "retained_n": retained_n,
        "overall_retention_pct": _pct(retained_n),
        "run_in_retained_n": run_in_n,
        "run_in_retention_pct": _pct(run_in_n),
        "active_retained_n": active_n,
        "active_retention_pct": _pct(active_n),
    }


def classify_dropout(
    user_df: pd.DataFrame,
    ema_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Purpose:
        Distinguish between formal withdrawal (is_enrolled = FALSE) and
        non-responsive participants (is_enrolled = TRUE but no EMA completions
        for >= 7 consecutive days).

    Inputs:
        user_df - pd.DataFrame of user records. Must contain columns:
                  user_id, is_enrolled, enrolled_at.
        ema_df  - pd.DataFrame from load_ema(). Must contain columns:
                  user_id, sent_at, status.

    Outputs:
        pd.DataFrame with columns:
            user_id, classification, last_active_date, days_since_active.
        classification values: 'active', 'formal_withdrawal', 'non_responsive'.

    Example:
        enrolled but last completion 9 days ago  ->  'non_responsive'
    Source of data:
        app_user.is_enrolled + app_ema.status (completed) timestamps.
    """
    columns = ["user_id", "classification", "last_active_date", "days_since_active"]
    if user_df is None or user_df.empty:
        return pd.DataFrame(columns=columns)

    completions = (
        ema_df[ema_df["status"] == "completed"].copy()
        if ema_df is not None and not ema_df.empty
        else pd.DataFrame(columns=["user_id", "sent_at"])
    )
    if not completions.empty:
        completions["sent_at"] = pd.to_datetime(
            completions["sent_at"], utc=True, errors="coerce"
        )
    last_active = completions.groupby("user_id")["sent_at"].max()

    now = pd.Timestamp.now(tz="UTC")
    records = []
    for _, user in user_df.iterrows():
        user_id = user["user_id"]
        enrolled = bool(user.get("is_enrolled", False))
        last_date = last_active.get(user_id, pd.NaT)
        days_since = (
            (now - last_date).days if pd.notna(last_date) else np.nan
        )

        if not enrolled:
            classification = "formal_withdrawal"
        elif pd.isna(last_date) or days_since >= 7:
            classification = "non_responsive"
        else:
            classification = "active"

        records.append({
            "user_id": user_id,
            "classification": classification,
            "last_active_date": last_date.date() if pd.notna(last_date) else pd.NaT,
            "days_since_active": days_since,
        })

    return pd.DataFrame.from_records(records, columns=columns)


# BIOMETRIC-EMA CONCORDANCE
# Compares physiological volatility (HR-MSSD) with self-report volatility
# (EMA-MSSD) to assess signal concordance for JITAI triggering.


def compute_hr_mssd(
    hr_df: pd.DataFrame,
    window_minutes: int = 60,
) -> pd.DataFrame:
    """
    Purpose:
        Compute rolling MSSD on the heart-rate time series within a sliding
        window. Analogous to EMA-MSSD but derived from the physiological
        signal. Non-wear rows (bpm = 0 or NaN) are excluded from each window.

    Inputs:
        hr_df          - pd.DataFrame from load_heart_rate(). Must contain
                         columns: user_id, timestamp, bpm.
        window_minutes - int. Rolling window size in minutes (default 60).

    Outputs:
        pd.DataFrame with columns:
            user_id, window_end_timestamp, hr_mssd.
        One row per rolling window endpoint per participant.

    Example:
        bpm 60,62,61,65 in the hour before 11:00  ->  one hr_mssd row at 11:00
    Source of data:
        app_heartratesample.bpm (via load_heart_rate()).
    """
    columns = ["user_id", "window_end_timestamp", "hr_mssd"]
    if hr_df is None or hr_df.empty:
        return pd.DataFrame(columns=columns)

    df = hr_df.copy()
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    df = df[~flag_non_wear(df).values].dropna(subset=["timestamp"])
    df = df.sort_values(["user_id", "timestamp"])

    records = []
    window = pd.Timedelta(minutes=window_minutes)
    for user_id, frame in df.groupby("user_id"):
        frame = frame.reset_index(drop=True)
        timestamps = frame["timestamp"]
        for i, end_time in enumerate(timestamps):
            mask = (timestamps <= end_time) & (timestamps > end_time - window)
            series = frame.loc[mask, "bpm"].astype(float)
            if series.shape[0] >= 2:
                records.append({
                    "user_id": user_id,
                    "window_end_timestamp": end_time,
                    "hr_mssd": compute_mssd(series),
                })

    return pd.DataFrame.from_records(records, columns=columns)


def compute_hr_ema_concordance(
    hr_df: pd.DataFrame,
    ema_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Purpose:
        Compute per-participant Pearson correlation between HR-MSSD (computed
        in the 60-minute window before each EMA prompt) and EMA-MSSD (computed
        from consecutive answered EMA pairs). Measures the degree to which
        physiological and self-report volatility agree.

    Inputs:
        hr_df  - pd.DataFrame from load_heart_rate(). Must contain columns:
                 user_id, timestamp, bpm.
        ema_df - pd.DataFrame from load_ema_item_responses() filtered to
                 item_id = 'B1' (energy) or 'B2' (stress). Must contain
                 columns: user_id, sent_at, value_numeric, responded_at.
                 Do NOT use mood from load_ema() - use the same B1/B2 signal
                 the decision engine uses so HR concordance reflects the actual
                 trigger signal, not a different EMA construct.

    Outputs:
        pd.DataFrame with columns:
            user_id, pearson_r, p_value, n_paired_observations.
        One row per participant. NaN if fewer than 5 paired observations.

    Example:
        per-user pairs of (HR-MSSD before prompt, EMA squared-diff at prompt)
        ->  pearson_r, p_value, n_paired_observations
    Source of data:
        app_heartratesample.bpm + app_emaitemresponse.value_numeric (B1/B2).
    """
    columns = ["user_id", "pearson_r", "p_value", "n_paired_observations"]
    if (hr_df is None or hr_df.empty or ema_df is None or ema_df.empty):
        return pd.DataFrame(columns=columns)

    ema = ema_df.copy()
    ema["sent_at"] = pd.to_datetime(ema["sent_at"], utc=True, errors="coerce")
    ema = ema.sort_values(["user_id", "sent_at"])
    ema["ema_sq_diff"] = (
        ema.groupby("user_id")["value_numeric"].diff() ** 2
    )

    hr = hr_df.copy()
    hr["timestamp"] = pd.to_datetime(hr["timestamp"], utc=True, errors="coerce")
    hr = hr[~flag_non_wear(hr).values].dropna(subset=["timestamp"])

    window = pd.Timedelta(minutes=60)
    records = []
    for user_id, ema_frame in ema.groupby("user_id"):
        hr_frame = hr[hr["user_id"] == user_id]
        paired_hr = []
        paired_ema = []
        for sent_at, ema_sq in zip(ema_frame["sent_at"], ema_frame["ema_sq_diff"]):
            if pd.isna(ema_sq) or pd.isna(sent_at):
                continue
            mask = (
                (hr_frame["timestamp"] <= sent_at)
                & (hr_frame["timestamp"] > sent_at - window)
            )
            bpm = hr_frame.loc[mask, "bpm"].astype(float)
            hr_mssd = compute_mssd(bpm)
            if not np.isnan(hr_mssd):
                paired_hr.append(hr_mssd)
                paired_ema.append(float(ema_sq))

        n = len(paired_hr)
        if n >= 5:
            r, p = stats.pearsonr(paired_hr, paired_ema)
        else:
            r, p = np.nan, np.nan
        records.append({
            "user_id": user_id,
            "pearson_r": r,
            "p_value": p,
            "n_paired_observations": n,
        })

    return pd.DataFrame.from_records(records, columns=columns)


# BIOMARKER SUB-STUDY (TIER 2, HAIR STEROIDS)
# NOTE: hair_sample and hair_hygiene_covariates tables are PLANNED and are
# not yet in production (no Django migrations as of 2026-08-14).
# Cortisol and testosterone concentrations MUST be log-transformed and
# winsorized before any inferential analysis.


def preprocess_hair_steroids(hair_df: pd.DataFrame) -> pd.DataFrame:
    """
    Purpose:
        Apply log transformation and winsorization to raw hair cortisol and
        testosterone concentrations. These preprocessing steps are required
        given the expected right-skew and outliers in pg/mg steroid assay data.

    Inputs:
        hair_df - pd.DataFrame from the planned hair_sample table. Must contain
                  columns: user_id, cortisol_pg_mg, testosterone_pg_mg.

    Outputs:
        pd.DataFrame with original columns plus:
            cortisol_log, testosterone_log   (natural log of raw values),
            cortisol_wins, testosterone_wins (winsorized at 1st-99th pct).
        Rows with values <= 0 are set to NaN before log transformation.

    Example:
        cortisol_pg_mg [2.0, 4.0, 400.0]  ->  logs + values winsorized to 1-99pct
    Source of data:
        planned hair_sample table (passed in as a DataFrame; not yet in DB).
    """
    df = hair_df.copy()

    for raw, log_col, wins_col in [
        ("cortisol_pg_mg", "cortisol_log", "cortisol_wins"),
        ("testosterone_pg_mg", "testosterone_log", "testosterone_wins"),
    ]:
        values = pd.to_numeric(df[raw], errors="coerce")
        values = values.where(values > 0)
        df[log_col] = np.log(values)

        if values.notna().sum() >= 1:
            lower = values.quantile(0.01)
            upper = values.quantile(0.99)
            df[wins_col] = values.clip(lower=lower, upper=upper)
        else:
            df[wins_col] = values

    return df


def validate_sample_quality(hair_df: pd.DataFrame) -> pd.DataFrame:
    """
    Purpose:
        Flag hair samples that do not meet minimum quality thresholds for
        reliable steroid assay results. Requirements: >= 1 cm length,
        >= 50 mg mass.

    Inputs:
        hair_df - pd.DataFrame from the planned hair_sample table. Must contain
                  columns: sample_id, user_id, hair_length_cm, sample_mass_mg,
                  assay_method.

    Outputs:
        pd.DataFrame with original columns plus:
            length_ok (bool) - hair_length_cm >= 1.0,
            mass_ok   (bool) - sample_mass_mg >= 50.0,
            usable    (bool) - length_ok AND mass_ok.

    Example:
        length 1.5cm, mass 60mg  ->  length_ok=True, mass_ok=True, usable=True
    Source of data:
        planned hair_sample table (passed in as a DataFrame; not yet in DB).
    """
    df = hair_df.copy()
    df["length_ok"] = pd.to_numeric(
        df["hair_length_cm"], errors="coerce"
    ).ge(1.0)
    df["mass_ok"] = pd.to_numeric(
        df["sample_mass_mg"], errors="coerce"
    ).ge(50.0)
    df["usable"] = df["length_ok"] & df["mass_ok"]
    return df


def compute_substudy_sample_rate(
    user_df: pd.DataFrame,
    hair_df: pd.DataFrame,
) -> float:
    """
    Purpose:
        Compute the proportion of eligible Tier 2 sub-study participants who
        provided a usable hair sample within the collection window at
        enrollment. Reported as a secondary feasibility metric.

    Inputs:
        user_df - pd.DataFrame of user records. Must contain columns:
                  user_id, is_enrolled.
        hair_df - pd.DataFrame from the planned hair_sample table after
                  validate_sample_quality() has been applied. Must contain
                  columns: user_id, usable.

    Outputs:
        float. usable_sample_count / eligible_participant_count.
        Returns NaN if no eligible participants exist.

    Example:
        20 eligible, 15 usable samples  ->  0.75
    Source of data:
        app_user.is_enrolled + planned hair_sample.usable.
    """
    if user_df is None or user_df.empty:
        return float("nan")

    eligible = user_df[user_df["is_enrolled"].fillna(False).astype(bool)]
    eligible_count = int(eligible["user_id"].nunique())
    if eligible_count == 0:
        return float("nan")

    if hair_df is None or hair_df.empty:
        return 0.0

    usable_users = hair_df[hair_df["usable"].fillna(False).astype(bool)]
    usable_count = int(usable_users["user_id"].nunique())
    return usable_count / eligible_count


# MODERATION FRAMEWORK
# Three pre-specified tiers of baseline moderators (Qualtrics / Baseline).
# Source: data-dictionary.md section 3.1.
# Tier 1 = confirmatory; Tier 2 = confirmatory (biomarker); Tier 3 = exploratory.


def _fit_moderation(
    outcomes_df: pd.DataFrame,
    moderators_df: pd.DataFrame,
    moderator_columns: List[str],
) -> List[Dict]:
    """
    Fit outcome ~ treatment * moderator OLS for each moderator column.

    Example:
        _fit_moderation(outcomes, moderators, ["supps_p"])  ->  [ {moderator,
        interaction_estimate, se, p_value, ci_lower, ci_upper}, ... ]
    Source of data:
        outcomes_df.outcome_value / treatment_indicator + moderators_df columns
        (Qualtrics / Baseline). Uses statsmodels OLS.
    """
    try:
        import statsmodels.formula.api as smf
    except ImportError as exc:
        raise ImportError(
            "statsmodels is required for moderation analysis. "
            "Install it (see analytics/requirements.txt)."
        ) from exc

    merged = outcomes_df.merge(moderators_df, on="user_id", how="inner")

    records = []
    for moderator in moderator_columns:
        frame = merged.rename(
            columns={
                "outcome_value": "y",
                "treatment_indicator": "treat",
                moderator: "mod",
            }
        )[["y", "treat", "mod"]].dropna()

        if frame.shape[0] < 3 or frame["treat"].nunique() < 2:
            records.append({
                "moderator": moderator, "interaction_estimate": np.nan,
                "se": np.nan, "p_value": np.nan,
                "ci_lower": np.nan, "ci_upper": np.nan,
            })
            continue

        model = smf.ols("y ~ treat * mod", data=frame).fit()
        term = "treat:mod"
        ci = model.conf_int().loc[term]
        records.append({
            "moderator": moderator,
            "interaction_estimate": float(model.params[term]),
            "se": float(model.bse[term]),
            "p_value": float(model.pvalues[term]),
            "ci_lower": float(ci[0]),
            "ci_upper": float(ci[1]),
        })

    return records


def compute_tier1_moderation(
    outcomes_df: pd.DataFrame,
    moderators_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Purpose:
        Estimate heterogeneous treatment effects for Tier 1 confirmatory
        moderators: Urgency (SUPPS-P), Emotion Regulation (DERS-16), Eating
        Motives (TFEQ-R18), Drinking Motives (DMQ-R), and Body Listening
        (MAIA-2). Interaction terms are Treatment x Moderator.

    Inputs:
        outcomes_df   - pd.DataFrame with columns: user_id, outcome_domain,
                        outcome_value, treatment_indicator.
        moderators_df - pd.DataFrame with columns: user_id, supps_p, ders_16,
                        tfeq_r18, dmq_r, maia_2.

    Outputs:
        pd.DataFrame with columns:
            moderator, interaction_estimate, se, p_value, ci_lower, ci_upper.
        One row per Tier 1 moderator.

    Example:
        ->  5 rows (supps_p, ders_16, tfeq_r18, dmq_r, maia_2) with T x M effects
    Source of data:
        Qualtrics / Baseline moderator scores + JITAI outcome measures.
    """
    moderators = ["supps_p", "ders_16", "tfeq_r18", "dmq_r", "maia_2"]
    records = _fit_moderation(outcomes_df, moderators_df, moderators)
    return pd.DataFrame.from_records(
        records,
        columns=["moderator", "interaction_estimate", "se",
                 "p_value", "ci_lower", "ci_upper"],
    )


def compute_tier2_biomarker_moderation(
    outcomes_df: pd.DataFrame,
    biomarker_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Purpose:
        Estimate the Hair Cortisol x Reactive Behavior interaction as a Tier 2
        confirmatory moderation analysis. Cortisol values must be preprocessed
        via preprocess_hair_steroids() before calling this function. ACE score
        is handled as a sensitive moderator under IRB sensitive-data protocols.

    Inputs:
        outcomes_df  - pd.DataFrame with columns: user_id, outcome_domain,
                       outcome_value, treatment_indicator.
        biomarker_df - pd.DataFrame with columns: user_id, cortisol_wins,
                       testosterone_wins, ace_score.

    Outputs:
        pd.DataFrame with columns:
            moderator, interaction_estimate, se, p_value, ci_lower, ci_upper.
        One row per Tier 2 biomarker moderator.

    Example:
        ->  3 rows (cortisol_wins, testosterone_wins, ace_score) T x M effects
    Source of data:
        planned hair_sample (winsorized) + ACE (Qualtrics/Baseline, sensitive).
    """
    moderators = ["cortisol_wins", "testosterone_wins", "ace_score"]
    records = _fit_moderation(outcomes_df, biomarker_df, moderators)
    return pd.DataFrame.from_records(
        records,
        columns=["moderator", "interaction_estimate", "se",
                 "p_value", "ci_lower", "ci_upper"],
    )


def compute_tier3_exploratory(
    outcomes_df: pd.DataFrame,
    moderators_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Purpose:
        Run exploratory moderation analyses for Tier 3 constructs: Subjective
        Social Status (MacArthur ladder), Everyday Discrimination, and
        Chronotype (rMEQ). Results are hypothesis-generating; appropriate
        corrections for multiple comparisons should be applied downstream.

    Inputs:
        outcomes_df   - pd.DataFrame with columns: user_id, outcome_domain,
                        outcome_value, treatment_indicator.
        moderators_df - pd.DataFrame with columns: user_id, macarthur_ladder,
                        everyday_discrimination, rmeq_score.

    Outputs:
        pd.DataFrame with columns:
            moderator, interaction_estimate, se, p_value, ci_lower, ci_upper,
            bonferroni_corrected_p.
        One row per Tier 3 moderator.

    Example:
        3 moderators tested  ->  bonferroni_corrected_p = min(p * 3, 1.0)
    Source of data:
        Qualtrics / Baseline Tier 3 moderator scores + JITAI outcome measures.
    """
    moderators = ["macarthur_ladder", "everyday_discrimination", "rmeq_score"]
    records = _fit_moderation(outcomes_df, moderators_df, moderators)

    n_tests = len(moderators)
    for record in records:
        p = record["p_value"]
        record["bonferroni_corrected_p"] = (
            min(p * n_tests, 1.0) if pd.notna(p) else np.nan
        )

    return pd.DataFrame.from_records(
        records,
        columns=["moderator", "interaction_estimate", "se", "p_value",
                 "ci_lower", "ci_upper", "bonferroni_corrected_p"],
    )


# REPORTING & VISUALIZATION
# Aggregate all preregistered feasibility benchmarks and produce standard
# figures for the analysis report.


def summarize_feasibility(
    ema_df: pd.DataFrame,
    jitai_df: pd.DataFrame,
    hr_df: pd.DataFrame,
    user_df: pd.DataFrame,
    item_df: Optional[pd.DataFrame] = None,
) -> Dict:
    """
    Purpose:
        Aggregate all preregistered feasibility benchmarks into a single
        summary dictionary. Top-level entry point for the feasibility analysis.

    Inputs:
        ema_df   - pd.DataFrame from load_ema().
        jitai_df - pd.DataFrame from load_jitai_log().
        hr_df    - pd.DataFrame from load_heart_rate().
        user_df  - pd.DataFrame of user records with enrolled_at.
        item_df  - Optional composite B1/B2 per-prompt signal from
                   _prep_trigger_signal(load_ema_item_responses(), user_df).
                   Required to populate run_in_mssd_mean and overall_lt_mssd_mean;
                   left as NaN when not supplied.

    Outputs:
        dict with keys:
            ema_response_rate_pct        (benchmark >= 75),
            wear_time_mean_hrs_per_day   (benchmark >= 8),
            wear_time_days_meeting_goal  (benchmark >= 5 days/week),
            retention_pct                (Day 1 through Day 35),
            intervention_dosage_mean_per_day,
            cooldown_violation_count,
            daily_cap_violation_count,
            delivery_funnel_received_pct,
            run_in_mssd_mean             (mean Week-1 base MSSD across participants),
            overall_lt_mssd_mean         (mean LT-MSSD across participants).

    Example:
        summarize_feasibility(ema, jitai, hr, users, item)  ->  dict of 10 metrics
    Source of data:
        Composes the loaders above (app_ema/app_jitailog/app_heartratesample/
        app_user/app_emaitemresponse) through the compute_* feasibility functions.
    """
    response = compute_ema_response_rate(ema_df)
    response_rate = (
        float(response.loc[response["user_id"] == "ALL", "response_rate_pct"].iloc[0])
        if not response.empty else np.nan
    )

    wear = compute_wear_time(hr_df)
    if not wear.empty:
        wear_hours = wear["wear_minutes"] / 60.0
        wear_mean_hrs = float(wear_hours.mean())
        days_meeting_goal = int((wear_hours >= 8).sum())
    else:
        wear_mean_hrs = np.nan
        days_meeting_goal = 0

    retention = compute_retention(user_df)

    dosage = compute_intervention_dosage(
        jitai_df.drop(columns=["enrolled_at"], errors="ignore").merge(
            user_df[["user_id", "enrolled_at"]], on="user_id", how="left"
        )
        if jitai_df is not None and not jitai_df.empty else jitai_df
    )
    dosage_mean = (
        float(dosage.loc[dosage["is_active_phase"], "prompts_sent"].mean())
        if not dosage.empty else np.nan
    )

    cooldown = check_cooldown_compliance(jitai_df)
    cooldown_violations = (
        int(cooldown["violation"].sum()) if not cooldown.empty else 0
    )

    cap = check_daily_cap_compliance(jitai_df)
    cap_violations = int(cap["exceeds_cap"].sum()) if not cap.empty else 0

    funnel = compute_delivery_funnel(jitai_df)
    received_pct = (
        float(funnel.loc[funnel["stage"] == "received_on_device", "pct_of_sent"].iloc[0])
        if not funnel.empty else np.nan
    )

    if item_df is not None and not item_df.empty:
        run_in = compute_run_in_mssd(item_df)
        run_in_mean = (
            float(run_in["run_in_mssd"].mean()) if not run_in.empty else np.nan
        )
        lt = compute_lt_mssd(item_df)
        overall_lt_mean = (
            float(lt["lt_mssd"].mean()) if not lt.empty else np.nan
        )
    else:
        run_in_mean = np.nan
        overall_lt_mean = np.nan

    return {
        "ema_response_rate_pct": response_rate,
        "wear_time_mean_hrs_per_day": wear_mean_hrs,
        "wear_time_days_meeting_goal": days_meeting_goal,
        "retention_pct": retention["overall_retention_pct"],
        "intervention_dosage_mean_per_day": dosage_mean,
        "cooldown_violation_count": cooldown_violations,
        "daily_cap_violation_count": cap_violations,
        "delivery_funnel_received_pct": received_pct,
        "run_in_mssd_mean": run_in_mean,
        "overall_lt_mssd_mean": overall_lt_mean,
    }


def plot_ema_completion_over_time(
    ema_df: pd.DataFrame,
    user_df: Optional[pd.DataFrame] = None,
) -> plt.Figure:
    """
    Purpose:
        Plot daily EMA completion rate across the study period with the bold
        cohort mean over a shaded interquartile band and faint per-participant
        spaghetti lines. Two horizontal references are drawn: the 75%
        preregistered feasibility benchmark and the 80% analytic threshold for
        reliable AR(1) rho/sigma recovery. The Week-1 run-in is shaded and
        separated from the Weeks 2+ active micro-randomization phase so a drop in
        response once adaptive prompts begin is visible.

    Inputs:
        ema_df  - pd.DataFrame from load_ema() (user_id, sent_at, status).
        user_df - optional pd.DataFrame from load_users(); when given, the run-in
                  boundary is anchored to min(enrolled_at). Otherwise it is
                  anchored to the first sent_at (staggered enrollment caveat).

    Outputs:
        plt.Figure. Cohort mean completion line + IQR band + spaghetti, with
        75%/80% reference lines and run-in shading.

    Example:
        plot_ema_completion_over_time(ema, user_df)  ->  Figure with 75%/80% lines
    Source of data:
        app_ema.sent_at / status (+ app_user.enrolled_at for the phase boundary).
    """
    df = ema_df.copy()
    df["sent_at"] = pd.to_datetime(df["sent_at"], utc=True, errors="coerce")
    df = df.dropna(subset=["sent_at"])
    if df.empty:
        return _empty_figure("No EMA prompts")
    df["date"] = df["sent_at"].dt.date
    df["completed"] = df["status"].eq("completed")

    daily = df.groupby("date").agg(
        delivered=("status", "size"), completed=("completed", "sum"),
    ).reset_index()
    daily["rate_pct"] = 100.0 * daily["completed"] / daily["delivered"]

    per_user = df.groupby(["user_id", "date"]).agg(
        delivered=("status", "size"), completed=("completed", "sum"),
    ).reset_index()
    per_user["rate_pct"] = 100.0 * per_user["completed"] / per_user["delivered"]
    iqr = per_user.groupby("date")["rate_pct"].quantile([0.25, 0.75]).unstack()

    fig, ax = plt.subplots(figsize=(11, 5.5))
    for _, g in per_user.groupby("user_id"):
        g = g.sort_values("date")
        ax.plot(g["date"], g["rate_pct"], color="gray", alpha=0.22, linewidth=0.8, zorder=1)
    ax.fill_between(iqr.index, iqr[0.25], iqr[0.75], color="tab:blue", alpha=0.15,
                    label="IQR across participants", zorder=2)
    ax.plot(daily["date"], daily["rate_pct"], color="tab:blue", marker="o",
            linewidth=2, label="cohort mean", zorder=3)

    ax.axhline(75, color="red", linestyle="--", label="75% feasibility benchmark")
    ax.axhline(80, color="darkorange", linestyle=":", label="80% AR(1) recovery threshold")

    if user_df is not None and "enrolled_at" in getattr(user_df, "columns", []) \
            and pd.to_datetime(user_df["enrolled_at"], utc=True, errors="coerce").notna().any():
        anchor = pd.to_datetime(user_df["enrolled_at"], utc=True, errors="coerce").min().date()
    else:
        anchor = df["date"].min()
    run_in_end = anchor + datetime.timedelta(days=7)
    ax.axvspan(anchor, run_in_end, color="gray", alpha=0.12, label="run-in (Week 1)", zorder=0)
    ax.axvline(run_in_end, color="black", linewidth=0.8, zorder=0)

    ax.set_xlabel("Date")
    ax.set_ylabel("Completion rate (%)")
    ax.set_title("Daily EMA completion rate (cohort mean, IQR, per-participant)")
    ax.set_ylim(0, 100)
    ax.legend(loc="lower left", fontsize=8, ncol=2)
    fig.autofmt_xdate()
    fig.tight_layout()
    return fig


_DECISION_CATEGORIES = [
    ("Sent / randomized", "prompt sent", "tab:green"),
    ("Below threshold", "below within-person", "tab:gray"),
    ("Cooldown blocked", "cooldown", "tab:orange"),
    ("Daily-cap blocked", "daily cap", "tab:red"),
    ("Insufficient history/data", "insufficient", "tab:purple"),
]


def _decision_category(reason: str) -> str:
    """Map a JITAILog.trigger_reason string to a coarse decision-gate category."""
    r = str(reason).lower()
    for label, key, _ in _DECISION_CATEGORIES:
        if key in r:
            return label
    return "Other"


def plot_mssd_distribution(
    jitai_df: pd.DataFrame,
    item_df: Optional[pd.DataFrame] = None,
) -> plt.Figure:
    """
    Purpose:
        Plot the distribution of observed_mssd at JITAI decision points, colored
        by the decision engine's evaluation outcome (Sent/randomized, Below
        threshold, Cooldown blocked, Daily-cap blocked, Insufficient history).
        Overlays the sample mean observed_mssd and, when item_df is supplied, the
        cohort-mean theoretical expected MSSD 2*sigma_hat^2*(1-rho_hat).

    Inputs:
        jitai_df - pd.DataFrame from load_jitai_log() (observed_mssd,
                   trigger_reason).
        item_df  - optional B1/B2 item responses (raw load_ema_item_responses()
                   or a composite from _prep_trigger_signal). Enables the
                   expected-MSSD reference line.

    Outputs:
        plt.Figure. Step histograms of observed_mssd segmented by decision gate,
        with sample-mean and expected-MSSD vertical reference lines.

    Example:
        plot_mssd_distribution(jitai, item_df)  ->  Figure segmented by outcome
    Source of data:
        app_jitailog.observed_mssd / trigger_reason; expected MSSD from B1/B2
        via compute_ar1_parameters + compute_expected_mssd.
    """
    df = jitai_df.copy()
    if df.empty or df["observed_mssd"].dropna().empty:
        return _empty_figure("No observed MSSD values")
    df["_cat"] = df["trigger_reason"].map(_decision_category)

    fig, ax = plt.subplots(figsize=(10, 5.5))
    order = [c[0] for c in _DECISION_CATEGORIES] + ["Other"]
    colors = {c[0]: c[2] for c in _DECISION_CATEGORIES}
    colors["Other"] = "tab:brown"
    for cat in order:
        values = df.loc[df["_cat"] == cat, "observed_mssd"].dropna()
        if not values.empty:
            sns.histplot(values, ax=ax, bins=30, element="step", stat="count",
                         fill=True, alpha=0.35, color=colors[cat],
                         label=f"{cat} (n={len(values)})")

    mean_obs = float(df["observed_mssd"].mean())
    ax.axvline(mean_obs, color="black", linewidth=1.6,
               label=f"sample mean = {mean_obs:.2f}")

    if item_df is not None and not item_df.empty:
        signal = item_df
        if "item_id" in item_df.columns:
            signal = _prep_trigger_signal(item_df)
        expected = []
        for _, g in signal.sort_values("sent_at").groupby("user_id"):
            rho, sig = compute_ar1_parameters(g["value_numeric"])
            if not (np.isnan(rho) or np.isnan(sig)):
                expected.append(compute_expected_mssd(sig, rho))
        if expected:
            exp_mean = float(np.mean(expected))
            ax.axvline(exp_mean, color="purple", linestyle="--",
                       label=r"E[MSSD]=$2\hat{\sigma}^2(1-\hat{\rho})$ = "
                             f"{exp_mean:.2f}")

    ax.set_xlabel("observed_mssd")
    ax.set_ylabel("decision points")
    ax.set_title("Observed MSSD by decision-gate outcome")
    ax.legend(fontsize=8)
    fig.tight_layout()
    return fig


def plot_delivery_funnel(jitai_df: pd.DataFrame) -> plt.Figure:
    """
    Purpose:
        Plot the push-notification delivery funnel as a horizontal bar chart
        showing count and percentage of prompts reaching each stage:
        sent -> accepted_by_expo -> received_on_device -> receipt_reported.

    Inputs:
        jitai_df - pd.DataFrame from load_jitai_log(). Must contain columns:
                   send_prompt, delivery_status, push_sent_at,
                   device_received_at, receipt_reported_at.

    Outputs:
        plt.Figure. Horizontal bar chart of funnel stage conversion rates.

    Example:
        plot_delivery_funnel(jitai)  ->  2-panel Figure (funnel + latency boxplot)
    Source of data:
        app_jitailog send_prompt + funnel timestamps (compute_delivery_funnel /
        compute_push_latency).
    """
    funnel = compute_delivery_funnel(jitai_df)
    if funnel.empty:
        return _empty_figure("No sent prompts")

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(13, 5),
                                   gridspec_kw={"width_ratios": [1.4, 1]})

    counts = funnel["count"].tolist()
    stages = funnel["stage"].tolist()
    pcts = funnel["pct_of_sent"].tolist()
    ax1.barh(stages, counts, color="steelblue")
    for i, (count, pct) in enumerate(zip(counts, pcts)):
        if i == 0 or not counts[i - 1]:
            marginal = ""
        else:
            drop = 100.0 * (counts[i] - counts[i - 1]) / counts[i - 1]
            marginal = f", Δ {drop:+.0f}%"
        label = f"{count} ({pct:.0f}%{marginal})" if pd.notna(pct) else str(count)
        ax1.text(count, i, f" {label}", va="center", fontsize=9)
    ax1.invert_yaxis()
    ax1.set_xlabel("count")
    ax1.set_title("Push delivery funnel (cumulative % and marginal Δ%)")

    latency = compute_push_latency(jitai_df)
    cols = ["sent_to_received_sec", "received_to_reported_sec", "total_pipeline_sec"]
    data = [latency[c].dropna() for c in cols if c in latency.columns]
    labels = ["sent→\nreceived", "received→\nreported", "total\npipeline"]
    if any(len(d) for d in data):
        ax2.boxplot([d for d in data], labels=labels, showfliers=False, showmeans=True)
        ax2.set_ylabel("seconds")
        ax2.set_title("Stage transition latency")
    else:
        ax2.axis("off")
        ax2.text(0.5, 0.5, "No latency data", ha="center", va="center")
    fig.tight_layout()
    return fig


def export_feasibility_report(
    summary_dict: Dict,
    output_path: str,
) -> None:
    """
    Purpose:
        Write the feasibility summary dictionary to a CSV file for offline
        review, PI sharing, or import into SPSS / R.

    Inputs:
        summary_dict - dict returned by summarize_feasibility().
        output_path  - str. File path for the output CSV
                       (e.g., 'analytics/output/feasibility_report.csv').

    Outputs:
        None. Writes a single-row CSV to output_path with one column per
        benchmark metric. Raises FileNotFoundError if the output directory
        does not exist.

    Example:
        export_feasibility_report(summary, "analytics/output/report.csv")
    Source of data:
        The dict from summarize_feasibility().
    """
    output = Path(output_path)
    if not output.parent.exists():
        raise FileNotFoundError(
            f"Output directory does not exist: {output.parent}"
        )

    pd.DataFrame([summary_dict]).to_csv(output, index=False)


# TOP-LEVEL REPORT BUILDERS
# Orchestrate the loaders + compute_* functions into ready-to-render bundles.
# build_study_report() -> cohort view; build_participant_report(uid) -> one slice.
# Per the MSSD taxonomy: observed_mssd is the engine's rolling signal (read from
# JITAILog), while the overall volatility descriptor is LT-MSSD (daily means).


def build_study_report() -> Dict:
    """
    Purpose:
        Assemble the overall (cohort-level) analytic report. Loads every source
        table once and composes the feasibility scorecard, delivery funnel,
        two-stage decision audit, engagement, retention, missingness, compliance
        flags, and per-participant LT-MSSD.

    Inputs:
        None. Pulls all enrolled participants from the database via the loaders.

    Outputs:
        dict with keys:
            feasibility          - dict from summarize_feasibility() (incl.
                                   run_in_mssd_mean + overall_lt_mssd_mean),
            ema_response_rate    - per-user + 'ALL' DataFrame,
            item_missingness     - per-user + 'ALL' DataFrame,
            delivery_funnel      - stage DataFrame,
            decision_stages      - per-user + 'ALL' DataFrame,
            engagement           - per-user + 'ALL' DataFrame,
            retention            - dict,
            cooldown_violations  - DataFrame,
            daily_cap_violations - DataFrame,
            lt_mssd_per_user     - DataFrame (user_id, lt_mssd).

    Example:
        report = build_study_report()
        report["feasibility"]["overall_lt_mssd_mean"]  ->  cohort LT-MSSD mean
    Source of data:
        All loaders (app_user/app_ema/app_emaitemresponse/app_jitailog/
        app_heartratesample/app_engagementlog).
    """
    users = load_users()
    ema = load_ema()
    jitai = load_jitai_log()
    hr = load_heart_rate()
    engagement = load_engagement_log()
    signal = _prep_trigger_signal(load_ema_item_responses(), users)

    return {
        "feasibility": summarize_feasibility(ema, jitai, hr, users, item_df=signal),
        "ema_response_rate": compute_ema_response_rate(ema),
        "item_missingness": compute_item_missingness(ema),
        "delivery_funnel": compute_delivery_funnel(jitai),
        "decision_stages": audit_decision_stages(jitai),
        "engagement": compute_engagement_rates(engagement, jitai),
        "retention": compute_retention(users),
        "cooldown_violations": check_cooldown_compliance(jitai),
        "daily_cap_violations": check_daily_cap_compliance(jitai),
        "lt_mssd_per_user": compute_lt_mssd(signal),
    }


def build_participant_report(user_id: int) -> Dict:
    """
    Purpose:
        Assemble the per-participant analytic report: a horizontal slice of the
        same metrics used in the study report, plus this participant's MSSD
        story (run-in baseline, LT-MSSD, and the engine's rolling observed_mssd
        trajectory at each decision point).

    Inputs:
        user_id - int. The participant to report on.

    Outputs:
        dict with keys:
            user_id,
            ema_response_rate      - single-row DataFrame ('ALL' aggregate),
            median_latency_min     - float,
            item_missingness       - DataFrame,
            wear_time              - per-day DataFrame,
            wear_mean_hrs_per_day  - float,
            run_in_mssd            - float (Week-1 base MSSD),
            lt_mssd                - float (overall day-to-day volatility),
            decision_stages        - single-row DataFrame,
            cooldown_violations    - int,
            dosage_mean_active     - float,
            engagement             - single-row DataFrame,
            hr_ema_concordance     - single-row DataFrame,
            dropout_status         - str,
            mssd_trajectory        - DataFrame (triggered_at, observed_mssd,
                                     send_prompt, trigger_reason) -- observed_mssd
                                     is the engine's ROLLING signal, read as-is.

    Example:
        rpt = build_participant_report(42)
        rpt["mssd_trajectory"]  ->  per-decision-point rolling observed_mssd
    Source of data:
        All loaders filtered to user_id. observed_mssd comes straight from
        JITAILog (rolling engine signal); run_in/lt come from B1/B2 items.
    """
    users = load_users(user_id=user_id)
    ema = load_ema(user_id=user_id)
    jitai = load_jitai_log(user_id=user_id)
    hr = load_heart_rate(user_id=user_id)
    engagement = load_engagement_log(user_id=user_id)
    signal = _prep_trigger_signal(
        load_ema_item_responses(user_id=user_id), users
    )

    latency = compute_response_latency(ema)
    median_latency = float(latency.median()) if latency.notna().any() else np.nan

    wear = compute_wear_time(hr)
    wear_mean = (
        float((wear["wear_minutes"] / 60.0).mean()) if not wear.empty else np.nan
    )

    run_in = compute_run_in_mssd(signal)
    run_in_value = (
        float(run_in["run_in_mssd"].iloc[0]) if not run_in.empty else np.nan
    )
    lt = compute_lt_mssd(signal)
    lt_value = float(lt["lt_mssd"].iloc[0]) if not lt.empty else np.nan

    cooldown = check_cooldown_compliance(jitai)
    cooldown_count = int(cooldown["violation"].sum()) if not cooldown.empty else 0

    dosage = compute_intervention_dosage(
        jitai.drop(columns=["enrolled_at"], errors="ignore").merge(
            users[["user_id", "enrolled_at"]], on="user_id", how="left"
        )
        if not jitai.empty else jitai
    )
    dosage_mean = (
        float(dosage.loc[dosage["is_active_phase"], "prompts_sent"].mean())
        if not dosage.empty else np.nan
    )

    dropout = classify_dropout(users, ema)
    dropout_status = (
        str(dropout["classification"].iloc[0]) if not dropout.empty else "unknown"
    )

    trajectory_cols = ["triggered_at", "observed_mssd", "send_prompt", "trigger_reason"]
    trajectory = (
        jitai.sort_values("triggered_at")[trajectory_cols]
        if not jitai.empty
        else pd.DataFrame(columns=trajectory_cols)
    )

    return {
        "user_id": user_id,
        "ema_response_rate": compute_ema_response_rate(ema)
            .query("user_id == 'ALL'"),
        "median_latency_min": median_latency,
        "item_missingness": compute_item_missingness(ema),
        "wear_time": wear,
        "wear_mean_hrs_per_day": wear_mean,
        "run_in_mssd": run_in_value,
        "lt_mssd": lt_value,
        "decision_stages": audit_decision_stages(jitai)
            .query("user_id == 'ALL'"),
        "cooldown_violations": cooldown_count,
        "dosage_mean_active": dosage_mean,
        "engagement": compute_engagement_rates(engagement, jitai)
            .query("user_id == 'ALL'"),
        "hr_ema_concordance": compute_hr_ema_concordance(hr, signal),
        "dropout_status": dropout_status,
        "mssd_trajectory": trajectory,
    }


# FEASIBILITY REPORT FIGURES
# Each returns a matplotlib.figure.Figure (no file I/O) and reuses the compute_*
# functions above as its data source. Benchmark reference lines follow the
# analysis plan: EMA >=75%, wear >=8 h/day & >=5 days/week, cooldown 60 min,
# daily cap 4, randomization p=0.5.


def _empty_figure(message: str) -> plt.Figure:
    """Return a placeholder figure when there is nothing to plot."""
    fig, ax = plt.subplots(figsize=(7, 3))
    ax.text(0.5, 0.5, message, ha="center", va="center", fontsize=11)
    ax.axis("off")
    return fig


def plot_wear_time_heatmap(hr_df: pd.DataFrame) -> plt.Figure:
    """
    Purpose:
        Heatmap of daily wearable coverage (wear_pct) with participants on the
        y-axis and study days on the x-axis. Surfaces per-participant compliance
        patterns and systematic non-wear days at a glance.

    Inputs:
        hr_df - pd.DataFrame from load_heart_rate() (user_id, timestamp, bpm).

    Outputs:
        plt.Figure. Heatmap (participant x date) colored by wear_pct (0-100).

    Example:
        plot_wear_time_heatmap(hr_df)  ->  Figure, rows=participants, cols=days
    Source of data:
        app_heartratesample via compute_wear_time() / identify_wear_gaps().
    """
    wear = compute_wear_time(hr_df)
    if wear.empty:
        return _empty_figure("No wear-time data")

    pivot = wear.pivot(index="user_id", columns="date", values="wear_pct")
    fail_pct = 100.0 * 8.0 / 14.0  # < 8 h of the 14 h waking window fails the goal

    # participant-days containing a > 2 h non-wear gap
    gaps = identify_wear_gaps(hr_df)
    gap_days = set(zip(gaps["user_id"], gaps["date"])) if not gaps.empty else set()

    fig, ax = plt.subplots(figsize=(min(1 + 0.35 * pivot.shape[1], 16),
                                    1 + 0.5 * pivot.shape[0]))
    sns.heatmap(pivot, ax=ax, cmap="YlGnBu", vmin=0, vmax=100,
                cbar_kws={"label": "wear %"})

    rows = list(pivot.index)
    cols = list(pivot.columns)
    for i, uid in enumerate(rows):
        for j, day in enumerate(cols):
            val = pivot.iloc[i, j]
            if pd.isna(val):
                continue
            if val < fail_pct:  # fails the >= 8 h/day goal
                ax.text(j + 0.5, i + 0.5, "✗", ha="center", va="center",
                        color="red", fontsize=8, fontweight="bold")
            elif (uid, day) in gap_days:  # passes 8h but has a > 2 h gap
                ax.text(j + 0.5, i + 0.5, "·", ha="center", va="center",
                        color="black", fontsize=12)

    ax.set_title("Daily wearable coverage (wear %) — ✗ fails ≥8 h/day, "
                 "· has >2 h gap")
    ax.set_xlabel("date")
    ax.set_ylabel("participant")
    fig.tight_layout()
    return fig


def plot_wear_hours_distribution(hr_df: pd.DataFrame) -> plt.Figure:
    """
    Purpose:
        Distribution of daily wear-hours across all participant-days, with the
        >= 8 h/day benchmark drawn as a reference line.

    Inputs:
        hr_df - pd.DataFrame from load_heart_rate().

    Outputs:
        plt.Figure. Histogram of wear-hours/day with an 8 h benchmark line.

    Example:
        plot_wear_hours_distribution(hr_df)  ->  Figure, x=wear hours/day
    Source of data:
        app_heartratesample via compute_wear_time() (wear_minutes / 60).
    """
    wear = compute_wear_time(hr_df)
    if wear.empty:
        return _empty_figure("No wear-time data")

    hours = wear["wear_minutes"] / 60.0
    fig, ax = plt.subplots(figsize=(8, 5))
    sns.histplot(hours, ax=ax, bins=20, kde=False, alpha=0.7)
    ax.axvline(8, color="red", linestyle="--", label="8 h/day benchmark")
    ax.set_xlabel("wear hours per day")
    ax.set_ylabel("participant-days")
    ax.set_title("Distribution of daily wear time")
    ax.legend()
    fig.tight_layout()
    return fig


def plot_response_latency(ema_df: pd.DataFrame) -> plt.Figure:
    """
    Purpose:
        Distribution of EMA response latency (minutes from prompt to submission),
        with the 60-minute in-window boundary drawn as a reference line.

    Inputs:
        ema_df - pd.DataFrame from load_ema() (sent_at, responded_at).

    Outputs:
        plt.Figure. Histogram of response latency with a 60-min window line.

    Example:
        plot_response_latency(ema_df)  ->  Figure, x=latency minutes
    Source of data:
        app_ema.sent_at / responded_at via compute_response_latency().
    """
    latency = compute_response_latency(ema_df).dropna()
    if latency.empty:
        return _empty_figure("No answered EMAs")

    fig, ax = plt.subplots(figsize=(8, 5))
    upper = float(np.nanpercentile(latency, 99))
    sns.histplot(latency.clip(upper=upper), ax=ax, bins=30, alpha=0.7)
    ax.axvline(60, color="red", linestyle="--", label="60-min response window")
    ax.set_xlabel("response latency (minutes)")
    ax.set_ylabel("EMA responses")
    ax.set_title("EMA response latency")
    ax.legend()
    fig.tight_layout()
    return fig


def plot_bitem_missingness(item_df: pd.DataFrame) -> plt.Figure:
    """
    Purpose:
        Heatmap of per-B-item response completeness by participant: for each
        (participant, item_id), the share of that participant's answered prompts
        that include the item. Complements compute_item_missingness (which covers
        only the top-level mood/stress/energy fields).

    Inputs:
        item_df - pd.DataFrame from load_ema_item_responses()
                  (user_id, ema_id, item_id).

    Outputs:
        plt.Figure. Heatmap (participant x item_id) of response rate (0-1).

    Example:
        plot_bitem_missingness(item_df)  ->  Figure, cols=B1,B2,... rows=participants
    Source of data:
        app_emaitemresponse via load_ema_item_responses().
    """
    if item_df is None or item_df.empty:
        return _empty_figure("No EMA item responses")

    prompts_per_user = item_df.groupby("user_id")["ema_id"].nunique()
    present = (
        item_df.groupby(["user_id", "item_id"])["ema_id"].nunique()
        .reset_index(name="present")
    )
    present["rate"] = present.apply(
        lambda r: r["present"] / prompts_per_user[r["user_id"]], axis=1
    )
    pivot = present.pivot(index="user_id", columns="item_id", values="rate")

    fig, ax = plt.subplots(figsize=(1 + 0.8 * pivot.shape[1], 1 + 0.5 * pivot.shape[0]))
    sns.heatmap(pivot, ax=ax, cmap="RdYlGn", vmin=0, vmax=1, annot=True, fmt=".2f",
                cbar_kws={"label": "response rate"})
    ax.set_title("Per-item response completeness by participant")
    ax.set_xlabel("EMA item")
    ax.set_ylabel("participant")
    fig.tight_layout()
    return fig


def plot_decision_funnel(jitai_df: pd.DataFrame) -> plt.Figure:
    """
    Purpose:
        Two-stage JITAI decision funnel: total decision points -> eligible
        (MSSD threshold met) -> randomized-to-send (coin flip) -> sent. Distinct
        from plot_delivery_funnel, which covers the downstream push pipeline.

    Inputs:
        jitai_df - pd.DataFrame from load_jitai_log().

    Outputs:
        plt.Figure. Horizontal descending funnel bars with counts and % of total.

    Example:
        plot_decision_funnel(jitai_df)  ->  Figure, 4 descending bars
    Source of data:
        app_jitailog via audit_decision_stages() ('ALL' row).
    """
    audit = audit_decision_stages(jitai_df)
    if audit.empty:
        return _empty_figure("No JITAI decision points")

    row = audit[audit["user_id"] == "ALL"].iloc[0]
    stages = ["total_decision_points", "eligible", "randomized_to_send", "sent"]
    labels = ["evaluated", "eligible", "randomized to send", "sent"]
    counts = [int(row[s]) for s in stages]
    total = counts[0] if counts[0] else 1

    fig, ax = plt.subplots(figsize=(9, 5))
    ax.barh(labels, counts, color="tab:purple")
    for y, c in enumerate(counts):
        ax.text(c, y, f" {c} ({100.0 * c / total:.0f}%)", va="center")
    ax.invert_yaxis()
    ax.set_xlabel("decision points")
    ax.set_title("JITAI two-stage decision funnel")
    fig.tight_layout()
    return fig


def plot_intervention_dosage(
    jitai_df: pd.DataFrame,
    user_df: pd.DataFrame,
) -> plt.Figure:
    """
    Purpose:
        Per-participant distribution of prompts delivered per active study day
        (Weeks 2+), with the hard daily cap of 4 drawn as a reference line.

    Inputs:
        jitai_df - pd.DataFrame from load_jitai_log().
        user_df  - pd.DataFrame from load_users() (for enrolled_at / active phase).

    Outputs:
        plt.Figure. Box plot of daily prompts-sent per participant + cap line.

    Example:
        plot_intervention_dosage(jitai_df, user_df)  ->  Figure, box per participant
    Source of data:
        app_jitailog + app_user via compute_intervention_dosage().
    """
    if jitai_df is None or jitai_df.empty:
        return _empty_figure("No JITAI decision points")

    dosage = compute_intervention_dosage(
        jitai_df.drop(columns=["enrolled_at"], errors="ignore").merge(
            user_df[["user_id", "enrolled_at"]], on="user_id", how="left"
        )
    )
    active = dosage[dosage["is_active_phase"]]
    if active.empty:
        return _empty_figure("No active-phase prompts")

    users = sorted(active["user_id"].unique())
    data = [active.loc[active["user_id"] == u, "prompts_sent"].values for u in users]
    fig, ax = plt.subplots(figsize=(min(1 + 0.7 * len(users), 14), 5))
    ax.boxplot(data, labels=[str(u) for u in users], showmeans=True)
    ax.axhline(4, color="red", linestyle="--", label="daily cap = 4")
    ax.set_xlabel("participant")
    ax.set_ylabel("prompts sent per active day")
    ax.set_title("Intervention dosage per participant (active phase)")
    ax.legend()
    fig.tight_layout()
    return fig


def plot_engagement_rates(
    engagement_df: pd.DataFrame,
    jitai_df: pd.DataFrame,
) -> plt.Figure:
    """
    Purpose:
        Grouped bar chart of per-participant open / act / dismiss rates on
        delivered JITAI prompts.

    Inputs:
        engagement_df - pd.DataFrame from load_engagement_log().
        jitai_df      - pd.DataFrame from load_jitai_log().

    Outputs:
        plt.Figure. Grouped bars (open/act/dismiss %) per participant.

    Example:
        plot_engagement_rates(engagement_df, jitai_df)  ->  Figure, 3 bars/participant
    Source of data:
        app_engagementlog + app_jitailog via compute_engagement_rates().
    """
    rates = compute_engagement_rates(engagement_df, jitai_df)
    rates = rates[rates["user_id"] != "ALL"]
    if rates.empty:
        return _empty_figure("No delivered prompts")

    users = rates["user_id"].astype(str).tolist()
    x = np.arange(len(users))
    width = 0.25
    fig, ax = plt.subplots(figsize=(min(1 + 0.9 * len(users), 14), 5))
    ax.bar(x - width, rates["open_rate_pct"], width, label="open %", color="tab:blue")
    ax.bar(x, rates["act_rate_pct"], width, label="act %", color="tab:green")
    ax.bar(x + width, rates["dismiss_rate_pct"], width, label="dismiss %", color="tab:orange")
    ax.set_xticks(x)
    ax.set_xticklabels(users)
    ax.set_xlabel("participant")
    ax.set_ylabel("rate (%)")
    ax.set_title("Engagement rates on delivered prompts")
    ax.legend()
    fig.tight_layout()
    return fig


def plot_retention_curve(user_df: pd.DataFrame) -> plt.Figure:
    """
    Purpose:
        Participants on study over time: a step of participants begun (by
        enrollment day) and a step of those currently retained, with the Week-1
        run-in region shaded distinctly from the active phase.

    Inputs:
        user_df - pd.DataFrame from load_users() (user_id, is_enrolled, enrolled_at).

    Outputs:
        plt.Figure. Step lines of begun vs retained participants by study day.

    Note:
        Withdrawal timestamps are not stored, so the retained line reflects the
        final is_enrolled state applied across each participant's study window;
        it shows the level of retention, not the exact day of each dropout.

    Example:
        plot_retention_curve(user_df)  ->  Figure, begun vs retained step lines
    Source of data:
        app_user.is_enrolled / enrolled_at.
    """
    if user_df is None or user_df.empty:
        return _empty_figure("No participants")

    df = user_df.copy()
    df["enrolled_at"] = pd.to_datetime(df["enrolled_at"], utc=True, errors="coerce")
    df = df.dropna(subset=["enrolled_at"])
    if df.empty:
        return _empty_figure("No enrollment timestamps")

    start = df["enrolled_at"].min()
    offsets = ((df["enrolled_at"] - start).dt.days).to_numpy()
    enrolled_flags = df["is_enrolled"].fillna(False).astype(bool).to_numpy()

    max_day = int(offsets.max()) + 35
    days = np.arange(0, max_day + 1)
    begun = np.array([(offsets <= d).sum() for d in days])
    retained = np.array([((offsets <= d) & enrolled_flags).sum() for d in days])

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.step(days, begun, where="post", label="begun", color="tab:blue")
    ax.step(days, retained, where="post", label="retained (is_enrolled)", color="tab:green")
    ax.axvspan(0, 7, color="gray", alpha=0.15, label="run-in (Week 1)")
    ax.set_xlabel("study day (from first enrollment)")
    ax.set_ylabel("participants")
    ax.set_title("Participants on study over time")
    ax.legend()
    fig.tight_layout()
    return fig


def plot_lt_mssd_by_participant(item_df: pd.DataFrame) -> plt.Figure:
    """
    Purpose:
        Bar chart of each participant's overall LT-MSSD (day-to-day volatility of
        daily-mean EMA). Complements the rolling observed_mssd trajectory.

    Inputs:
        item_df - composite B1/B2 per-prompt signal from
                  _prep_trigger_signal(load_ema_item_responses(), user_df),
                  or the raw load_ema_item_responses() frame.

    Outputs:
        plt.Figure. Bar of LT-MSSD per participant.

    Example:
        plot_lt_mssd_by_participant(signal)  ->  Figure, one bar per participant
    Source of data:
        app_emaitemresponse (B1/B2) via compute_lt_mssd().
    """
    if item_df is None or item_df.empty:
        return _empty_figure("No EMA item responses")

    signal = item_df
    if "value_numeric" not in signal.columns or "sent_at" not in signal.columns:
        signal = _prep_trigger_signal(item_df)
    elif "item_id" in signal.columns:
        signal = _prep_trigger_signal(item_df)

    lt = compute_lt_mssd(signal).dropna(subset=["lt_mssd"])
    if lt.empty:
        return _empty_figure("Insufficient data for LT-MSSD")

    fig, ax = plt.subplots(figsize=(min(1 + 0.7 * len(lt), 14), 5))
    ax.bar(lt["user_id"].astype(str), lt["lt_mssd"], color="tab:red", alpha=0.8)
    ax.set_xlabel("participant")
    ax.set_ylabel("LT-MSSD")
    ax.set_title("Long-term MSSD (day-to-day volatility) by participant")
    fig.tight_layout()
    return fig


def plot_hr_ema_concordance(
    hr_df: pd.DataFrame,
    item_df: pd.DataFrame,
) -> plt.Figure:
    """
    Purpose:
        Per-participant HR-EMA volatility concordance: a bar of the Pearson r
        between HR-MSSD (60-min pre-prompt) and EMA volatility, with bars for
        statistically significant participants (p < 0.05) highlighted.

    Inputs:
        hr_df   - pd.DataFrame from load_heart_rate().
        item_df - composite B1/B2 signal, or raw load_ema_item_responses() frame.

    Outputs:
        plt.Figure. Bar of pearson_r per participant; significant bars in color.

    Example:
        plot_hr_ema_concordance(hr_df, item_df)  ->  Figure, r per participant
    Source of data:
        app_heartratesample + app_emaitemresponse via compute_hr_ema_concordance().
    """
    signal = item_df
    if item_df is not None and not item_df.empty and "item_id" in item_df.columns:
        signal = _prep_trigger_signal(item_df)

    conc = compute_hr_ema_concordance(hr_df, signal)
    conc = conc.dropna(subset=["pearson_r"])
    if conc.empty:
        return _empty_figure("Insufficient paired HR/EMA data (need >= 5 pairs)")

    colors = ["tab:green" if (pd.notna(p) and p < 0.05) else "tab:gray"
              for p in conc["p_value"]]
    fig, ax = plt.subplots(figsize=(min(1 + 0.7 * len(conc), 14), 5))
    ax.bar(conc["user_id"].astype(str), conc["pearson_r"], color=colors)
    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_ylim(-1, 1)
    ax.set_xlabel("participant")
    ax.set_ylabel("Pearson r (HR-MSSD vs EMA volatility)")
    ax.set_title("HR-EMA volatility concordance (green = p < 0.05)")
    fig.tight_layout()
    return fig


def plot_participant_mssd_trajectory(jitai_df: pd.DataFrame) -> plt.Figure:
    """
    Purpose:
        One participant's rolling observed_mssd over time at each JITAI decision
        point, with points colored by outcome: sent, eligible-but-not-sent, and
        ineligible. observed_mssd is the engine's rolling trigger signal, read
        straight from JITAILog (not recomputed offline).

    Inputs:
        jitai_df - pd.DataFrame from load_jitai_log(user_id=...) for ONE
                   participant (triggered_at, observed_mssd, send_prompt,
                   randomization_draw).

    Outputs:
        plt.Figure. Line of observed_mssd over triggered_at with colored markers.

    Example:
        plot_participant_mssd_trajectory(load_jitai_log(user_id=42))  ->  Figure
    Source of data:
        app_jitailog.observed_mssd (rolling engine signal) + triggered_at.
    """
    if jitai_df is None or jitai_df.empty:
        return _empty_figure("No JITAI decision points for this participant")

    df = jitai_df.copy()
    df["triggered_at"] = pd.to_datetime(df["triggered_at"], utc=True, errors="coerce")
    df = df.sort_values("triggered_at")

    sent = df["send_prompt"].fillna(False).astype(bool)
    eligible = df["randomization_draw"].notna()
    ineligible = ~eligible
    elig_not_sent = eligible & ~sent

    fig, ax = plt.subplots(figsize=(11, 5))
    ax.plot(df["triggered_at"], df["observed_mssd"], color="lightgray",
            linewidth=1, zorder=1)
    ax.scatter(df.loc[ineligible, "triggered_at"], df.loc[ineligible, "observed_mssd"],
               s=28, color="tab:gray", label="ineligible", zorder=2)
    ax.scatter(df.loc[elig_not_sent, "triggered_at"], df.loc[elig_not_sent, "observed_mssd"],
               s=32, color="tab:orange", label="eligible, not sent", zorder=3)
    ax.scatter(df.loc[sent, "triggered_at"], df.loc[sent, "observed_mssd"],
               s=40, color="tab:green", label="sent", zorder=4)
    ax.set_xlabel("decision time")
    ax.set_ylabel("observed_mssd (rolling)")
    ax.set_title("Per-participant rolling MSSD trajectory")
    ax.legend()
    fig.autofmt_xdate()
    fig.tight_layout()
    return fig


def plot_ema_disposition_over_time(ema_df: pd.DataFrame) -> plt.Figure:
    """
    Purpose:
        Stacked-area decomposition of every delivered EMA prompt per study day
        into its disposition: Completed in-window (responded <= 60 min),
        Responded late (responded > 60 min), and Expired/Missed. A Delivery
        failed band is included only when an EMA links to a failed JITAI via
        source_jitai_log_id (scheduled EMAs carry no EMA-level delivery status;
        push-level failures are shown in the delivery-funnel figure).

    Inputs:
        ema_df - pd.DataFrame from load_ema() (sent_at, responded_at, status,
                 optionally source_jitai_log_id).

    Outputs:
        plt.Figure. Daily stacked area of prompt dispositions.

    Example:
        plot_ema_disposition_over_time(ema)  ->  Figure, stacked disposition areas
    Source of data:
        app_ema.status / responded_at (via load_ema()).
    """
    df = ema_df.copy()
    df["sent_at"] = pd.to_datetime(df["sent_at"], utc=True, errors="coerce")
    df = df.dropna(subset=["sent_at"])
    if df.empty:
        return _empty_figure("No EMA prompts")
    df["date"] = df["sent_at"].dt.date

    latency = (
        pd.to_datetime(df["responded_at"], utc=True, errors="coerce") - df["sent_at"]
    ).dt.total_seconds() / 60.0
    answered = df["responded_at"].notna()

    df["disposition"] = np.select(
        [answered & latency.between(0, 60), answered & (latency > 60)],
        ["Completed in-window", "Responded late"],
        default="Expired/Missed",
    )

    daily = (
        df.groupby(["date", "disposition"]).size().unstack(fill_value=0)
    )
    order = ["Completed in-window", "Responded late", "Expired/Missed"]
    colors = {"Completed in-window": "tab:green", "Responded late": "tab:orange",
              "Expired/Missed": "tab:gray"}
    order = [c for c in order if c in daily.columns]

    fig, ax = plt.subplots(figsize=(11, 5.5))
    ax.stackplot(daily.index, [daily[c] for c in order],
                 labels=order, colors=[colors[c] for c in order], alpha=0.85)
    ax.set_xlabel("date")
    ax.set_ylabel("delivered prompts")
    ax.set_title("EMA prompt disposition over time")
    ax.legend(loc="upper right", fontsize=8)
    fig.autofmt_xdate()
    fig.tight_layout()
    return fig


def plot_mssd_distribution_by_participant(
    jitai_df: pd.DataFrame,
    item_df: Optional[pd.DataFrame] = None,
    user_ids: Optional[List[int]] = None,
    max_facets: int = 12,
) -> plt.Figure:
    """
    Purpose:
        Small-multiple facets of the observed_mssd distribution per participant,
        each with the participant's sample-mean line and, when item_df is given,
        the participant's theoretical expected MSSD 2*sigma_hat^2*(1-rho_hat).
        Reveals within-person baseline differences and threshold calibration.

    Inputs:
        jitai_df   - pd.DataFrame from load_jitai_log() (user_id, observed_mssd).
        item_df    - optional B1/B2 responses for the expected-MSSD line.
        user_ids   - optional subset of participants to facet.
        max_facets - cap on the number of facets (default 12) for large cohorts.

    Outputs:
        plt.Figure. Grid of per-participant observed_mssd histograms.

    Example:
        plot_mssd_distribution_by_participant(jitai, item_df)  ->  facet grid
    Source of data:
        app_jitailog.observed_mssd; expected MSSD from B1/B2 (compute_ar1_parameters).
    """
    if jitai_df is None or jitai_df.empty:
        return _empty_figure("No JITAI decision points")

    users = user_ids if user_ids is not None else sorted(jitai_df["user_id"].unique())
    users = list(users)[:max_facets]
    if not users:
        return _empty_figure("No participants to facet")

    expected_by_user = {}
    if item_df is not None and not item_df.empty:
        signal = _prep_trigger_signal(item_df) if "item_id" in item_df.columns else item_df
        for uid, g in signal.sort_values("sent_at").groupby("user_id"):
            rho, sig = compute_ar1_parameters(g["value_numeric"])
            if not (np.isnan(rho) or np.isnan(sig)):
                expected_by_user[uid] = compute_expected_mssd(sig, rho)

    ncols = min(3, len(users))
    nrows = int(np.ceil(len(users) / ncols))
    fig, axes = plt.subplots(nrows, ncols, figsize=(4.5 * ncols, 3.2 * nrows),
                             squeeze=False)
    axes_flat = axes.flatten()
    for ax, uid in zip(axes_flat, users):
        vals = jitai_df.loc[jitai_df["user_id"] == uid, "observed_mssd"].dropna()
        if not vals.empty:
            sns.histplot(vals, ax=ax, bins=20, color="tab:blue", alpha=0.6)
            ax.axvline(float(vals.mean()), color="black", linewidth=1.2,
                       label=f"mean {vals.mean():.2f}")
        if uid in expected_by_user:
            ax.axvline(expected_by_user[uid], color="purple", linestyle="--",
                       label=f"E[MSSD] {expected_by_user[uid]:.2f}")
        ax.set_title(f"participant {uid}")
        ax.set_xlabel("observed_mssd")
        ax.legend(fontsize=7)
    for ax in axes_flat[len(users):]:
        ax.axis("off")

    fig.suptitle("Observed MSSD distribution by participant")
    fig.tight_layout()
    return fig


def plot_delivery_funnel_stratified(
    jitai_df: pd.DataFrame,
    by: str = "receipt_platform",
) -> plt.Figure:
    """
    Purpose:
        Grouped delivery funnel stratified by an operational dimension -
        receipt_platform (iOS vs Android) or receipt_app_state (foreground vs
        background) - to expose OS- or state-specific background sync loss.

    Inputs:
        jitai_df - pd.DataFrame from load_jitai_log().
        by       - "receipt_platform" or "receipt_app_state".

    Outputs:
        plt.Figure. Grouped bars: funnel stage counts per stratum value.

    Example:
        plot_delivery_funnel_stratified(jitai, by="receipt_platform")  ->  Figure
    Source of data:
        app_jitailog send_prompt + funnel timestamps + receipt_platform/app_state.
    """
    if jitai_df is None or jitai_df.empty:
        return _empty_figure("No JITAI decision points")

    sent = jitai_df[jitai_df["send_prompt"].fillna(False).astype(bool)].copy()
    if sent.empty or by not in sent.columns:
        return _empty_figure(f"No sent prompts with {by}")
    sent[by] = sent[by].replace("", np.nan)
    strata = sorted(sent[by].dropna().unique())
    if not strata:
        return _empty_figure(f"No {by} values on sent prompts")

    stages = ["sent", "accepted_by_expo", "received_on_device", "receipt_reported"]
    x = np.arange(len(stages))
    width = 0.8 / len(strata)

    fig, ax = plt.subplots(figsize=(10, 5.5))
    for i, stratum in enumerate(strata):
        g = sent[sent[by] == stratum]
        counts = [
            len(g),
            int(g["push_sent_at"].notna().sum()),
            int(g["device_received_at"].notna().sum()),
            int(g["receipt_reported_at"].notna().sum()),
        ]
        offset = (i - (len(strata) - 1) / 2) * width
        bars = ax.bar(x + offset, counts, width, label=f"{stratum} (n={len(g)})")
        for b, c in zip(bars, counts):
            ax.text(b.get_x() + b.get_width() / 2, c, str(c),
                    ha="center", va="bottom", fontsize=7)

    ax.set_xticks(x)
    ax.set_xticklabels(stages, rotation=15)
    ax.set_ylabel("count")
    ax.set_title(f"Delivery funnel stratified by {by}")
    ax.legend()
    fig.tight_layout()
    return fig


def plot_jitai_decision_timeline(
    jitai_df: pd.DataFrame,
    ema_df: Optional[pd.DataFrame] = None,
    user_ids: Optional[List[int]] = None,
    max_lanes: int = 8,
    day_window: int = 3,
) -> plt.Figure:
    """
    Purpose:
        Per-participant swimlane of JITAI decision points over a short time
        window, colored by outcome (sent / cooldown blocked / daily-cap blocked /
        below threshold / other), with a 60-minute cooldown buffer drawn after
        each sent prompt and EMA responses shown as light ticks. Confirms the
        decision rules (60-min cooldown, 4/day cap) are being enforced.

    Inputs:
        jitai_df   - pd.DataFrame from load_jitai_log().
        ema_df     - optional pd.DataFrame from load_ema() for EMA-response ticks.
        user_ids   - optional subset of participants (one lane each).
        max_lanes  - cap on the number of participant lanes (default 8).
        day_window - number of days from the first decision to display (default 3),
                     keeping the swimlane legible.

    Outputs:
        plt.Figure. Swimlane timeline (participants x time) of decision events.

    Example:
        plot_jitai_decision_timeline(jitai, ema)  ->  Figure, one lane/participant
    Source of data:
        app_jitailog.triggered_at / trigger_reason / send_prompt (+ app_ema).
    """
    if jitai_df is None or jitai_df.empty:
        return _empty_figure("No JITAI decision points")

    df = jitai_df.copy()
    df["triggered_at"] = pd.to_datetime(df["triggered_at"], utc=True, errors="coerce")
    df = df.dropna(subset=["triggered_at"])
    if df.empty:
        return _empty_figure("No decision timestamps")

    start = df["triggered_at"].min()
    end = start + pd.Timedelta(days=day_window)
    df = df[df["triggered_at"] < end]

    users = user_ids if user_ids is not None else sorted(df["user_id"].unique())
    users = list(users)[:max_lanes]
    if not users:
        return _empty_figure("No participants to plot")

    reason_color = {
        "Sent / randomized": "tab:green",
        "Cooldown blocked": "tab:orange",
        "Daily-cap blocked": "tab:red",
        "Below threshold": "tab:gray",
        "Insufficient history/data": "tab:purple",
        "Other": "tab:brown",
    }

    fig, ax = plt.subplots(figsize=(13, 1.1 * len(users) + 1.5))
    seen = set()
    for lane, uid in enumerate(users):
        g = df[df["user_id"] == uid]
        for _, row in g.iterrows():
            cat = _decision_category(row["trigger_reason"])
            color = reason_color.get(cat, "tab:brown")
            label = cat if cat not in seen else None
            seen.add(cat)
            ax.scatter(row["triggered_at"], lane, color=color, s=30, zorder=3,
                       label=label)
            if bool(row["send_prompt"]):  # 60-min cooldown buffer after a send
                ax.hlines(lane, row["triggered_at"],
                          row["triggered_at"] + pd.Timedelta(minutes=60),
                          color="tab:green", alpha=0.35, linewidth=6, zorder=1)
        if ema_df is not None and not ema_df.empty:
            e = ema_df[ema_df["user_id"] == uid].copy()
            e["sent_at"] = pd.to_datetime(e["sent_at"], utc=True, errors="coerce")
            e = e[(e["sent_at"] >= start) & (e["sent_at"] < end)
                  & e["responded_at"].notna()]
            ax.scatter(e["sent_at"], [lane - 0.3] * len(e), marker="|",
                       color="black", alpha=0.4, s=60, zorder=2)

    ax.set_yticks(range(len(users)))
    ax.set_yticklabels([f"participant {u}" for u in users])
    ax.set_xlabel("time")
    ax.set_title(f"JITAI decision timeline (first {day_window} days) — "
                 "green bar = 60-min cooldown, | = EMA response")
    ax.legend(loc="upper right", fontsize=7, ncol=3)
    fig.autofmt_xdate()
    fig.tight_layout()
    return fig
