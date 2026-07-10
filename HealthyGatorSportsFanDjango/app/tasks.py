import logging
import os
import random

import pandas as pd
from celery import shared_task
from django.db import IntegrityError
from django.db.models import Exists, OuterRef

from app.models import EMA, HeartRateSample, JITAILog, StressSample, User
from app.notification_service import get_prompt_id, send_jitai_prompt
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

    all_emas = list(
        EMA.objects.filter(
            user=user,
            status='completed',
            mood__isnull=False,
            stress__isnull=False,
            energy__isnull=False,
        )
        .order_by('sent_at')
        .values('sent_at', 'mood', 'stress', 'energy')
    )

    if not all_emas:
        return

    df = pd.DataFrame(all_emas)
    df['user_id'] = user.user_id
    df = df.rename(columns={'sent_at': 'timestamp'})
    df['ema'] = df[['mood', 'stress', 'energy']].mean(axis=1)

    df = calculate_mssd(df, window=3)
    result_df = apply_decision_rules(df)

    match = result_df[result_df['timestamp'] == pd.Timestamp(latest_new_ema.sent_at)]
    if match.empty:
        return

    row = match.iloc[0]
    eligible = bool(row['eligible'])
    raw_mssd = row['observed_mssd']
    observed_mssd = None if pd.isna(raw_mssd) else float(raw_mssd)
    trigger_reason = str(row['decision_reason'])

    if eligible:
        draw = random.uniform(0, 1)
        send_prompt = draw < p
    else:
        draw = None
        send_prompt = False

    recent_hr = HeartRateSample.objects.filter(user=user).order_by('-timestamp').first()
    recent_stress = StressSample.objects.filter(user=user).order_by('-timestamp').first()

    try:
        jitai_log, created = JITAILog.objects.get_or_create(
            decision_point_id=decision_point_id,
            defaults={
                'user': user,
                'prompt_id': get_prompt_id(trigger_reason) if send_prompt else '',
                'trigger_reason': trigger_reason,
                'hr_at_trigger': recent_hr.bpm if recent_hr else None,
                'stress_at_trigger': recent_stress.stress_score if recent_stress else None,
                'ema': latest_new_ema,
                'observed_mssd': observed_mssd,
                'randomization_probability': p,
                'randomization_draw': draw,
                'send_prompt': send_prompt,
                'status': 'pending' if send_prompt else 'not_sent',
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

    if send_prompt and user.push_token:
        send_jitai_prompt(user, jitai_log)
