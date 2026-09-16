"""Shared data and palette layer for the REACT monitoring notebooks.

Imported by every notebook under dashboard/stages/. Holds the Django bootstrap,
the fixture switch, the fourteen source frames, every Stage 1-3 compute function
and the validated Plotly palette. The notebooks themselves hold only plots.

Read-only: nothing here writes to the database. With SYNTHETIC_DATA True no
database is contacted at all - the frames come from dashboard/fixture_cohort.json.

Regenerate the fixture with: python dashboard/make_fixture.py
"""

from __future__ import annotations

import json
import math
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

os.environ.setdefault("DJANGO_ALLOW_ASYNC_UNSAFE", "true")

# Anchored to this file, not the working directory, so the notebooks run from
# anywhere - dashboard/stages/, the repo root, or a Jupyter server elsewhere.
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BACKEND_DIR = REPO_ROOT / "backend"
for _path in (REPO_ROOT, BACKEND_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "project.settings")

import django  # noqa: E402

django.setup()

from django.conf import settings  # noqa: E402
from django.utils import timezone  # noqa: E402

import plotly.graph_objects as go  # noqa: E402
from plotly.subplots import make_subplots  # noqa: E402

from app.models import (  # noqa: E402
    EMA,
    CheckinReminder,
    EMAItemResponse,
    EngagementLog,
    HeartRateSample,
    JITAILog,
    PhoneTelemetry,
    User,
    WearableDevice,
    WearableSync,
)
from dashboard.models import Alert, MetricsCohort, MetricsDaily, MetricsParticipant  # noqa: E402

_db = settings.DATABASES["default"]

# CHANGE THIS  <------------ 
SYNTHETIC_DATA = True

EMA_FIELDS = (
    "id", "user_id", "prompt_id", "ema_type", "status",
    "sent_at", "responded_at", "expires_at",
    "outcome_window_start", "outcome_window_end",
    "source_jitai_log_id", "served_sub_item_ids",
    "mood", "stress", "energy",
)


METRICS_DAILY_FIELDS = (
    "id", "user_id", "study_day", "local_date", "is_run_in", "is_active_day",
    "computed_at", "item_bank_version",
    "ema_scheduled_n", "ema_jitai_n", "ema_post_prompt_n",
    "slots_expected", "slots_covered", "slots_reminded_uncovered", "slots_silent",
    "reminders_sent", "reminders_per_checkin_median",
    "completeness_mean", "ema_missing_b1b2_n",
    "decision_points_n", "eligible_n", "sent_n", "delivered_n", "cap_hit",
    "min_gap_min", "cooldown_violations_n", "runin_violation_n",
    "prompt_opened_n", "prompt_acted_n", "prompt_dismissed_n", "outcome_captured_n",
    "wear_valid_pct", "wear_gap_pct", "gaps_gt2h_n", "max_gap_min", "hr_minutes_valid",
    "last_sync_age_h_eod", "clock_skew_p95_ms", "delivery_failures_n",
)


METRICS_PARTICIPANT_FIELDS = (
    "id", "user_id", "computed_at", "enrolled_at", "day1_date", "study_day_now", "phase",
    "is_enrolled_snapshot", "first_seen_not_enrolled_at", "last_ema_at", "last_sync_at",
    "active_retention", "risk_score", "risk_components",
    "slot_coverage_rate", "slot_coverage_num", "slot_coverage_den",
    "prompt_response_rate", "prompt_response_num", "prompt_response_den",
    "wear_rate", "wear_num", "wear_den",
)


METRICS_COHORT_FIELDS = (
    "id", "as_of", "phase_filter", "n_participants", "n_active",
    "benchmarks", "series_14d", "integrity", "funnel",
    "decision_points_n", "eligible_n", "sent_n", "delivered_n",
    "cooldown_violations_n", "runin_violations_n", "cap_hit_days", "delivery_failures_n",
)


ALERT_FIELDS = ("id", "user_id", "rule_id", "severity", "fired_at", "resolved_at", "payload")


JITAI_FIELDS = (
    "id", "user_id", "prompt_id", "triggered_at", "trigger_reason", "trigger_signal",
    "decision_point_id", "ema_id",
    "observed_mssd", "threshold_at_decision", "threshold_source",
    "randomization_probability", "randomization_draw",
    "message_arm", "arm_randomization_probability", "arm_randomization_draw",
    "send_prompt", "status",
    "hr_at_trigger", "stress_at_trigger", "ema_mood", "ema_stress", "ema_energy",
    "decision_made_at", "push_sent_at", "device_received_at", "receipt_reported_at",
    "receipt_event_id", "delivery_status", "delivery_error",
    "receipt_platform", "receipt_app_state",
    "eligible_prompt_ids", "evaluated_items", "matched_categories",
    "category_drawn", "fallback_reason",
)

# When True every frame comes from the committed fixture and nothing touches the
# database - the notebook runs with no connection at all. When False the ORM path
# below is used unchanged. Regenerate with: python dashboard/make_fixture.py
from dashboard.data.config import PARTICIPANT_TZ

FIXTURE_PATH = REPO_ROOT / "dashboard" / "fixture_cohort.json"
HR_DAYS = 14

_FIXTURE = None

UTC_COLUMNS = {
    "app_user": ("enrolled_at",),
    "app_wearabledevice": ("last_synced_at",),
    "app_wearablesync": ("observed_at", "last_synced_at"),
    "app_heartratesample": ("timestamp",),
    "app_ema": ("sent_at", "responded_at", "expires_at",
                "outcome_window_start", "outcome_window_end"),
    "app_checkinreminder": ("sent_at",),
    "app_phonetelemetry": ("occurred_at", "recorded_at"),
    "app_jitailog": ("triggered_at", "decision_made_at", "push_sent_at",
                     "device_received_at", "receipt_reported_at"),
    "app_engagementlog": ("occurred_at", "recorded_at"),
    "dashboard_metricsdaily": ("computed_at",),
    "dashboard_metricsparticipant": ("computed_at", "enrolled_at",
                                     "first_seen_not_enrolled_at", "last_ema_at", "last_sync_at"),
    "dashboard_metricscohort": ("as_of",),
    "dashboard_alert": ("fired_at", "resolved_at"),
}

# Compared against today_local() results, which are datetime.date. A Timestamp
# here would silently break every participant-day join.
DATE_COLUMNS = {
    "app_user": ("birthdate",),
    "dashboard_metricsdaily": ("local_date",),
    "dashboard_metricsparticipant": ("day1_date",),
}

BOOL_COLUMNS = {
    "app_user": ("is_enrolled",),
    "app_wearabledevice": ("is_active",),
    "app_jitailog": ("send_prompt",),
    "dashboard_metricsdaily": ("is_run_in", "is_active_day", "cap_hit"),
    "dashboard_metricsparticipant": ("is_enrolled_snapshot", "active_retention"),
}


def load_fixture():
    global _FIXTURE
    if _FIXTURE is None:
        with open(FIXTURE_PATH, encoding="utf-8") as handle:
            _FIXTURE = json.load(handle)
        _FIXTURE["tables"]["app_heartratesample"] = expand_heart_rate(_FIXTURE)
    return _FIXTURE


def expand_heart_rate(fixture):
    """Run-length encoded worn stretches -> one row per minute.

    Stored compressed because 1-minute fidelity is what wear_lane bins on and
    what wear_coverage compares gap-coverage against; a coarser cadence would
    flag every day as burst charging.
    """
    rows = []
    sample_id = 0
    for day in fixture.get("heart_rate_runs", []):
        local_date = pd.Timestamp(day["local_date"])
        midnight = local_date.tz_localize(PARTICIPANT_TZ)
        for start_minute, series in day["runs"]:
            for offset, bpm in enumerate(series):
                sample_id += 1
                rows.append({
                    "id": sample_id,
                    "user_id": day["user_id"],
                    "timestamp": (midnight + pd.Timedelta(minutes=start_minute + offset)
                                  ).tz_convert("UTC").isoformat(),
                    "bpm": bpm,
                    "source": "garmin_labfront",
                })
    return rows


def coerce(table, df):
    for column in UTC_COLUMNS.get(table, ()):
        if column in df.columns:
            df[column] = pd.to_datetime(df[column], utc=True, errors="coerce")
    for column in DATE_COLUMNS.get(table, ()):
        if column in df.columns:
            df[column] = [None if pd.isna(v) else pd.Timestamp(v).date() for v in df[column]]
    for column in BOOL_COLUMNS.get(table, ()):
        if column in df.columns:
            df[column] = df[column].fillna(False).astype(bool)
    return df


def fixture_table(table, fields):
    rows = load_fixture()["tables"].get(table, [])
    return coerce(table, pd.DataFrame(rows, columns=list(fields)))


# LOADER HELPERS
def frame(queryset, *fields):
    """One frame per table, from the fixture or the ORM.

    Dispatches on the model's own db_table so the ten loader cells below are
    identical in both modes. Django querysets are lazy, so constructing one with
    no database behind it is safe - nothing is evaluated until .values() runs,
    which the synthetic branch never reaches.
    """
    table = queryset.model._meta.db_table
    if SYNTHETIC_DATA:
        return fixture_table(table, fields)
    return pd.DataFrame(list(queryset.values(*fields)), columns=list(fields))


def shape(name, df):
    return {"table": name, "rows": len(df), "columns": df.shape[1]}


# STUDY CONSTANTS
from dashboard.data.config import (
    BENCHMARKS,
    DAILY_PROMPT_CAP,
    ITEM_BANK_VERSION,
    JITAI_COOLDOWN_MINUTES,
    MSSD_WINDOW,
    OUTCOME_WINDOW_HOURS,
    PARTICIPANT_TZ,
    RATE_MIN_PARTICIPANTS,
    RATE_MIN_UNITS,
    RUN_IN_DAYS,
    STUDY_DAYS,
    THRESHOLD_QUANTILE,
    WAKING_WINDOW_END_HOUR,
    WAKING_WINDOW_START_HOUR,
)


# STAGE 1 — COHORT BOARD
import math
from datetime import date, timedelta

import numpy as np

from dashboard.data.cohort import (
    INELIGIBLE_REASONS,
    WAKING_MINUTES,
    WEAR_DIVERGENCE,
    ks_uniform,
    suppress_rate,
    wilson_interval,
)
from dashboard.data.config import (
    NOTIFICATION_WINDOW_END_HOUR,
    NOTIFICATION_WINDOW_START_HOUR,
    PHASE1_USER_IDS,
    SCHEDULED_CHECK_IN_DAILY_CAP,
    UNMEASURABLE_BENCHMARKS,
    WEAR_GAP_MIN,
    randomization_p,
)
from dashboard.data.daily import ACTED_CHOICES, FEEDBACK_TYPE, OUTCOME_TYPES, SCHEDULED_TYPE
from dashboard.data.windows import today_local

