#!/usr/bin/env python3
"""
REACT dress rehearsal.

Runs nine synthetic participants (p01-p09, not real people) through several
continuous simulated days with multiple decision points a day, calling the
real evaluate_jitai_triggers path
(_evaluate_user) and the real /jitai/receipt/ endpoint for every push that goes
out -- no live Celery beat, no real Expo network calls (PushClient is patched
to a local stand-in so nothing leaves the machine), no Heroku.

Exercises, on purpose:
  - cooldown (60 min)               -> p06, bursty clustered slots
  - daily cap (4/day)                -> p05, dense volatile slots
  - missing check-in / non-response  -> p09, silent for two full days
  - missing push token                -> p03
  - invalid push token                -> p04
  - calm/no-op participant            -> p07, near-flat answers

At the end, computes a feasibility table straight from JITAILog + EMA and
prints it. See Resources/REACT_Dress_Rehearsal_2026-07-28.docx for the write-up.

Run: cd backend && python3 dress_rehearsal.py [--days N] [--reset]
"""
import argparse
import os
import random
import sys
from datetime import timedelta
from unittest.mock import MagicMock, patch

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'project.settings')
import django  # noqa: E402
django.setup()  # noqa: E402

import pandas as pd  # noqa: E402
from django.conf import settings as django_settings  # noqa: E402
from django.contrib.auth.models import User as AuthUser  # noqa: E402
from django.utils import timezone  # noqa: E402

if 'testserver' not in django_settings.ALLOWED_HOSTS:
    django_settings.ALLOWED_HOSTS.append('testserver')
from rest_framework.test import APIClient  # noqa: E402
from rest_framework_simplejwt.tokens import RefreshToken  # noqa: E402

from app.models import EMA, HeartRateSample, JITAILog, StressSample, User, WearableDevice  # noqa: E402
from app.tasks import _evaluate_user  # noqa: E402

EMAIL_DOMAIN = 'dress-rehearsal.react.test'
SEED = 20260728

PARTICIPANTS = [
    dict(name='p01', persona='regular', token='ExponentPushToken[p01_ok]', silent_days=set()),
    dict(name='p02', persona='regular', token='ExponentPushToken[p02_ok]', silent_days=set()),
    dict(name='p03', persona='regular', token=None, silent_days=set()),
    dict(name='p04', persona='regular', token='not-a-real-expo-token', silent_days=set()),
    dict(name='p05', persona='spiky', token='ExponentPushToken[p05_ok]', silent_days=set()),
    dict(name='p06', persona='bursty', token='ExponentPushToken[p06_ok]', silent_days=set()),
    dict(name='p07', persona='flat', token='ExponentPushToken[p07_ok]', silent_days=set()),
    dict(name='p08', persona='regular', token='ExponentPushToken[p08_ok]', silent_days=set()),
    dict(name='p09', persona='regular', token='ExponentPushToken[p09_ok]', silent_days={2, 3}),
]

STABLE = (4, 4, 4)
LOW = (1, 1, 1)
HIGH = (7, 7, 7)
MILD_LOW = (3, 3, 3)
MILD_HIGH = (5, 5, 5)

SCHEDULES = {
    # (hour, minute) offsets within the simulated day
    'regular': [(9, 0), (13, 0), (17, 0), (21, 0)],
    'spiky': [(8, 0), (9, 20), (10, 40), (12, 0), (14, 0), (15, 20), (16, 40), (18, 0)],
    'bursty': [(9, 0), (9, 20), (9, 40), (10, 0), (15, 0), (15, 20)],
    'flat': [(9, 0), (13, 0), (17, 0), (21, 0)],
}


def make_pseudo_participant(name, token):
    email = f'{name}@{EMAIL_DOMAIN}'
    User.objects.filter(email=email).delete()
    user = User(email=email, first_name=name.capitalize(), last_name='DressRehearsal',
                birthdate='1995-01-01', gender='other', push_token=token,
                is_enrolled=True, enrolled_at=timezone.now())
    user.set_password('dress-rehearsal')
    user.save()
    WearableDevice.objects.create(
        user=user, labfront_participant_id=f'dr-{name}', is_active=True,
        last_synced_at=timezone.now(),
    )
    return user


def reset_participants():
    User.objects.filter(email__endswith=f'@{EMAIL_DOMAIN}').delete()


def authenticated_client(app_user):
    auth_user, _ = AuthUser.objects.get_or_create(
        username=app_user.email, defaults={'email': app_user.email},
    )
    client = APIClient()
    refresh = RefreshToken.for_user(auth_user)
    client.credentials(HTTP_AUTHORIZATION=f'Bearer {str(refresh.access_token)}')
    return client


