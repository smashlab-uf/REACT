from app.models import DistressFlag, JITAILog, User
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = (
        'Fill JITAILog.distress_flag_active on decision rows written before the '
        'column existed. A row is active when a distress flag covered its '
        'decision_made_at: a momentary flag between raised_at and expires_at, or '
        'a suppressing baseline flag raised before the decision and not yet '
        'marked contact-documented. Only ever sets the column to true or false '
        'from the flag history, so re-running gives the same result.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Report what would change without writing.')
        parser.add_argument('--user', type=int, default=None,
                            help='Restrict to one user_id.')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        users = User.objects.filter(distress_flags__isnull=False).distinct()
        if options['user'] is not None:
            users = users.filter(pk=options['user'])

        changed = active_total = 0

        for user in users:
            flags = list(DistressFlag.objects.filter(user=user))
            to_update = []
            for log in JITAILog.objects.filter(user=user).only('id', 'decision_made_at', 'distress_flag_active'):
                active = any(flag.was_active_at(log.decision_made_at) for flag in flags)
                active_total += active
                if active != log.distress_flag_active:
                    log.distress_flag_active = active
                    to_update.append(log)
            changed += len(to_update)
            if to_update and not dry_run:
                JITAILog.objects.bulk_update(to_update, ['distress_flag_active'])

        verb = 'would change' if dry_run else 'changed'
        self.stdout.write(f'{verb} {changed} rows; {active_total} decision rows active under a flag.')
