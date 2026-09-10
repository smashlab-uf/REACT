"""Alert rules and their fire-and-resolve lifecycle.

Each rule reports the conditions firing right now. evaluate_alerts() opens what
is newly firing, updates severity and payload on what is still firing, and
resolves what has cleared, so an open alert always describes a live condition
and re-running changes nothing on its own.

Rules read MetricsDaily and MetricsParticipant, not raw tables, with two
deliberate exceptions marked below: the liveness checks. If the pipeline is
dead, the precomputed metrics are stale too, so asking them whether the pipeline
is alive would be circular.
"""

from collections import defaultdict, namedtuple
from datetime import timedelta

from app.models import HeartRateSample, JITAILog, User
from django.db.models import Sum
from django.utils import timezone as django_timezone

from dashboard.data.config import (
    BENCHMARKS,
    DAILY_PROMPT_CAP,
    JITAI_COOLDOWN_MINUTES,
    NOTIFICATION_WINDOW_END_HOUR,
    NOTIFICATION_WINDOW_START_HOUR,
)
from dashboard.data.windows import elapsed, participant_time, today_local
from dashboard.models import Alert, MetricsDaily, MetricsParticipant

# Monitoring policy, distinct from the study protocol constants in config.py.
# These say when a researcher should be told something looks wrong; they are not
# part of the intervention design and need no PI sign-off to tune.
SYNC_STALE_WARN_HOURS = 24
SYNC_STALE_CRITICAL_HOURS = 72
COVERAGE_WINDOW_DAYS = 3
COVERAGE_FLOOR = 0.50
NO_EMA_HOURS = 48
DELIVERY_FAILURE_FLOOR = 2
WEAR_LOW_DAYS = 3
DOSAGE_WINDOW_DAYS = 7
DOSAGE_COLLAPSE_RATIO = 0.25
DOSAGE_MIN_PRIOR = 4
PIPELINE_STALL_HOURS = 6

CRITICAL = 'critical'
WARNING = 'warning'
IN_STUDY_PHASES = ('run_in', 'mrt')

Finding = namedtuple('Finding', 'user severity payload')

RULES = []


def rule(rule_id):
    def register(func):
        func.rule_id = rule_id
        RULES.append(func)
        return func
    return register


class Context:
    """One pass over the metric tables, shared by every rule."""

    def __init__(self, now):
        self.now = now
        self.today = today_local(now)
        self.participants = list(
            MetricsParticipant.objects.select_related('user').filter(enrolled_at__isnull=False)
        )
        self.in_study = [p for p in self.participants if p.phase in IN_STUDY_PHASES]

        self.recent = defaultdict(list)
        window_start = self.today - timedelta(days=DOSAGE_WINDOW_DAYS * 2)
        for row in (
            MetricsDaily.objects
            .filter(is_active_day=True, local_date__gte=window_start)
            .select_related('user')
            .order_by('local_date')
        ):
            self.recent[row.user_id].append(row)

    def last_days(self, user_id, count, predicate=None):
        rows = self.recent.get(user_id, [])
        if predicate is not None:
            rows = [row for row in rows if predicate(row)]
        return rows[-count:]

    def hours_since(self, moment):
        return elapsed(moment, self.now).total_seconds() / 3600


def _protocol_violations(field, threshold=0):
    """All-time violations, grouped by participant.

    Deliberately not windowed: a protocol violation is a permanent finding about
    the trial, and it should not scroll off the dashboard because it happened
    two weeks ago.
    """
    rows = (
        MetricsDaily.objects
        .filter(is_active_day=True, **{f'{field}__gt': threshold})
        .select_related('user')
        .order_by('local_date')
    )
    grouped = defaultdict(list)
    for row in rows:
        grouped[row.user].append(row)
    return grouped


@rule('runin_violation')
def runin_violation(context):
    findings = []
    for user, rows in _protocol_violations('runin_violation_n').items():
        findings.append(Finding(user, CRITICAL, {
            'prompts': sum(row.runin_violation_n for row in rows),
            'days': [row.local_date.isoformat() for row in rows],
            'detail': 'JITAI prompts were sent during the run-in baseline; '
                      'evaluate_jitai_triggers has no run-in gate.',
        }))
    return findings