def pick_values(persona, day_index, rng):
    if day_index == 0:
        return STABLE
    if persona == 'flat':
        return rng.choice([STABLE, MILD_LOW, MILD_HIGH, STABLE, STABLE])
    if persona == 'spiky':
        return rng.choice([LOW, HIGH])
    if persona == 'bursty':
        return rng.choice([LOW, HIGH, STABLE])
    return rng.choice([STABLE, STABLE, LOW, HIGH, MILD_LOW])


def run_simulation(days):
    rng = random.Random(SEED)
    random.seed(SEED)

    sim_start = (timezone.now() - timedelta(days=days)).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )

    users = {}
    for spec in PARTICIPANTS:
        users[spec['name']] = make_pseudo_participant(spec['name'], spec['token'])

    fake_publish_response = MagicMock()
    fake_publish_response.validate_response.return_value = None

    events = []  # bookkeeping rows for the feasibility table

    with patch('app.notification_service.PushClient') as MockPushClient:
        MockPushClient.return_value.publish.return_value = fake_publish_response

        for day_index in range(days):
            day_base = sim_start + timedelta(days=day_index)

            for spec in PARTICIPANTS:
                name = spec['name']
                user = users[name]
                persona = spec['persona']

                if day_index in spec['silent_days']:
                    events.append(dict(
                        participant=name, day=day_index, slot=None,
                        emitted=False, reason='silent_day',
                    ))
                    continue

                for (hour, minute) in SCHEDULES[persona]:
                    slot_time = day_base + timedelta(hours=hour, minutes=minute)
                    mood, stress, energy = pick_values(persona, day_index, rng)

                    ema = EMA.objects.create(
                        user=user, prompt_id='dress_rehearsal_checkin',
                        status='completed', mood=mood, stress=stress, energy=energy,
                    )
                    EMA.objects.filter(pk=ema.pk).update(
                        sent_at=slot_time, responded_at=slot_time + timedelta(seconds=45),
                    )

                    bpm = 62 + (14 if (mood, stress, energy) in (LOW, HIGH) else rng.randint(-3, 3))
                    stress_score = 30 + (45 if (mood, stress, energy) in (LOW, HIGH) else rng.randint(-5, 5))
                    HeartRateSample.objects.create(
                        user=user, timestamp=slot_time, bpm=max(45, min(180, bpm)),
                        source='dress_rehearsal',
                    )
                    StressSample.objects.create(
                        user=user, timestamp=slot_time,
                        stress_score=max(0, min(100, stress_score)),
                        source='dress_rehearsal',
                    )

                    p = 0.85
                    _evaluate_user(user, p)

                    jitai_row = JITAILog.objects.filter(ema=ema).first()
                    if jitai_row is None:
                        events.append(dict(
                            participant=name, day=day_index, slot=slot_time,
                            emitted=False, reason='no_jitai_row',
                        ))
                        continue

                    JITAILog.objects.filter(pk=jitai_row.pk).update(decision_made_at=slot_time)
                    jitai_row.refresh_from_db()

                    delivered = jitai_row.delivery_status == 'accepted_by_expo'
                    device_received_at = None
                    receipt_reported_at = None
                    total_latency_ms = None

                    if delivered:
                        push_sent_at = slot_time + timedelta(seconds=rng.randint(1, 4))
                        device_received_at = push_sent_at + timedelta(seconds=rng.randint(2, 25))
                        JITAILog.objects.filter(pk=jitai_row.pk).update(push_sent_at=push_sent_at)

                        client = authenticated_client(user)
                        resp = client.post('/jitai/receipt/', {
                            'jitai_log_id': jitai_row.pk,
                            'device_received_at': device_received_at.isoformat(),
                            'platform': rng.choice(['ios', 'android']),
                            'app_state': rng.choice(['foreground', 'background']),
                        }, format='json')

                        if resp.status_code == 200:
                            receipt_reported_at = device_received_at + timedelta(seconds=rng.randint(1, 3))
                            JITAILog.objects.filter(pk=jitai_row.pk).update(
                                receipt_reported_at=receipt_reported_at,
                            )
                            total_latency_ms = int(
                                (device_received_at - slot_time).total_seconds() * 1000
                            )

                    events.append(dict(
                        participant=name, day=day_index, slot=slot_time,
                        emitted=True,
                        trigger_reason=jitai_row.trigger_reason,
                        send_prompt=jitai_row.send_prompt,
                        delivery_status=jitai_row.delivery_status,
                        delivered=delivered,
                        total_latency_ms=total_latency_ms,
                    ))

    return users, events, sim_start


