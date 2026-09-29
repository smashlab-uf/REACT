from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from app.baseline_import import BaselineImportError, import_baseline_flags

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
        'record for that user, so re-running the same export is a no-op. The '
        'same logic backs the "Import baseline export" button in Django Admin.'
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

        try:
            report = import_baseline_flags(
                file_path, id_column=id_column, id_field=id_field, dry_run=dry_run)
        except BaselineImportError as error:
            raise CommandError(str(error))

        if report.missing_screen_columns:
            self.stdout.write(self.style.WARNING(
                f'{len(report.missing_screen_columns)} screening item(s) not found in the '
                f'export; nobody can be flagged on a screen missing its items: '
                f'{report.missing_screen_columns}'
            ))
        if report.incomplete:
            self.stdout.write(self.style.WARNING(
                f'{len(report.incomplete)} row(s) have a blank screening item and cannot be '
                f'fully screened: {report.incomplete}'
            ))

        if dry_run:
            for row in report.planned:
                self.stdout.write(
                    f'  would flag user_id={row["user_id"]} ({row["email"]}): {row["signals"]}')
        count = len(report.planned) if dry_run else len(report.created)
        self.stdout.write(
            ('Would create' if dry_run else 'Created') + f' {count} baseline flag(s). '
            f'{report.skipped_existing} already on record, '
            f'{report.skipped_no_signal} with no positive signal.'
        )
        if report.bad_identifier:
            self.stdout.write(self.style.WARNING(
                f'{len(report.bad_identifier)} flagged row(s) had a missing or unusable '
                f'{id_column!r} and could not be matched to anyone: {report.bad_identifier}'
            ))
        if report.unmatched:
            self.stdout.write(self.style.WARNING(
                f'{len(report.unmatched)} flagged row(s) matched no User.{id_field}: '
                f'{report.unmatched}'
            ))
        if report.ambiguous:
            self.stdout.write(self.style.ERROR(
                f'{len(report.ambiguous)} flagged row(s) matched more than one User.{id_field}, '
                f'skipped rather than guessed: {report.ambiguous}'
            ))
