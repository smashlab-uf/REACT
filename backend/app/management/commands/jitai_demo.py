import os
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from app.models import EMA, JITAILog, User, WearableDevice
from app.tasks import _evaluate_user


SEP = '─' * 62


def _hr(label=''):
    return f'\n{SEP}\n  {label}\n{SEP}' if label else f'\n{SEP}'


class Command(BaseCommand):
    help = 'Demo the JITAI MRT task: log creation, push, and idempotency'

    def add_arguments(self, parser):
        parser.add_argument(
            '--push-token',
            type=str,
            default='',
            help='Expo push token (ExponentPushToken[...]) for live notification demo',
        )
        parser.add_argument(
            '--p',
            type=float,
            default=None,
            help='Override JITAI_RANDOMIZATION_PROBABILITY for this run (default: env var or 0.5)',
        )
        parser.add_argument(
            '--seed-only',
            action='store_true',
            help='Only seed the DB (demo user + baseline EMAs + 1 fresh EMA). Used by demo.sh.',
        )

    def handle(self, *args, **options):
        push_token = options['push_token'].strip()

        if options['p'] is not None:
            os.environ['JITAI_RANDOMIZATION_PROBABILITY'] = str(options['p'])

        p = float(os.environ.get('JITAI_RANDOMIZATION_PROBABILITY', '0.5'))

        self.stdout.write(_hr('REACT — JITAI DEMO'))
        self.stdout.write(f'  p (randomization probability) : {p}')
        self.stdout.write(
            f'  push token                    : '
            f'{push_token if push_token else "(none — push will be skipped)"}'
        )
        self.stdout.write(
            f'\n  Tip: run with --p 0.99 to guarantee send_prompt=True and see the push fire.'
        )

        user = self._seed(push_token)

        if options['seed_only']:
            self.stdout.write(self.style.SUCCESS('\n  Seed complete. Start the Celery worker and run demo.sh to continue.'))
            return

        self._run1(user, p)

    def _seed(self, push_token):
        self.stdout.write(_hr('STEP 1: SEED'))
        self.stdout.write(
            '  Creates a demo participant with 6 stable historical EMAs\n'
            '  (already processed — used only to build the within-person MSSD baseline)\n'
            '  and 1 fresh volatile EMA that is the actual decision point.\n'
        )

        User.objects.filter(email='jitai-demo@react.test').delete()

        user = User.objects.create(
            email='jitai-demo@react.test',
            first_name='Demo',
            last_name='Participant',
            birthdate='2000-01-01',
            gender='other',
            is_enrolled=True,
            push_token=push_token or None,
        )

        WearableDevice.objects.create(
            user=user,
            labfront_participant_id='demo-labfront-001',
            is_active=True,
        )

        base = timezone.now() - timedelta(hours=14)

        for i in range(6):
            e = EMA.objects.create(
                user=user,
                prompt_id='default',
                sent_at=base + timedelta(hours=i * 2),
                responded_at=base + timedelta(hours=i * 2, minutes=5),
                status='completed',
                mood=1, stress=1, energy=1,
            )
            JITAILog.objects.create(
                user=user,
                prompt_id='',
                trigger_reason='below within-person threshold',
                ema=e,
                decision_point_id=f'ema_{e.pk}',
                randomization_probability=0.5,
                randomization_draw=None,
                send_prompt=False,
                status='not_sent',
            )

        fresh_ema = EMA.objects.create(
            user=user,
            prompt_id='default',
            sent_at=base + timedelta(hours=12),
            responded_at=base + timedelta(hours=12, minutes=5),
            status='completed',
            mood=3, stress=3, energy=3,
        )

        self.stdout.write(f'  user_id          : {user.user_id}')
        self.stdout.write(f'  push_token       : {user.push_token or "(none)"}')
        self.stdout.write(f'  history EMAs     : 6  (mood=stress=energy=1, MSSD ≈ 0)')
        self.stdout.write(f'  fresh EMA id     : {fresh_ema.pk}  (mood=stress=energy=1, spike from 5 → sharp MSSD rise)')
        self.stdout.write(f'  decision_point   : ema_{fresh_ema.pk}')

        return user

    def _run1(self, user, p):
        self.stdout.write(_hr('STEP 2: RUN 1 — first evaluation'))
        self.stdout.write(
            '  _evaluate_user finds the fresh EMA, computes MSSD,\n'
            '  performs coin flip, writes jitai_log, attempts push.\n'
        )

        before = JITAILog.objects.filter(user=user).count()
        _evaluate_user(user, p)
        after = JITAILog.objects.filter(user=user).count()

        new_logs = JITAILog.objects.filter(
            user=user,
            trigger_reason__in=['prompt sent', 'below within-person threshold',
                                 'cooldown active', 'daily cap reached',
                                 'insufficient within-person history',
                                 'missing or insufficient EMA data'],
        ).exclude(randomization_draw=None, send_prompt=False).order_by('-triggered_at')

        fresh_log = JITAILog.objects.filter(user=user).order_by('-triggered_at').first()

        if after == before:
            self.stdout.write(self.style.ERROR(
                '  No new row created.\n'
                '  The spike EMA did not exceed the within-person threshold.\n'
                '  This should not happen with the seeded data — check decision_engine.py.'
            ))
            return

        self.stdout.write(self.style.SUCCESS('  Row created.'))
        self._print_log(fresh_log)

        self._run2(user, p, fresh_log)

    def _run2(self, user, p, first_log):
        self.stdout.write(_hr('STEP 3: RUN 2 — repeat call (Celery fires again, no new EMA)'))
        self.stdout.write(
            '  The fresh EMA is now linked to a jitai_log.\n'
            '  _evaluate_user finds no unprocessed EMAs → returns early.\n'
        )

        before = JITAILog.objects.filter(user=user).count()
        _evaluate_user(user, p)
        after = JITAILog.objects.filter(user=user).count()

        self.stdout.write(f'  rows before : {before}')
        self.stdout.write(f'  rows after  : {after}')

        if after == before:
            self.stdout.write(self.style.SUCCESS(
                '  No duplicate. EMA already linked to a jitai_log — early exit.'
            ))
        else:
            self.stdout.write(self.style.ERROR('  Unexpected extra row.'))

        self._run3(user, p)

    def _run3(self, user, p):
        self.stdout.write(_hr('STEP 4: RUN 3 — get_or_create guard (concurrent retry race)'))
        self.stdout.write(
            '  Scenario: Worker A writes the jitai_log row but crashes before\n'
            '  setting the EMA foreign key. The EMA appears unprocessed to Worker B.\n'
            '  Worker B calls _evaluate_user → get_or_create finds the existing row\n'
            '  by decision_point_id → created=False → returns early. No duplicate.\n'
        )

        race_ema = EMA.objects.create(
            user=user,
            prompt_id='default',
            sent_at=timezone.now() - timedelta(minutes=5),
            responded_at=timezone.now() - timedelta(minutes=4),
            status='completed',
            mood=1, stress=1, energy=1,
        )
        dp_id = f'ema_{race_ema.pk}'

        JITAILog.objects.create(
            user=user,
            prompt_id='default',
            trigger_reason='prompt sent',
            decision_point_id=dp_id,
            randomization_probability=p,
            randomization_draw=0.21,
            send_prompt=True,
            status='pending',
        )

        self.stdout.write(f'  Pre-written row : decision_point_id={dp_id}  ema FK=null (simulated crash)')

        before = JITAILog.objects.filter(user=user).count()
        _evaluate_user(user, p)
        after = JITAILog.objects.filter(user=user).count()

        self.stdout.write(f'  rows before     : {before}')
        self.stdout.write(f'  rows after      : {after}')

        if after == before:
            self.stdout.write(self.style.SUCCESS(
                f'  No duplicate. get_or_create found existing row for {dp_id} → returned early.'
            ))
        else:
            self.stdout.write(self.style.ERROR('  Unexpected extra row.'))

        self._summary(user)

    def _summary(self, user):
        self.stdout.write(_hr('SUMMARY'))

        fresh_logs = JITAILog.objects.filter(
            user=user,
        ).exclude(
            trigger_reason='below within-person threshold',
            send_prompt=False,
            randomization_draw=None,
        ).order_by('triggered_at')

        self.stdout.write(f'  Rows created by this demo run : {fresh_logs.count()}')
        for i, log in enumerate(fresh_logs, 1):
            self.stdout.write(f'\n  Row {i}:')
            self._print_log(log, indent='    ')

        self.stdout.write(f'\n  Change p live and run again:')
        self.stdout.write(f'    python3 manage.py jitai_demo --p 0.1   (90% control arm)')
        self.stdout.write(f'    python3 manage.py jitai_demo --p 0.99  (99% intervention arm)')
        self.stdout.write(
            f'\n  Add your push token to see a real notification:\n'
            f'    python3 manage.py jitai_demo --p 0.99 --push-token "ExponentPushToken[...]"\n'
        )

    def _print_log(self, log, indent='  '):
        draw = f'{log.randomization_draw:.4f}' if log.randomization_draw is not None else 'null (ineligible)'
        send = 'yes → push attempted' if log.send_prompt else 'no (control arm or ineligible)'
        self.stdout.write(f'{indent}id                        : {log.pk}')
        self.stdout.write(f'{indent}decision_point_id         : {log.decision_point_id}')
        self.stdout.write(f'{indent}randomization_probability : {log.randomization_probability}')
        self.stdout.write(f'{indent}randomization_draw        : {draw}')
        self.stdout.write(f'{indent}send_prompt               : {send}')
        self.stdout.write(f'{indent}trigger_reason            : {log.trigger_reason}')
        self.stdout.write(f'{indent}observed_mssd             : {log.observed_mssd}')
        self.stdout.write(f'{indent}status                    : {log.status}')
