"""
Schema-aligned synthetic cohort generator for the REACT feasibility pipeline.

Unlike the legacy ``synthetic_generator.py`` (1-5 Likert analysis frames used for MSSD
construct-validity in ``mssd_validation.py``), this module emits frames whose columns match the
**production app_* schema / analytics/scripts.py loaders** exactly, so the same data drives
``build_study_report()``, ``build_participant_report()``, and every ``compute_*`` /  ``plot_*``
function with no adaptation.

Frames returned by ``generate_react_cohort``:
    users              -> load_users()
    ema                -> load_ema()
    ema_item_responses -> load_ema_item_responses()   (B1=energy, B2=stress; the MSSD signal)
    jitai              -> load_jitai_log()
    heart_rate         -> load_heart_rate()
    stress_samples     -> load_stress_samples()
    engagement         -> load_engagement_log()
    wearable_devices   -> load_wearable_devices()

Signal model: two independent AR(1) latent series per participant -> B1 (energy) and B2 (stress),
mapped to a 1-7 Likert (matches EMA MinValueValidator(1)/MaxValueValidator(7)). EMA missingness is
clustered via the legacy ``_clustered_missing_mask``. JITAI decision points follow the two-stage
gate (within-person eligibility -> coin flip p=0.5) with a 60-min cooldown and a delivery funnel.

Author: @tylerrleee
"""

from __future__ import annotations

from typing import Dict

import numpy as np
import pandas as pd

from synthetic_generator import _clustered_missing_mask


