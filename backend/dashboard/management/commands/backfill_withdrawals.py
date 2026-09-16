from app.models import User
from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand

from dashboard.models import MetricsParticipant

# Django records which fields changed using their verbose names, so the
# is_enrolled flag shows up as "Is enrolled" inside the change_message JSON.
IS_ENROLLED_MARKER = 'is enrolled'


class Command(BaseCommand):
    help = (
        'Backdate MetricsParticipant.first_seen_not_enrolled_at from the admin '
        'audit log. The recompute task can only observe a withdrawal at polling '
        'resolution and only from the moment it starts running, so a withdrawal '
        'that predates monitoring is stamped with the first run that noticed it. '
        'This moves such a timestamp back to when it actually happened; it never '
        'moves one forward.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Report what would change without writing.',
        )

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        # The audit log records which fields changed but never their new value,
        # so a toggle cannot be read as withdrawal or re-enrollment on its own.
        # For a participant who is unenrolled now, the most recent toggle is
        # necessarily the one that unenrolled them.
        entries = (
            LogEntry.objects
            .filter(content_type=ContentType.objects.get_for_model(User), action_flag=CHANGE)
            .order_by('action_time')
            .values_list('object_id', 'action_time', 'change_message')
        )
        last_toggle = {}
        for object_id, action_time, change_message in entries:
            if IS_ENROLLED_MARKER in (change_message or '').lower():
                last_toggle[str(object_id)] = action_time

        if not last_toggle:
            self.stdout.write(self.style.WARNING(
                'No is_enrolled changes found in django_admin_log — nothing to backfill.'
            ))
            return

        candidates = User.objects.filter(is_enrolled=False, enrolled_at__isnull=False)
        filled, skipped, unmatched = 0, 0, 0

        for user in candidates:
            withdrawn_at = last_toggle.get(str(user.pk))
            if withdrawn_at is None:
                unmatched += 1
                continue

            row = MetricsParticipant.objects.filter(user=user).first()
            if row is None:
                unmatched += 1
                continue
            # An earlier observation is always the better one. A stored value at
            # or before the audit time is already at least as good, so leave it.
            current = row.first_seen_not_enrolled_at
            if current is not None and current <= withdrawn_at:
                skipped += 1
                continue

            self.stdout.write(f'user {user.pk}: {current} -> {withdrawn_at}')
            if not dry_run:
                row.first_seen_not_enrolled_at = withdrawn_at
                row.save(update_fields=['first_seen_not_enrolled_at'])
            filled += 1

        if dry_run:
            self.stdout.write(self.style.WARNING('Dry run — no changes written.'))
        self.stdout.write(self.style.SUCCESS(
            f'Backfilled: {filled} filled, {skipped} already set, {unmatched} with no audit entry.'
        ))