@rule('cooldown_violation')
def cooldown_violation(context):
    findings = []
    for user, rows in _protocol_violations('cooldown_violations_n').items():
        gaps = [row.min_gap_min for row in rows if row.min_gap_min is not None]
        findings.append(Finding(user, CRITICAL, {
            'violations': sum(row.cooldown_violations_n for row in rows),
            'days': [row.local_date.isoformat() for row in rows],
            'min_gap_min': min(gaps) if gaps else None,
            'cooldown_minutes': JITAI_COOLDOWN_MINUTES,
        }))
    return findings


@rule('cap_exceeded')
def cap_exceeded(context):
    findings = []
    for user, rows in _protocol_violations('sent_n', threshold=DAILY_PROMPT_CAP).items():
        findings.append(Finding(user, CRITICAL, {
            'daily_cap': DAILY_PROMPT_CAP,
            'days': {row.local_date.isoformat(): row.sent_n for row in rows},
        }))
    return findings


@rule('sync_stale')
def sync_stale(context):
    findings = []
    for participant in context.in_study:
        # Never having synced is measured from enrollment, so a participant who
        # enrolled an hour ago is not flagged for a device that has not reported.
        reference = participant.last_sync_at or participant.enrolled_at
        if reference is None:
            continue
        hours = context.hours_since(reference)
        if hours < SYNC_STALE_WARN_HOURS:
            continue
        # The payload holds the anchor, never the elapsed hours. A value that
        # ticks with the clock would make every poll look like a change and
        # rewrite the row 144 times a day. Severity still escalates, because
        # crossing a threshold band is a real change.
        findings.append(Finding(
            participant.user,
            CRITICAL if hours >= SYNC_STALE_CRITICAL_HOURS else WARNING,
            {
                'last_sync_at': participant.last_sync_at.isoformat()
                if participant.last_sync_at else None,
                'ever_synced': participant.last_sync_at is not None,
                'measured_from': (participant.last_sync_at or participant.enrolled_at).isoformat(),
                'warn_after_hours': SYNC_STALE_WARN_HOURS,
                'critical_after_hours': SYNC_STALE_CRITICAL_HOURS,
            },
        ))
    return findings


@rule('slot_coverage_low')
def slot_coverage_low(context):
    findings = []
    for participant in context.in_study:
        rows = context.last_days(participant.user_id, COVERAGE_WINDOW_DAYS,
                                 lambda row: row.slots_expected)
        if len(rows) < COVERAGE_WINDOW_DAYS:
            continue
        covered = sum(row.slots_covered or 0 for row in rows)
        expected = sum(row.slots_expected for row in rows)
        coverage = covered / expected if expected else 0.0
        if coverage >= COVERAGE_FLOOR:
            continue
        findings.append(Finding(participant.user, WARNING, {
            'coverage': round(coverage, 3),
            'floor': COVERAGE_FLOOR,
            'covered': covered,
            'expected': expected,
            'window_days': COVERAGE_WINDOW_DAYS,
        }))
    return findings


@rule('no_ema_48h')
def no_ema_48h(context):
    findings = []
    for participant in context.in_study:
        if participant.enrolled_at is None:
            continue
        if context.hours_since(participant.enrolled_at) < NO_EMA_HOURS:
            continue
        last = participant.last_ema_at
        hours = context.hours_since(last) if last else None
        if hours is not None and hours < NO_EMA_HOURS:
            continue
        findings.append(Finding(participant.user, WARNING, {
            'last_ema_at': last.isoformat() if last else None,
            'ever_submitted': last is not None,
            'threshold_hours': NO_EMA_HOURS,
        }))
    return findings


@rule('delivery_failures')
def delivery_failures(context):
    findings = []
    for participant in context.in_study:
        rows = context.last_days(participant.user_id, COVERAGE_WINDOW_DAYS)
        offending = {
            row.local_date.isoformat(): row.delivery_failures_n
            for row in rows
            if (row.delivery_failures_n or 0) >= DELIVERY_FAILURE_FLOOR
        }
        if not offending:
            continue
        findings.append(Finding(participant.user, WARNING, {
            'days': offending, 'floor': DELIVERY_FAILURE_FLOOR,
        }))
    return findings


@rule('wear_low')
def wear_low(context):
    findings = []
    for participant in context.in_study:
        rows = context.last_days(participant.user_id, WEAR_LOW_DAYS,
                                 lambda row: row.wear_valid_pct is not None)
        if len(rows) < WEAR_LOW_DAYS:
            continue
        if any(row.wear_valid_pct >= BENCHMARKS['wear'] for row in rows):
            continue
        findings.append(Finding(participant.user, WARNING, {
            'target': BENCHMARKS['wear'],
            'days': {row.local_date.isoformat(): round(row.wear_valid_pct, 3) for row in rows},
        }))
    return findings