SERIES_DAYS = 14
ACTIVE_RETENTION_DAYS = 7
SLOT_HOURS = (NOTIFICATION_WINDOW_END_HOUR - NOTIFICATION_WINDOW_START_HOUR) / SCHEDULED_CHECK_IN_DAILY_CAP
ELIGIBILITY_BAND = (0.10, 0.35)
CAP_HIT_ALARM = 0.10
OUTCOME_CAPTURE_ALARM = 0.60
KS_ALARM_P = 0.01
OPENED_EVENT = "notification_tapped"
SIGNAL_SUB_ITEM = "C0_behavior_change"


def to_local(series):
    return pd.to_datetime(series, utc=True).dt.tz_convert(PARTICIPANT_TZ)


def local_date_of(moment):
    if moment is None or pd.isna(moment):
        return None
    return pd.Timestamp(moment).tz_convert(PARTICIPANT_TZ).date()


def cohort_users(phase="all"):
    ids = set(users_df.loc[users_df["enrolled_at"].notna(), "user_id"])
    if phase == "phase1":
        ids &= set(PHASE1_USER_IDS)
    elif phase == "phase2":
        ids -= set(PHASE1_USER_IDS)
    return sorted(ids)


def withdrawal_dates():
    if metrics_participant_df.empty:
        return {}
    return {
        uid: local_date_of(moment)
        for uid, moment in zip(
            metrics_participant_df["user_id"],
            metrics_participant_df["first_seen_not_enrolled_at"],
        )
        if pd.notna(moment)
    }


def day1_dates():
    enrolled = users_df[users_df["enrolled_at"].notna()]
    return {uid: local_date_of(m) for uid, m in zip(enrolled["user_id"], enrolled["enrolled_at"])}


def participant_days(phase="all", as_of=None):
    as_of = as_of or today_local()
    day1 = day1_dates()
    withdrawn = withdrawal_dates()
    rows = []
    for uid in cohort_users(phase):
        anchor = day1.get(uid)
        if anchor is None:
            continue
        stop = withdrawn.get(uid)
        for offset in range(STUDY_DAYS):
            local = anchor + timedelta(days=offset)
            if local > as_of:
                break
            if stop is not None and local >= stop:
                break
            rows.append({
                "user_id": uid,
                "local_date": local,
                "study_day": offset,
                "is_run_in": offset < RUN_IN_DAYS,
            })
    frame = pd.DataFrame(rows, columns=["user_id", "local_date", "study_day", "is_run_in"])
    return frame.astype({"user_id": "int64", "study_day": "int64", "is_run_in": "bool"})


def benchmark(name, k, n, participants, unit, extra=None):
    if name in UNMEASURABLE_BENCHMARKS:
        return {
            "measurable": False, "suppressed": False, "unit": unit,
            "target": BENCHMARKS.get(name), "value": None,
            "wilson_low": None, "wilson_high": None,
            "numerator": None, "denominator": None, "participants": 0,
        }
    value = suppress_rate(k, n, participants)
    low, high = wilson_interval(k, n) if value is not None else (None, None)
    entry = {
        "measurable": True, "suppressed": value is None, "unit": unit,
        "target": BENCHMARKS.get(name), "value": value,
        "wilson_low": low, "wilson_high": high,
        "numerator": k, "denominator": n, "participants": participants,
    }
    if extra:
        entry.update(extra)
    return entry


def gauge(name, k, n, target=None, alarm=False, extra=None):
    low, high = wilson_interval(k, n)
    entry = {
        "gauge": name, "value": (k / n) if n else None,
        "numerator": k, "denominator": n,
        "wilson_low": low, "wilson_high": high, "target": target,
        "alarm": bool(alarm), "contradiction": bool(n and k > n),
    }
    if extra:
        entry.update(extra)
    return entry


def scheduled_slots(users=None):
    rows = ema_df[ema_df["ema_type"].eq(SCHEDULED_TYPE) & ema_df["status"].eq("completed")].copy()
    if users is not None:
        rows = rows[rows["user_id"].isin(users)]
    if rows.empty:
        return pd.DataFrame(columns=["user_id", "local_date", "slot_index", "hour"])
    local = to_local(rows["sent_at"])
    rows["local_date"] = local.dt.date
    rows["hour"] = local.dt.hour
    rows = rows[
        rows["hour"].ge(NOTIFICATION_WINDOW_START_HOUR)
        & rows["hour"].lt(NOTIFICATION_WINDOW_END_HOUR)
    ]
    rows["slot_index"] = np.floor((rows["hour"] - NOTIFICATION_WINDOW_START_HOUR) / SLOT_HOURS).astype(int)
    return rows[["user_id", "local_date", "slot_index", "hour"]]


def slot_coverage_days(phase="all"):
    days = participant_days(phase)
    slots = scheduled_slots(set(days["user_id"]))
    covered = (
        slots.groupby(["user_id", "local_date"])["slot_index"].nunique().reset_index(name="slots_covered")
        if not slots.empty
        else pd.DataFrame(columns=["user_id", "local_date", "slots_covered"])
    )
    days = days.merge(covered, on=["user_id", "local_date"], how="left")
    days["slots_covered"] = days["slots_covered"].fillna(0).astype(int)
    days["slots_expected"] = SCHEDULED_CHECK_IN_DAILY_CAP
    return days


def slot_coverage(phase="all"):
    days = slot_coverage_days(phase)
    k = int(days["slots_covered"].sum())
    n = int(days["slots_expected"].sum())
    participants = days["user_id"].nunique()
    per_user = days.groupby("user_id")[["slots_covered", "slots_expected"]].sum()
    means = (per_user["slots_covered"] / per_user["slots_expected"])
    mean = float(means.mean()) if len(means) else None
    return benchmark(
        "slot_coverage", k, n, participants, "check-in slots covered",
        extra={
            "label": "Slot coverage (proxy)",
            "proxy": True,
            "participant_mean": mean if suppress_rate(k, n, participants) is not None else None,
            "participant_days": len(days),
        },
    )


def slot_diagnostic(phase="all"):
    users = cohort_users(phase)
    reminders = checkin_reminders_df[checkin_reminders_df["user_id"].isin(users)].copy()
    if reminders.empty:
        return pd.DataFrame(columns=["daily_count_at_send", "local_hour", "n"])
    local = to_local(reminders["sent_at"])
    reminders["local_hour"] = local.dt.hour
    return (
        reminders.groupby(["daily_count_at_send", "local_hour"])
        .size().reset_index(name="n")
        .sort_values(["daily_count_at_send", "local_hour"])
    )


def mrt_day_keys(phase="all"):
    days = participant_days(phase)
    mrt = days[~days["is_run_in"]]
    return set(zip(mrt["user_id"], mrt["local_date"])), mrt


def decisions(phase="all", mrt_only=True):
    users = cohort_users(phase)
    rows = jitai_log_df[jitai_log_df["user_id"].isin(users)].copy()
    anchor = rows["push_sent_at"].fillna(rows["decision_made_at"]).fillna(rows["triggered_at"])
    rows["local_date"] = to_local(anchor).dt.date
    if mrt_only:
        keys, _ = mrt_day_keys(phase)
        mask = np.array([(u, d) in keys for u, d in zip(rows["user_id"], rows["local_date"])], dtype=bool)
        rows = rows[mask]
    return rows


def responded_logs(delivered):
    window = pd.Timedelta(hours=OUTCOME_WINDOW_HOURS)
    push = dict(zip(delivered["id"], pd.to_datetime(delivered["push_sent_at"], utc=True)))
    events = engagement_log_df[
        engagement_log_df["jitai_log_id"].isin(push) & engagement_log_df["event_type"].eq(OPENED_EVENT)
    ]
    responded = set()
    for log_id, occurred in zip(events["jitai_log_id"], pd.to_datetime(events["occurred_at"], utc=True)):
        deadline = push.get(log_id)
        if deadline is not None and pd.notna(deadline) and occurred <= deadline + window:
            responded.add(log_id)
    acted_emas = set(
        ema_item_responses_df.loc[
            ema_item_responses_df["sub_item_id"].eq(SIGNAL_SUB_ITEM)
            & ema_item_responses_df["value_choice"].isin(ACTED_CHOICES),
            "ema_id",
        ]
    )
    feedback = ema_df[
        ema_df["source_jitai_log_id"].isin(push)
        & ema_df["ema_type"].eq(FEEDBACK_TYPE)
        & ema_df["status"].eq("completed")
        & ema_df["id"].isin(acted_emas)
    ]
    responded |= set(feedback["source_jitai_log_id"])
    return responded


def prompt_response(phase="all"):
    rows = decisions(phase)
    sent = rows[rows["send_prompt"].astype(bool)] if not rows.empty else rows
    delivered = sent[sent["device_received_at"].notna()] if not sent.empty else sent
    if delivered.empty:
        return benchmark("prompt_response", 0, 0, 0, "delivered prompts responded to",
                         extra={"window": "weeks 2-5", "by_platform": {}})
    responded = responded_logs(delivered)
    k = int(delivered["id"].isin(responded).sum())
    n = int(len(delivered))
    participants = int(delivered["user_id"].nunique())
    by_platform = {}
    for platform, group in delivered.groupby(delivered["receipt_platform"].fillna("unknown")):
        hits = int(group["id"].isin(responded).sum())
        by_platform[platform] = {
            "numerator": hits,
            "denominator": int(len(group)),
            "participants": int(group["user_id"].nunique()),
            "value": suppress_rate(hits, len(group), group["user_id"].nunique()),
        }
    return benchmark(
        "prompt_response", k, n, participants, "delivered prompts responded to",
        extra={"window": "weeks 2-5", "by_platform": by_platform},
    )


def long_gap_minutes(valid, threshold=WEAR_GAP_MIN):
    total = run = 0
    for flag in valid:
        if flag:
            if run > threshold:
                total += run
            run = 0
        else:
            run += 1
    if run > threshold:
        total += run
    return total