def build_feasibility_table(users, events, days):
    ev_df = pd.DataFrame(events)
    rows = []
    for spec in PARTICIPANTS:
        name = spec['name']
        sub = ev_df[ev_df['participant'] == name]
        emitted = sub[sub['emitted'] == True]  # noqa: E712
        scheduled_days = days - len(spec['silent_days'])
        days_with_checkin = emitted['day'].nunique() if not emitted.empty else 0

        decision_points = len(emitted)
        prompts_sent = int(emitted['send_prompt'].sum()) if not emitted.empty else 0
        delivered = int(emitted['delivered'].sum()) if not emitted.empty else 0
        cooldown_blocked = int((emitted['trigger_reason'] == 'cooldown active').sum()) if not emitted.empty else 0
        cap_blocked = int((emitted['trigger_reason'] == 'daily cap reached').sum()) if not emitted.empty else 0
        below_threshold = int((emitted['trigger_reason'] == 'below within-person threshold').sum()) if not emitted.empty else 0
        insufficient = int((emitted['trigger_reason'] == 'insufficient within-person history').sum()) if not emitted.empty else 0
        latencies = emitted['total_latency_ms'].dropna() if not emitted.empty else pd.Series(dtype=float)

        total_slots = sum(1 for e in events if e['participant'] == name)
        completion_rate = (decision_points / total_slots * 100) if total_slots else 0.0
        retention_rate = (days_with_checkin / days * 100) if days else 0.0

        rows.append(dict(
            participant=name,
            persona=spec['persona'],
            sim_days=days,
            silent_days=len(spec['silent_days']),
            days_with_checkin=days_with_checkin,
            retention_pct=round(retention_rate, 1),
            decision_points=decision_points,
            check_in_completion_pct=round(completion_rate, 1),
            prompts_sent=prompts_sent,
            pushes_delivered=delivered,
            delivery_success_pct=round(delivered / prompts_sent * 100, 1) if prompts_sent else None,
            cooldown_blocks=cooldown_blocked,
            daily_cap_blocks=cap_blocked,
            below_threshold=below_threshold,
            insufficient_history=insufficient,
            avg_total_latency_ms=round(latencies.mean(), 0) if not latencies.empty else None,
        ))

    return pd.DataFrame(rows)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--days', type=int, default=5)
    parser.add_argument('--reset', action='store_true', help='Delete pseudo-participants and exit')
    args = parser.parse_args()

    if args.reset:
        reset_participants()
        print('Dress rehearsal pseudo-participants removed.')
        return

    reset_participants()
    users, events, sim_start = run_simulation(args.days)
    table = build_feasibility_table(users, events, args.days)

    pd.set_option('display.width', 220)
    pd.set_option('display.max_columns', 20)

    print(f'\nSimulated {args.days} continuous days starting {sim_start.date()} '
          f'across {len(PARTICIPANTS)} pseudo-participants.\n')
    print(table.to_string(index=False))

    total_dp = table['decision_points'].sum()
    total_cooldown = table['cooldown_blocks'].sum()
    total_cap = table['daily_cap_blocks'].sum()
    silent_participant_days = table['silent_days'].sum()
    missing_token_failures = JITAILog.objects.filter(
        user__email__endswith=f'@{EMAIL_DOMAIN}', delivery_error='missing push token',
    ).count()
    invalid_token_failures = JITAILog.objects.filter(
        user__email__endswith=f'@{EMAIL_DOMAIN}', delivery_error='invalid Expo push token',
    ).count()

    print('\n--- checks ---')
    print(f'total decision points logged      : {total_dp}')
    print(f'cooldown-blocked decision points   : {total_cooldown}  (>0: {"OK" if total_cooldown > 0 else "MISSING"})')
    print(f'daily-cap-blocked decision points  : {total_cap}  (>0: {"OK" if total_cap > 0 else "MISSING"})')
    print(f'silent participant-days simulated  : {silent_participant_days}  (>0: {"OK" if silent_participant_days > 0 else "MISSING"})')
    print(f'missing-push-token failures logged : {missing_token_failures}  (>0: {"OK" if missing_token_failures > 0 else "MISSING"})')
    print(f'invalid-push-token failures logged : {invalid_token_failures}  (>0: {"OK" if invalid_token_failures > 0 else "MISSING"})')

    clean = all([total_cooldown > 0, total_cap > 0, silent_participant_days > 0,
                 missing_token_failures > 0, invalid_token_failures > 0])
    print(f'\nAll target paths exercised and table computed straight from JITAILog/EMA: '
          f'{"YES — clean" if clean else "NO — see MISSING rows above"}')

    csv_path = os.path.join(os.path.dirname(__file__), 'dress_rehearsal_feasibility.csv')
    table.to_csv(csv_path, index=False)
    print(f'\nFeasibility table written to {csv_path}')


if __name__ == '__main__':
    sys.exit(main())
