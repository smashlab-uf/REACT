import logging
import os
import random
from datetime import datetime, time, timedelta

import pandas as pd
from celery import shared_task
from django.db import IntegrityError
from django.db.models import Exists, OuterRef
from django.utils import timezone as django_timezone

from app.ema_catalog import SCHEDULED_CHECK_IN_DAILY_CAP
from app.models import CheckinReminder, EMA, HeartRateSample, JITAILog, StressSample, User
from app.notification_service import (
    mark_delivery_failed,
    select_control_prompt,
    select_prompt,
    send_checkin_reminder,
    send_jitai_prompt,
)
from app.views import PARTICIPANT_TZ, _latest_active_jitai, _today_scheduled_check_in_count
from decision_engine.decision_engine import apply_decision_rules, calculate_mssd

logger = logging.getLogger(__name__)

# Confirmed by Dr. Chang 2026-08-21. NOTIFICATION_WINDOW governs when
# reminders may fire — distinct from any other "waking window" concept
# (e.g. Abigail's wear-time denominator) — do not reuse this constant for
# anything but notification timing.
NOTIFICATION_WINDOW_START_HOUR = 9
NOTIFICATION_WINDOW_END_HOUR = 21
CHECKIN_REMINDER_DELAY_MINUTES = 30


@shared_task
def ingest_wearable_data():
    logger.info("ingest_wearable_data: Labfront API integration not yet implemented")


@shared_task
def evaluate_jitai_triggers():
    enrolled_users = User.objects.filter(
        is_enrolled=True,
        wearabledevice__is_active=True,
    )

    p = float(os.environ.get('JITAI_RANDOMIZATION_PROBABILITY', '0.5'))

    for user in enrolled_users:
        try:
            _evaluate_user(user, p)
        except Exception:
            logger.exception(
                "evaluate_jitai_triggers failed for user_id=%s", user.user_id
            )


def _evaluate_user(user, p):

    latest_new_ema = (
        EMA.objects.filter(user=user, status='completed')
        .exclude(Exists(JITAILog.objects.filter(ema=OuterRef('pk'))))
        .order_by('-sent_at')
        .first()
    )

    if latest_new_ema is None:
        return

    decision_point_id = f"ema_{latest_new_ema.pk}"

    # Volatility mapping confirmed by Dr. Chang 2026-08-17 (copied to Celia/Tien):
    # mood -> B1 valence rating, stress -> B2 stress rating, energy -> B1
    # calm-to-excited rating. Averaged the same way the old mood/stress/energy
    # fields were, so Tien's calibration stays comparable.
    SIGNAL_SUB_ITEMS = {'mood': 'B1_valence', 'stress': 'B2_stress', 'energy': 'B1_arousal'}

    ema_qs = (
        EMA.objects.filter(user=user, status='completed')
        .prefetch_related('item_responses')
        .order_by('sent_at')
    )

    rows = []
    for ema_obj in ema_qs:
        sub_vals = {
            r.sub_item_id: r.value_numeric
            for r in ema_obj.item_responses.all()
            if r.sub_item_id in SIGNAL_SUB_ITEMS.values()
        }
        if all(sub_id in sub_vals for sub_id in SIGNAL_SUB_ITEMS.values()):
            rows.append({
                'timestamp': ema_obj.sent_at,
                'ema': sum(sub_vals.values()) / len(sub_vals),
            })

    if not rows:
        return

    df = pd.DataFrame(rows)
    df['user_id'] = user.user_id

    hr_rows = list(
        HeartRateSample.objects
        .filter(user=user)
        .order_by('timestamp')
        .values('timestamp', 'bpm')
    )
    if hr_rows:
        hr_df = pd.DataFrame(hr_rows)
        hr_df['user_id'] = user.user_id
        hr_df = hr_df.rename(columns={'bpm': 'hr'})
        df = pd.merge_asof(
            df.sort_values('timestamp'),
            hr_df[['user_id', 'timestamp', 'hr']].sort_values('timestamp'),
            on='timestamp',
            by='user_id',
            direction='backward',
            tolerance=pd.Timedelta('30min'),
        )

    df = calculate_mssd(df, window=3)
    result_df = apply_decision_rules(df)

    match = result_df[result_df['timestamp'] == pd.Timestamp(latest_new_ema.sent_at)]
    if match.empty:
        return

    row = match.iloc[0]
    eligible = bool(row['send_prompt'])
    raw_mssd = row['observed_mssd']
    observed_mssd = None if pd.isna(raw_mssd) else float(raw_mssd)
    trigger_reason = str(row['decision_reason'])
    trigger_signal = None

    arm_p = None
    arm_draw = None
    message_arm = None

    if eligible:
        draw = random.uniform(0, 1)
        send_prompt = draw < p
        # eligible_prompt_ids is recorded at every eligible decision point,
        # sent or not — it's an MRT analysis field, not just bookkeeping for
        # what got delivered.
        selected_prompt_id, eligible_ids = select_prompt(latest_new_ema)

        if send_prompt:
            # Second-stage draw, confirmed by Dr. Chang 2026-08-25: 0.5/0.5
            # coping vs. active control, logged separately from the send
            # draw above so the two effects can be analyzed independently.
            arm_p = float(os.environ.get('JITAI_ARM_RANDOMIZATION_PROBABILITY', '0.5'))
            arm_draw = random.uniform(0, 1)
            message_arm = 'coping' if arm_draw < arm_p else 'control'
            if message_arm == 'control':
                selected_prompt_id, eligible_ids = select_control_prompt()
            if not selected_prompt_id:
                send_prompt = False
    else:
        draw = None
        send_prompt = False
        selected_prompt_id = ''
        eligible_ids = None

    recent_hr = HeartRateSample.objects.filter(user=user).order_by('-timestamp').first()
    recent_stress = StressSample.objects.filter(user=user).order_by('-timestamp').first()

    _snap = {
        r.sub_item_id: r.value_numeric
        for r in latest_new_ema.item_responses.filter(sub_item_id__in=SIGNAL_SUB_ITEMS.values())
    }

    try:
        jitai_log, created = JITAILog.objects.get_or_create(
            decision_point_id=decision_point_id,
            defaults={
                'user': user,
                'prompt_id': selected_prompt_id if send_prompt else '',
                'trigger_reason': trigger_reason,
                'hr_at_trigger': recent_hr.bpm if recent_hr else None,
                'stress_at_trigger': recent_stress.stress_score if recent_stress else None,
                'ema': latest_new_ema,
                'observed_mssd': observed_mssd,
                'randomization_probability': p,
                'randomization_draw': draw,
                'message_arm': message_arm,
                'arm_randomization_probability': arm_p,
                'arm_randomization_draw': arm_draw,
                'send_prompt': send_prompt,
                'status': 'pending' if send_prompt else 'not_sent',
                'delivery_status': 'pending' if send_prompt else 'not_sent',
                'trigger_signal': trigger_signal,
                'ema_mood': _snap.get(SIGNAL_SUB_ITEMS['mood']),
                'ema_stress': _snap.get(SIGNAL_SUB_ITEMS['stress']),
                'ema_energy': _snap.get(SIGNAL_SUB_ITEMS['energy']),
                'eligible_prompt_ids': eligible_ids,
            },
        )
    except IntegrityError:
        logger.warning(
            "decision_point_id=%s already exists (race) — skipping user_id=%s",
            decision_point_id, user.user_id,
        )
        return

    if not created:
        return

    if send_prompt:
        if user.push_token:
            send_jitai_prompt(user, jitai_log)
        else:
            logger.warning(
                "send_prompt=True but no push token for user_id=%s — skipping send",
                user.user_id,
            )
            mark_delivery_failed(jitai_log, 'missing push token')


