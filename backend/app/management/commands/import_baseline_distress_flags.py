import sys
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from app.models import DistressFlag, User

ID_FIELD_CHOICES = ('email', 'user_id')


class Command(BaseCommand):
    help = (
        'Score a Qualtrics baseline export for the Section 11 safety signals '
        '(analytics/baseline_survey_scoring/section11.py) and write a '
        'DistressFlag(source="baseline") for every participant who screens '
        'positive on at least one. Never touches the momentary path or '
        'free_text_risk -- there is no free-text column in this pipeline, so '
        'a disclosure is still a staff call, made and recorded outside this '
        'command. Skips a row whose exact signal set already has a flag on '
        'record for that user, so re-running the same export is a no-op.'
    )

    def add_arguments(self, parser):
        parser.add_argument('--file', required=True, type=Path,
                            help='Path to the Qualtrics export (.csv or .xlsx).')
        parser.add_argument('--id-column', default='participant_id',
                            help="Column in the export identifying the participant. "
                                 "Defaults to 'participant_id'.")
        parser.add_argument('--id-field', default='user_id', choices=ID_FIELD_CHOICES,
                            help="User field --id-column values are matched against. "
                                 "Defaults to 'user_id' (confirmed: participant_id maps "
                                 "to User.user_id).")
        parser.add_argument('--dry-run', action='store_true',
                            help='Report what would be created without writing.')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        id_column = options['id_column']
        id_field = options['id_field']
        file_path = options['file']

        if not file_path.exists():
            raise CommandError(f'{file_path} does not exist')

        analytics_dir = str(settings.REPO_ROOT / 'analytics' / 'baseline_survey_scoring')
        if analytics_dir not in sys.path:
            sys.path.insert(0, analytics_dir)
        import qualtrics
        import scoring
        from section11 import section11_signals

        frame = qualtrics.load_export(file_path)
        if id_column not in frame.columns:
            raise CommandError(
                f'--id-column {id_column!r} not found in the export. '
                f'Columns present: {sorted(frame.columns)}'
            )

        scores, _ = scoring.score_participants(frame)
        signals_by_row = section11_signals(scores)

        created = skipped_existing = skipped_no_signal = 0
        unmatched, ambiguous, bad_identifier = [], [], []

        for idx in frame.index:
            codes = signals_by_row.loc[idx]
            if not codes:
                skipped_no_signal += 1
                continue

            identifier = frame.loc[idx, id_column]
            if identifier is None or (isinstance(identifier, float) and identifier != identifier):
                bad_identifier.append((idx, codes))
                continue

            lookup_value = identifier
            if id_field == 'user_id':
                try:
                    lookup_value = int(identifier)
                except (TypeError, ValueError):
                    try:
                        as_float = float(identifier)
                    except (TypeError, ValueError):
                        bad_identifier.append((identifier, codes))
                        continue
                    if not as_float.is_integer():
                        bad_identifier.append((identifier, codes))
                        continue
                    lookup_value = int(as_float)

            matches = list(User.objects.filter(**{id_field: lookup_value}))
            if len(matches) == 0:
                unmatched.append((identifier, codes))
                continue
            if len(matches) > 1:
                ambiguous.append((identifier, codes))
                continue

            user = matches[0]
            if DistressFlag.objects.filter(user=user, source='baseline', signals=codes).exists():
                skipped_existing += 1
                continue

            if dry_run:
                self.stdout.write(f'  would flag user_id={user.user_id} ({user.email}): {codes}')
            else:
                DistressFlag.objects.create(user=user, source='baseline', signals=codes)
            created += 1

        self.stdout.write(
            ('Would create' if dry_run else 'Created') + f' {created} baseline flag(s). '
            f'{skipped_existing} already on record, {skipped_no_signal} with no positive signal.'
        )
        if bad_identifier:
            self.stdout.write(self.style.WARNING(
                f'{len(bad_identifier)} flagged row(s) had a missing or unusable '
                f'{id_column!r} and could not be matched to anyone: {bad_identifier}'
            ))
        if unmatched:
            self.stdout.write(self.style.WARNING(
                f'{len(unmatched)} flagged row(s) matched no User.{id_field}: {unmatched}'
            ))
        if ambiguous:
            self.stdout.write(self.style.ERROR(
                f'{len(ambiguous)} flagged row(s) matched more than one User.{id_field}, '
                f'skipped rather than guessed: {ambiguous}'
            ))
