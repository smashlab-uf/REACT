"""Read endpoints for the monitoring dashboard.

Everything but the timeline reads the precomputed metric tables, so a 60-second
poll costs one indexed query and never re-aggregates. The timeline is the single
exception and is deliberately narrow: one participant, one day.
"""

from datetime import datetime

from app.models import (
    CheckinReminder,
    EMA,
    EngagementLog,
    HeartRateSample,
    JITAILog,
    User,
)
from app.views import IsAdminUserOrDashboardAPIKey
from django.db.models import Count
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from dashboard.data.cohort import participants_for
from dashboard.data.config import DAILY_PROMPT_CAP, STUDY_DAYS
from dashboard.data.daily import METRIC_FIELDS
from dashboard.data.windows import (
    participant_day_bounds,
    participant_time,
    scheduled_slot_bounds,
    slot_index_for,
    study_day_for,
    today_local,
    waking_window_bounds,
)
from dashboard.models import Alert, MetricsCohort, MetricsDaily, MetricsParticipant
from dashboard.serializers import (
    AlertSerializer,
    MetricsCohortSerializer,
    MetricsDailySerializer,
    MetricsParticipantSerializer,
    participant_label,
)

PHASE_FILTERS = {choice for choice, _ in MetricsCohort.PHASE_FILTER_CHOICES}
DEFAULT_PHASE = MetricsCohort.PHASE_ALL


