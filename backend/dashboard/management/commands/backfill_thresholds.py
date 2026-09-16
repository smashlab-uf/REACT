import pandas as pd
from app.models import JITAILog, User
from app.tasks import build_decision_frame
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = (
        'Fill JITAILog.threshold_at_decision on rows written before the engine '
        'started recording it. Replays build_decision_frame, the same code path '
        'the live decisions ran through, and stamps threshold_source as '
        '"reconstructed" so the timeline can tell a recorded comparison from a '
        'replayed one. Never overwrites a value the engine wrote.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Report what would change without writing.')
        parser.add_argument('--user', type=int, default=None,
                            help='Restrict to one user_id.')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        users = User.objects.filter(enrolled_at__isnull=False)
        if options['user'] is not None:
            users = users.filter(pk=options['user'])

        filled = skipped = unmatched = 0

        for user in users:
            pending = list(
                JITAILog.objects
                .filter(user=user, threshold_at_decision__isnull=True)
                .exclude(ema__isnull=True)
                .select_related('ema')
            )
            if not pending:
                continue

            frame = build_decision_frame(user)
            if frame is None:
                unmatched += len(pending)
                continue

            by_timestamp = {
                pd.Timestamp(value): threshold
                for value, threshold in zip(frame['timestamp'], frame['user_threshold'])
            }

            updates = []
            for log in pending:
                threshold = by_timestamp.get(pd.Timestamp(log.ema.sent_at))
                # A NaN threshold is the engine's "not enough within-person
                # history yet", which is a real state and not a missing value.
                # Leaving it null keeps it rendering as a gap rather than a zero.
                if threshold is None or pd.isna(threshold):
                    unmatched += 1
                    continue
                log.threshold_at_decision = float(threshold)
                log.threshold_source = 'reconstructed'
                updates.append(log)

            if updates and not dry_run:
                JITAILog.objects.bulk_update(
                    updates, ['threshold_at_decision', 'threshold_source'])
            filled += len(updates)

        # Rows the engine recorded a real comparison for. An engine-sourced row
        # with a null threshold is the "not enough history yet" state and is
        # counted as unreconstructable, not as already done.
        skipped = JITAILog.objects.filter(
            threshold_source='engine', threshold_at_decision__isnull=False).count()

        if dry_run:
            self.stdout.write(self.style.WARNING('Dry run — no changes written.'))
        self.stdout.write(self.style.SUCCESS(
            f'Thresholds: {filled} reconstructed, {skipped} already recorded by the '
            f'engine, {unmatched} with no reconstructable threshold.'
        ))