def generate_react_cohort(
    n_users: int = 6,
    days: int = 35,
    ema_per_day: int = 5,
    seed: int = 7,
    resp_rate: float = 0.8,
    mean_gap_length: int = 3,
    randomization_probability: float = 0.5,
    cooldown_minutes: int = 60,
    daily_cap: int = 4,
    start: pd.Timestamp | None = None,
) -> Dict[str, pd.DataFrame]:
    """
    Generate a full synthetic REACT cohort in the production app_* schema.

    Inputs:
        n_users        - number of participants (last one is marked withdrawn).
        days           - study length in days (35 = full deployment; 14 = plan sub-study).
        ema_per_day    - scheduled EMA prompts per day.
        seed           - RNG seed for reproducibility.
        resp_rate      - fraction of EMA prompts answered (clustered missingness).
        mean_gap_length- mean length of a missing run (prompts).
        randomization_probability - P(send | eligible); PI sign-off = 0.5.
        cooldown_minutes - minimum gap between sent prompts per participant.
        daily_cap      - max prompts sent per participant per day (hard cap = 4).
        start          - study Day-1 anchor (tz-aware). Default None anchors the
                         study to the recent past (now - days) so time-relative
                         metrics (retention phases, stale-sync, active-phase
                         dosage) behave sensibly. Pass an explicit timestamp to
                         pin dates for exact reproducibility.

    Outputs:
        dict of 8 DataFrames keyed: users, ema, ema_item_responses, jitai,
        heart_rate, stress_samples, engagement, wearable_devices. All timestamp
        columns are tz-aware UTC. Column names match analytics/scripts.py loaders.

    Example:
        frames = generate_react_cohort(days=35)
        frames["ema_item_responses"].query("item_id == 'B1'")   # energy signal
    Source of data:
        Synthetic (AR(1) latent volatility). Not real participants.
    """
    rng = np.random.default_rng(seed)
    if start is None:
        start = pd.Timestamp.now(tz="UTC").normalize() - pd.Timedelta(days=days)

    users, ema, items, jitai, hr, stress, eng, devices = [], [], [], [], [], [], [], []
    ema_id = item_id = jid = eng_id = hr_id = stress_id = dev_id = 0

    for uid in range(1, n_users + 1):
        enrolled = start
        withdrawn = uid == n_users
        users.append(dict(
            user_id=uid, email=f"u{uid}@synthetic.gatorfan",
            first_name=f"User{uid}", last_name="Synthetic",
            birthdate="2003-01-01", gender=rng.choice(["male", "female", "other"]),
            is_enrolled=not withdrawn, enrolled_at=enrolled,
        ))

        dev_id += 1
        devices.append(dict(
            id=dev_id, user_id=uid,
            labfront_participant_id=f"labfront-{uid:08d}",
            is_active=not withdrawn,
            # last user is stale (no sync in days); others synced recently
            last_synced_at=(start if withdrawn
                            else start + pd.Timedelta(days=days, hours=8)),
        ))

        # two independent AR(1) latent signals -> energy (B1) and stress (B2)
        mu1, sigma1, rho1 = rng.uniform(3, 5), rng.uniform(0.4, 1.4), rng.uniform(0.2, 0.8)
        mu2, sigma2, rho2 = rng.uniform(2, 4), rng.uniform(0.4, 1.2), rng.uniform(0.2, 0.8)

        n_prompts = days * ema_per_day
        z1 = np.zeros(n_prompts)
        z2 = np.zeros(n_prompts)
        z1[0] = rng.normal(0, sigma1)
        z2[0] = rng.normal(0, sigma2)
        for t in range(1, n_prompts):
            z1[t] = rho1 * z1[t - 1] + rng.normal(0, sigma1 * np.sqrt(1 - rho1 ** 2))
            z2[t] = rho2 * z2[t - 1] + rng.normal(0, sigma2 * np.sqrt(1 - rho2 ** 2))
        energy_series = np.clip(np.round(mu1 + z1), 1, 7)
        stress_series = np.clip(np.round(mu2 + z2), 1, 7)
        answered_mask = ~_clustered_missing_mask(n_prompts, resp_rate, mean_gap_length, rng)

        platform = str(rng.choice(["iOS", "Android"]))  # one device OS per participant
        last_sent = None
        decision_count = 0
        last_ema_id, last_energy, last_stress = 0, 4.0, 4.0
        idx = 0
        for d in range(days):
            prompts_today = 0
            for k in range(ema_per_day):
                ts = enrolled + pd.Timedelta(days=d, hours=9 + 2 * k)
                energy = float(energy_series[idx])
                stress_val = float(stress_series[idx])
                answered = bool(answered_mask[idx])
                idx += 1

                ema_id += 1
                if answered:
                    # ~12% respond late (outside the 60-min window)
                    if rng.random() < 0.12:
                        resp = ts + pd.Timedelta(minutes=int(rng.uniform(61, 180)))
                    else:
                        resp = ts + pd.Timedelta(minutes=int(rng.uniform(3, 55)))
                else:
                    resp = pd.NaT
                ema.append(dict(
                    id=ema_id, user_id=uid, prompt_id=f"p-{d}-{k}", sent_at=ts,
                    responded_at=resp,
                    status="completed" if answered else "expired",
                    ema_type="scheduled_check_in", source_jitai_log_id=None,
                    outcome_window_start=pd.NaT, outcome_window_end=pd.NaT,
                    expires_at=ts + pd.Timedelta(minutes=60),
                    mood=energy if answered else None,
                    stress=stress_val if answered else None,
                    energy=energy if answered else None,
                ))
                if answered:
                    for iid, base in [("B1", energy), ("B2", stress_val)]:
                        item_id += 1
                        items.append(dict(
                            id=item_id, ema_id=ema_id, item_id=iid,
                            sub_item_id=iid + "a", response_type="likert",
                            value_numeric=int(base), value_choice=None,
                            value_choices=None, user_id=uid, sent_at=ts,
                        ))

                # Track the most recent EMA context for JITAI decision linkage.
                last_ema_id, last_energy, last_stress = ema_id, energy, stress_val

            # JITAI decision points on a 30-min waking-hours grid (biometric cadence),
            # decoupled from EMA prompts so the cooldown (60 min) and daily cap (4)
            # become binding constraints and every decision_reason category appears.
            for slot in range(16, 44):  # 08:00 .. 21:30, every 30 minutes
                if rng.random() < 0.5:
                    continue
                ts = enrolled + pd.Timedelta(days=d, minutes=30 * slot)
                jid += 1
                obs = float(2 * sigma1 ** 2 * (1 - rho1) + rng.normal(0, 0.3))
                obs = max(obs, 0.0)
                decision_count += 1

                # Two-stage gate, mirroring decision_engine reason strings.
                # randomization_draw is recorded for every ELIGIBLE decision (MSSD
                # threshold met AND enough history), so audit eligibility counts
                # cooldown/cap-blocked decisions too.
                draw = None
                sent = False
                if decision_count <= 3:
                    reason = "insufficient within-person history"
                elif obs <= 1.0:
                    reason = "below within-person threshold"
                else:
                    draw = float(rng.random())
                    cooled = (last_sent is None) or (
                        (ts - last_sent).total_seconds() / 60 >= cooldown_minutes)
                    if not cooled:
                        reason = "cooldown active"
                    elif prompts_today >= daily_cap:
                        reason = "daily cap reached"
                    elif draw < randomization_probability:
                        sent = True
                        reason = "prompt sent"
                        last_sent = ts
                        prompts_today += 1
                    else:
                        reason = "eligible, not randomized"

                # Delivery funnel (only for sent); ~4% fail to reach the device.
                push = ts + pd.Timedelta(seconds=1) if sent else pd.NaT
                failed = bool(sent and rng.random() < 0.04)
                if sent and not failed:
                    recv = (push + pd.Timedelta(seconds=int(rng.uniform(2, 6)))
                            if rng.random() < 0.95 else pd.NaT)
                    rep = (recv + pd.Timedelta(seconds=int(rng.uniform(1, 4)))
                           if pd.notna(recv) and rng.random() < 0.92 else pd.NaT)
                else:
                    recv = pd.NaT
                    rep = pd.NaT

                if not sent:
                    delivery_status = "not_sent"
                elif failed:
                    delivery_status = "failed"
                elif pd.notna(recv):
                    delivery_status = "received_on_device"
                else:
                    delivery_status = "accepted_by_expo"

                jitai.append(dict(
                    id=jid, user_id=uid, prompt_id=f"jp-{d}-{slot}",
                    triggered_at=ts, decision_point_id=f"dp-{jid}",
                    decision_made_at=ts,
                    trigger_reason=reason,
                    trigger_signal="mssd", observed_mssd=obs,
                    randomization_probability=randomization_probability,
                    randomization_draw=draw, send_prompt=sent, eligible_prompt_ids=None,
                    hr_at_trigger=int(rng.uniform(60, 110)),
                    stress_at_trigger=int(rng.uniform(20, 80)),
                    ema_id=last_ema_id, ema_mood=int(last_energy),
                    ema_stress=int(last_stress), ema_energy=int(last_energy),
                    status="delivered" if (sent and not failed) else "not_sent",
                    delivery_status=delivery_status,
                    push_sent_at=push, device_received_at=recv, receipt_reported_at=rep,
                    receipt_platform=(platform if sent else ""),
                    receipt_app_state=(str(rng.choice(["foreground", "background"]))
                                       if sent else ""),
                    delivery_error=("APNs/FCM delivery timeout" if failed else ""),
                ))
                if sent and pd.notna(recv):
                    eng_id += 1
                    eng.append(dict(
                        id=eng_id, user_id=uid, jitai_log_id=jid,
                        event_type="ema_opened",
                        occurred_at=recv + pd.Timedelta(minutes=int(rng.uniform(1, 20))),
                        recorded_at=recv + pd.Timedelta(minutes=21),
                    ))

            # ~10% of days are low-wear: device worn only in the morning
            # (~3 h) -> fails the >= 8 h/day goal and shows a ✗ on the heatmap.
            low_wear_day = rng.random() < 0.10
            day_hours = range(8, 11) if low_wear_day else range(8, 22)
            # heart rate: waking-hour samples (15-min cadence) with a midday non-wear gap
            for hh in day_hours:
                if not low_wear_day and 13 <= hh < 16:  # 3h non-wear gap
                    continue
                for mm in (0, 15, 30, 45):
                    hr_id += 1
                    hr.append(dict(
                        id=hr_id, user_id=uid,
                        timestamp=enrolled + pd.Timedelta(days=d, hours=hh, minutes=mm),
                        bpm=int(np.clip(rng.normal(72, 8), 45, 150)),
                        source="garmin_labfront",
                    ))
                    # Garmin stress score (0-100), coarser 3-min-ish cadence -> one per hour here
                    if mm == 0:
                        stress_id += 1
                        stress.append(dict(
                            id=stress_id, user_id=uid,
                            timestamp=enrolled + pd.Timedelta(days=d, hours=hh),
                            stress_score=int(np.clip(rng.normal(45, 18), 0, 100)),
                            source="garmin_labfront",
                        ))

    frames = dict(
        users=pd.DataFrame(users),
        ema=pd.DataFrame(ema),
        ema_item_responses=pd.DataFrame(items),
        jitai=pd.DataFrame(jitai),
        heart_rate=pd.DataFrame(hr),
        stress_samples=pd.DataFrame(stress),
        engagement=pd.DataFrame(eng),
        wearable_devices=pd.DataFrame(devices),
    )
    _coerce_timestamps(frames)
    return frames


