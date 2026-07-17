import csv
import logging
import random
from pathlib import Path

from django.utils import timezone

from exponent_server_sdk import (
    DeviceNotRegisteredError,
    PushClient,
    PushMessage,
    PushServerError,
    PushTicketError,
)

logger = logging.getLogger(__name__)
EXPO_PUSH_TOKEN_PREFIXES = ('ExponentPushToken[', 'ExpoPushToken[')

_CATALOG_PATH = Path(__file__).parent / 'data' / 'REACTprompts.csv'


def _load_catalog(path: Path) -> list[dict]:
    catalog = []
    with open(path, newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            pid = row['ID'].strip()
            if not pid:
                continue
            if row['Notification Message'].strip().lower() == 'retired':
                continue
            catalog.append({
                'id': pid,
                'goal_type': row['Goal Type'].strip(),
                'ema': row['Trigger Condition'].strip() == 'Survey',
            })
    return catalog


_CATALOG = _load_catalog(_CATALOG_PATH)
_EMA_CATALOG = [p for p in _CATALOG if p['ema']]


def select_prompt(ema) -> tuple[str, list[str]]:
    eligible = [p['id'] for p in _EMA_CATALOG]
    if not eligible:
        logger.error("select_prompt: EMA prompt pool is empty — refusing to fall back to full catalog")
        return '', []
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


def mark_delivery_failed(jitai_log, error: str) -> None:
    jitai_log.status = 'failed'
    jitai_log.delivery_status = 'failed'
    jitai_log.delivery_error = error[:2000]
    jitai_log.save(update_fields=['status', 'delivery_status', 'delivery_error'])


def mark_delivery_accepted(jitai_log) -> None:
    jitai_log.status = 'delivered'
    jitai_log.delivery_status = 'accepted_by_expo'
    jitai_log.push_sent_at = timezone.now()
    jitai_log.delivery_error = ''
    jitai_log.save(
        update_fields=['status', 'delivery_status', 'push_sent_at', 'delivery_error']
    )


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
        mark_delivery_failed(jitai_log, 'missing push token')
        return False

    if not is_valid_expo_push_token(user.push_token):
        logger.warning(
            "Invalid Expo push token for user_id=%s — clearing push_token",
            user.user_id,
        )
        user.push_token = None
        user.save(update_fields=['push_token'])
        mark_delivery_failed(jitai_log, 'invalid Expo push token')
        return False

    message = build_jitai_push_message(user, jitai_log)

    for attempt in range(2):
        try:
            response = PushClient().publish(message)
            response.validate_response()
            mark_delivery_accepted(jitai_log)
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
            mark_delivery_failed(jitai_log, 'device not registered')
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
            mark_delivery_failed(jitai_log, str(exc))
            return False
        except Exception as exc:
            logger.error("Expo push failed for user_id=%s: %s", user.user_id, exc)
            mark_delivery_failed(jitai_log, str(exc))
            return False

    mark_delivery_failed(jitai_log, 'Expo push failed')
    return False