def wear_days(phase="all"):
    days = participant_days(phase)
    start_minute = WAKING_WINDOW_START_HOUR * 60
    hr = heart_rate_df[heart_rate_df["bpm"].gt(0)].copy() if not heart_rate_df.empty else heart_rate_df
    scored = {}
    if not hr.empty:
        local = to_local(hr["timestamp"])
        hr["local_date"] = local.dt.date
        hr["minute"] = local.dt.hour * 60 + local.dt.minute
        hr = hr[hr["minute"].ge(start_minute) & hr["minute"].lt(WAKING_WINDOW_END_HOUR * 60)]
        for (uid, local_date), group in hr.groupby(["user_id", "local_date"]):
            bins = np.zeros(WAKING_MINUTES, dtype=bool)
            bins[group["minute"].to_numpy() - start_minute] = True
            scored[(uid, local_date)] = (float(bins.mean()), long_gap_minutes(bins))
    keys = list(zip(days["user_id"], days["local_date"]))
    days["minute_coverage"] = [scored.get(key, (np.nan, np.nan))[0] for key in keys]
    days["gap_minutes"] = [scored.get(key, (np.nan, np.nan))[1] for key in keys]
    days["gap_coverage"] = (WAKING_MINUTES - days["gap_minutes"]) / WAKING_MINUTES
    return days


def wear_coverage(phase="all"):
    days = wear_days(phase)
    scored = days[days["gap_coverage"].notna()]
    n = int(len(scored))
    k = int((scored["gap_coverage"] >= BENCHMARKS["wear"]).sum())
    participants = int(scored["user_id"].nunique())
    diverged = int((scored["gap_coverage"] - scored["minute_coverage"] >= WEAR_DIVERGENCE).sum())
    return benchmark(
        "wear", k, n, participants, "participant-days meeting the coverage target",
        extra={
            "mean_gap_coverage": float(scored["gap_coverage"].mean()) if n else None,
            "mean_minute_coverage": float(scored["minute_coverage"].mean()) if n else None,
            "burst_charging_days": diverged,
            "divergence_threshold": WEAR_DIVERGENCE,
            "gap_threshold_min": WEAR_GAP_MIN,
            "maps_to_metricsdaily": {
                "gap_coverage": "wear_valid_pct",
                "minute_coverage": "hr_minutes_valid / 840",
            },
        },
    )


def retention(phase="all", as_of=None):
    as_of = as_of or today_local()
    users = cohort_users(phase)
    day1 = day1_dates()
    withdrawn = withdrawal_dates()
    started = [uid for uid in users if day1.get(uid) is not None and day1[uid] <= as_of]
    complete = [uid for uid in started if (as_of - day1[uid]).days >= STUDY_DAYS - 1]
    enrolled_now = set(users_df.loc[users_df["is_enrolled"].astype(bool), "user_id"])
    retained = [uid for uid in complete if uid in enrolled_now and uid not in withdrawn]

    in_window = [
        uid for uid in started
        if (as_of - day1[uid]).days < STUDY_DAYS
        and (withdrawn.get(uid) is None or withdrawn[uid] > as_of)
    ]
    slots = scheduled_slots(set(in_window))
    cutoff = as_of - timedelta(days=ACTIVE_RETENTION_DAYS)
    recent = set(slots.loc[slots["local_date"] > cutoff, "user_id"]) if not slots.empty else set()
    active_k = len([uid for uid in in_window if uid in recent])

    formal = benchmark(
        "retention", len(retained), len(complete), len(complete),
        "participants retained at day 35",
    )
    formal["active"] = benchmark(
        "retention", active_k, len(in_window), len(in_window),
        f"participants with a scheduled check-in in {ACTIVE_RETENTION_DAYS} days",
    )
    formal["started_day1"] = len(started)
    formal["reached_day35"] = len(complete)
    return formal


def hair_sample(phase="all"):
    return {
        "measurable": False, "suppressed": False,
        "unit": "hair samples collected",
        "label": "Hair sample",
        "target": 0.90, "value": None,
        "wilson_low": None, "wilson_high": None,
        "numerator": None, "denominator": None, "participants": 0,
        "source": "external: no table in production; needs an RA sheet loaded into a derived table",
    }


def series_14d(phase="all", as_of=None):
    as_of = as_of or today_local()
    window_start = as_of - timedelta(days=SERIES_DAYS - 1)
    slots = slot_coverage_days(phase)
    wear = wear_days(phase)
    rows_jitai = decisions(phase, mrt_only=False)
    delivered = (
        rows_jitai[rows_jitai["send_prompt"].astype(bool) & rows_jitai["device_received_at"].notna()]
        if not rows_jitai.empty else rows_jitai
    )
    responded = responded_logs(delivered) if not delivered.empty else set()

    out = []
    for offset in range(SERIES_DAYS):
        local = window_start + timedelta(days=offset)
        day_slots = slots[slots["local_date"].eq(local)] if not slots.empty else slots
        day_wear = wear[wear["local_date"].eq(local) & wear["gap_coverage"].notna()] if not wear.empty else wear
        day_delivered = delivered[delivered["local_date"].eq(local)] if not delivered.empty else delivered
        participants = int(day_slots["user_id"].nunique()) if len(day_slots) else 0

        covered = int(day_slots["slots_covered"].sum()) if len(day_slots) else 0
        expected = int(day_slots["slots_expected"].sum()) if len(day_slots) else 0
        wear_scored = int(len(day_wear))
        wear_met = int((day_wear["gap_coverage"] >= BENCHMARKS["wear"]).sum()) if wear_scored else 0
        delivered_n = int(len(day_delivered))
        responded_n = int(day_delivered["id"].isin(responded).sum()) if delivered_n else 0

        out.append({
            "date": local,
            "participants": participants,
            "slots_covered": covered,
            "slots_expected": expected,
            "slot_coverage": suppress_rate(covered, expected, participants),
            "delivered_n": delivered_n,
            "responded_n": responded_n,
            "prompt_response": suppress_rate(
                responded_n, delivered_n, int(day_delivered["user_id"].nunique()) if delivered_n else 0),
            "wear_days_scored": wear_scored,
            "wear_days_met": wear_met,
            "wear_pass_rate": suppress_rate(
                wear_met, wear_scored, int(day_wear["user_id"].nunique()) if wear_scored else 0),
        })
    return pd.DataFrame(out)


def enrollment_funnel(phase="all", as_of=None):
    as_of = as_of or today_local()
    users = cohort_users(phase)
    day1 = day1_dates()
    withdrawn = withdrawal_dates()
    started = [uid for uid in users if day1.get(uid) is not None and day1[uid] <= as_of]
    active = [
        uid for uid in started
        if (as_of - day1[uid]).days < STUDY_DAYS
        and (withdrawn.get(uid) is None or withdrawn[uid] > as_of)
    ]
    completed = [uid for uid in started if (as_of - day1[uid]).days >= STUDY_DAYS - 1]
    return pd.DataFrame([
        {"stage": "consented", "n": None, "measurable": False,
         "detail": "nothing in the schema records consent"},
        {"stage": "started day 1", "n": len(started), "measurable": True, "detail": None},
        {"stage": "active today", "n": len(active), "measurable": True, "detail": None},
        {"stage": "completed day 34", "n": len(completed), "measurable": True, "detail": None},
        {"stage": "withdrew", "n": len([u for u in started if u in withdrawn]), "measurable": True,
         "detail": "first_seen_not_enrolled_at, resolution = polling interval"},
    ])


def cooldown_violations(rows):
    violations = 0
    pairs = []
    sent = rows[rows["send_prompt"].astype(bool)] if not rows.empty else rows
    if sent.empty:
        return 0, pairs
    anchor = pd.to_datetime(sent["push_sent_at"].fillna(sent["decision_made_at"]), utc=True)
    frame = pd.DataFrame({"user_id": sent["user_id"].to_numpy(), "id": sent["id"].to_numpy(), "at": anchor.to_numpy()})
    for uid, group in frame.sort_values("at").groupby("user_id"):
        times = list(group["at"])
        ids = list(group["id"])
        for i in range(1, len(times)):
            minutes = (times[i] - times[i - 1]).total_seconds() / 60
            if minutes < JITAI_COOLDOWN_MINUTES:
                violations += 1
                pairs.append({"user_id": uid, "earlier_id": ids[i - 1], "later_id": ids[i],
                              "gap_min": round(minutes, 1)})
    return violations, pairs


def outcome_capture(delivered):
    if delivered.empty:
        return 0
    linked = ema_df[
        ema_df["source_jitai_log_id"].isin(set(delivered["id"]))
        & ema_df["ema_type"].isin(OUTCOME_TYPES)
        & ema_df["responded_at"].notna()
    ]
    if linked.empty:
        return 0
    responded_at = pd.to_datetime(linked["responded_at"], utc=True)
    start = pd.to_datetime(linked["outcome_window_start"], utc=True)
    end = pd.to_datetime(linked["outcome_window_end"], utc=True)
    inside = linked[(responded_at >= start) & (responded_at <= end)]
    return int(inside["source_jitai_log_id"].nunique())


