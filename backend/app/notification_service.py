import logging
import os

from exponent_server_sdk import (
    DeviceNotRegisteredError,
    PushClient,
    PushMessage,
    PushServerError,
    PushTicketError,
)

logger = logging.getLogger(__name__)
EXPO_PUSH_TOKEN_PREFIXES = ('ExponentPushToken[', 'ExpoPushToken[')

# Maps trigger reasons (from decision engine) to prompt_id keys in the React
# Native app's local template store. The mobile app looks up the message text
# by prompt_id — text never reaches the backend (IRB constraint).
# Add entries here as the prompt library grows; unknown reasons fall back to
# 'default'.
PROMPT_LIBRARY = {
    'default': os.environ.get('JITAI_DEFAULT_PROMPT_ID', 'default'),
    # trigger_reason is always one of these strings from apply_decision_rules():
    #   "prompt sent"                     ← only value when send_prompt=True
    #   "below within-person threshold"   ← not sent; logged only
    #   "insufficient within-person history"
    #   "missing or insufficient EMA data"
    #   "cooldown active"
    #   "daily cap reached"
    # Once Prof. Chang defines distinct prompt templates, add entries keyed on
    # "prompt sent" (or a richer signal string constructed in tasks._evaluate_user).
}


def get_prompt_id(trigger_reason: str) -> str:
    return PROMPT_LIBRARY.get(trigger_reason, PROMPT_LIBRARY['default'])


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
