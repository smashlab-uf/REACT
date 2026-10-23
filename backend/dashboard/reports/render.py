"""Turn a report context from build.py into one self-contained HTML page."""

from django.template.loader import render_to_string

from dashboard.reports import svg

TEMPLATES = {
    'daily': 'dashboard/reports/daily.html',
    'weekly': 'dashboard/reports/weekly.html',
}

COMPOSE_METRICS = (
    ('opened', 'Compose opened (share of windows)'),
    ('drafts', 'Drafts started'),
    ('submitted', 'Drafts submitted'),
    ('abandoned', 'Drafts abandoned'),
    ('keystrokes', 'Characters typed'),
    ('visit_seconds', 'Seconds in compose'),
    ('early_drafts', 'Drafts started, 0–60 min'),
    ('late_drafts', 'Drafts started, 60–120 min'),
)
STRIP_METRIC = 'drafts'
GAME_DAY_STRIP_ITEM = 'B1_valence'

STANDING_ACTIONS = (
    'Verify outcome-window records in both arms (sent and randomized out).',
    'Confirm reminder delivery and time-to-completion.',
    'Review check-in completion trends for attrition signals.',
    'Cross-check distress overrides against the staff contact log.',
    'Label any enrolled account without an RS or ST study ID.',
)


def _trend_rows(context):
    rows = []
    for entry in context['benchmarks']:
        name = entry['name']
        days = [(day['date'], day['benchmarks'][name]) for day in context['trend']]
        rows.append({
            'label': entry['label'],
            'provisional': entry.get('provisional'),
            'days': [cell for _, cell in days],
            'spark': svg.sparkline([(str(date), cell['value']) for date, cell in days]),
        })
    return rows


def _compose_rows(compose):
    by_metric = {metric['metric']: metric for metric in compose['comparison']['metrics']}
    return [{'label': label, **by_metric[name]} for name, label in COMPOSE_METRICS if name in by_metric]


def _actions(context):
    urgent = [
        f"Resolve: {issue['description']} ({issue['scope']})"
        for issue in context['issues'] if issue['severity'] in ('critical', 'high')
    ]
    return urgent + list(STANDING_ACTIONS)


def render_report(context):
    view = dict(context)
    view['benchmark_chart'] = svg.benchmark_intervals(context['benchmarks'])
    view['compose_rows'] = _compose_rows(context['compose'])
    strip = next((m for m in context['compose']['comparison']['metrics']
                  if m['metric'] == STRIP_METRIC), None)
    view['compose_strip'] = svg.difference_strip(strip['difference_by_participant']) if strip else ''
    view['compose_strip_metric'] = dict(COMPOSE_METRICS)[STRIP_METRIC]
    if context['kind'] == 'weekly':
        view['trend_rows'] = _trend_rows(context)
        view['trend_dates'] = [day['date'] for day in context['trend']]
        view['actions'] = _actions(context)
        game_item = next((item for item in context['game_days']['items']
                          if item['item'] == GAME_DAY_STRIP_ITEM), None)
        view['game_day_strip'] = svg.difference_strip(
            game_item['difference_by_participant'], label='Game − non-game day') if game_item else ''
        view['game_day_strip_label'] = game_item['label'] if game_item else ''
    return render_to_string(TEMPLATES[context['kind']], view)