def mrt_integrity(phase="all"):
    rows = decisions(phase)
    keys, mrt_days = mrt_day_keys(phase)
    decision_points = int(len(rows))
    eligible = int(rows["randomization_draw"].notna().sum()) if decision_points else 0
    sent = rows[rows["send_prompt"].astype(bool)] if decision_points else rows
    delivered = sent[sent["device_received_at"].notna()] if len(sent) else sent

    ineligible_with_draw = int(
        (rows["randomization_draw"].notna() & rows["trigger_reason"].isin(INELIGIBLE_REASONS)).sum()
    ) if decision_points else 0
    eligible_no_draw = int(
        (rows["randomization_draw"].isna() & ~rows["trigger_reason"].isin(INELIGIBLE_REASONS)).sum()
    ) if decision_points else 0

    drawn = rows[rows["randomization_draw"].notna()] if decision_points else rows
    draws = drawn["randomization_draw"].astype(float).tolist() if len(drawn) else []
    probabilities = drawn["randomization_probability"].astype(float).tolist() if len(drawn) else []
    flags = drawn["send_prompt"].astype(bool).tolist() if len(drawn) else []
    mismatches = sum(1 for flag, draw, p in zip(flags, draws, probabilities) if flag != (draw < p))
    ks_stat, ks_p = ks_uniform(draws)

    cap_days = int(rows.loc[rows["trigger_reason"].eq("daily cap reached")]
                   .drop_duplicates(["user_id", "local_date"]).shape[0]) if decision_points else 0
    violations, violation_pairs = cooldown_violations(rows)
    captured = outcome_capture(delivered)

    scheduled_emas = ema_df[
        ema_df["ema_type"].eq(SCHEDULED_TYPE) & ema_df["status"].eq("completed")
        & ema_df["user_id"].isin(set(mrt_days["user_id"]))
    ]
    scheduled_n = 0
    if not scheduled_emas.empty:
        local = to_local(scheduled_emas["sent_at"]).dt.date
        scheduled_n = int(sum((u, d) in keys for u, d in zip(scheduled_emas["user_id"], local)))

    send_target = randomization_p()
    send = gauge("send_rate", int(len(sent)), eligible, target=send_target)
    send["alarm"] = bool(
        send["wilson_low"] is not None
        and not (send["wilson_low"] <= send_target <= send["wilson_high"])
    )
    eligibility = gauge("eligibility_rate", eligible, decision_points, target=ELIGIBILITY_BAND)
    eligibility["alarm"] = bool(
        eligibility["value"] is not None
        and not (ELIGIBILITY_BAND[0] <= eligibility["value"] <= ELIGIBILITY_BAND[1])
    )
    eligibility["availability_confound"] = (decision_points / scheduled_n) if scheduled_n else None
    eligibility["ema_scheduled_n"] = scheduled_n
    eligibility["reason_ineligible_with_draw"] = ineligible_with_draw
    eligibility["reason_eligible_but_no_draw"] = eligible_no_draw
    eligibility["alarm_reason_disagreement"] = bool(ineligible_with_draw or eligible_no_draw)

    cap = gauge("cap_hit_rate", cap_days, int(len(mrt_days)), target=CAP_HIT_ALARM)
    cap["alarm"] = bool(cap["value"] is not None and cap["value"] > CAP_HIT_ALARM)
    outcome = gauge("outcome_capture", captured, int(len(delivered)), target=OUTCOME_CAPTURE_ALARM)
    outcome["alarm"] = bool(outcome["value"] is not None and outcome["value"] < OUTCOME_CAPTURE_ALARM)

    return {
        "window": "weeks 2-5 (study_day >= run-in)",
        "eligibility_rate": eligibility,
        "send_rate": send,
        "randomization_audit": {
            "gauge": "randomization_audit",
            "draws": len(draws),
            "mismatches": mismatches,
            "ks_statistic": ks_stat,
            "ks_p_value": ks_p,
            "alarm_p": KS_ALARM_P,
            "alarm": bool(mismatches or (ks_p is not None and ks_p < KS_ALARM_P)),
        },
        "cap_hit_rate": cap,
        "cooldown": {
            "gauge": "cooldown",
            "violations": violations,
            "threshold_min": JITAI_COOLDOWN_MINUTES,
            "alarm": bool(violations),
            "pairs": violation_pairs,
        },
        "outcome_capture": outcome,
        "decision_points_n": decision_points,
        "eligible_n": eligible,
        "sent_n": int(len(sent)),
        "delivered_n": int(len(delivered)),
    }


def alert_feed(include_resolved=False):
    rows = alerts_df if include_resolved else alerts_open_df
    if rows.empty:
        return pd.DataFrame(columns=["fired_at", "severity", "rule_id", "user_id", "scope", "link_date", "payload"])
    feed = rows.copy()
    feed["scope"] = np.where(feed["user_id"].isna(), "cohort", "participant")
    feed["link_date"] = [
        (payload or {}).get("date") or (payload or {}).get("local_date")
        for payload in feed["payload"]
    ]
    feed["rank"] = feed["severity"].map(Alert.SEVERITY_RANK)
    feed = feed.sort_values(["rank", "fired_at"], ascending=[True, False])
    return feed[["fired_at", "severity", "rule_id", "user_id", "scope", "link_date", "resolved_at", "payload"]]


def cohort_board(phase="all", as_of=None):
    return {
        "phase": phase,
        "as_of": as_of or today_local(),
        "n_participants": len(cohort_users(phase)),
        "benchmarks": {
            "slot_coverage": slot_coverage(phase),
            "prompt_response": prompt_response(phase),
            "wear": wear_coverage(phase),
            "retention": retention(phase, as_of),
            "hair_sample": hair_sample(phase),
        },
        "series_14d": series_14d(phase, as_of),
        "funnel": enrollment_funnel(phase, as_of),
        "integrity": mrt_integrity(phase),
        "alerts": alert_feed(),
    }


def benchmark_table(board):
    rows = []
    for name, entry in board["benchmarks"].items():
        rows.append({
            "tile": entry.get("label", name),
            "measurable": entry["measurable"],
            "suppressed": entry["suppressed"],
            "value": entry["value"],
            "target": entry["target"],
            "wilson_low": entry["wilson_low"],
            "wilson_high": entry["wilson_high"],
            "numerator": entry["numerator"],
            "denominator": entry["denominator"],
            "participants": entry["participants"],
            "unit": entry["unit"],
        })
    return pd.DataFrame(rows)


def integrity_table(board):
    rows = []
    for key, entry in board["integrity"].items():
        if not isinstance(entry, dict):
            continue
        rows.append({
            "gauge": entry.get("gauge", key),
            "value": entry.get("value"),
            "numerator": entry.get("numerator"),
            "denominator": entry.get("denominator"),
            "target": entry.get("target"),
            "alarm": entry.get("alarm"),
            "contradiction": entry.get("contradiction"),
        })
    return pd.DataFrame(rows)


# STAGE 2 / 3 — ITEM BANK AND COMPLETENESS
from dashboard.data.item_bank import load_item_bank, sub_item_index
from dashboard.data.participant import (
    RISK_COVERAGE_FLOOR,
    RISK_EMA_STALE_CAP,
    RISK_MISSING_SIGNAL_CAP,
    RISK_SYNC_STALE_HOURS,
    RISK_TRAILING_DAYS,
    RISK_WEAR_FLOOR,
    RISK_WEIGHTS,
)
from dashboard.data.windows import scheduled_slot_bounds

MINUTES_PER_DAY = 1440
SIGNAL_SUB_ITEMS = ("B1_valence", "B1_arousal", "B2_stress")
GRID_METRICS = ("slot_coverage", "wear", "delivered_n", "completeness_mean")


def is_answered(row):
    return (
        pd.notna(row.get("value_numeric"))
        or (row.get("value_choice") not in (None, "") and pd.notna(row.get("value_choice")))
        or bool(row.get("value_choices"))
    )


def answered_maps(ema_ids):
    subset = ema_item_responses_df[ema_item_responses_df["ema_id"].isin(set(ema_ids))]
    out = {}
    for record in subset.to_dict("records"):
        entry = out.setdefault(record["ema_id"], {})
        if is_answered(record):
            value = record["value_numeric"]
            if pd.isna(value):
                value = record["value_choice"] if pd.notna(record["value_choice"]) else record["value_choices"]
            entry[record["sub_item_id"]] = {"item_id": record["item_id"], "value": value}
    return out


def satisfies(condition, value):
    if "equals" in condition:
        return value == condition["equals"]
    if "not_equals" in condition:
        return value is not None and value != condition["not_equals"]
    return True


def candidate_sub_items(served, answers, bank):
    index = sub_item_index()
    if served:
        return [(sub_id, index[sub_id]) for sub_id in served if sub_id in index]
    candidates = []
    for item_id in {entry["item_id"] for entry in answers.values()}:
        item = bank.get(item_id)
        if item is None:
            continue
        for sub in item["sub_items"]:
            if "schedule_condition" in sub and sub["sub_item_id"] not in answers:
                continue
            candidates.append((sub["sub_item_id"], sub))
    return candidates


def askable_sub_items(served, answers, bank=None):
    bank = bank or load_item_bank()
    values = {sub_id: entry["value"] for sub_id, entry in answers.items()}
    askable = []
    for sub_id, sub in candidate_sub_items(served, answers, bank):
        depends_on = sub.get("depends_on")
        if depends_on and not satisfies(depends_on, values.get(depends_on["sub_item_id"])):
            continue
        askable.append(sub_id)
    return askable


def ema_completeness(ema_rows):
    bank = load_item_bank()
    maps = answered_maps(ema_rows["id"])
    out = []
    for record in ema_rows.to_dict("records"):
        answers = maps.get(record["id"], {})
        served = record.get("served_sub_item_ids") or None
        askable = askable_sub_items(served, answers, bank)
        answered = [sub_id for sub_id in askable if sub_id in answers]
        out.append({
            "ema_id": record["id"],
            "user_id": record["user_id"],
            "ema_type": record["ema_type"],
            "askable_n": len(askable),
            "answered_n": len(answered),
            "completeness": (len(answered) / len(askable)) if askable else None,
            "missing_b1b2": (
                not all(sub_id in answers for sub_id in SIGNAL_SUB_ITEMS)
                if record["ema_type"] in (SCHEDULED_TYPE,) + tuple(OUTCOME_TYPES) else None
            ),
            "item_bank_version": ITEM_BANK_VERSION,
            "askable": askable,
            "answered": answered,
        })
    return pd.DataFrame(out, columns=[
        "ema_id", "user_id", "ema_type", "askable_n", "answered_n", "completeness",
        "missing_b1b2", "item_bank_version", "askable", "answered",
    ])


# STAGE 2 — PARTICIPANT-DAY BACKBONE
def sync_measurable():
    if not wearable_sync_df.empty:
        return True
    return bool(wearable_devices_df["last_synced_at"].notna().any())


