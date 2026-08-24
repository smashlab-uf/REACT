import csv
from datetime import datetime

from django.core.management.base import BaseCommand, CommandError

from app.models import EventDay

DATE_FORMATS = ['%Y-%m-%d', '%m/%d/%Y', '%m/%d/%y']


def _parse_date(raw):
    raw = raw.strip()
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise ValueError(f'unrecognized date {raw!r} — expected YYYY-MM-DD or MM/DD/YYYY')


class Command(BaseCommand):
    help = (
        'Import UF sporting event dates into EventDay from a CSV with '
        '"date", "sport", and optional "description" columns.'
    )

    def add_arguments(self, parser):
        parser.add_argument('csv_path', type=str)
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Parse and report what would happen without writing to the database.',
        )

    def handle(self, *args, **options):
        path = options['csv_path']
        dry_run = options['dry_run']

        try:
            f = open(path, newline='', encoding='utf-8-sig')
        except OSError as exc:
            raise CommandError(f'could not open {path!r}: {exc}')

        with f:
            reader = csv.DictReader(f)
            if reader.fieldnames is None:
                raise CommandError('CSV has no header row')
            fields = {name.strip().lower(): name for name in reader.fieldnames}
            if 'date' not in fields:
                raise CommandError(f'CSV must have a "date" column — found: {reader.fieldnames}')

            created, updated, skipped = 0, 0, 0
            for line_num, row in enumerate(reader, start=2):
                raw_date = (row.get(fields['date']) or '').strip()
                if not raw_date:
                    skipped += 1
                    continue
                try:
                    event_date = _parse_date(raw_date)
                except ValueError as exc:
                    self.stderr.write(f'line {line_num}: skipping — {exc}')
                    skipped += 1
                    continue

                sport = (row.get(fields.get('sport', '')) or '').strip()
                description = (row.get(fields.get('description', '')) or '').strip()

                if dry_run:
                    self.stdout.write(f'{event_date}  {sport or "(no sport)"}  {description}')
                    continue

                _, was_created = EventDay.objects.update_or_create(
                    date=event_date,
                    defaults={'sport': sport, 'description': description},
                )
                if was_created:
                    created += 1
                else:
                    updated += 1

        if dry_run:
            self.stdout.write(self.style.WARNING('Dry run — no changes written.'))
            return

        self.stdout.write(self.style.SUCCESS(
            f'Imported: {created} created, {updated} updated, {skipped} skipped.'
        ))