def _coerce_timestamps(frames: Dict[str, pd.DataFrame]) -> None:
    """Coerce every *_at / timestamp column to tz-aware UTC, matching loader output."""
    time_cols = {
        "sent_at", "responded_at", "outcome_window_start", "outcome_window_end",
        "expires_at", "triggered_at", "decision_made_at", "push_sent_at",
        "device_received_at", "receipt_reported_at", "timestamp", "occurred_at",
        "recorded_at", "enrolled_at", "last_synced_at",
    }
    for df in frames.values():
        for col in df.columns:
            if col in time_cols and df[col].notna().any():
                df[col] = pd.to_datetime(df[col], utc=True)


def patch_scripts_loaders(scripts_module, frames: Dict[str, pd.DataFrame]) -> None:
    """
    Monkeypatch a loaded ``analytics.scripts`` module so its load_* functions serve
    the synthetic ``frames`` instead of querying the database.

    Inputs:
        scripts_module - the imported scripts module (e.g. ``import scripts as s``).
        frames         - dict returned by generate_react_cohort().

    Outputs:
        None. Rebinds load_users / load_ema / load_ema_item_responses /
        load_jitai_log / load_heart_rate / load_stress_samples /
        load_engagement_log / load_wearable_devices on the module in place.

    Example:
        frames = generate_react_cohort()
        patch_scripts_loaders(scripts, frames)
        scripts.build_study_report()   # now runs on synthetic data, no DB
    Source of data:
        The synthetic frames (see generate_react_cohort).
    """
    def _filtered(key):
        df = frames[key]

        def loader(user_id=None, start_date=None, end_date=None):
            if user_id is None:
                return df
            return df[df["user_id"] == user_id]
        return loader

    scripts_module.load_users = lambda user_id=None: (
        frames["users"] if user_id is None
        else frames["users"][frames["users"]["user_id"] == user_id]
    )
    scripts_module.load_ema = _filtered("ema")
    scripts_module.load_ema_item_responses = _filtered("ema_item_responses")
    scripts_module.load_jitai_log = _filtered("jitai")
    scripts_module.load_heart_rate = _filtered("heart_rate")
    scripts_module.load_stress_samples = _filtered("stress_samples")
    scripts_module.load_engagement_log = _filtered("engagement")
    scripts_module.load_wearable_devices = lambda: frames["wearable_devices"]