def slot_classification(phase="all"):
    days = participant_days(phase)
    slots = scheduled_slots(set(days["user_id"]))
    covered = {}
    for uid, local_date, slot_index in zip(slots["user_id"], slots["local_date"], slots["slot_index"]):
        covered.setdefault((uid, local_date), set()).add(int(slot_index))

    reminders = checkin_reminders_df[checkin_reminders_df["user_id"].isin(set(days["user_id"]))].copy()
    reminded = {}
    index_reminded = {}
    if not reminders.empty:
        local = to_local(reminders["sent_at"])
        reminders["local_date"] = local.dt.date
        reminders["hour"] = local.dt.hour
        in_window = reminders[
            reminders["hour"].ge(NOTIFICATION_WINDOW_START_HOUR)
            & reminders["hour"].lt(NOTIFICATION_WINDOW_END_HOUR)
        ]
        for uid, local_date, hour in zip(in_window["user_id"], in_window["local_date"], in_window["hour"]):
            slot = int((hour - NOTIFICATION_WINDOW_START_HOUR) // SLOT_HOURS)
            reminded.setdefault((uid, local_date), set()).add(slot)
        for uid, local_date, index in zip(reminders["user_id"], reminders["local_date"], reminders["daily_count_at_send"]):
            if 0 <= index < SCHEDULED_CHECK_IN_DAILY_CAP:
                index_reminded.setdefault((uid, local_date), set()).add(int(index))

    rows = []
    for key in zip(days["user_id"], days["local_date"]):
        hit = covered.get(key, set())
        nudged = reminded.get(key, set()) - hit
        rows.append({
            "slots_covered": len(hit),
            "slots_reminded_uncovered": len(nudged),
            "slots_silent": SCHEDULED_CHECK_IN_DAILY_CAP - len(hit) - len(nudged),
            "slots_reminded_by_index": len(index_reminded.get(key, set()) - hit),
        })
    counts = pd.DataFrame(rows, columns=[
        "slots_covered", "slots_reminded_uncovered", "slots_silent", "slots_reminded_by_index",
    ]).astype("int64")
    return pd.concat([days.reset_index(drop=True), counts], axis=1)


def daily_grid_metrics(phase="all"):
    days = slot_classification(phase)
    wear = wear_days(phase)[["user_id", "local_date", "gap_coverage", "minute_coverage", "gap_minutes"]]
    days = days.merge(wear, on=["user_id", "local_date"], how="left")

    logs = decisions(phase, mrt_only=False)
    if logs.empty:
        days["delivered_n"] = 0
        days["decision_points_n"] = 0
        days["eligible_n"] = 0
        days["sent_n"] = 0
    else:
        counts = logs.assign(
            delivered=logs["device_received_at"].notna().astype(int),
            eligible=logs["randomization_draw"].notna().astype(int),
            sent=logs["send_prompt"].astype(bool).astype(int),
        ).groupby(["user_id", "local_date"]).agg(
            delivered_n=("delivered", "sum"),
            decision_points_n=("delivered", "size"),
            eligible_n=("eligible", "sum"),
            sent_n=("sent", "sum"),
        ).reset_index()
        days = days.merge(counts, on=["user_id", "local_date"], how="left")
        for column in ("delivered_n", "decision_points_n", "eligible_n", "sent_n"):
            days[column] = days[column].fillna(0).astype(int)

    emas = ema_df[
        ema_df["user_id"].isin(set(days["user_id"])) & ema_df["status"].eq("completed")
    ].copy()
    if emas.empty:
        days["completeness_mean"] = np.nan
        days["ema_missing_b1b2_n"] = 0
    else:
        emas["local_date"] = to_local(emas["sent_at"]).dt.date
        scored = ema_completeness(emas[emas["ema_type"].isin((SCHEDULED_TYPE,) + tuple(OUTCOME_TYPES))])
        scored = scored.merge(
            emas[["id", "local_date"]].rename(columns={"id": "ema_id"}), on="ema_id", how="left")
        rollup = scored.groupby(["user_id", "local_date"]).agg(
            completeness_mean=("completeness", "mean"),
            ema_missing_b1b2_n=("missing_b1b2", "sum"),
        ).reset_index()
        days = days.merge(rollup, on=["user_id", "local_date"], how="left")
        days["ema_missing_b1b2_n"] = days["ema_missing_b1b2_n"].fillna(0).astype(int)

    days["slot_coverage"] = days["slots_covered"] / days["slots_expected"] if "slots_expected" in days else np.nan
    days["slots_expected"] = SCHEDULED_CHECK_IN_DAILY_CAP
    days["slot_coverage"] = days["slots_covered"] / SCHEDULED_CHECK_IN_DAILY_CAP
    days["wear"] = days["gap_coverage"]
    return days


# STAGE 2A — HEATMAP






# STAGE 2B — SLOT SPLIT
def slot_split(phase="all", days=RISK_TRAILING_DAYS, as_of=None):
    as_of = as_of or today_local()
    frame = daily_grid_metrics(phase)
    cutoff = as_of - timedelta(days=days)
    recent = frame[frame["local_date"].gt(cutoff)]
    if recent.empty:
        return pd.DataFrame(columns=["user_id", "covered", "reminded", "silent", "days"])
    split = recent.groupby("user_id").agg(
        covered=("slots_covered", "sum"),
        reminded=("slots_reminded_uncovered", "sum"),
        silent=("slots_silent", "sum"),
        days=("local_date", "nunique"),
    ).reset_index()
    total = split[["covered", "reminded", "silent"]].sum(axis=1)
    for column in ("covered", "reminded", "silent"):
        split[f"{column}_pct"] = split[column] / total
    return split


# STAGE 2C — RISK SCORE
def phase_of(study_day_now, withdrawn):
    if withdrawn is not None:
        return "withdrawn"
    if study_day_now is None or study_day_now < 0:
        return "pre_enrollment"
    if study_day_now < RUN_IN_DAYS:
        return "run_in"
    if study_day_now < STUDY_DAYS:
        return "mrt"
    return "complete"


def last_sync_ages(as_of=None):
    as_of = as_of or pd.Timestamp.utcnow()
    ages = {}
    for uid, moment in zip(wearable_devices_df["user_id"], wearable_devices_df["last_synced_at"]):
        if pd.notna(moment):
            ages[uid] = (as_of - pd.Timestamp(moment)).total_seconds() / 3600
    return ages


def risk_scores(phase="all", as_of=None):
    as_of = as_of or today_local()
    frame = daily_grid_metrics(phase)
    day1 = day1_dates()
    withdrawn = withdrawal_dates()
    ages = last_sync_ages()
    measurable = sync_measurable()
    slots = scheduled_slots(set(cohort_users(phase)))
    last_ema = slots.groupby("user_id")["local_date"].max().to_dict() if not slots.empty else {}

    out = []
    for uid in cohort_users(phase):
        anchor = day1.get(uid)
        study_day_now = (as_of - anchor).days if anchor else None
        current = phase_of(study_day_now, withdrawn.get(uid))
        if current not in ("run_in", "mrt"):
            out.append({"user_id": uid, "phase": current, "study_day_now": study_day_now,
                        "risk_score": None, "risk_components": None})
            continue

        own = frame[frame["user_id"].eq(uid)]
        trailing = own[own["local_date"].gt(as_of - timedelta(days=RISK_TRAILING_DAYS))]
        stale_days = (as_of - last_ema[uid]).days if uid in last_ema else RISK_EMA_STALE_CAP
        age = ages.get(uid)

        components = {
            "ema_stale": RISK_WEIGHTS["ema_stale"] * min(stale_days, RISK_EMA_STALE_CAP),
            "low_coverage": RISK_WEIGHTS["low_coverage"] * int(
                (trailing["slot_coverage"] < RISK_COVERAGE_FLOOR).sum()),
            "sync_stale": RISK_WEIGHTS["sync_stale"] * int(
                bool(measurable and age is not None and age > RISK_SYNC_STALE_HOURS)),
            "low_wear": RISK_WEIGHTS["low_wear"] * int(
                (trailing["gap_coverage"] < RISK_WEAR_FLOOR).sum()),
            "missing_signal": RISK_WEIGHTS["missing_signal"] * min(
                int(trailing["ema_missing_b1b2_n"].sum()), RISK_MISSING_SIGNAL_CAP),
            "open_critical": RISK_WEIGHTS["open_critical"] * int(bool(
                not alerts_open_df.empty
                and ((alerts_open_df["user_id"] == uid)
                     & alerts_open_df["severity"].isin(Alert.ACTIONABLE_SEVERITIES)).any()
            )),
        }
        out.append({
            "user_id": uid,
            "phase": current,
            "study_day_now": study_day_now,
            "risk_score": int(sum(components.values())),
            "risk_components": components,
            "sync_measurable": measurable,
        })
    scores = pd.DataFrame(out, columns=[
        "user_id", "phase", "study_day_now", "risk_score", "risk_components", "sync_measurable",
    ])
    return scores.sort_values(
        "risk_score", ascending=False, na_position="last").reset_index(drop=True)


# STAGE 2D — RIGHT RAIL
def participant_rail(user_id, phase="all", as_of=None):
    as_of = as_of or today_local()
    frame = daily_grid_metrics(phase)
    own = frame[frame["user_id"].eq(user_id)]
    scores = risk_scores(phase, as_of)
    score = scores[scores["user_id"].eq(user_id)]
    anchor = day1_dates().get(user_id)
    study_day_now = (as_of - anchor).days if anchor else None

    own_emas = ema_df[ema_df["user_id"].eq(user_id) & ema_df["status"].eq("completed")]
    last_ema = to_local(own_emas["sent_at"]).max() if not own_emas.empty else None
    age = last_sync_ages().get(user_id)
    covered = int(own["slots_covered"].sum())
    expected = int(own["slots_expected"].sum())
    wear_scored = own[own["gap_coverage"].notna()]

    return {
        "user_id": user_id,
        "phase": score["phase"].iloc[0] if len(score) else None,
        "study_day": study_day_now,
        "days_remaining": (STUDY_DAYS - 1 - study_day_now) if study_day_now is not None else None,
        "risk_score": score["risk_score"].iloc[0] if len(score) else None,
        "risk_components": score["risk_components"].iloc[0] if len(score) else None,
        "last_sync_age_h": age,
        "sync_measurable": sync_measurable(),
        "last_ema_at": last_ema,
        "slot_coverage_num": covered,
        "slot_coverage_den": expected,
        "slot_coverage_rate": suppress_rate(covered, expected, 1),
        "wear_days_scored": int(len(wear_scored)),
        "wear_days_met": int((wear_scored["gap_coverage"] >= BENCHMARKS["wear"]).sum()),
        "prompts_delivered": int(own["delivered_n"].sum()),
        "open_alerts": alerts_open_df[alerts_open_df["user_id"].eq(user_id)][
            ["severity", "rule_id", "fired_at", "payload"]].to_dict("records"),
    }


# STAGE 3 — LANES
def day_window(local_date):
    start = pd.Timestamp(local_date).tz_localize(PARTICIPANT_TZ)
    return start, start + pd.Timedelta(days=1)


def local_minutes(moment, local_date):
    if moment is None or pd.isna(moment):
        return None
    start, _ = day_window(local_date)
    return (pd.Timestamp(moment).tz_convert(PARTICIPANT_TZ) - start).total_seconds() / 60


def wear_lane(user_id, local_date):
    start, end = day_window(local_date)
    samples = heart_rate_df[
        heart_rate_df["user_id"].eq(user_id) & heart_rate_df["bpm"].gt(0)
    ] if not heart_rate_df.empty else heart_rate_df
    bins = np.zeros(MINUTES_PER_DAY, dtype=bool)
    if not samples.empty:
        stamps = pd.to_datetime(samples["timestamp"], utc=True).dt.tz_convert(PARTICIPANT_TZ)
        inside = samples[(stamps >= start) & (stamps < end)]
        if not inside.empty:
            minutes = ((pd.to_datetime(inside["timestamp"], utc=True).dt.tz_convert(PARTICIPANT_TZ) - start)
                       .dt.total_seconds() // 60).astype(int)
            bins[minutes.to_numpy()] = True

    gaps, run_start = [], None
    for minute in range(MINUTES_PER_DAY + 1) if bins.any() else []:
        filled = bins[minute] if minute < MINUTES_PER_DAY else True
        if filled:
            if run_start is not None and minute - run_start > WEAR_GAP_MIN:
                gaps.append({"start": run_start, "end": minute, "minutes": minute - run_start})
            run_start = None
        elif run_start is None:
            run_start = minute
    return {
        "user_id": user_id,
        "local_date": local_date,
        "has_data": bool(bins.any()),
        "covered_minutes": int(bins.sum()),
        "waking_window": [WAKING_WINDOW_START_HOUR * 60, WAKING_WINDOW_END_HOUR * 60],
        "waking_covered_minutes": int(bins[WAKING_WINDOW_START_HOUR * 60:WAKING_WINDOW_END_HOUR * 60].sum()),
        "gaps_gt_2h": gaps,
        "bins": bins,
    }


def sync_lane(user_id, local_date):
    start, end = day_window(local_date)
    own = wearable_sync_df[wearable_sync_df["user_id"].eq(user_id)] if not wearable_sync_df.empty else wearable_sync_df
    advances, carried_in = [], None
    if not own.empty:
        observed = pd.to_datetime(own["observed_at"], utc=True).dt.tz_convert(PARTICIPANT_TZ)
        before = own[observed < start]
        if not before.empty:
            carried_in = before.iloc[-1].to_dict()
        inside = own[(observed >= start) & (observed < end)]
        advances = [{
            "minute": local_minutes(row["observed_at"], local_date),
            "source": row["source"],
            "last_synced_at": row["last_synced_at"],
            "samples_written": row["samples_written"],
        } for row in inside.to_dict("records")]
    device = wearable_devices_df[wearable_devices_df["user_id"].eq(user_id)]
    return {
        "user_id": user_id,
        "local_date": local_date,
        "observed": bool(advances or carried_in),
        "measurable": sync_measurable(),
        "carried_in": carried_in,
        "advances": advances,
        "device_last_synced_at": device["last_synced_at"].iloc[0] if len(device) else None,
    }


def checkin_lane(user_id, local_date):
    start, end = day_window(local_date)
    own = ema_df[ema_df["user_id"].eq(user_id)]
    stamps = to_local(own["sent_at"])
    today = own[(stamps >= start) & (stamps < end)]
    scored = ema_completeness(today).set_index("ema_id") if not today.empty else None
    marks = []
    for row in today.to_dict("records"):
        marks.append({
            "ema_id": row["id"],
            "minute": local_minutes(row["sent_at"], local_date),
            "ema_type": row["ema_type"],
            "status": row["status"],
            "completeness": (scored.at[row["id"], "completeness"] if scored is not None else None),
            "missing_b1b2": bool(scored.at[row["id"], "missing_b1b2"]) if scored is not None else None,
        })

    own_reminders = checkin_reminders_df[checkin_reminders_df["user_id"].eq(user_id)]
    reminder_stamps = to_local(own_reminders["sent_at"])
    today_reminders = own_reminders[(reminder_stamps >= start) & (reminder_stamps < end)]
    ticks = [{
        "minute": local_minutes(row["sent_at"], local_date),
        "daily_count_at_send": row["daily_count_at_send"],
    } for row in today_reminders.to_dict("records")]

    slots = [
        {"index": index, "start": local_minutes(bounds[0], local_date), "end": local_minutes(bounds[1], local_date)}
        for index, bounds in enumerate(scheduled_slot_bounds(local_date))
    ]
    return {"user_id": user_id, "local_date": local_date, "slots": slots,
            "emas": marks, "reminders": ticks}


# STAGE 3 — DECISION, DELIVERY AND MSSD LANES
def classify_decision(row):
    reason = row["trigger_reason"]
    if reason in INELIGIBLE_REASONS:
        return reason
    if row["send_prompt"]:
        return "eligible-sent"
    if pd.notna(row["randomization_draw"]):
        return "eligible-not-sent"
    return "unclassified"


def participant_decisions(user_id, local_date=None):
    own = jitai_log_df[jitai_log_df["user_id"].eq(user_id)].copy()
    if own.empty:
        return own.assign(local_date=[], minute=[], outcome=[])
    anchor = own["decision_made_at"].fillna(own["triggered_at"])
    own["local_date"] = to_local(anchor).dt.date
    if local_date is not None:
        own = own[own["local_date"].eq(local_date)]
    own["minute"] = [local_minutes(m, d) for m, d in zip(anchor.loc[own.index], own["local_date"])]
    own["outcome"] = [classify_decision(row) for row in own.to_dict("records")]
    return own


def decision_lane(user_id, local_date):
    rows = participant_decisions(user_id, local_date)
    return [{
        "jitai_log_id": row["id"],
        "decision_point_id": row["decision_point_id"],
        "minute": row["minute"],
        "outcome": row["outcome"],
        "observed_mssd": row["observed_mssd"],
        "randomization_draw": row["randomization_draw"],
        "trigger_reason": row["trigger_reason"],
    } for row in rows.to_dict("records")]


def delivery_lane(user_id, local_date):
    rows = participant_decisions(user_id, local_date)
    sent = rows[rows["send_prompt"].astype(bool)] if not rows.empty else rows
    if sent.empty:
        return []
    events = engagement_log_df[engagement_log_df["jitai_log_id"].isin(set(sent["id"]))]
    by_log = {}
    for log_id, event_type in zip(events["jitai_log_id"], events["event_type"]):
        by_log.setdefault(log_id, set()).add(event_type)
    linked = ema_df[ema_df["source_jitai_log_id"].isin(set(sent["id"])) & ema_df["ema_type"].isin(OUTCOME_TYPES)]
    ema_by_log = {row["source_jitai_log_id"]: row for row in linked.to_dict("records")}

    out = []
    for row in sent.to_dict("records"):
        linked_ema = ema_by_log.get(row["id"])
        out.append({
            "jitai_log_id": row["id"],
            "push_sent_minute": local_minutes(row["push_sent_at"], local_date),
            "device_received_minute": local_minutes(row["device_received_at"], local_date),
            "receipt_reported_minute": local_minutes(row["receipt_reported_at"], local_date),
            "delivery_status": row["delivery_status"],
            "delivery_error": row["delivery_error"],
            "receipt_platform": row["receipt_platform"],
            "receipt_app_state": row["receipt_app_state"],
            "message_arm": row["message_arm"],
            "engagement": sorted(by_log.get(row["id"], set())),
            "outcome_window": [
                local_minutes(linked_ema["outcome_window_start"], local_date) if linked_ema else None,
                local_minutes(linked_ema["outcome_window_end"], local_date) if linked_ema else None,
            ],
            "linked_ema_id": linked_ema["id"] if linked_ema else None,
            "linked_ema_responded_minute": local_minutes(linked_ema["responded_at"], local_date) if linked_ema else None,
        })
    return out


def mssd_lane(user_id, local_date):
    rows = participant_decisions(user_id, local_date)
    out = []
    for row in rows.to_dict("records"):
        threshold = row["threshold_at_decision"]
        eligible = pd.notna(row["randomization_draw"])
        observed = row["observed_mssd"]
        out.append({
            "jitai_log_id": row["id"],
            "minute": row["minute"],
            "observed_mssd": observed,
            "threshold": threshold,
            "threshold_source": row["threshold_source"],
            "eligible": bool(eligible),
            "unexplained": bool(
                row["threshold_source"] == "engine"
                and pd.notna(observed) and pd.notna(threshold)
                and observed > threshold and not eligible
            ),
        })
    return out


def timeline(user_id, days=7, end=None):
    end = end or today_local()
    return [
        {
            "local_date": end - timedelta(days=offset),
            "wear": wear_lane(user_id, end - timedelta(days=offset)),
            "sync": sync_lane(user_id, end - timedelta(days=offset)),
            "checkins": checkin_lane(user_id, end - timedelta(days=offset)),
            "decisions": decision_lane(user_id, end - timedelta(days=offset)),
            "delivery": delivery_lane(user_id, end - timedelta(days=offset)),
            "mssd": mssd_lane(user_id, end - timedelta(days=offset)),
        }
        for offset in reversed(range(days))
    ]


# STAGE 3B — DELIVERY FUNNEL
def delivery_funnel(user_id):
    own = jitai_log_df[jitai_log_df["user_id"].eq(user_id)]
    sent = own[own["send_prompt"].astype(bool)]
    pushed = sent[sent["push_sent_at"].notna()]
    received = pushed[pushed["device_received_at"].notna()]
    reported = received[received["receipt_reported_at"].notna()]
    engaged_ids = set(engagement_log_df.loc[
        engagement_log_df["jitai_log_id"].isin(set(reported["id"])), "jitai_log_id"])
    stages = [
        ("send_prompt", len(sent)),
        ("push_sent_at", len(pushed)),
        ("device_received_at", len(received)),
        ("receipt_reported_at", len(reported)),
        ("engagement_event", len(engaged_ids)),
    ]
    rows = []
    for index, (name, count) in enumerate(stages):
        previous = stages[index - 1][1] if index else None
        rows.append({
            "stage": name,
            "n": count,
            "drop_from_previous": (previous - count) if previous is not None else None,
            "drop_pct": ((previous - count) / previous) if previous else None,
        })
    splits = {}
    for column in ("receipt_platform", "receipt_app_state"):
        splits[column] = (
            received.groupby(received[column].fillna("unknown")).size().to_dict()
            if not received.empty else {}
        )
    errors = (
        sent.loc[sent["delivery_error"].notna() & sent["delivery_error"].ne(""), "delivery_error"]
        .value_counts().to_dict()
    )
    return {
        "user_id": user_id,
        "stages": pd.DataFrame(rows),
        "splits": splits,
        "delivery_errors": errors,
    }


# STAGE 3C — ITEM COMPLETENESS MATRIX
def completeness_matrix(user_id, local_date):
    start, end = day_window(local_date)
    own = ema_df[ema_df["user_id"].eq(user_id)]
    stamps = to_local(own["sent_at"])
    today = own[(stamps >= start) & (stamps < end)]
    if today.empty:
        return pd.DataFrame()
    scored = ema_completeness(today)
    maps = answered_maps(today["id"])
    columns = sorted({sub_id for askable in scored["askable"] for sub_id in askable})
    rows = []
    for record in scored.to_dict("records"):
        answers = maps.get(record["ema_id"], {})
        askable = set(record["askable"])
        cells = {
            sub_id: ("answered" if sub_id in answers else "not answered")
            if sub_id in askable else "not applicable"
            for sub_id in columns
        }
        rows.append({
            "ema_id": record["ema_id"],
            "ema_type": record["ema_type"],
            "completeness": record["completeness"],
            "missing_b1b2": record["missing_b1b2"],
            "item_bank_version": record["item_bank_version"],
            **cells,
        })
    return pd.DataFrame(rows)


# 4 — ALERT RULES
ALERT_SPEC_SEVERITY = {
    "runin_prompt_sent": "high", "randomization_mismatch": "high",
    "cooldown_violation": "high", "cap_exceeded": "high", "scheduler_silent": "high",
    "sync_stale": "medium", "no_checkin_72h": "medium", "wear_low": "medium",
    "mssd_signal_missing": "medium", "threshold_never_fires": "medium",
    "clock_skew": "low", "enrollment_flip": "info",
}
NO_CHECKIN_HOURS = 72
SCHEDULER_SILENT_FLOOR = 3
WEAR_LOW_CONSECUTIVE_DAYS = 3
MSSD_MISSING_FRACTION = 0.20
THRESHOLD_NEVER_FIRES_MIN_POINTS = 20
CLOCK_SKEW_MINUTES = 5


def finding(rule_id, user_id=None, local_date=None, stage=None, **payload):
    return {
        "rule_id": rule_id,
        "severity": ALERT_SPEC_SEVERITY[rule_id],
        "stage": stage,
        "user_id": user_id,
        "local_date": local_date,
        "payload": payload,
    }


def evaluate_rules(phase="all", as_of=None):
    as_of = as_of or today_local()
    frame = daily_grid_metrics(phase)
    logs = decisions(phase, mrt_only=False)
    out = []

    if not logs.empty:
        run_in_keys = {(u, d) for u, d, r in zip(frame["user_id"], frame["local_date"], frame["is_run_in"]) if r}
        for row in logs[logs["send_prompt"].astype(bool)].to_dict("records"):
            if (row["user_id"], row["local_date"]) in run_in_keys:
                out.append(finding("runin_prompt_sent", row["user_id"], row["local_date"], 1,
                                   jitai_log_id=row["id"]))
        drawn = logs[logs["randomization_draw"].notna()]
        for row in drawn.to_dict("records"):
            if bool(row["send_prompt"]) != (row["randomization_draw"] < row["randomization_probability"]):
                out.append(finding("randomization_mismatch", row["user_id"], row["local_date"], 1,
                                   draw=row["randomization_draw"], p=row["randomization_probability"],
                                   send_prompt=bool(row["send_prompt"])))
        _, pairs = cooldown_violations(logs)
        for pair in pairs:
            out.append(finding("cooldown_violation", pair["user_id"], None, 1,
                               gap_min=pair["gap_min"], threshold_min=JITAI_COOLDOWN_MINUTES,
                               earlier_id=pair["earlier_id"], later_id=pair["later_id"]))

    for row in frame.to_dict("records"):
        if row["delivered_n"] > DAILY_PROMPT_CAP:
            out.append(finding("cap_exceeded", row["user_id"], row["local_date"], 1,
                               delivered_n=row["delivered_n"], cap=DAILY_PROMPT_CAP))
        if row["slots_silent"] >= SCHEDULER_SILENT_FLOOR:
            out.append(finding("scheduler_silent", row["user_id"], row["local_date"], 2,
                               slots_silent=int(row["slots_silent"])))

    measurable = sync_measurable()
    if not measurable:
        out.append(finding("sync_stale", None, None, 2, measurable=False,
                           detail="nothing writes the sync clock; cohort-scoped, not per participant"))
    else:
        for uid, age in last_sync_ages().items():
            if age > RISK_SYNC_STALE_HOURS:
                out.append(finding("sync_stale", uid, None, 2, last_sync_age_h=round(age, 1)))

    slots = scheduled_slots(set(frame["user_id"]))
    last_ema = slots.groupby("user_id")["local_date"].max().to_dict() if not slots.empty else {}
    for uid in sorted(set(frame["user_id"])):
        latest = last_ema.get(uid)
        hours = ((as_of - latest).days * 24) if latest else None
        if hours is None or hours > NO_CHECKIN_HOURS:
            out.append(finding("no_checkin_72h", uid, None, 2,
                               last_scheduled_ema=latest, hours=hours))

        own = frame[frame["user_id"].eq(uid)].sort_values("local_date")
        streak = 0
        for coverage in own["gap_coverage"]:
            streak = streak + 1 if pd.notna(coverage) and coverage < RISK_WEAR_FLOOR else 0
            if streak >= WEAR_LOW_CONSECUTIVE_DAYS:
                out.append(finding("wear_low", uid, None, 2, consecutive_days=streak))
                break

        week = own[own["local_date"].gt(as_of - timedelta(days=RISK_TRAILING_DAYS))]
        scheduled_n = int(week["slots_covered"].sum())
        missing = int(week["ema_missing_b1b2_n"].sum())
        if scheduled_n and missing / scheduled_n > MSSD_MISSING_FRACTION:
            out.append(finding("mssd_signal_missing", uid, None, 3,
                               missing=missing, emas=scheduled_n))

        if not logs.empty:
            recent = logs[logs["user_id"].eq(uid) & logs["local_date"].gt(as_of - timedelta(days=RISK_TRAILING_DAYS))]
            if len(recent) >= THRESHOLD_NEVER_FIRES_MIN_POINTS and recent["randomization_draw"].notna().sum() == 0:
                out.append(finding("threshold_never_fires", uid, None, 3,
                                   decision_points=int(len(recent)),
                                   caveat="also fires under an unrecorded distress suspension"))

    telemetry = phone_telemetry_df[phone_telemetry_df["user_id"].isin(set(frame["user_id"]))]
    if not telemetry.empty:
        skew = (pd.to_datetime(telemetry["recorded_at"], utc=True)
                - pd.to_datetime(telemetry["occurred_at"], utc=True)).dt.total_seconds() / 60
        for uid, group in skew.groupby(telemetry["user_id"]):
            p95 = float(np.percentile(group.dropna(), 95)) if group.notna().any() else None
            if p95 is not None and (p95 > CLOCK_SKEW_MINUTES or p95 < 0):
                out.append(finding("clock_skew", uid, None, 3, p95_minutes=round(p95, 2)))

    flipped = users_df[users_df["enrolled_at"].notna() & ~users_df["is_enrolled"].astype(bool)]
    for uid in flipped["user_id"]:
        if uid in set(frame["user_id"]) or uid in cohort_users(phase):
            out.append(finding("enrollment_flip", uid, None, 1))

    order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    result = pd.DataFrame(out, columns=["rule_id", "severity", "stage", "user_id", "local_date", "payload"])
    if result.empty:
        return result
    return result.assign(rank=result["severity"].map(order)).sort_values(
        ["rank", "rule_id"]).drop(columns="rank").reset_index(drop=True)


# STAGE 1 PLOTS — PALETTE
import json

import plotly.graph_objects as go
from plotly.subplots import make_subplots

VIZ_MODE = "light"

PALETTE = {
    "light": {
        "surface": "#fcfcfb", "text": "#0b0b0b", "text_secondary": "#52514e",
        "muted": "#898781", "grid": "#e1e0d9", "axis": "#c3c2b7",
        "series_1": "#2a78d6", "series_2": "#eb6834",
        "ordinal": ["#86b6ef", "#5598e7", "#2a78d6", "#184f95"],
        "band": "#f0efec",
    },
    "dark": {
        "surface": "#1a1a19", "text": "#ffffff", "text_secondary": "#c3c2b7",
        "muted": "#898781", "grid": "#2c2c2a", "axis": "#383835",
        "series_1": "#3987e5", "series_2": "#d95926",
        "ordinal": ["#cde2fb", "#86b6ef", "#3987e5", "#184f95"],
        "band": "#383835",
    },
}
STATUS = {"good": "#0ca30c", "warning": "#fab219", "serious": "#ec835a", "critical": "#d03b3b"}
STATUS_ICON = {"good": "\u25cf", "warning": "\u25b2", "serious": "\u25b2", "critical": "\u25a0"}
KS_MIN_DRAWS = 20
FONT = 'system-ui, -apple-system, "Segoe UI", sans-serif'


def ink(role):
    return PALETTE[VIZ_MODE][role]


def data_through():
    """Latest local date any source table carries, for the provenance stamp."""
    latest = None
    for column, frame_ in (("sent_at", ema_df), ("triggered_at", jitai_log_df),
                           ("sent_at", checkin_reminders_df)):
        if frame_.empty or column not in frame_.columns:
            continue
        stamp = pd.to_datetime(frame_[column], utc=True).max()
        if pd.notna(stamp):
            day = stamp.tz_convert(PARTICIPANT_TZ).date()
            latest = day if latest is None else max(latest, day)
    return latest


def provenance(context=None):
    """as-of / data-through / item bank / phase / n, for the figure footer.

    Hover text does not survive a PNG and these figures get screenshotted into
    lab meetings, so the stamp is baked into every figure rather than offered
    on demand.
    """
    context = context or {}
    parts = [f"as of {datetime.now().strftime('%Y-%m-%d %H:%M')}"]
    through = data_through()
    if through is not None:
        parts.append(f"data through {through}")
    parts.append(f"item_bank {ITEM_BANK_VERSION}")
    if context.get("phase"):
        parts.append(f"phase {context['phase']}")
    if context.get("n") is not None:
        parts.append(f"n={context['n']}")
    parts.append("SYNTHETIC" if SYNTHETIC_DATA else "live data")
    return " \u00b7 ".join(parts)


def base_layout(fig, height, title=None, margin=None, context=None, stamp=True):
    bottom = (margin or {}).get("b", 28)
    fig.update_layout(
        template="none",
        paper_bgcolor=ink("surface"),
        plot_bgcolor=ink("surface"),
        font=dict(family=FONT, size=12, color=ink("text_secondary")),
        title=dict(text=title, font=dict(size=14, color=ink("text")), x=0, xanchor="left") if title else None,
        height=height + (18 if stamp else 0),
        margin=(dict(margin, b=bottom + 18) if margin else
                dict(l=140, r=40, t=44 if title else 12, b=bottom + 18)),
        showlegend=False,
        hoverlabel=dict(bgcolor=ink("surface"), font=dict(family=FONT, color=ink("text"))),
    )
    fig.update_xaxes(showgrid=False, zeroline=False, linecolor=ink("axis"),
                     tickfont=dict(color=ink("muted")))
    fig.update_yaxes(showgrid=False, zeroline=False, linecolor=ink("axis"),
                     tickfont=dict(color=ink("muted")))
    if stamp:
        fig.add_annotation(
            x=1.0, y=0.0, xref="paper", yref="paper", xanchor="right", yanchor="top",
            yshift=-bottom - 2, showarrow=False, text=provenance(context),
            font=dict(family=FONT, size=9, color=ink("muted")))
        if SYNTHETIC_DATA:
            # A watermark on every panel, not a note on one: these get cropped
            # and pasted individually into slides.
            fig.add_annotation(
                x=0.5, y=0.5, xref="paper", yref="paper", showarrow=False,
                text="SYNTHETIC", textangle=-25,
                font=dict(family=FONT, size=max(28, min(64, height // 6)),
                          color=ink("muted")),
                opacity=0.10)
    return fig


def pct(value, digits=0):
    return "-" if value is None or pd.isna(value) else f"{value * 100:.{digits}f}%"


# STAGE 1 PLOTS — PLATFORM SERIES FOR THE PROMPT-RESPONSE SPARKLINE
def platform_series_14d(phase="all", as_of=None):
    as_of = as_of or today_local()
    window_start = as_of - timedelta(days=SERIES_DAYS - 1)
    rows_jitai = decisions(phase, mrt_only=False)
    delivered = (
        rows_jitai[rows_jitai["send_prompt"].astype(bool) & rows_jitai["device_received_at"].notna()]
        if not rows_jitai.empty else rows_jitai
    )
    responded = responded_logs(delivered) if not delivered.empty else set()
    out = []
    for offset in range(SERIES_DAYS):
        local = window_start + timedelta(days=offset)
        day = delivered[delivered["local_date"].eq(local)] if not delivered.empty else delivered
        row = {"date": local}
        for platform in ("ios", "android"):
            group = day[day["receipt_platform"].fillna("unknown").eq(platform)] if len(day) else day
            n = int(len(group))
            k = int(group["id"].isin(responded).sum()) if n else 0
            row[f"{platform}_num"] = k
            row[f"{platform}_den"] = n
            row[platform] = suppress_rate(k, n, int(group["user_id"].nunique()) if n else 0)
        out.append(row)
    return pd.DataFrame(out)


# STAGE 2/3 PLOTS — PALETTE EXTENSION
# Every value below was run through scripts/validate_palette.js. Three obvious
# choices failed and the design changed because of it; see the notes on each.
PALETTE["light"].update({
    "page": "#f9f9f7",
    # Ordinal floor (step 250), not the full 100-700 sequential range: the pale
    # end of that range sits at 1.29:1 against the surface, which would make a
    # zero cell indistinguishable from a structurally blank one.
    "heatmap": [[0.0, "#86b6ef"], [0.25, "#3987e5"], [0.5, "#256abf"],
                [0.75, "#184f95"], [1.0, "#0d366b"]],
    "series_3": "#1baf7a", "series_4": "#eda100",
    "series_5": "#e87ba4", "series_6": "#008300",
    # Four discrete steps, not seven. A seven-step single-hue ramp cannot clear
    # the adjacent-lightness floor inside the usable band (three pairs measure
    # 0.047-0.049 against a 0.06 gate), so the exact slot count is carried by an
    # in-cell numeral instead of by hue.
    "heatmap_steps": ["#86b6ef", "#5598e7", "#2a78d6", "#184f95"],
    # Dose is diverging, not sequential: 0 delivered is under-dosed and 4 is at
    # the cap, an alarm condition. Blue and red are the documented opposite
    # poles; the midpoint is neutral so "expected" reads as nothing.
    "dose": [[0.0, "#2a78d6"], [0.25, "#9ec5f4"], [0.5, "#f0efec"],
             [0.75, "#e08a8a"], [1.0, "#d03b3b"]],
})
PALETTE["dark"].update({
    "page": "#0d0d0d",
    "heatmap": [[0.0, "#b7d3f6"], [0.25, "#6da7ec"], [0.5, "#3987e5"],
                [0.75, "#256abf"], [1.0, "#184f95"]],
    "series_3": "#199e70", "series_4": "#c98500",
    "series_5": "#d55181", "series_6": "#008300",
    "heatmap_steps": ["#cde2fb", "#86b6ef", "#3987e5", "#184f95"],
    "dose": [[0.0, "#3987e5"], [0.25, "#256abf"], [0.5, "#383835"],
             [0.75, "#a33030"], [1.0, "#d03b3b"]],
})

def series_hues(n):
    return [ink(f"series_{index}") for index in range(1, n + 1)]

# Three identities in a part-to-whole bar, not three states. The status trio was
# measured first and fails: good vs critical is dE 4.1 under deuteranopia.
SLOT_CATEGORIES = (
    ("covered", "Covered - check-in submitted", "series_1"),
    ("reminded", "Reminded, not covered - participant skipped", "series_3"),
    ("silent", "Silent - scheduler never fired (not non-compliance)", "series_2"),
)

# One y-row per outcome. As bare scatter marks these five fail the all-pairs
# normal-vision floor (dE 12.9), so position carries identity and hue follows.
DECISION_ROWS = (
    "below within-person threshold",
    "cooldown active",
    "daily cap reached",
    "eligible-not-sent",
    "eligible-sent",
)
DECISION_LABEL = {
    "below within-person threshold": "below threshold",
    "cooldown active": "cooldown active",
    "daily cap reached": "cap reached",
    "eligible-not-sent": "eligible, not sent",
    "eligible-sent": "eligible, sent",
}
COMPLETENESS_STATES = ("answered", "not answered", "not applicable")
METRIC_LABEL = {
    "slot_coverage": "Slot coverage (0-1)",
    "wear": "Wear coverage (0-1)",
    "delivered_n": "Prompts delivered (count)",
    "completeness_mean": "Item completeness (0-1)",
}
CLOCK_TICKS = list(range(0, MINUTES_PER_DAY + 1, 180))
CLOCK_LABELS = [f"{minute // 60:02d}:00" for minute in CLOCK_TICKS]


def describe_source():
    """One line naming where the frames came from, for the notebooks to print."""
    if SYNTHETIC_DATA:
        meta = load_fixture()["meta"]
        return (f"fixture {FIXTURE_PATH.name} - {meta['n_users']} participants, "
                f"seed {meta['seed']}, anchored {meta['anchor_date']}")
    return f"Django ORM - {_db.get('HOST') or '(local)'} / {_db.get('NAME')}"


# The fourteen frames every notebook reads. Built once on import.
metrics_daily_df = frame(MetricsDaily.objects.order_by("user_id", "study_day"),
                         *METRICS_DAILY_FIELDS)
metrics_participant_df = frame(MetricsParticipant.objects.order_by("user_id"),
                               *METRICS_PARTICIPANT_FIELDS)
metrics_cohort_df = frame(MetricsCohort.objects.order_by("-as_of"), *METRICS_COHORT_FIELDS)
cohort_latest = metrics_cohort_df.drop_duplicates("phase_filter")
alerts_df = frame(Alert.objects.order_by("-fired_at"), *ALERT_FIELDS)
alerts_open_df = alerts_df[alerts_df["resolved_at"].isna()]

users_df = frame(User.objects.order_by("user_id"),
                 "user_id", "is_enrolled", "enrolled_at", "gender", "birthdate")
if not SYNTHETIC_DATA:
    users_df["has_push_token"] = [
        bool(token) for token in
        User.objects.order_by("user_id").values_list("push_token", flat=True)
    ]

wearable_devices_df = frame(WearableDevice.objects.order_by("user_id"),
                            "id", "user_id", "labfront_participant_id", "is_active",
                            "last_synced_at")
wearable_sync_df = frame(WearableSync.objects.order_by("user_id", "observed_at"),
                         "id", "user_id", "observed_at", "source", "last_synced_at",
                         "samples_written")
ema_df = frame(EMA.objects.order_by("user_id", "sent_at"), *EMA_FIELDS)
ema_item_responses_df = frame(
    EMAItemResponse.objects.order_by("ema_id", "item_id", "sub_item_id"),
    "id", "ema_id", "item_id", "sub_item_id", "response_type",
    "value_numeric", "value_choice", "value_choices")
checkin_reminders_df = frame(CheckinReminder.objects.order_by("user_id", "sent_at"),
                             "id", "user_id", "sent_at", "daily_count_at_send")
jitai_log_df = frame(JITAILog.objects.order_by("user_id", "triggered_at"), *JITAI_FIELDS)
engagement_log_df = frame(EngagementLog.objects.order_by("user_id", "occurred_at"),
                          "id", "user_id", "jitai_log_id", "event_type",
                          "occurred_at", "recorded_at")
phone_telemetry_df = frame(PhoneTelemetry.objects.order_by("user_id", "occurred_at"),
                           "id", "user_id", "session_id", "event_type", "occurred_at",
                           "recorded_at", "screen_name", "latency_ms", "metadata")

_hr_qs = HeartRateSample.objects.order_by("user_id", "timestamp")
if SYNTHETIC_DATA:
    heart_rate_df = frame(_hr_qs, "id", "user_id", "timestamp", "bpm", "source")
    hr_total = len(heart_rate_df)
    if HR_DAYS is not None:
        heart_rate_df = heart_rate_df[
            heart_rate_df["timestamp"] >= pd.Timestamp.utcnow() - pd.Timedelta(days=HR_DAYS)]
else:
    hr_total = HeartRateSample.objects.count()
    if HR_DAYS is not None:
        _hr_qs = _hr_qs.filter(timestamp__gte=timezone.now() - timedelta(days=HR_DAYS))
    heart_rate_df = frame(_hr_qs, "id", "user_id", "timestamp", "bpm", "source")

LOADED_TABLES = [
    ("dashboard_metricsdaily", metrics_daily_df),
    ("dashboard_metricsparticipant", metrics_participant_df),
    ("dashboard_metricscohort", metrics_cohort_df),
    ("dashboard_alert", alerts_df),
    ("app_user", users_df),
    ("app_wearabledevice", wearable_devices_df),
    ("app_wearablesync", wearable_sync_df),
    ("app_ema", ema_df),
    ("app_emaitemresponse", ema_item_responses_df),
    ("app_checkinreminder", checkin_reminders_df),
    ("app_jitailog", jitai_log_df),
    ("app_engagementlog", engagement_log_df),
    ("app_phonetelemetry", phone_telemetry_df),
    (f"app_heartratesample (last {HR_DAYS}d)", heart_rate_df),
]


def table_summary():
    return pd.DataFrame([shape(name, df) for name, df in LOADED_TABLES])
