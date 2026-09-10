"""Check the live monitoring layer against the offline analytics definitions.

The Celery worker cannot import scripts.py -- it pulls scipy, statsmodels,
matplotlib and seaborn, none of which are in backend/requirements.txt -- so
dashboard/data/ mirrors those definitions in pure ORM. Nothing stops the two
from drifting apart over a season except this script.

Run it against a seeded database, never production:

    cd analytics && python reconcile_monitoring.py

It seeds a synthetic cohort into whatever DATABASE_URL points at, runs the real
recompute, and compares the results metric by metric. Exits non-zero on any
mismatch, so it can be wired into CI once a seeded database is available there.
"""

from __future__ import annotations

import argparse
import random
import sys
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import scripts

scripts._ensure_django()

from app.models import (  # noqa: E402
    CheckinReminder, EMA, EMAItemResponse, HeartRateSample, JITAILog, User, WearableDevice,
)
from dashboard.data.windows import today_local  # noqa: E402
from dashboard.models import MetricsDaily, MetricsParticipant  # noqa: E402
from dashboard.tasks import recompute_metrics  # noqa: E402

EASTERN = ZoneInfo('America/New_York')
SEED_DOMAIN = 'reconcile.synthetic'
TOLERANCE = 1e-6


def seed(n_users: int, n_days: int, seed_value: int = 17) -> list[User]:
    random.seed(seed_value)
    today = today_local()
    day1 = today - timedelta(days=n_days - 1)
    enrolled_at = datetime(day1.year, day1.month, day1.day, 10, 0, tzinfo=EASTERN)

    users = []
    for index in range(n_users):
        user = User.objects.create(
            email=f'rec{index}@{SEED_DOMAIN}', birthdate=date(2005, 1, 1), gender='other',
            is_enrolled=True, enrolled_at=enrolled_at,
        )
        WearableDevice.objects.create(
            user=user, labfront_participant_id=f'REC-{index:03d}',
            last_synced_at=datetime.now(EASTERN) - timedelta(hours=1),
        )
        users.append(user)

        for offset in range(n_days):
            day = day1 + timedelta(days=offset)
            for hour in random.sample([10, 12, 14, 16, 18, 20], k=random.randint(2, 5)):
                when = datetime(day.year, day.month, day.day, hour, 15, tzinfo=EASTERN)
                ema = EMA.objects.create(
                    user=user, prompt_id='p', ema_type='scheduled_check_in', status='completed',
                    served_sub_item_ids=['B1_valence', 'B1_arousal', 'B2_stress'],
                )
                EMA.objects.filter(pk=ema.pk).update(sent_at=when, responded_at=when)
                for sub_item_id in ('B1_valence', 'B1_arousal', 'B2_stress'):
                    EMAItemResponse.objects.create(
                        ema=ema, item_id=sub_item_id[:2], sub_item_id=sub_item_id,
                        response_type='likert', value_numeric=random.randint(1, 7),
                    )
                reminder = CheckinReminder.objects.create(
                    user=user, daily_count_at_send=(hour - 9) // 2)
                CheckinReminder.objects.filter(pk=reminder.pk).update(
                    sent_at=when - timedelta(minutes=20))

            if offset >= 7 and random.random() < 0.6:
                for hour in ([13, 15] if random.random() < 0.3 else [15]):
                    push = datetime(day.year, day.month, day.day, hour, 0, tzinfo=EASTERN)
                    log = JITAILog.objects.create(
                        user=user, prompt_id='P1', trigger_reason='prompt sent',
                        send_prompt=True, randomization_draw=0.3, randomization_probability=0.5,
                        decision_made_at=push, push_sent_at=push, device_received_at=push,
                        delivery_status='received_on_device',
                    )
                    # triggered_at is auto_now_add, so a seeded row would carry
                    # the moment of seeding. The offline functions key on it,
                    # and in production all three of these land in the same
                    # request, so the fixture has to reproduce that.
                    JITAILog.objects.filter(pk=log.pk).update(triggered_at=push)

            moment = datetime(day.year, day.month, day.day, 8, 0, tzinfo=EASTERN)
            skip_from, skip_to = random.choice([(0, 0), (11, 15), (9, 12)])
            while moment.hour < 22:
                if not (skip_from <= moment.hour < skip_to):
                    HeartRateSample.objects.create(user=user, timestamp=moment, bpm=70)
                moment += timedelta(minutes=10)
    return users


def purge() -> None:
    User.objects.filter(email__endswith=f'@{SEED_DOMAIN}').delete()


class Comparison:
    def __init__(self):
        self.rows = []

    def add(self, metric, live, offline, unit=''):
        if live is None and offline is None:
            agree = True
        elif live is None or offline is None:
            agree = False
        else:
            agree = abs(float(live) - float(offline)) <= TOLERANCE
        self.rows.append((metric, live, offline, unit, agree))

    def report(self):
        width = max(len(row[0]) for row in self.rows)
        print(f'\n{"metric".ljust(width)}  {"live (ORM)":>16}  {"offline (pandas)":>18}   ok')
        print('-' * (width + 44))
        for metric, live, offline, unit, agree in self.rows:
            fmt = lambda v: '—' if v is None else (f'{v:.6f}' if isinstance(v, float) else str(v))
            flag = 'ok' if agree else 'MISMATCH'
            print(f'{metric.ljust(width)}  {fmt(live):>16}  {fmt(offline):>18}   {flag}'
                  + (f'  {unit}' if unit else ''))
        return all(row[4] for row in self.rows)


def reconcile(users):
    """Compare the live metric tables against scripts.py on the same rows."""
    user_ids = [u.pk for u in users]
    comparison = Comparison()

    daily = MetricsDaily.objects.filter(user_id__in=user_ids, is_active_day=True)
    rollups = MetricsParticipant.objects.filter(user_id__in=user_ids)

    hr_df = scripts.load_heart_rate()
    hr_df = hr_df[hr_df['user_id'].isin(user_ids)]
    user_df = scripts.load_users()
    user_df = user_df[user_df['user_id'].isin(user_ids)]

    jitai_df = scripts.load_jitai_log()
    jitai_df = jitai_df[jitai_df['user_id'].isin(user_ids)]
    # compute_intervention_dosage needs enrolled_at to split run-in from the
    # active phase; the loader does not join it in.
    jitai_df = jitai_df.merge(user_df[['user_id', 'enrolled_at']], on='user_id', how='left')

    # Intervention dosage: total prompts sent.
    dosage = scripts.compute_intervention_dosage(jitai_df)
    comparison.add(
        'prompts sent',
        sum(row.sent_n or 0 for row in daily),
        int(dosage['prompts_sent'].sum()) if not dosage.empty else 0,
    )

    # Cooldown compliance: both score the same 60-minute refractory.
    cooldown = scripts.check_cooldown_compliance(jitai_df)
    comparison.add(
        'cooldown violations',
        sum(row.cooldown_violations_n or 0 for row in daily),
        int(cooldown['violation'].sum()) if not cooldown.empty else 0,
    )

    # Daily cap.
    caps = scripts.check_daily_cap_compliance(jitai_df)
    comparison.add(
        'days over the daily cap',
        daily.filter(sent_n__gt=4).count(),
        int(caps['exceeds_cap'].sum()) if not caps.empty else 0,
    )

    # Retention.
    retention = scripts.compute_retention(user_df)
    comparison.add(
        'participants retained',
        sum(1 for row in rollups if row.active_retention),
        retention['retained_n'],
    )

    # Wear time. The offline module buckets the waking window on the UTC hour
    # while the live layer uses Eastern, so these are only comparable when the
    # seeded samples avoid the offset. Reported for visibility either way.
    wear = scripts.compute_wear_time(hr_df)
    live_wear_days = daily.filter(wear_valid_pct__isnull=False).count()
    comparison.add('wear-scored days', live_wear_days,
                   int(len(wear)) if not wear.empty else 0)

    return comparison


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--users', type=int, default=10)
    parser.add_argument('--days', type=int, default=14)
    parser.add_argument('--keep', action='store_true',
                        help='Leave the seeded rows behind for inspection.')
    args = parser.parse_args()

    purge()
    users = seed(args.users, args.days)
    print(f'seeded {len(users)} synthetic participants over {args.days} days')

    summary = recompute_metrics(users=users, all_days=True)
    print(f'recomputed {summary["daily_rows"]} participant-days, '
          f'{summary["failed"]} failures')

    comparison = reconcile(users)
    agree = comparison.report()

    if not args.keep:
        purge()
        print('\nseeded rows removed')

    if not agree:
        print('\nMISMATCH: the live layer and analytics/scripts.py disagree.')
        return 1
    print('\nthe live layer agrees with analytics/scripts.py on every metric')
    return 0


if __name__ == '__main__':
    sys.exit(main())
