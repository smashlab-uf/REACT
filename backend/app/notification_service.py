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

from app.ema_catalog import ROUTING_SUB_ITEM_IDS, ROUTING_TRIGGER_RULES

logger = logging.getLogger(__name__)
EXPO_PUSH_TOKEN_PREFIXES = ('ExponentPushToken[', 'ExpoPushToken[')

# Finalized message bank (Eliana Bacal REACT_IRB01_Message_Bank_v1.docx,
# delivered 2026-09-06), columns: ID, Trigger, Technique, Message, Length,
# Context. Replaces the old REACTprompts.csv schema entirely — IDs now match
# the real IRB numbering.
_CATALOG_PATH = Path(__file__).parent / 'data' / 'REACTprompts.csv'


def _load_catalog(path: Path) -> list[dict]:
    catalog = []
    with open(path, newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            pid = row['ID'].strip()
            if not pid:
                continue
            catalog.append({
                'id': pid,
                'trigger': row['Trigger (emotional state)'].strip(),
                'technique': row['Technique'].strip(),
                'length': row['Length'].strip(),
                'context': row['Context'].strip().lower(),
            })
    return catalog


_CATALOG = _load_catalog(_CATALOG_PATH)
_CONTROL_CATALOG = [p for p in _CATALOG if p['id'].startswith('C')]
# Reachable coping pool: General context only — Cyber-specific messages stay
# parked pending Celia's telemetry work, confirmed by Dr. Chang 2026-08-25/27.
_EMA_CATALOG = [p for p in _CATALOG if p['context'] == 'general' and not p['id'].startswith('C')]
_CATALOG_BY_TRIGGER: dict[str, list[str]] = {}
for _p in _EMA_CATALOG:
    _CATALOG_BY_TRIGGER.setdefault(_p['trigger'], []).append(_p['id'])

# General fallback pool, curated by Eliana Bacal, delivered 2026-09-09 —
# closes the hard gate in Papers/REACT_Routing_Rules_v1.1_2026-09-08.docx §7.
# State-neutral subset of the General-context bank: messages that don't name
# or require a specific emotion, so they're safe when nothing matched or a
# matched category was exhausted. P017 stays excluded even though it's
# General context, since "It is okay to feel this way..." presumes distress.
_GENERAL_FALLBACK_PROMPT_IDS = {'P014', 'P016', 'P019', 'P021', 'P024'}
_GENERAL_FALLBACK_IDS = [p['id'] for p in _EMA_CATALOG if p['id'] in _GENERAL_FALLBACK_PROMPT_IDS]

# Alcohol severity override, confirmed by Dr. Chang 2026-09-08
# (Papers/REACT_Routing_Rules_v1.1_2026-09-08.docx §2). B6_drink_count's
# recall window is "today" (per B6_consumed wording), so the NIAAA
# daily-limit definition applies. No NIAAA reference value exists for
# participants who select 'other' for gender — defaults to the lower,
# more-readily-triggered threshold as an interim choice, not confirmed by
# Dr. Chang; flag if this needs revisiting.
ALCOHOL_SEVERITY_SUB_ITEM_ID = 'B6_drink_count'
ALCOHOL_SEVERITY_THRESHOLDS = {'female': 3, 'male': 4, 'other': 3}
ALCOHOL_SEVERITY_PROMPT_ID = 'P020'


def _extract_value(response):
    if response.value_numeric is not None:
        return response.value_numeric
    if response.value_choices is not None:
        return response.value_choices
    return response.value_choice


def _matched_trigger_categories(evaluated_items: dict) -> list[str]:
    matched = []
    for category, groups in ROUTING_TRIGGER_RULES.items():
        for group in groups:
            if all(comparator(evaluated_items.get(sub_item_id), threshold) for sub_item_id, comparator, threshold in group):
                matched.append(category)
                break
    return matched


def select_prompt(ema, exclude_prompt_ids=None) -> dict:
    """Route to a coping message by matching the triggering check-in's
    answers against ROUTING_TRIGGER_RULES (confirmed by Dr. Chang 2026-09-07).

    Selection is category-first: draw a matched category uniformly, then a
    message uniformly within it — not a flat union across all matched
    messages, which would over-weight categories with more messages.

    If the drawn category has no eligible messages left after the last-5
    exclusion, redraw among the remaining matched categories (per Dr. Chang
    2026-09-08 — the exclusion is never skipped, even for single-message
    categories). If no matched category has anything eligible — including
    the case where nothing matched at all — fall through to the general
    pool (see _GENERAL_FALLBACK_IDS above), logging 'category_exhausted' or
    'no_category_matched' respectively.
    """
    exclude_prompt_ids = set(exclude_prompt_ids or [])

    responses = {}
    if ema is not None:
        responses = {
            r.sub_item_id: _extract_value(r)
            for r in ema.item_responses.filter(sub_item_id__in=ROUTING_SUB_ITEM_IDS)
        }
    evaluated_items = {sub_item_id: responses.get(sub_item_id) for sub_item_id in ROUTING_SUB_ITEM_IDS}
    matched_categories = _matched_trigger_categories(evaluated_items)

    result = {
        'prompt_id': '',
        'eligible_prompt_ids': [],
        'evaluated_items': evaluated_items,
        'matched_categories': matched_categories,
        'category_drawn': None,
        'fallback_reason': '',
    }

    # Alcohol severity override takes priority over normal category routing:
    # someone past the daily NIAAA limit isn't going to engage with a
    # reflective prompt, per Dr. Chang 2026-09-08. Skipped if P020 was
    # itself recently delivered — that interaction with the last-5 exclusion
    # wasn't addressed in his ruling, so this falls through to normal
    # routing rather than repeating it.
    if ema is not None and ALCOHOL_SEVERITY_PROMPT_ID not in exclude_prompt_ids:
        drink_count_response = ema.item_responses.filter(sub_item_id=ALCOHOL_SEVERITY_SUB_ITEM_ID).first()
        if drink_count_response is not None and drink_count_response.value_numeric is not None:
            gender = getattr(ema.user, 'gender', None)
            threshold = ALCOHOL_SEVERITY_THRESHOLDS.get(gender, ALCOHOL_SEVERITY_THRESHOLDS['other'])
            if drink_count_response.value_numeric > threshold:
                result['category_drawn'] = 'Urge / craving'
                result['eligible_prompt_ids'] = [ALCOHOL_SEVERITY_PROMPT_ID]
                result['prompt_id'] = ALCOHOL_SEVERITY_PROMPT_ID
                result['fallback_reason'] = 'closest_fit'
                return result

    remaining = list(matched_categories)
    while remaining:
        category = remaining.pop(random.randrange(len(remaining)))
        pool = [pid for pid in _CATALOG_BY_TRIGGER.get(category, []) if pid not in exclude_prompt_ids]
        if pool:
            result['category_drawn'] = category
            result['eligible_prompt_ids'] = pool
            result['prompt_id'] = random.choice(pool)
            return result

    result['fallback_reason'] = 'category_exhausted' if matched_categories else 'no_category_matched'
    fallback_pool = [pid for pid in _GENERAL_FALLBACK_IDS if pid not in exclude_prompt_ids]
    if not fallback_pool:
        logger.error(
            "select_prompt: general fallback pool exhausted or empty (fallback_reason=%s) — "
            "refusing to send",
            result['fallback_reason'],
        )
        return result

    result['eligible_prompt_ids'] = fallback_pool
    result['prompt_id'] = random.choice(fallback_pool)
    return result


def select_control_prompt() -> tuple[str, list[str]]:
    eligible = [p['id'] for p in _CONTROL_CATALOG]
    if not eligible:
        logger.error(
            "select_control_prompt: active-control message pool is empty — "
            "pending Eliana's reconciled catalog (C001-C004)"
        )
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
    # A worker may have loaded this user before the device logged out.
    user.refresh_from_db(fields=['push_token'])
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


def build_checkin_reminder_message(user) -> PushMessage:
    # Unlike the JITAI push, this carries a visible title/body — it's a
    # neutral scheduling nudge, not intervention content, so it isn't
    # subject to the silent-push/no-message-text IRB constraint.
    return PushMessage(
        to=user.push_token,
        title='REACT',
        body='Time for your check-in.',
        data={'type': 'checkin_reminder'},
    )


def send_checkin_reminder(user) -> bool:
    user.refresh_from_db(fields=['push_token'])
    if not is_valid_expo_push_token(user.push_token):
        if user.push_token:
            logger.warning(
                "Invalid Expo push token for user_id=%s — clearing push_token",
                user.user_id,
            )
            user.push_token = None
            user.save(update_fields=['push_token'])
        return False

    message = build_checkin_reminder_message(user)
    try:
        response = PushClient().publish(message)
        response.validate_response()
        logger.info("Check-in reminder sent: user_id=%s", user.user_id)
        return True
    except DeviceNotRegisteredError:
        logger.warning(
            "Expo: device not registered for user_id=%s — clearing push_token",
            user.user_id,
        )
        user.push_token = None
        user.save(update_fields=['push_token'])
        return False
    except Exception as exc:
        logger.warning("Check-in reminder push failed for user_id=%s: %s", user.user_id, exc)
        return False
