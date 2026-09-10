from app.models import User
from django.core.management.base import BaseCommand, CommandError

from dashboard.tasks import recompute_metrics


class Command(BaseCommand):
    help = (
        'Recompute the monitoring layer synchronously. With no arguments this '
        'does what the periodic task does, covering the trailing few days. Use '
        '--all for the initial fill or after a metric definition changes.'
    )

    def add_arguments(self, parser):
        window = parser.add_mutually_exclusive_group()
        window.add_argument('--all', action='store_true',
                            help='Every study day from day 0 to today.')
        window.add_argument('--from-day', type=int,
                            help='Every study day from this one to today.')
        window.add_argument('--days', type=int,
                            help='Trailing N local days (default: the task setting).')
        parser.add_argument('--user', type=int, action='append', dest='users',
                            help='Limit to this user_id. Repeatable.')

    def handle(self, *args, **options):
        users = None
        if options['users']:
            users = list(User.objects.filter(user_id__in=options['users']))
            missing = set(options['users']) - {u.pk for u in users}
            if missing:
                raise CommandError(f'no such user_id: {sorted(missing)}')

        summary = recompute_metrics(
            users=users, days=options['days'],
            from_day=options['from_day'], all_days=options['all'],
        )

        for key, value in summary.items():
            self.stdout.write(f'  {key.replace("_", " "):22s} {value}')
        style = self.style.ERROR if summary['failed'] else self.style.SUCCESS
        self.stdout.write(style(
            f'Recomputed {summary["daily_rows"]} participant-days '
            f'across {summary["participants"]} participants.'
        ))
