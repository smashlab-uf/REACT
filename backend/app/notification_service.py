import logging
import random

from exponent_server_sdk import (
    DeviceNotRegisteredError,
    PushClient,
    PushMessage,
    PushServerError,
    PushTicketError,
)

logger = logging.getLogger(__name__)
EXPO_PUSH_TOKEN_PREFIXES = ('ExponentPushToken[', 'ExpoPushToken[')

# Prompt catalog — Eliana Bacal, 2026-07-09.
# Message text lives in the React Native app's local template store — never here (IRB).
# ema=True: appropriate for EMA-triggered decisions (current system).
# ema=False: telemetry-only (HR/HRV spike) — excluded until Labfront triggers are wired.
_CATALOG = [
    {'id': 'P001', 'state': 'High Arousal / Anger',   'ema': False},
    {'id': 'P002', 'state': 'High Arousal / Anxiety', 'ema': False},
    {'id': 'P003', 'state': 'Interpersonal Conflict',  'ema': False},
    {'id': 'P004', 'state': 'High Arousal / Anxiety', 'ema': True},
    {'id': 'P005', 'state': 'General Stress',          'ema': True},
    {'id': 'P006', 'state': 'Urge / Craving',          'ema': False},
    {'id': 'P007', 'state': 'Interpersonal Conflict',  'ema': False},
    {'id': 'P008', 'state': 'Urge / Craving',          'ema': True},
    {'id': 'P009', 'state': 'High Arousal / Anger',   'ema': False},
    {'id': 'P010', 'state': 'High Arousal / Anger',   'ema': False},
    {'id': 'P011', 'state': 'Low Mood / Withdrawal',  'ema': True},
    {'id': 'P012', 'state': 'General Stress',          'ema': True},
    {'id': 'P013', 'state': 'Urge / Craving',          'ema': True},
    {'id': 'P014', 'state': 'Social Pressure',         'ema': True},
    {'id': 'P015', 'state': 'Urge / Craving',          'ema': True},
    {'id': 'P016', 'state': 'General Stress',          'ema': True},
    {'id': 'P017', 'state': 'Urge / Craving',          'ema': True},
    {'id': 'P018', 'state': 'General Stress',          'ema': True},
    {'id': 'P019', 'state': 'Urge / Craving',          'ema': True},
    {'id': 'P020', 'state': 'General Stress',          'ema': True},
    {'id': 'P021', 'state': 'High Arousal / Anger',   'ema': False},
    {'id': 'P022', 'state': 'High Arousal / Anxiety', 'ema': True},
    {'id': 'P023', 'state': 'Interpersonal Conflict',  'ema': False},
    {'id': 'P024', 'state': 'High Arousal / Anxiety', 'ema': True},
    {'id': 'P025', 'state': 'Low Mood / Withdrawal',  'ema': True},
    {'id': 'P026', 'state': 'General Stress',          'ema': True},
    {'id': 'P027', 'state': 'Interpersonal Conflict',  'ema': False},
    {'id': 'P028', 'state': 'Urge / Craving',          'ema': True},
    {'id': 'P029', 'state': 'High Arousal / Anger',   'ema': False},
    {'id': 'P030', 'state': 'Urge / Craving',          'ema': True},
    {'id': 'P031', 'state': 'High Arousal / Anger',   'ema': False},
    {'id': 'P032', 'state': 'High Arousal / Anxiety', 'ema': True},
    {'id': 'P033', 'state': 'General Stress',          'ema': True},
    {'id': 'P034', 'state': 'Low Mood / Withdrawal',  'ema': True},
    {'id': 'P035', 'state': 'Urge / Craving',          'ema': True},
    {'id': 'P036', 'state': 'Social Pressure',         'ema': True},
    {'id': 'P037', 'state': 'Urge / Craving',          'ema': True},
    {'id': 'P038', 'state': 'Urge / Craving',          'ema': True},
    {'id': 'P039', 'state': 'Urge / Craving',          'ema': True},
    {'id': 'P040', 'state': 'General Stress',          'ema': True},
    {'id': 'P041', 'state': 'Urge / Craving',          'ema': True},
]

_EMA_CATALOG = [p for p in _CATALOG if p['ema']]


def _classify_state(mood: int, stress: int, energy: int) -> str:
    if mood <= 3 and energy <= 3:
        return 'Low Mood / Withdrawal'
    if stress >= 5 and mood <= 3:
        return 'High Arousal / Anxiety'
    if stress >= 5:
        return 'General Stress'
    return 'General Stress'


def select_prompt(ema) -> tuple[str, list[str]]:
    """Return (selected_prompt_id, eligible_prompt_ids) based on EMA scores."""
    if ema.mood is not None and ema.stress is not None and ema.energy is not None:
        state = _classify_state(ema.mood, ema.stress, ema.energy)
        eligible = [p['id'] for p in _EMA_CATALOG if p['state'] == state]
        if not eligible:
            eligible = [p['id'] for p in _EMA_CATALOG]
    else:
        eligible = [p['id'] for p in _EMA_CATALOG]
    return random.choice(eligible), eligible


def is_valid_expo_push_token(push_token: str | None) -> bool:
    if not push_token:
        return False
    return (
        push_token.endswith(']')
        and any(push_token.startswith(prefix) for prefix in EXPO_PUSH_TOKEN_PREFIXES)
    )


def mark_jitai_status(jitai_log, status: str) -> None:
    if jitai_log.status != status:
        jitai_log.status = status
        jitai_log.save(update_fields=['status'])


def build_jitai_push_message(user, jitai_log) -> PushMessage:
    return PushMessage(
        to=user.push_token,
        data={
            'type': 'ema_prompt',
            'prompt_id': jitai_log.prompt_id,
            'jitai_log_id': jitai_log.pk,
        },
    )


def send_jitai_prompt(user, jitai_log) -> bool:
    if not user.push_token:
        logger.warning("No push token for user_id=%s — skipping", user.user_id)
        mark_jitai_status(jitai_log, 'failed')
        return False

    if not is_valid_expo_push_token(user.push_token):
        logger.warning(
            "Invalid Expo push token for user_id=%s — clearing push_token",
            user.user_id,
        )
        user.push_token = None
        user.save(update_fields=['push_token'])
        mark_jitai_status(jitai_log, 'failed')
        return False

    message = build_jitai_push_message(user, jitai_log)

    for attempt in range(2):
        try:
            response = PushClient().publish(message)
            response.validate_response()
            mark_jitai_status(jitai_log, 'delivered')
            logger.info(
                "Expo push sent: user_id=%s prompt_id=%s",
                user.user_id, jitai_log.prompt_id,
            )
            return True
        except DeviceNotRegisteredError:
            logger.warning(
                "Expo: device not registered for user_id=%s — clearing push_token",
                user.user_id,
            )
            user.push_token = None
            user.save(update_fields=['push_token'])
            mark_jitai_status(jitai_log, 'failed')
            return False
        except (PushServerError, PushTicketError) as exc:
            if attempt == 0:
                logger.warning(
                    "Expo push transient error (retrying): user_id=%s %s",
                    user.user_id, exc,
                )
                continue
            logger.error(
                "Expo push failed after retry: user_id=%s %s",
                user.user_id, exc,
            )
        except Exception as exc:
            logger.error("Expo push failed for user_id=%s: %s", user.user_id, exc)
            break

    mark_jitai_status(jitai_log, 'failed')
    return False