def _phase_param(request):
    phase = request.query_params.get('phase', DEFAULT_PHASE)
    if phase not in PHASE_FILTERS:
        return None, Response(
            {'error': f'phase must be one of {sorted(PHASE_FILTERS)}'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    return phase, None


class MonitorCohortView(APIView):
    permission_classes = [IsAdminUserOrDashboardAPIKey]

    def get(self, request):
        phase, error = _phase_param(request)
        if error:
            return error

        snapshot = MetricsCohort.objects.filter(phase_filter=phase).order_by('-as_of').first()
        if snapshot is None:
            # Explicit absence rather than a 404, so a poller can render "not
            # computed yet" without treating it as an error, and rather than an
            # empty body, which would read as a cohort with no data.
            return Response({
                'available': False,
                'phase_filter': phase,
                'detail': 'No snapshot yet. Run "manage.py recompute_metrics".',
            })
        return Response({'available': True, **MetricsCohortSerializer(snapshot).data})


class MonitorGridView(APIView):
    permission_classes = [IsAdminUserOrDashboardAPIKey]

    def get(self, request):
        phase, error = _phase_param(request)
        if error:
            return error

        metric = request.query_params.get('metric', 'slots_covered')
        if metric not in METRIC_FIELDS:
            return Response(
                {'error': f'unknown metric {metric!r}', 'available': sorted(METRIC_FIELDS)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        users = list(participants_for(phase).select_related('wearabledevice'))
        rollups = {
            row.user_id: row for row in MetricsParticipant.objects.filter(user__in=users)
        }

        # Overlays travel with the metric because Stage 2 draws them on every
        # metric, and fetching them per toggle would be four times the queries
        # to paint the same marks.
        cells = {}
        for (
            user_id, study_day, value, is_active, local_date, run_in,
            covered, reminded, silent,
        ) in (
            MetricsDaily.objects
            .filter(user__in=users)
            .values_list(
                'user_id', 'study_day', metric, 'is_active_day', 'local_date',
                'is_run_in', 'slots_covered', 'slots_reminded_uncovered', 'slots_silent',
            )
        ):
            # A day the participant did not live through carries no value, even
            # if the column happens to hold one. Null and zero stay distinct all
            # the way to the wire, and every overlay nulls out with it.
            cells[(user_id, study_day)] = (
                {
                    'value': value, 'local_date': local_date, 'run_in': run_in,
                    'covered': covered, 'reminded': reminded, 'silent': silent,
                }
                if is_active else None
            )

        alert_days = self._alert_days(users)

        rows = []
        for user in sorted(users, key=lambda u: u.pk):
            rollup = rollups.get(user.pk)
            days = [cells.get((user.pk, day)) for day in range(STUDY_DAYS)]
            flagged = alert_days.get(user.pk, set())
            rows.append({
                'user_id': user.pk,
                'participant_id': participant_label(user),
                'phase': rollup.phase if rollup else None,
                'study_day_now': rollup.study_day_now if rollup else None,
                'risk_score': rollup.risk_score if rollup else None,
                'risk_components': rollup.risk_components if rollup else None,
                'values': [day['value'] if day else None for day in days],
                'local_dates': [day['local_date'] if day else None for day in days],
                'run_in': [day['run_in'] if day else None for day in days],
                'covered': [day['covered'] if day else None for day in days],
                'reminded': [day['reminded'] if day else None for day in days],
                'silent': [day['silent'] if day else None for day in days],
                'alert': [
                    (index in flagged) if day else None for index, day in enumerate(days)
                ],
            })

        return Response({
            'metric': metric,
            'phase': phase,
            'study_days': list(range(STUDY_DAYS)),
            # So the "dark at cap" scale is read from the constants authority
            # rather than typed into a chart file.
            'daily_prompt_cap': DAILY_PROMPT_CAP,
            'n_participants': len(rows),
            'rows': rows,
        })

    @staticmethod
    def _alert_days(users):
        """Open alerts placed on the study day they fired on.

        An alert is open or not rather than dated per day, so the cell it marks
        is the one for its own firing date. Cohort alerts have no user and mark
        nothing here; they belong to the Stage 1 header.
        """
        by_user = {}
        for alert in (
            Alert.objects
            .filter(user__in=users, resolved_at__isnull=True)
            .select_related('user')
        ):
            study_day = study_day_for(alert.user, participant_time(alert.fired_at).date())
            if study_day is not None and 0 <= study_day < STUDY_DAYS:
                by_user.setdefault(alert.user_id, set()).add(study_day)
        return by_user


class MonitorParticipantView(APIView):
    permission_classes = [IsAdminUserOrDashboardAPIKey]

    def get(self, request, user_id):
        user = User.objects.filter(pk=user_id).select_related('wearabledevice').first()
        if user is None:
            return Response({'error': 'participant not found'},
                            status=status.HTTP_404_NOT_FOUND)

        rollup = MetricsParticipant.objects.filter(user=user).first()
        daily = MetricsDaily.objects.filter(user=user).order_by('study_day')
        alerts = Alert.objects.filter(user=user, resolved_at__isnull=True).order_by('-fired_at')

        return Response({
            'user_id': user.pk,
            'participant_id': participant_label(user),
            'participant': MetricsParticipantSerializer(rollup).data if rollup else None,
            'daily': MetricsDailySerializer(daily, many=True).data,
            'alerts': AlertSerializer(alerts, many=True).data,
        })


class MonitorTimelineView(APIView):
    """The one endpoint that reads raw tables, for one participant-day at a time."""

    permission_classes = [IsAdminUserOrDashboardAPIKey]

    def get(self, request, user_id):
        user = User.objects.filter(pk=user_id).select_related('wearabledevice').first()
        if user is None:
            return Response({'error': 'participant not found'},
                            status=status.HTTP_404_NOT_FOUND)

        raw_date = request.query_params.get('date')
        if raw_date:
            try:
                local_date = datetime.strptime(raw_date, '%Y-%m-%d').date()
            except ValueError:
                return Response({'error': 'date must be YYYY-MM-DD'},
                                status=status.HTTP_400_BAD_REQUEST)
        else:
            local_date = today_local()

        day_start, day_end = participant_day_bounds(local_date)
        slots = scheduled_slot_bounds(local_date)
        events = []

        emas = list(
            EMA.objects
            .filter(user=user, sent_at__gte=day_start, sent_at__lt=day_end)
            .annotate(answered=Count('item_responses'))
            .order_by('sent_at')
        )
        covered = {}
        for ema in emas:
            index = slot_index_for(ema.sent_at, slots=slots)
            if ema.ema_type == 'scheduled_check_in' and ema.status == 'completed' and index is not None:
                covered.setdefault(index, ema.sent_at)
            events.append({
                'kind': 'ema', 'at': ema.sent_at, 'ema_type': ema.ema_type,
                'status': ema.status, 'slot': index, 'answered': ema.answered,
                'served': len(ema.served_sub_item_ids or []) or None,
                'source_jitai_log': ema.source_jitai_log_id,
            })

        reminders = list(
            CheckinReminder.objects
            .filter(user=user, sent_at__gte=day_start, sent_at__lt=day_end)
            .order_by('sent_at')
            .values_list('daily_count_at_send', 'sent_at')
        )
        reminded = {index for index, _ in reminders}
        events.extend(
            {'kind': 'reminder', 'at': sent_at, 'slot': index}
            for index, sent_at in reminders
        )

        logs = list(
            JITAILog.objects
            .filter(user=user, decision_made_at__gte=day_start, decision_made_at__lt=day_end)
            .order_by('decision_made_at')
        )
        events.extend({
            'kind': 'decision', 'at': log.decision_made_at, 'id': log.id,
            'trigger_reason': log.trigger_reason, 'observed_mssd': log.observed_mssd,
            'eligible': log.randomization_draw is not None,
            'send_prompt': log.send_prompt, 'prompt_id': log.prompt_id or None,
            'message_arm': log.message_arm, 'push_sent_at': log.push_sent_at,
            'device_received_at': log.device_received_at,
            'delivery_status': log.delivery_status, 'delivery_error': log.delivery_error or None,
        } for log in logs)

        events.extend({
            'kind': 'engagement', 'at': occurred_at, 'event_type': event_type,
            'jitai_log': jitai_log_id,
        } for event_type, occurred_at, jitai_log_id in (
            EngagementLog.objects
            .filter(user=user, occurred_at__gte=day_start, occurred_at__lt=day_end)
            .order_by('occurred_at')
            .values_list('event_type', 'occurred_at', 'jitai_log_id')
        ))

        window_start, window_end = waking_window_bounds(local_date)
        samples = list(
            HeartRateSample.objects
            .filter(user=user, timestamp__gte=window_start, timestamp__lt=window_end, bpm__gt=0)
            .order_by('timestamp')
            .values_list('timestamp', flat=True)
        )

        return Response({
            'user_id': user.pk,
            'participant_id': participant_label(user),
            'local_date': local_date,
            'study_day': study_day_for(user, local_date),
            'slots': [
                {
                    'index': index, 'start': start, 'end': end,
                    'covered': index in covered,
                    'reminded': index in reminded,
                    'covered_at': covered.get(index),
                }
                for index, (start, end) in enumerate(slots)
            ],
            # A day of heart rate is thousands of rows, so the timeline reports
            # the edges and the density rather than the samples themselves.
            'wear': {
                'window_start': window_start, 'window_end': window_end,
                'samples': len(samples),
                'first_sample': samples[0] if samples else None,
                'last_sample': samples[-1] if samples else None,
            },
            'events': sorted(events, key=lambda event: event['at']),
        })


class MonitorAlertsView(APIView):
    permission_classes = [IsAdminUserOrDashboardAPIKey]

    def get(self, request):
        alerts = (
            Alert.objects
            .filter(resolved_at__isnull=True)
            .select_related('user', 'user__wearabledevice')
            .order_by('severity', '-fired_at')
        )
        severity = request.query_params.get('severity')
        if severity:
            alerts = alerts.filter(severity=severity)

        data = AlertSerializer(alerts, many=True).data
        return Response({
            'open': len(data),
            'critical': sum(1 for alert in data if alert['severity'] == 'critical'),
            'warning': sum(1 for alert in data if alert['severity'] == 'warning'),
            'alerts': data,
        })
