"""Assemble the daily and weekly PI report contexts from the section functions.

The result is a plain dict keyed by the template's section numbers, so the
renderer only lays it out and a test can assert on the numbers directly.
"""

from datetime import timedelta

from django.utils import timezone as django_timezone

from dashboard.reports import sections as s

WEEK_DAYS = 7
STATUS_ORDER = (s.FAIL, s.WATCH, s.SUPPRESSED, s.NO_DATA, s.NOT_MEASURED, s.PASS)


def _worst(statuses):
    statuses = list(statuses)
    for status in STATUS_ORDER:
        if status in statuses:
            return status
    return s.NO_DATA


def _cooldown_checks(cooldown):
    return [
        s.check(f"Cooldown >= {cooldown['cooldown_minutes']} min", cooldown['status'],
                 f"{len(cooldown['violations'])} violation(s) across {cooldown['sent']} prompt(s)"),
        s.check(f"Daily cap <= {cooldown['daily_cap']}", cooldown['cap_status'],
                 f"{len(cooldown['cap_exceeded'])} participant-day(s) over the cap"),
    ]


def _benchmark_checks(benchmarks):
    return [
        s.check(entry['label'], entry['status'],
                 f"{entry['numerator']}/{entry['denominator']} {entry['unit']}")
        for entry in benchmarks if entry['status'] == s.FAIL and not entry.get('provisional')
    ]


def _core(start, end, now, weekly):
    users = list(s.study_participants())
    labels = s.labels_for(users)
    rows = s.decision_rows(users, start, end)
    n_days = (end - start).days + 1

    benchmarks = s.benchmarks(users, start, end)
    integrity = s.randomization_integrity(rows, include_ks=weekly)
    messages = s.message_eligibility(rows)
    outcome = s.outcome_windows(rows, now)
    cooldown = s.cooldown(rows, labels)
    seen = s.prompt_seen(rows, now, labels)
    quality = s.data_quality(users, rows, start, end)

    check_groups = [
        ('Benchmarks', _benchmark_checks(benchmarks)),
        ('Randomization', integrity),
        ('Message eligibility', messages['checks']),
        ('Outcome window', outcome['checks']),
        ('Delivery', seen['checks']),
        ('Cooldown', _cooldown_checks(cooldown)),
        ('Data quality', quality),
    ]
    return {
        'start': start,
        'end': end,
        'generated_at': now,
        'metrics_computed_at': s.metrics_freshness(users),
        'cohort': {'participants': len(users)},
        'status': s.participant_status(),
        'benchmarks': benchmarks,
        'hair_sample': s.hair_sample(),
        'checkins': s.checkins_by_participant(users, start, end),
        'decisions': s.decision_points(rows, n_days=n_days),
        'integrity': integrity,
        'messages': messages,
        'outcome': outcome,
        'cooldown': cooldown,
        'seen': seen,
        'distress': s.distress(users, rows, start, end, now),
        'quality': quality,
        'compose': s.compose(users, rows, start, end, now, with_ci=weekly),
        'issues': s.issues(users, check_groups, labels),
    }


def build_daily(report_date, now=None):
    now = now or django_timezone.now()
    context = _core(report_date, report_date, now, weekly=False)
    context['kind'] = 'daily'
    return context


def _assessment(context):
    areas = {
        'Randomization': _worst(check['status'] for check in context['integrity']),
        'Message eligibility': _worst(check['status'] for check in context['messages']['checks']),
        'Outcome window': _worst(check['status'] for check in context['outcome']['checks']),
        'Cooldown logic': _worst([context['cooldown']['status'], context['cooldown']['cap_status']]),
        'Check-in delivery': next(
            entry['status'] for entry in context['benchmarks'] if entry['name'] == 'slot_coverage'),
        'Reminder system': next(
            check['status'] for check in context['outcome']['checks']
            if check['name'] == 'Reminder delivered'),
    }
    return {
        'working': [area for area, status in areas.items() if status == s.PASS],
        'monitoring': [
            f'{area} ({status.lower()})' for area, status in areas.items()
            if status not in (s.PASS, s.FAIL)],
        'risks': [area for area, status in areas.items() if status == s.FAIL],
        'areas': areas,
    }


def _summary(context):
    decisions = context['decisions']
    by_name = {entry['name']: entry for entry in context['benchmarks']}

    def reading(name):
        entry = by_name[name]
        if entry['value'] is not None:
            text = f"{entry['value']:.0%}"
        elif entry['denominator']:
            text = f"{entry['numerator']}/{entry['denominator']} (rate withheld: under 10 participants or 30 units)"
        else:
            text = 'no data'
        return f'{text} (provisional)' if entry.get('provisional') else text

    critical = [issue for issue in context['issues'] if issue['severity'] == 'critical']
    return (
        f"This week, REACT had {context['status']['active']} active participants and evaluated "
        f"{decisions['evaluated']} decision points ({decisions['available']} randomized, "
        f"{decisions['sent']} prompts sent). Check-in completion was {reading('slot_coverage')} and "
        f"retention {reading('retention')}. "
        + (f"{len(critical)} critical issue(s) need attention." if critical
           else 'No critical protocol deviations were detected.')
    )


def build_weekly(end, now=None, labfront_export=None):
    now = now or django_timezone.now()
    start = end - timedelta(days=WEEK_DAYS - 1)
    context = _core(start, end, now, weekly=True)
    users = list(s.study_participants())
    context.update({
        'kind': 'weekly',
        'flow': s.participant_flow(),
        'trend': s.benchmark_trend(users, start, end),
        'game_days': s.game_days(users, end),
        'garmin': s.garmin_at_checkins(users, start, end, labfront_export),
    })
    context['assessment'] = _assessment(context)
    context['summary'] = _summary(context)
    return context
