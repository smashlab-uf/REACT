from app.models import EMA, CheckinReminder, JITAILog, User
from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.contenttypes.models import ContentType
from django.core.management.base import BaseCommand
from django.db.models import Min
from django.utils import timezone as django_timezone

# Django records which fields changed by verbose name, so is_enrolled appears as
# "Is enrolled" inside the change_message JSON.
IS_ENROLLED_MARKER = 'is enrolled'


class Command(BaseCommand):
    help = (
        'Fill User.enrolled_at on participants enrolled before the field was '
        'ever written. Nothing used to set it, so ticking is_enrolled in Django '
        'Admin left it NULL, and the monitoring layer counts every study day '
        'from it. Takes the earliest evidence the participant was in the study, '
        'never a later timestamp, because study_day is measured forward from '
        'this value and a late stamp silently drops the data that came before.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true',
                            help='Report what would change without writing.')

    def handle(self, *args, **options):
        dry_run = options['dry_run']

        # The audit log records which fields changed but never their new value.
        # For someone enrolled now, the most recent is_enrolled toggle is
        # necessarily the one that enrolled them.
        toggles = {}
        for object_id, action_time, change_message in (
            LogEntry.objects
            .filter(content_type=ContentType.objects.get_for_model(User), action_flag=CHANGE)
            .order_by('action_time')
            .values_list('object_id', 'action_time', 'change_message')
        ):
            if IS_ENROLLED_MARKER in (change_message or '').lower():
                toggles[str(object_id)] = action_time

        # Only enrolled participants. Someone with data but no enrollment is not
        # in the study, and stamping them would fabricate the denominator every
        # benchmark divides by.
        candidates = User.objects.filter(is_enrolled=True, enrolled_at__isnull=True)
        filled = 0

        for user in candidates:
            evidence = [
                EMA.objects.filter(user=user).aggregate(at=Min('sent_at'))['at'],
                JITAILog.objects.filter(user=user).aggregate(at=Min('triggered_at'))['at'],
                CheckinReminder.objects.filter(user=user).aggregate(at=Min('sent_at'))['at'],
                toggles.get(str(user.pk)),
            ]
            observed = [moment for moment in evidence if moment is not None]
            # An admin tick can postdate real activity by days: the participant
            # was plainly in the study by the time they submitted their first
            # check-in, whatever the audit log says. Taking the earliest keeps
            # those days inside the study window.
            enrolled_at = min(observed) if observed else django_timezone.now()

            source = 'activity' if observed else 'now (no evidence)'
            self.stdout.write(f'user {user.pk}: -> {enrolled_at.isoformat()} [{source}]')
            if not dry_run:
                User.objects.filter(pk=user.pk).update(enrolled_at=enrolled_at)
            filled += 1

        skipped = User.objects.filter(is_enrolled=True, enrolled_at__isnull=False).count()
        unenrolled = User.objects.filter(is_enrolled=False).count()

        if dry_run:
            self.stdout.write(self.style.WARNING('Dry run — no changes written.'))
        self.stdout.write(self.style.SUCCESS(
            f'Enrollment: {filled} stamped, {skipped} already set, '
            f'{unenrolled} not enrolled and left alone.'
        ))
