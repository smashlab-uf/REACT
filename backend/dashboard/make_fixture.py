"""Generate the synthetic cohort fixture the monitoring notebook reads.

Emits one JSON document keyed by db_table, shaped so that
pd.DataFrame(rows, columns=fields) reproduces the ORM frame column-for-column.
It is a DataFrame source, never a Django fixture: nothing here is ever loaded
into a database, so the auto_now_add trap on EMA.sent_at and JITAILog.triggered_at
does not apply and the notebook can run with no database reachable at all.

Usage:
    python backend/dashboard/make_fixture.py            # write fixture_cohort.json beside it
    python backend/dashboard/make_fixture.py --check    # exit non-zero if the file is stale
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone as dt_timezone
from pathlib import Path

import numpy as np

# backend/ is what has to be importable: it holds project.settings, app and the
# dashboard package itself.
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from dashboard.data.config import (  # noqa: E402
    DAILY_PROMPT_CAP,
    ITEM_BANK_VERSION,
    JITAI_COOLDOWN_MINUTES,
    NOTIFICATION_WINDOW_END_HOUR,
    NOTIFICATION_WINDOW_START_HOUR,
    OUTCOME_WINDOW_HOURS,
    PARTICIPANT_TZ,
    RUN_IN_DAYS,
    SCHEDULED_CHECK_IN_DAILY_CAP,
    STUDY_DAYS,
    WAKING_WINDOW_END_HOUR,
    WAKING_WINDOW_START_HOUR,
)
from dashboard.data.item_bank import load_item_bank  # noqa: E402
from syntheticData.synthetic_generator import _clustered_missing_mask  # noqa: E402

# Anchored to this file, so moving the package cannot strand the fixture.
OUT_PATH = Path(__file__).resolve().parent / "fixture_cohort.json"

SCHEDULED_TYPE = "scheduled_check_in"
POST_PROMPT_TYPE = "post_prompt"
FEEDBACK_TYPE = "prompt_feedback"
SIGNAL_SUB_ITEMS = ("B1_valence", "B1_arousal", "B2_stress")
ACTED_CHOICES = ("I paused or waited", "I changed what I was going to do")
INELIGIBLE_REASONS = (
    "below within-person threshold",
    "cooldown active",
    "daily cap reached",
)
SLOT_HOURS = (NOTIFICATION_WINDOW_END_HOUR - NOTIFICATION_WINDOW_START_HOUR) // SCHEDULED_CHECK_IN_DAILY_CAP
HR_DAYS = 14
WAKING_MINUTES = (WAKING_WINDOW_END_HOUR - WAKING_WINDOW_START_HOUR) * 60


def iso(moment):
    if moment is None:
        return None
    return moment.astimezone(dt_timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def at(local_date, hour, minute=0):
    return datetime(local_date.year, local_date.month, local_date.day, hour, minute,
                    tzinfo=PARTICIPANT_TZ)


def compact(row):
    """Drop null values. pd.DataFrame(rows, columns=fields) fills absent keys with
    NaN, so the frame is identical and the file is roughly half the size."""
    return {key: value for key, value in row.items() if value is not None}


class Ids:
    def __init__(self):
        self.counters = {}

    def next(self, name):
        self.counters[name] = self.counters.get(name, 0) + 1
        return self.counters[name]


def participant_plan(n_users, rng):
    """Study day each participant has reached, and what makes them interesting.

    Staggered so the cohort spans run-in, MRT, completion and withdrawal at once.
    """
    plan = []
    for index in range(n_users):
        if index == 0:
            role, study_day = "complete", STUDY_DAYS - 1
        elif index == 1:
            role, study_day = "withdrawn", 21
        elif index == 2:
            role, study_day = "inactive", 18
        elif index < 6:
            role, study_day = "run_in", int(rng.integers(RUN_IN_DAYS - 2, RUN_IN_DAYS))
        else:
            role, study_day = "mrt", int(rng.integers(RUN_IN_DAYS + 5, STUDY_DAYS - 1))
        plan.append({"role": role, "study_day": study_day})
    return plan


def ema_sub_items(bank, ema_type, rng, missing_signal=False):
    """A served sub-item set and the answers given, honouring depends_on.

    Returns (served_ids, answers) where answers maps sub_item_id -> a row dict
    fragment. Served ids always include the dependents the client would have
    been sent; the resolver in the notebook re-applies the gate.
    """
    if ema_type == FEEDBACK_TYPE:
        item_ids = ["C0"]
    elif ema_type == POST_PROMPT_TYPE:
        item_ids = ["B1", "B2"]
    else:
        # B1 and B2 every check-in (they carry the MSSD signal), plus one item
        # off the B4-B7 rotation, which is what the served set actually looks like.
        item_ids = ["B1", "B2", str(rng.choice(["B4", "B5", "B6", "B7"]))]

    served, answers = [], {}
    for item_id in item_ids:
        item = bank.get(item_id)
        if item is None:
            continue
        for sub in item["sub_items"]:
            sub_id = sub["sub_item_id"]
            served.append(sub_id)
            depends_on = sub.get("depends_on")
            if depends_on:
                parent = answers.get(depends_on["sub_item_id"])
                parent_value = parent["value_choice"] if parent else None
                if "equals" in depends_on and parent_value != depends_on["equals"]:
                    continue
                if "not_equals" in depends_on and (
                        parent_value is None or parent_value == depends_on["not_equals"]):
                    continue
            if missing_signal and sub_id in SIGNAL_SUB_ITEMS:
                continue
            answers[sub_id] = answer_for(sub, item_id, rng)
    return served, answers


def answer_for(sub, item_id, rng):
    response_type = sub["response_type"]
    row = {"item_id": item_id, "sub_item_id": sub["sub_item_id"],
           "response_type": response_type,
           "value_numeric": None, "value_choice": None, "value_choices": None}
    if response_type == "likert":
        low = sub.get("min_value", 1)
        high = sub.get("max_value", 7)
        row["value_numeric"] = int(rng.integers(low, high + 1))
    elif response_type == "number":
        row["value_numeric"] = int(rng.integers(0, 6))
    elif response_type in ("single_choice", "yes_no"):
        choices = sub.get("choices") or ["Yes", "No"]
        row["value_choice"] = str(rng.choice(choices))
    elif response_type == "multi_choice":
        choices = sub.get("choices") or ["Other"]
        size = int(rng.integers(1, min(3, len(choices)) + 1))
        row["value_choices"] = [str(c) for c in rng.choice(choices, size=size, replace=False)]
    return row


def wear_runs(rng, mode="normal"):
    """Worn stretches inside the waking window, as (start_minute, n_minutes).

    Three shapes, because the wear tile reads two numbers that only diverge for
    a reason:
      normal - worn nearly all day with one short break. gap-coverage and
               minute-coverage agree, so nothing is flagged.
      burst  - many short stretches whose gaps all stay under the 2-hour
               threshold: gap-coverage reads high while minute-coverage is poor.
               That divergence is exactly what burst_charging_days counts.
      low    - one genuine gap past two hours, which is what drops gap-coverage
               below the wear floor and feeds the wear_low rule.
    """
    start = WAKING_WINDOW_START_HOUR * 60
    end = WAKING_WINDOW_END_HOUR * 60
    span = end - start

    if mode == "burst":
        runs, cursor = [], start
        while cursor < end - 30:
            length = int(rng.integers(10, 25))
            runs.append((cursor, min(length, end - cursor)))
            cursor += length + int(rng.integers(60, 110))
        return runs

    if mode == "low":
        gap = int(rng.integers(470, 620))
        first = int(rng.integers(60, 120))
        return [(start, first), (start + first + gap, max(0, span - first - gap))]

    break_start = start + int(rng.integers(120, span - 240))
    break_length = int(rng.integers(20, 90))
    return [(start, break_start - start),
            (break_start + break_length, end - break_start - break_length)]


def build(n_users, days, seed):
    rng = np.random.default_rng(seed)
    bank = load_item_bank()
    ids = Ids()
    today = date.today()

    tables = {name: [] for name in (
        "app_user", "app_wearabledevice", "app_wearablesync", "app_ema",
        "app_emaitemresponse", "app_checkinreminder", "app_jitailog",
        "app_engagementlog", "app_phonetelemetry",
        "dashboard_metricsdaily", "dashboard_metricsparticipant",
        "dashboard_metricscohort", "dashboard_alert",
    )}
    hr_runs = []
    plan = participant_plan(n_users, rng)
    draws_pool = list(rng.uniform(0, 1, size=200))
    draw_cursor = 0

    for index, entry in enumerate(plan):
        user_id = 1001 + index
        role = entry["role"]
        study_day_now = entry["study_day"]
        day1 = today - timedelta(days=study_day_now)
        enrolled_at = at(day1, 10)
        withdrew_on = day1 + timedelta(days=15) if role == "withdrawn" else None

        tables["app_user"].append({
            "user_id": user_id,
            "is_enrolled": role != "withdrawn",
            "enrolled_at": iso(enrolled_at),
            "gender": str(rng.choice(["male", "female", "other"])),
            "birthdate": str(date(2005, 1, 1) + timedelta(days=int(rng.integers(0, 1400)))),
            "has_push_token": role != "inactive",
        })
        tables["app_wearabledevice"].append({
            "id": ids.next("device"), "user_id": user_id,
            "labfront_participant_id": f"SYN-{index:03d}",
            "is_active": role not in ("withdrawn", "inactive"),
            "last_synced_at": iso(at(today, 8) - timedelta(hours=int(rng.integers(1, 40)))),
        })

        last_days = range(max(0, study_day_now - days + 1), study_day_now + 1)
        for study_day in last_days:
            local_date = day1 + timedelta(days=study_day)
            if withdrew_on is not None and local_date >= withdrew_on:
                break
            is_run_in = study_day < RUN_IN_DAYS
            tally = build_day(tables, hr_runs, ids, rng, bank, user_id, role, local_date,
                              study_day, is_run_in, today, draws_pool, draw_cursor)
            draw_cursor = (draw_cursor + 4) % (len(draws_pool) - 4)
            add_metrics_daily(tables, ids, user_id, local_date, study_day,
                              is_run_in, today, tally)

        tables["dashboard_metricsparticipant"].append({
            "id": ids.next("mp"), "user_id": user_id,
            "computed_at": iso(at(today, 6)),
            "enrolled_at": iso(enrolled_at),
            "day1_date": str(day1),
            "study_day_now": study_day_now,
            "phase": {"complete": "complete", "withdrawn": "withdrawn",
                      "run_in": "run_in"}.get(role, "mrt"),
            "is_enrolled_snapshot": role != "withdrawn",
            "first_seen_not_enrolled_at": iso(at(withdrew_on, 6)) if withdrew_on else None,
            "last_ema_at": iso(at(today - timedelta(days=1), 17)),
            "last_sync_at": None,
            "active_retention": role in ("run_in", "mrt"),
            "risk_score": None, "risk_components": None,
            "slot_coverage_rate": None, "slot_coverage_num": 0, "slot_coverage_den": 0,
            "prompt_response_rate": None, "prompt_response_num": 0, "prompt_response_den": 0,
            "wear_rate": None, "wear_num": 0, "wear_den": 0,
        })

    seed_alerts(tables, ids, today)
    seed_cohort_snapshot(tables, ids, today, n_users)
    return {
        "meta": {
            "generator": "dashboard/make_fixture.py",
            "seed": seed, "n_users": n_users, "days": days,
            "study_days": STUDY_DAYS, "anchor_date": str(today),
            "item_bank_version": ITEM_BANK_VERSION,
            "note": ("Synthetic. The four dashboard_* tables are fabricated, not produced by "
                     "recompute_metrics; only MetricsParticipant.first_seen_not_enrolled_at and "
                     "Alert are read by the notebook's own computations."),
        },
        "tables": tables,
        "heart_rate_runs": hr_runs,
    }


def build_day(tables, hr_runs, ids, rng, bank, user_id, role, local_date,
              study_day, is_run_in, today, draws_pool, draw_cursor):
    # Each anomaly is pinned to one participant so it fires exactly once and can
    # be traced back, rather than emerging by chance from the distributions.
    silent_day = user_id == 1012 and study_day % 6 == 2
    cap_day = user_id == 1008 and study_day % 13 == 5
    cap_bind_day = user_id == 1014 and study_day % 6 == 3
    cooldown_day = user_id == 1007 and study_day % 5 == 4
    burst_day = user_id == 1013 and study_day % 5 == 1
    low_wear_day = user_id == 1010 and (today - local_date).days in (1, 2, 3)
    heavy_missing = user_id == 1009
    runin_send_day = is_run_in and study_day == RUN_IN_DAYS - 2
    # One guaranteed delivered prompt a week per MRT participant. Without it the
    # prompt-response denominator carries fewer than RATE_MIN_PARTICIPANTS and the
    # tile suppresses - which is the state the fixture exists to get out of.
    weekly_send_day = not is_run_in and study_day % 7 == 1
    wear_mode = "burst" if burst_day else ("low" if low_wear_day else "normal")

    tally = {"slots_covered": 0, "slots_reminded": 0, "ema_outcome": 0, "ema_feedback": 0,
             "missing_b1b2": 0, "decisions": 0, "eligible": 0, "sent": 0, "delivered": 0,
             "failures": 0, "cap_hit": False, "min_gap_min": None,
             "cooldown_violations": 0, "hr_minutes": 0}

    covered_slots = []
    if role != "inactive":
        n_slots = 0 if silent_day else int(rng.integers(2, SCHEDULED_CHECK_IN_DAILY_CAP + 1))
        missing = _clustered_missing_mask(SCHEDULED_CHECK_IN_DAILY_CAP, 0.75, 2.0, rng)
        covered_slots = [slot for slot in range(SCHEDULED_CHECK_IN_DAILY_CAP)
                         if not missing[slot]][:n_slots]

    for slot in covered_slots:
        hour = NOTIFICATION_WINDOW_START_HOUR + slot * SLOT_HOURS
        sent_at = at(local_date, hour, int(rng.integers(0, 60)))
        missing_signal = heavy_missing and slot % 2 == 0
        add_ema(tables, ids, rng, bank, user_id, SCHEDULED_TYPE, sent_at,
                missing_signal=missing_signal)
        tally["slots_covered"] += 1
        tally["missing_b1b2"] += int(missing_signal)

    if not silent_day and role != "inactive":
        for slot in range(SCHEDULED_CHECK_IN_DAILY_CAP):
            if slot in covered_slots or rng.random() > 0.92:
                continue
            hour = NOTIFICATION_WINDOW_START_HOUR + slot * SLOT_HOURS
            tables["app_checkinreminder"].append({
                "id": ids.next("reminder"), "user_id": user_id,
                "sent_at": iso(at(local_date, hour, 30)),
                "daily_count_at_send": slot,
            })
            tally["slots_reminded"] += 1

    if role == "inactive" or (is_run_in and not runin_send_day):
        tally["hr_minutes"] = add_wear(hr_runs, rng, user_id, local_date, today, wear_mode)
        add_sync(tables, ids, rng, user_id, local_date, today, tally["hr_minutes"])
        return tally

    n_decisions = 1 if runin_send_day else int(rng.integers(1, 5))
    if cap_bind_day:
        n_decisions = DAILY_PROMPT_CAP + 3
        tally["cap_hit"] = True
    if cap_day:
        n_decisions = 3 * DAILY_PROMPT_CAP
    previous_push = None
    for point in range(n_decisions):
        hour = NOTIFICATION_WINDOW_START_HOUR + min(point * 3, 11)
        decided_at = at(local_date, hour, int(rng.integers(0, 59)))
        if cooldown_day and point == 1 and previous_push is not None:
            decided_at = previous_push + timedelta(minutes=int(JITAI_COOLDOWN_MINUTES // 2))
        draw = float(draws_pool[(draw_cursor + point) % len(draws_pool)])
        # Chosen so the cohort eligibility rate lands inside the gauge's 10-35%
        # target band: an alarm there should mean a seeded defect, not an
        # artefact of the generator's distributions.
        threshold = round(float(rng.uniform(1.8, 3.6)), 3)
        observed = round(float(rng.uniform(0.2, 3.0)), 3)
        capped = cap_bind_day and point >= DAILY_PROMPT_CAP
        eligible = observed > threshold and not capped
        # An engine row where the threshold was cleared but no draw was taken:
        # the "should have been eligible" marker mssd_lane looks for.
        unexplained = user_id == 1011 and study_day % 8 == 3 and point == 0
        if unexplained:
            observed, threshold = 3.2, 1.1
            eligible = False
        forced = (not capped) and (runin_send_day or weekly_send_day
                                   or cooldown_day or cap_day)
        if forced:
            eligible = True
        send = eligible and draw < 0.5
        reason = ("prompt sent" if send else
                  "randomized to no prompt" if eligible else
                  "daily cap reached" if capped else
                  "below within-person threshold")
        push_at = decided_at + timedelta(seconds=int(rng.integers(2, 20))) if send else None
        failed = send and study_day % 23 == 8 and point == 0
        received = (push_at + timedelta(seconds=int(rng.integers(3, 90)))
                    if send and not failed and (forced or rng.random() < 0.9) else None)
        platform = str(rng.choice(["ios", "android"]))
        log_id = ids.next("jitai")

        tables["app_jitailog"].append({
            "id": log_id, "user_id": user_id, "prompt_id": f"tmpl_{int(rng.integers(1, 9)):02d}",
            "triggered_at": iso(decided_at), "trigger_reason": reason,
            "trigger_signal": "mssd", "decision_point_id": f"dp-{user_id}-{local_date}-{point}",
            "ema_id": None,
            "observed_mssd": observed,
            "threshold_at_decision": threshold,
            "threshold_source": "engine" if study_day % 3 else "reconstructed",
            "randomization_probability": 0.5,
            "randomization_draw": draw if eligible else None,
            "message_arm": (str(rng.choice(["coping", "control"])) if send else None),
            "arm_randomization_probability": 0.5 if send else None,
            "arm_randomization_draw": round(float(rng.uniform(0, 1)), 4) if send else None,
            "send_prompt": bool(send),
            "status": "failed" if failed else ("delivered" if received else
                                               ("pending" if send else "not_sent")),
            "hr_at_trigger": int(rng.integers(58, 110)),
            "stress_at_trigger": int(rng.integers(10, 90)),
            "ema_mood": int(rng.integers(1, 8)), "ema_stress": int(rng.integers(1, 8)),
            "ema_energy": int(rng.integers(1, 8)),
            "decision_made_at": iso(decided_at),
            "push_sent_at": iso(push_at),
            "device_received_at": iso(received),
            "receipt_reported_at": iso(received + timedelta(seconds=2)) if received else None,
            "receipt_event_id": f"rcpt-{log_id}" if received else None,
            "delivery_status": ("failed" if failed else
                                "received_on_device" if received else
                                "accepted_by_expo" if send else "not_sent"),
            "delivery_error": "DeviceNotRegistered" if failed else None,
            "receipt_platform": platform if send else None,
            "receipt_app_state": (str(rng.choice(["foreground", "background"]))
                                  if received else None),
            "eligible_prompt_ids": [f"tmpl_{n:02d}" for n in range(1, 4)],
            "evaluated_items": {"B1_valence": int(rng.integers(1, 8)),
                                "B2_stress": int(rng.integers(1, 8))},
            "matched_categories": ["stress"] if eligible else [],
            "category_drawn": "stress" if send else None,
            "fallback_reason": None,
        })

        tally["decisions"] += 1
        tally["eligible"] += int(eligible)
        tally["sent"] += int(send)
        tally["delivered"] += int(received is not None)
        tally["failures"] += int(failed)
        if send:
            if previous_push is not None:
                gap = (push_at - previous_push).total_seconds() / 60
                tally["min_gap_min"] = int(gap if tally["min_gap_min"] is None
                                           else min(gap, tally["min_gap_min"]))
                tally["cooldown_violations"] += int(gap < JITAI_COOLDOWN_MINUTES)
            previous_push = push_at
        if received:
            add_engagement(tables, ids, rng, user_id, log_id, received)
            add_outcome_ema(tables, ids, rng, bank, user_id, log_id, push_at, tally)

    tally["hr_minutes"] = add_wear(hr_runs, rng, user_id, local_date, today, wear_mode)
    add_sync(tables, ids, rng, user_id, local_date, today, tally["hr_minutes"])
    add_telemetry(tables, ids, rng, user_id, local_date)
    return tally


def add_metrics_daily(tables, ids, user_id, local_date, study_day, is_run_in, today, tally):
    """One derived row per participant-day, tallied from the rows just generated.

    Counted rather than invented, so the derived table agrees with the raw
    tables it claims to summarise. The columns the generator has no basis for
    stay null - a structural null, which is what the layer means by it.
    """
    covered = tally["slots_covered"]
    reminded = tally["slots_reminded"]
    tables["dashboard_metricsdaily"].append({
        "id": ids.next("md"), "user_id": user_id, "study_day": study_day,
        "local_date": str(local_date), "is_run_in": is_run_in, "is_active_day": True,
        "computed_at": iso(at(today, 6)), "item_bank_version": ITEM_BANK_VERSION,
        "ema_scheduled_n": covered, "ema_jitai_n": tally["ema_feedback"],
        "ema_post_prompt_n": tally["ema_outcome"],
        "slots_expected": SCHEDULED_CHECK_IN_DAILY_CAP, "slots_covered": covered,
        "slots_reminded_uncovered": reminded,
        "slots_silent": SCHEDULED_CHECK_IN_DAILY_CAP - covered - reminded,
        "reminders_sent": reminded, "reminders_per_checkin_median": None,
        "completeness_mean": None, "ema_missing_b1b2_n": tally["missing_b1b2"],
        "decision_points_n": tally["decisions"], "eligible_n": tally["eligible"],
        "sent_n": tally["sent"], "delivered_n": tally["delivered"],
        "cap_hit": tally["cap_hit"], "min_gap_min": tally["min_gap_min"],
        "cooldown_violations_n": tally["cooldown_violations"],
        "runin_violation_n": tally["sent"] if is_run_in else 0,
        "prompt_opened_n": None, "prompt_acted_n": None, "prompt_dismissed_n": None,
        "outcome_captured_n": tally["ema_outcome"],
        "wear_valid_pct": None, "wear_gap_pct": None, "gaps_gt2h_n": None,
        "max_gap_min": None, "hr_minutes_valid": tally["hr_minutes"],
        "last_sync_age_h_eod": None, "clock_skew_p95_ms": None,
        "delivery_failures_n": tally["failures"],
    })


def add_ema(tables, ids, rng, bank, user_id, ema_type, sent_at, missing_signal=False,
            source_jitai_log_id=None, outcome_window=None):
    ema_id = ids.next("ema")
    served, answers = ema_sub_items(bank, ema_type, rng, missing_signal)
    tables["app_ema"].append({
        "id": ema_id, "user_id": user_id,
        "prompt_id": f"{ema_type}_{ema_id}", "ema_type": ema_type, "status": "completed",
        "sent_at": iso(sent_at),
        "responded_at": iso(sent_at + timedelta(minutes=int(rng.integers(1, 12)))),
        "expires_at": iso(sent_at + timedelta(minutes=30)),
        "outcome_window_start": iso(outcome_window[0]) if outcome_window else None,
        "outcome_window_end": iso(outcome_window[1]) if outcome_window else None,
        "source_jitai_log_id": source_jitai_log_id,
        "served_sub_item_ids": served,
        "mood": answers.get("B1_valence", {}).get("value_numeric"),
        "stress": answers.get("B2_stress", {}).get("value_numeric"),
        "energy": answers.get("B1_arousal", {}).get("value_numeric"),
    })
    for row in answers.values():
        tables["app_emaitemresponse"].append({
            "id": ids.next("item"), "ema_id": ema_id, **row})
    return ema_id


def add_outcome_ema(tables, ids, rng, bank, user_id, log_id, push_at, tally=None):
    if rng.random() > 0.65:
        return
    window = (push_at, push_at + timedelta(hours=OUTCOME_WINDOW_HOURS))
    responded_within = rng.random() < 0.8
    sent_at = push_at + timedelta(minutes=int(rng.integers(5, 100 if responded_within else 200)))
    add_ema(tables, ids, rng, bank, user_id, POST_PROMPT_TYPE, sent_at,
            source_jitai_log_id=log_id, outcome_window=window)
    add_ema(tables, ids, rng, bank, user_id, FEEDBACK_TYPE,
            sent_at + timedelta(minutes=2), source_jitai_log_id=log_id)
    if tally is not None:
        tally["ema_outcome"] += 1
        tally["ema_feedback"] += 1


def add_engagement(tables, ids, rng, user_id, log_id, received):
    events = ["notification_tapped"] if rng.random() < 0.6 else []
    if rng.random() < 0.25:
        events.append("notification_dismissed")
    if rng.random() < 0.5:
        events.append("ema_opened")
    for event_type in events:
        occurred = received + timedelta(minutes=int(rng.integers(1, 90)))
        tables["app_engagementlog"].append({
            "id": ids.next("engagement"), "user_id": user_id, "jitai_log_id": log_id,
            "event_type": event_type, "occurred_at": iso(occurred),
            "recorded_at": iso(occurred + timedelta(seconds=int(rng.integers(1, 30)))),
        })


def add_telemetry(tables, ids, rng, user_id, local_date):
    # One participant's device clock runs ahead of the server, which is what
    # clock_skew_p95_ms is for; the rest carry ordinary sub-second lag.
    skewed = user_id % 7 == 3
    for _ in range(int(rng.integers(1, 4))):
        occurred = at(local_date, int(rng.integers(9, 21)), int(rng.integers(0, 59)))
        lag = timedelta(minutes=9) if skewed else timedelta(seconds=int(rng.integers(1, 20)))
        tables["app_phonetelemetry"].append({
            "id": ids.next("telemetry"), "user_id": user_id,
            "session_id": f"s-{user_id}-{local_date}", "event_type": "compose_open",
            "occurred_at": iso(occurred), "recorded_at": iso(occurred + lag),
            "screen_name": "checkin", "latency_ms": int(rng.integers(40, 900)),
            "metadata": {"build": "1.4.2"},
        })


def add_sync(tables, ids, rng, user_id, local_date, today, worn_minutes):
    """The device's sync clock advancing. WearableSync exists precisely so a
    sync outage can be told apart from genuine non-wear, so a day with no wear
    still gets its sync advances unless the pipeline itself was down."""
    if (today - local_date).days >= HR_DAYS:
        return
    outage = user_id % 5 == 2 and local_date.day % 7 == 3
    if outage:
        return
    clock = at(local_date, WAKING_WINDOW_START_HOUR)
    for _ in range(int(rng.integers(2, 6))):
        clock = clock + timedelta(minutes=int(rng.integers(90, 260)))
        if clock.hour >= WAKING_WINDOW_END_HOUR:
            break
        tables["app_wearablesync"].append({
            "id": ids.next("sync"), "user_id": user_id,
            "observed_at": iso(clock + timedelta(minutes=2)),
            "source": str(rng.choice(["ingest", "client"])),
            "last_synced_at": iso(clock),
            "samples_written": int(worn_minutes // 4) if worn_minutes else 0,
        })


def add_wear(hr_runs, rng, user_id, local_date, today, mode):
    if (today - local_date).days >= HR_DAYS:
        return 0
    runs = []
    for start, length in wear_runs(rng, mode):
        if length <= 0:
            continue
        base = int(rng.integers(58, 78))
        series = [int(np.clip(base + rng.normal(0, 4), 45, 175)) for _ in range(length)]
        runs.append([start, series])
    hr_runs.append({"user_id": user_id, "local_date": str(local_date), "runs": runs})
    return sum(len(series) for _, series in runs)


def seed_alerts(tables, ids, today):
    fired = datetime(today.year, today.month, today.day, 6, tzinfo=PARTICIPANT_TZ)
    rows = [
        (None, "runin_violation", "critical", {"detail": "engine has no run-in gate"}),
        (None, "sync_stale", "critical", {"measurable": False}),
        (1007, "cooldown_violation", "critical", {"gap_min": 28}),
        (1008, "cap_exceeded", "critical", {"delivered_n": DAILY_PROMPT_CAP + 2}),
        (1009, "no_ema_48h", "high", {"hours": 62}),
        (1010, "wear_low", "high", {"consecutive_days": 3}),
        (1011, "slot_coverage_low", "warning", {"coverage": 0.31}),
    ]
    for user_id, rule_id, severity, payload in rows:
        tables["dashboard_alert"].append({
            "id": ids.next("alert"), "user_id": user_id, "rule_id": rule_id,
            "severity": severity, "fired_at": iso(fired), "resolved_at": None,
            "payload": {**payload, "date": str(today - timedelta(days=1))},
        })


def seed_cohort_snapshot(tables, ids, today, n_users):
    as_of = datetime(today.year, today.month, today.day, 6, tzinfo=PARTICIPANT_TZ)
    for phase_filter in ("all", "phase1", "phase2"):
        tables["dashboard_metricscohort"].append({
            "id": ids.next("cohort"), "as_of": iso(as_of), "phase_filter": phase_filter,
            "n_participants": n_users if phase_filter != "phase1" else 0,
            "n_active": n_users - 3 if phase_filter != "phase1" else 0,
            "benchmarks": {}, "series_14d": [], "integrity": {}, "funnel": [],
            "decision_points_n": 0, "eligible_n": 0, "sent_n": 0, "delivered_n": 0,
            "cooldown_violations_n": 0, "runin_violations_n": 0,
            "cap_hit_days": 0, "delivery_failures_n": 0,
        })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--users", type=int, default=14)
    parser.add_argument("--days", type=int, default=STUDY_DAYS)
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--out", type=Path, default=OUT_PATH)
    parser.add_argument("--check", action="store_true",
                        help="exit non-zero if the committed file differs from a fresh build")
    args = parser.parse_args()

    payload = build(args.users, args.days, args.seed)
    payload["tables"] = {name: [compact(row) for row in rows]
                         for name, rows in payload["tables"].items()}
    text = json.dumps(payload, separators=(",", ":"), sort_keys=False) + "\n"

    if args.check:
        if not args.out.exists():
            print(f"{args.out} does not exist")
            return 1
        current = args.out.read_text()
        stale = current != text
        print("STALE" if stale else "up to date", f"({args.out})")
        return 1 if stale else 0

    args.out.write_text(text)
    counts = {name: len(rows) for name, rows in payload["tables"].items()}
    hr_minutes = sum(len(series) for day in payload["heart_rate_runs"] for _, series in day["runs"])
    print(f"wrote {args.out} ({args.out.stat().st_size / 1_000_000:.2f} MB)")
    for name, count in counts.items():
        print(f"  {name:32s} {count}")
    print(f"  {'heart_rate_runs (minutes)':32s} {hr_minutes}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