@rule('dosage_collapse')
def dosage_collapse(context):
    """A participant whose prompt rate falls away relative to their own history.

    Recommended by paper-a-methods.md 7.2: under the expanding within-person
    threshold, a mid-study drop in volatility permanently raises the bar, and
    the intervention simply goes quiet with no error and no other signal.
    """
    findings = []
    for participant in context.in_study:
        rows = context.recent.get(participant.user_id, [])
        if len(rows) < DOSAGE_WINDOW_DAYS * 2:
            continue
        prior = sum(row.sent_n or 0 for row in rows[-DOSAGE_WINDOW_DAYS * 2:-DOSAGE_WINDOW_DAYS])
        recent = sum(row.sent_n or 0 for row in rows[-DOSAGE_WINDOW_DAYS:])
        if prior < DOSAGE_MIN_PRIOR or recent >= prior * DOSAGE_COLLAPSE_RATIO:
            continue
        findings.append(Finding(participant.user, WARNING, {
            'prior_week_sent': prior,
            'recent_week_sent': recent,
            'ratio': round(recent / prior, 3) if prior else None,
            'threshold_ratio': DOSAGE_COLLAPSE_RATIO,
        }))
    return findings


@rule('pipeline_stalled')
def pipeline_stalled(context):
    """Reads JITAILog directly: a liveness check cannot ask the metrics whether
    the job that writes the metrics is running.

    Only evaluated once the whole lookback window sits inside the notification
    window, so an overnight quiet spell is not reported as a stall.
    """
    hour = participant_time(context.now).hour
    if not (NOTIFICATION_WINDOW_START_HOUR + PIPELINE_STALL_HOURS <= hour
            < NOTIFICATION_WINDOW_END_HOUR):
        return []
    if not User.objects.filter(is_enrolled=True).exists():
        return []
    cutoff = context.now - timedelta(hours=PIPELINE_STALL_HOURS)
    if JITAILog.objects.filter(decision_made_at__gte=cutoff).exists():
        return []
    return [Finding(None, CRITICAL, {
        'hours': PIPELINE_STALL_HOURS,
        'detail': 'No JITAI decision points recorded cohort-wide during active hours.',
    })]


@rule('no_wearable_data')
def no_wearable_data(context):
    """Also reads raw: HeartRateSample being empty is the condition itself.

    Expected to fire today. ingest_wearable_data is a stub, so nothing polls
    Labfront and every wear metric is a structural null until it lands.
    """
    if not User.objects.filter(is_enrolled=True).exists():
        return []
    if HeartRateSample.objects.exists():
        return []
    return [Finding(None, CRITICAL, {
        'detail': 'No heart-rate samples exist for any participant. '
                  'ingest_wearable_data is not implemented.',
    })]


def evaluate_alerts(now=None):
    """Reconcile open alerts with what is firing. Returns (opened, updated, resolved)."""
    now = now or django_timezone.now()
    context = Context(now)

    firing = {}
    for rule_func in RULES:
        for finding in rule_func(context):
            key = (finding.user.pk if finding.user else None, rule_func.rule_id)
            firing[key] = finding

    open_alerts = {
        (alert.user_id, alert.rule_id): alert
        for alert in Alert.objects.filter(resolved_at__isnull=True)
    }

    opened = updated = 0
    for key, finding in firing.items():
        existing = open_alerts.get(key)
        if existing is None:
            Alert.objects.create(
                user=finding.user, rule_id=key[1], severity=finding.severity,
                fired_at=now, payload=finding.payload,
            )
            opened += 1
        elif existing.severity != finding.severity or existing.payload != finding.payload:
            # fired_at deliberately untouched: an escalating condition is the
            # same incident, and the researcher wants to know when it started.
            existing.severity = finding.severity
            existing.payload = finding.payload
            existing.save(update_fields=['severity', 'payload'])
            updated += 1

    stale = [alert for key, alert in open_alerts.items() if key not in firing]
    for alert in stale:
        alert.resolved_at = now
    if stale:
        Alert.objects.bulk_update(stale, ['resolved_at'])

    return opened, updated, len(stale)
