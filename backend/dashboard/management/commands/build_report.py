from datetime import date, timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone as django_timezone

from dashboard.data.windows import participant_time, today_local
from dashboard.reports.build import WEEK_DAYS, build_daily, build_weekly
from dashboard.reports.render import render_report
from dashboard.reports.sections import study_participants
from dashboard.tasks import recompute_metrics

DEFAULT_OUT = Path(settings.BASE_DIR).parent / 'analytics' / 'reports' / 'output'


def _date(value):
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise CommandError(f'not a YYYY-MM-DD date: {value}') from exc


class Command(BaseCommand):
    help = (
        'Write the PI feasibility and compliance report as one self-contained HTML file. '
        '--daily covers one Eastern day (default: yesterday); --weekly covers the 7 days '
        'ending --end (default: yesterday). Only RS study accounts are reported. The file '
        'carries study IDs, so the default output folder is git-ignored.'
    )

    def add_arguments(self, parser):
        kind = parser.add_mutually_exclusive_group(required=True)
        kind.add_argument('--daily', action='store_true', help='One-day report.')
        kind.add_argument('--weekly', action='store_true', help='Seven-day report.')
        parser.add_argument('--date', help='Daily report date, YYYY-MM-DD (Eastern).')
        parser.add_argument('--end', help='Last day of the weekly report, YYYY-MM-DD (Eastern).')
        parser.add_argument('--out', default=str(DEFAULT_OUT),
                            help=f'Output folder (default: {DEFAULT_OUT}).')
        parser.add_argument('--labfront-export', metavar='DIR',
                            help='Weekly only: a Labfront export folder (one subfolder per participant) '
                                 'to line up Garmin heart rate and stress with check-in times. Read only; '
                                 'nothing is written to the database.')
        parser.add_argument('--recompute', action='store_true',
                            help='Refresh the monitoring tables for RS participants over the '
                                 'report period first. This is the same write the periodic '
                                 'task makes, including the cohort snapshot and alerts.')

    def handle(self, *args, **options):
        now = django_timezone.now()
        yesterday = today_local(now) - timedelta(days=1)

        if options['daily']:
            if options['end']:
                raise CommandError('--end applies to --weekly; use --date with --daily.')
            end = _date(options['date']) if options['date'] else yesterday
            start = end
        else:
            if options['date']:
                raise CommandError('--date applies to --daily; use --end with --weekly.')
            end = _date(options['end']) if options['end'] else yesterday
            start = end - timedelta(days=WEEK_DAYS - 1)

        if end > today_local(now):
            raise CommandError(f'{end} is in the future.')

        export_dir = None
        if options['labfront_export']:
            if options['daily']:
                raise CommandError('--labfront-export applies to --weekly only.')
            export_dir = Path(options['labfront_export']).expanduser()
            if not export_dir.is_dir():
                raise CommandError(f'not a folder: {export_dir}')

        if options['recompute']:
            days = (today_local(now) - start).days + 1
            summary = recompute_metrics(users=study_participants(), days=days, now=now)
            style = self.style.ERROR if summary['failed'] else self.style.SUCCESS
            self.stdout.write(style(
                f'Recomputed {summary["daily_rows"]} participant-days across '
                f'{summary["participants"]} participants ({summary["failed"]} failed).'))

        context = (build_daily(end, now=now) if options['daily']
                   else build_weekly(end, now=now, labfront_export=export_dir))
        out_dir = Path(options['out'])
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f'react_{context["kind"]}_{end.isoformat()}.html'
        path.write_text(render_report(context), encoding='utf-8')

        if context['metrics_computed_at'] is None:
            self.stdout.write(self.style.WARNING(
                'No daily metrics have been computed for these participants; benchmark and '
                'check-in sections are empty. Run with --recompute or manage.py recompute_metrics.'))
        if export_dir is not None:
            garmin = context['garmin']
            covered = garmin['coverage_end']
            through = (f'data through {participant_time(covered):%Y-%m-%d %H:%M} Eastern' if covered
                       else 'no heart-rate or stress files for any RS participant')
            self.stdout.write(
                f'Labfront export: {through}; {garmin["counts"].get("aligned", 0)} of '
                f'{len(garmin["checkins"])} check-in(s) aligned.')
            if garmin['unmatched_folders']:
                self.stdout.write(self.style.WARNING(
                    'Export folders with no enrolled RS participant: ' + ', '.join(garmin['unmatched_folders'])))
        self.stdout.write(self.style.SUCCESS(
            f'Wrote {context["kind"]} report for {start} to {end}: {path}'))