def _scheduled_slot_bounds(participant_date):
    """The SCHEDULED_CHECK_IN_DAILY_CAP fixed time slots for one Eastern
    calendar day, evenly spaced across the notification window (e.g. 6 slots
    across 9am-9pm land ~2 hours apart, per Dr. Chang 2026-08-21)."""
    window_start = datetime.combine(participant_date, time(NOTIFICATION_WINDOW_START_HOUR), tzinfo=PARTICIPANT_TZ)
    window_end = datetime.combine(participant_date, time(NOTIFICATION_WINDOW_END_HOUR), tzinfo=PARTICIPANT_TZ)
    slot_length = (window_end - window_start) / SCHEDULED_CHECK_IN_DAILY_CAP
    return [
        (window_start + i * slot_length, window_start + (i + 1) * slot_length)
        for i in range(SCHEDULED_CHECK_IN_DAILY_CAP)
    ]


@shared_task
def send_checkin_reminders():
    now = django_timezone.now()
    participant_hour = now.astimezone(PARTICIPANT_TZ).hour
    if not (NOTIFICATION_WINDOW_START_HOUR <= participant_hour < NOTIFICATION_WINDOW_END_HOUR):
        return

    enrolled_users = User.objects.filter(
        is_enrolled=True,
        wearabledevice__is_active=True,
    )

    for user in enrolled_users:
        try:
            _maybe_send_reminder(user, now)
        except Exception:
            logger.exception(
                "send_checkin_reminders failed for user_id=%s", user.user_id
            )


def _maybe_send_reminder(user, now):
    if not user.push_token:
        return

    # No reminder within 30 min of an intervention prompt ("one buzz at a
    # time") — the 2-hour active-outcome-window check below is a superset
    # of that 30-minute guard.
    if _latest_active_jitai(user) is not None:
        return

    participant_now = now.astimezone(PARTICIPANT_TZ)
    slots = _scheduled_slot_bounds(participant_now.date())

    day_start = datetime.combine(participant_now.date(), time.min, tzinfo=PARTICIPANT_TZ)
    day_end = day_start + timedelta(days=1)
    completed_at = list(
        EMA.objects.filter(
            user=user, ema_type='scheduled_check_in', status='completed',
            sent_at__gte=day_start, sent_at__lt=day_end,
        ).values_list('sent_at', flat=True)
    )

    for slot_index, (slot_start, slot_end) in enumerate(slots):
        reminder_ready_at = slot_start + timedelta(minutes=CHECKIN_REMINDER_DELAY_MINUTES)
        if not (reminder_ready_at <= now < slot_end):
            continue  # not yet due for this slot, or the slot has already lapsed

        if any(slot_start <= t.astimezone(PARTICIPANT_TZ) < slot_end for t in completed_at):
            continue  # this slot was already completed — no reminder needed

        already_reminded = CheckinReminder.objects.filter(
            user=user, sent_at__gte=day_start, daily_count_at_send=slot_index,
        ).exists()
        if already_reminded:
            continue  # one reminder per check-in, then let it lapse

        if send_checkin_reminder(user):
            CheckinReminder.objects.create(user=user, daily_count_at_send=slot_index)
        return  # one buzz at a time per tick
