from datetime import timedelta

from django.db.models import Q
from django.utils import timezone

from app.models import DistressFlag
from dashboard.data import config

B1_VALENCE = 'B1_valence'
B2_STRESS = 'B2_stress'
B1_AFFECT_SAD = 'B1_affect_sad'
B1_AFFECT_ANXIOUS = 'B1_affect_anxious'

BASELINE_SIGNALS = (
    ('phq9_self_harm', 'PHQ-9 self-harm item above zero'),
    ('phq9_severity', 'PHQ-9 total moderately severe or severe'),
    ('scoff', 'SCOFF positive'),
    ('audit_c', 'AUDIT-C high risk'),
    ('problem_gambling', 'Problem gambling at or above cutoff'),
    ('food_insecurity', 'Food insecurity item positive'),
    ('free_text_risk', 'Disclosure of risk to self or others'),
)

MOMENTARY_SIGNALS = (
    ('b1_low_valence', 'Check-in valence at the scale floor'),
    ('b2_high_stress', 'Check-in stress at the scale ceiling'),
    ('b1_affect_sad', 'Check-in sadness at the scale ceiling'),
    ('b1_affect_anxious', 'Check-in anxiety at the scale ceiling'),
)

_MOMENTARY_SUB_ITEM_IDS = (B1_VALENCE, B2_STRESS, B1_AFFECT_SAD, B1_AFFECT_ANXIOUS)

RESOURCE_CARD_RESOURCES = [
    {
        'name': 'Campus Counseling Center',
        'description': 'Provides counseling, crisis support, and mental health services for University of Florida students.',
        'contact': '(352) 392-1575',
    },
    {
        'name': '988 Suicide and Crisis Lifeline',
        'description': 'Call or text 988. Provides 24/7 confidential crisis support for people experiencing suicidal thoughts, emotional distress, or a mental health crisis.',
        'contact': '988',
    },
    {
        'name': 'Hitchcock Field & Fork Pantry',
        'description': 'Provides food and basic necessities to members of the UF community experiencing food insecurity.',
        'contact': '(352) 294-3601',
    },
    {
        'name': 'National Alliance for Eating Disorders Helpline',
        'description': 'Provides eating disorder support.',
        'contact': '1-866-662-1235',
    },
    {
        'name': 'SAMHSA National Helpline',
        'description': 'Provides 24/7 treatment referrals and information for mental health and substance-use concerns.',
        'contact': '1-800-662-4357',
    },
    {
        'name': 'National Problem Gambling Helpline',
        'description': 'Also 1-800-MY-RESET.',
        'contact': '1-800-697-3738',
    },
    {
        'name': "Florida's Gambling Helpline",
        'description': 'Also 888-ADMIT-IT. Florida-specific gambling support, treatment referrals, and information.',
        'contact': '888-236-4848',
    },
]


def momentary_signals(ema):
    """Exact-value momentary distress check, confirmed by Dr. Chang 2026-09-22
    (see the cutoff comment in dashboard/data/config.py). A skipped/rotated-out
    item has no row in item_responses at all, so it can't match -- no separate
    guard needed. More than one signal can fire on one check-in (e.g. sad and
    anxious both at ceiling); all firing codes are returned, not deduped.
    """
    values = {
        row.sub_item_id: row.value_numeric
        for row in ema.item_responses.filter(sub_item_id__in=_MOMENTARY_SUB_ITEM_IDS)
    }
    signals = []
    valence = values.get(B1_VALENCE)
    if valence is not None and valence == config.DISTRESS_B1_VALENCE_FLOOR:
        signals.append('b1_low_valence')
    stress = values.get(B2_STRESS)
    if stress is not None and stress == config.DISTRESS_B2_STRESS_CEILING:
        signals.append('b2_high_stress')
    sad = values.get(B1_AFFECT_SAD)
    if sad is not None and sad == config.DISTRESS_B1_AFFECT_CEILING:
        signals.append('b1_affect_sad')
    anxious = values.get(B1_AFFECT_ANXIOUS)
    if anxious is not None and anxious == config.DISTRESS_B1_AFFECT_CEILING:
        signals.append('b1_affect_anxious')
    return signals


def raise_momentary_flag(ema):
    signals = momentary_signals(ema)
    if not signals:
        return None
    raised_at = timezone.now()
    return DistressFlag.objects.create(
        user=ema.user,
        source='momentary',
        signals=signals,
        ema=ema,
        raised_at=raised_at,
        expires_at=raised_at + timedelta(hours=config.DISTRESS_MOMENTARY_PAUSE_HOURS),
    )


def raise_baseline_flag(user, signals):
    return DistressFlag.objects.create(user=user, source='baseline', signals=list(signals))


def active_distress_flag(user, now=None):
    now = now or timezone.now()
    return (
        DistressFlag.objects
        .filter(user=user)
        .filter(
            Q(source='baseline', contact_documented_at__isnull=True)
            | Q(source='momentary', expires_at__gt=now)
        )
        .order_by('source', '-raised_at')
        .first()
    )


def resource_card():
    return {
        'title': 'Support resources',
        'message': (
            'If you or someone else is in immediate danger, call 911 or go to the nearest '
            'emergency department. These resources are available to you at any time.'
        ),
        'resources': RESOURCE_CARD_RESOURCES,
    }
