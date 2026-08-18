import logging
import os
import random

import pandas as pd
from celery import shared_task
from django.db import IntegrityError
from django.db.models import Exists, OuterRef

from app.models import EMA, HeartRateSample, JITAILog, StressSample, User
from app.notification_service import mark_delivery_failed, select_prompt, send_jitai_prompt
from decision_engine.decision_engine import apply_decision_rules, calculate_mssd

logger = logging.getLogger(__name__)


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

    if eligible:
        draw = random.uniform(0, 1)
        send_prompt = draw < p
        selected_prompt_id, eligible_ids = select_prompt(latest_new_ema)
        if send_prompt and not selected_prompt_id:
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
