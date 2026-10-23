"""Tests for the monitoring data layer.

Several of these are regression tests for bugs found while building it, and are
labelled as such: the DST subtraction shortcut, study close-out being read as a
withdrawal, phase ordering after day 34, alert payloads that churned on every
poll, and delivery failures sitting in the prompt-response denominator.
"""

import json
import tempfile
from datetime import date, datetime, timedelta
from io import StringIO
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

from app.models import (
    CheckinReminder,
    DistressFlag,
    EMA,
    EMAItemResponse,
    EngagementLog,
    EventDay,
    HeartRateSample,
    JITAILog,
    PhoneTelemetry,
    User,
    WearableDevice,
    WearableSync,
    record_sync,
)
from django.conf import settings
from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.auth.models import User as AuthUser
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.test import Client, TestCase, override_settings

from dashboard.data import accounts
from dashboard.data import compose as compose_data
from dashboard.data import labfront_export as lf
from dashboard.data import timeline as tl
from dashboard.data import windows as w
from dashboard.data.alerts import Context, evaluate_alerts, pipeline_stalled, randomization_audit
from dashboard.data.cohort import (
    WEAR_DIVERGENCE,
    _gauge,
    compute_cohort,
    ks_uniform,
    suppress_rate,
    wilson_interval,
)
from dashboard.data.config import DAILY_PROMPT_CAP, RUN_IN_DAYS, STUDY_DAYS
from dashboard.data.daily import METRIC_FIELDS, compute_daily
from dashboard.data.item_bank import load_item_bank, sub_item_index
from dashboard.data.participant import (
    RISK_SCORE_MAX,
    RISK_WEIGHTS,
    compute_participant,
    compute_risk_score,
    refresh_risk_scores,
)
from dashboard.models import Alert, MetricsCohort, MetricsDaily, MetricsParticipant
from dashboard.reports import build as report_build
from dashboard.reports import render as report_render
from dashboard.reports import svg as report_svg
from dashboard.reports import sections as report
from dashboard.tasks import recompute_metrics

EASTERN = ZoneInfo('America/New_York')
UTC = ZoneInfo('UTC')
DASHBOARD_KEY = 'test-dashboard-key'


def make_participant(email, enrolled_at, is_enrolled=True, device=True, last_sync=None):
    user = User.objects.create(
        email=email, birthdate=date(2005, 1, 1), gender='other',
        is_enrolled=is_enrolled, enrolled_at=enrolled_at,
    )
    if device:
        WearableDevice.objects.create(
            user=user, labfront_participant_id=f'LF-{user.pk}', last_synced_at=last_sync,
        )
    return user


def make_ema(user, when, ema_type='scheduled_check_in', answers=None,
             status='completed', served=None, source=None):
    ema = EMA.objects.create(
        user=user, prompt_id='p', ema_type=ema_type, status=status,
        served_sub_item_ids=served, source_jitai_log=source,
    )
    EMA.objects.filter(pk=ema.pk).update(sent_at=when, responded_at=when)
    for sub_item_id, value in (answers or {}).items():
        field = {'value_numeric': value} if isinstance(value, int) else {'value_choice': value}
        EMAItemResponse.objects.create(
            ema=ema, item_id=sub_item_id.split('_')[0], sub_item_id=sub_item_id,
            response_type='likert', **field,
        )
    return EMA.objects.get(pk=ema.pk)


def make_reminder(user, slot_index, when):
    reminder = CheckinReminder.objects.create(user=user, daily_count_at_send=slot_index)
    CheckinReminder.objects.filter(pk=reminder.pk).update(sent_at=when)
    return reminder


def make_decision(user, when, send=True, reason='prompt sent', **kwargs):
    return JITAILog.objects.create(
        user=user, prompt_id='P1' if send else '', trigger_reason=reason,
        send_prompt=send, randomization_draw=0.3 if reason == 'prompt sent' else None,
        randomization_probability=0.5, decision_made_at=when,
        push_sent_at=kwargs.pop('push_sent_at', when if send else None), **kwargs,
    )


# ---------------------------------------------------------------------------
# Time primitives
# ---------------------------------------------------------------------------

class WindowsTests(TestCase):
    NORMAL = date(2026, 9, 15)
    DST_FALLBACK = date(2026, 11, 1)

    def test_slot_geometry_matches_the_reminder_task(self):
        for local_date in (self.NORMAL, self.DST_FALLBACK):
            bounds = [(s.hour, e.hour) for s, e in w.scheduled_slot_bounds(local_date)]
            self.assertEqual(
                bounds, [(9, 11), (11, 13), (13, 15), (15, 17), (17, 19), (19, 21)],
            )

    def test_slot_index_boundaries_are_half_open(self):
        cases = [(8, 59, None), (9, 0, 0), (10, 59, 0), (11, 0, 1),
                 (19, 0, 5), (20, 59, 5), (21, 0, None)]
        for hour, minute, expected in cases:
            moment = datetime(2026, 9, 15, hour, minute, tzinfo=EASTERN)
            self.assertEqual(w.slot_index_for(moment), expected, f'{hour}:{minute}')

    def test_slot_index_uses_eastern_wall_clock_not_utc(self):
        self.assertEqual(w.slot_index_for(datetime(2026, 9, 16, 0, 30, tzinfo=UTC)), 5)

    def test_dst_fallback_day_is_twenty_five_hours(self):
        """Regression: subtracting two datetimes that share a tzinfo object skips
        the UTC conversion and returns the wall-clock difference, so a plain
        end - start reported 24 hours for a day that really lasts 25."""
        start, end = w.participant_day_bounds(self.DST_FALLBACK)
        self.assertEqual(w.elapsed(start, end).total_seconds() / 3600, 25)
        self.assertEqual((end - start).total_seconds() / 3600, 24)

        start, end = w.participant_day_bounds(self.NORMAL)
        self.assertEqual(w.elapsed(start, end).total_seconds() / 3600, 24)

    def test_waking_window_is_840_minutes_including_the_dst_day(self):
        for local_date in (self.NORMAL, self.DST_FALLBACK):
            self.assertEqual(w.waking_window_minutes(local_date), 840)

    def test_study_day_anchors_on_the_eastern_date(self):
        user = User(email='x@x', enrolled_at=datetime(2026, 9, 2, 3, 0, tzinfo=UTC))
        self.assertEqual(w.day1_date(user), date(2026, 9, 1))
        self.assertEqual(w.study_day_for(user, date(2026, 9, 1)), 0)
        self.assertEqual(w.study_day_for(user, date(2026, 9, 8)), 7)

    def test_run_in_and_study_range(self):
        self.assertTrue(w.is_run_in(RUN_IN_DAYS - 1))
        self.assertFalse(w.is_run_in(RUN_IN_DAYS))
        self.assertTrue(w.in_study_range(STUDY_DAYS - 1))
        self.assertFalse(w.in_study_range(STUDY_DAYS))
        self.assertFalse(w.in_study_range(-1))

    def test_local_dates_clip_to_the_study_window(self):
        user = User(email='x@x', enrolled_at=datetime(2026, 9, 1, 14, 0, tzinfo=UTC))
        now = datetime(2026, 9, 10, 18, 0, tzinfo=UTC)
        self.assertEqual(
            w.local_dates_for(user, 3, now),
            [(date(2026, 9, 8), 7), (date(2026, 9, 9), 8), (date(2026, 9, 10), 9)],
        )
        self.assertEqual(w.local_dates_for(User(email='y@y', enrolled_at=None), 3, now), [])
        past = User(email='z@z', enrolled_at=datetime(2025, 1, 1, tzinfo=UTC))
        self.assertEqual(w.local_dates_for(past, 3, now), [])


# ---------------------------------------------------------------------------
# Frozen item bank
# ---------------------------------------------------------------------------

class ItemBankTests(TestCase):
    def test_frozen_bank_round_trips(self):
        from app.ema_catalog import EMA_ITEM_BANK, EMA_SUB_ITEM_INDEX
        self.assertEqual(load_item_bank(), EMA_ITEM_BANK)
        self.assertEqual(sub_item_index(), EMA_SUB_ITEM_INDEX)

    def test_gating_metadata_survives_the_freeze(self):
        index = sub_item_index()
        self.assertTrue(any('depends_on' in sub for sub in index.values()))
        self.assertTrue(any('schedule_condition' in sub for sub in index.values()))

    def test_check_passes_against_the_committed_file(self):
        call_command('dump_item_bank', '--check', stdout=StringIO())


# ---------------------------------------------------------------------------
# compute_daily
# ---------------------------------------------------------------------------

class ComputeDailyTests(TestCase):
    LOCAL_DATE = date(2026, 9, 4)
    ENROLLED = datetime(2026, 9, 1, 14, 0, tzinfo=UTC)
    NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)

    def setUp(self):
        self.user = make_participant('d@x.test', self.ENROLLED, last_sync=self._at(12))

    def _at(self, hour, minute=0):
        return datetime(2026, 9, 4, hour, minute, tzinfo=EASTERN)

    def _compute(self, **kwargs):
        return compute_daily(self.user, self.LOCAL_DATE, now=self.NOW, **kwargs)

    def test_payload_covers_every_model_column(self):
        model_fields = {f.name for f in MetricsDaily._meta.get_fields()}
        model_fields -= {'id', 'user', 'computed_at'}
        keys = {'study_day', 'local_date', 'is_run_in', 'is_active_day', 'item_bank_version'}
        self.assertEqual(model_fields, set(METRIC_FIELDS) | keys)

    def test_outcome_reminder_without_slot_is_excluded_from_slot_metrics(self):
        make_reminder(self.user, 2, self._at(13, 0))
        make_reminder(self.user, None, self._at(15, 0))

        metrics = self._compute()
        self.assertEqual(metrics['slots_reminded_uncovered'], 1)
        self.assertEqual(metrics['reminders_sent'], 1)

    def test_slot_coverage_and_reminders(self):
        make_ema(self.user, self._at(10, 15), answers={'B1_valence': 5})
        make_ema(self.user, self._at(13, 30), answers={'B1_valence': 3})
        make_reminder(self.user, 2, self._at(13, 0))
        make_reminder(self.user, 4, self._at(17, 30))

        metrics = self._compute()
        self.assertEqual(metrics['slots_covered'], 2)
        self.assertEqual(metrics['slots_reminded_uncovered'], 1)
        self.assertEqual(metrics['slots_silent'], 3)
        self.assertEqual(metrics['reminders_sent'], 2)
        # One slot needed a nudge, the other did not.
        self.assertEqual(metrics['reminders_per_checkin_median'], 0.5)

    def test_ema_outside_the_notification_window_covers_no_slot(self):
        make_ema(self.user, self._at(22, 30), answers={'B1_valence': 5})
        metrics = self._compute()
        self.assertEqual(metrics['ema_scheduled_n'], 1)
        self.assertEqual(metrics['slots_covered'], 0)

    def test_missing_signal_items_are_counted(self):
        make_ema(self.user, self._at(10, 15),
                 answers={'B1_valence': 5, 'B1_arousal': 4, 'B2_stress': 6})
        make_ema(self.user, self._at(12, 15), answers={'B1_valence': 3})
        self.assertEqual(self._compute()['ema_missing_b1b2_n'], 1)

    def test_served_ids_make_a_skipped_item_visible(self):
        bank = load_item_bank()
        served = [s['sub_item_id'] for item in ('B1', 'B2', 'B4')
                  for s in bank[item]['sub_items']]
        answers = {'B1_valence': 5, 'B1_arousal': 4, 'B2_stress': 6}
        make_ema(self.user, self._at(10, 15), answers=answers, served=served)
        with_served = self._compute()['completeness_mean']

        EMA.objects.all().delete()
        make_ema(self.user, self._at(10, 15), answers=answers)
        inferred = self._compute()['completeness_mean']

        # B4 was served and skipped entirely, so inference cannot see it and
        # overstates completeness against the recorded truth.
        self.assertLess(with_served, inferred)
        self.assertAlmostEqual(with_served, 3 / 17)
        self.assertAlmostEqual(inferred, 3 / 14)

    def test_depends_on_gates_apply_on_top_of_served_ids(self):
        bank = load_item_bank()
        served = [s['sub_item_id'] for s in bank['B4']['sub_items']]
        make_ema(self.user, self._at(10, 15), answers={'B4_ate': 'Yes', 'B4_hunger': 5},
                 served=served)
        self.assertAlmostEqual(self._compute()['completeness_mean'], 2 / 8)

        EMA.objects.all().delete()
        make_ema(self.user, self._at(10, 15), answers={'B4_ate': 'No'}, served=served)
        self.assertAlmostEqual(self._compute()['completeness_mean'], 1 / 3)

    def test_mrt_integrity(self):
        make_decision(self.user, self._at(10, 0), device_received_at=self._at(10, 0),
                      delivery_status='received_on_device')
        make_decision(self.user, self._at(10, 20), device_received_at=self._at(10, 20),
                      delivery_status='received_on_device')
        make_decision(self.user, self._at(11, 0), send=False,
                      reason='below within-person threshold')
        make_decision(self.user, self._at(12, 0), send=False, reason='daily cap reached')

        metrics = self._compute()
        self.assertEqual(metrics['decision_points_n'], 4)
        self.assertEqual(metrics['eligible_n'], 2)
        self.assertEqual(metrics['sent_n'], 2)
        self.assertEqual(metrics['delivered_n'], 2)
        self.assertTrue(metrics['cap_hit'])
        self.assertEqual(metrics['min_gap_min'], 20)
        self.assertEqual(metrics['cooldown_violations_n'], 1)
        # Study day 3 is inside the run-in; these sent rows are written directly,
        # bypassing the gate in _evaluate_user, so they count as violations.
        self.assertEqual(metrics['runin_violation_n'], 2)

    def test_suppressed_n_counts_only_suppressed_decisions(self):
        make_decision(self.user, self._at(9, 0), send=False, reason='run-in period',
                      suppression_reason='run_in', status='suppressed',
                      delivery_status='suppressed')
        make_decision(self.user, self._at(10, 0), send=False,
                      reason='distress override (baseline)',
                      suppression_reason='distress_baseline', status='suppressed',
                      delivery_status='suppressed')
        make_decision(self.user, self._at(11, 0), send=False, reason='cooldown active')
        make_decision(self.user, self._at(12, 0))

        metrics = self._compute()
        self.assertEqual(metrics['suppressed_n'], 2)
        self.assertEqual(metrics['decision_points_n'], 4)
        self.assertEqual(metrics['eligible_n'], 1)
        self.assertEqual(metrics['sent_n'], 1)

    def test_suppressed_n_is_zero_not_null_on_a_day_with_no_suppression(self):
        make_decision(self.user, self._at(10, 0), send=False, reason='cooldown active')
        self.assertEqual(self._compute()['suppressed_n'], 0)

    def test_a_suppressed_run_in_decision_is_not_a_run_in_violation(self):
        make_decision(self.user, self._at(10, 0), send=False, reason='run-in period',
                      suppression_reason='run_in', status='suppressed',
                      delivery_status='suppressed')
        metrics = self._compute()
        self.assertEqual(metrics['runin_violation_n'], 0)
        self.assertEqual(metrics['sent_n'], 0)
        self.assertEqual(metrics['suppressed_n'], 1)

    def test_engagement_and_outcome_capture(self):
        log = make_decision(self.user, self._at(10, 0))
        EngagementLog.objects.create(user=self.user, jitai_log=log,
                                     event_type='notification_tapped', occurred_at=self._at(10, 5))
        EngagementLog.objects.create(user=self.user, jitai_log=log,
                                     event_type='notification_dismissed',
                                     occurred_at=self._at(10, 6))
        make_ema(self.user, self._at(10, 6), ema_type='prompt_feedback',
                 answers={'C0_behavior_change': 'I paused or waited'}, source=log)
        make_ema(self.user, self._at(11, 30), ema_type='post_prompt',
                 answers={'B1_valence': 4}, source=log)

        metrics = self._compute()
        self.assertEqual(metrics['prompt_opened_n'], 1)
        self.assertEqual(metrics['prompt_dismissed_n'], 1)
        self.assertEqual(metrics['prompt_acted_n'], 1)
        self.assertEqual(metrics['outcome_captured_n'], 1)

    def test_outcome_outside_the_two_hour_window_does_not_count(self):
        log = make_decision(self.user, self._at(10, 0))
        make_ema(self.user, self._at(13, 0), ema_type='post_prompt',
                 answers={'B1_valence': 4}, source=log)
        self.assertEqual(self._compute()['outcome_captured_n'], 0)

    def test_wear_gaps_measure_against_the_window_edges(self):
        moment = self._at(8, 0)
        while moment < self._at(12, 0):
            HeartRateSample.objects.create(user=self.user, timestamp=moment, bpm=70)
            moment += timedelta(minutes=5)

        metrics = self._compute()
        self.assertEqual(metrics['gaps_gt2h_n'], 1)
        self.assertEqual(metrics['max_gap_min'], 605)
        self.assertEqual(metrics['hr_minutes_valid'], 48)
        self.assertAlmostEqual(metrics['wear_valid_pct'], 235 / 840)

    def test_zero_bpm_is_non_wear_not_coverage(self):
        moment = self._at(8, 0)
        while moment < self._at(22, 0):
            HeartRateSample.objects.create(user=self.user, timestamp=moment, bpm=0)
            moment += timedelta(minutes=5)
        metrics = self._compute()
        self.assertEqual(metrics['wear_valid_pct'], 0.0)
        self.assertEqual(metrics['hr_minutes_valid'], 0)

    def test_never_synced_device_reports_no_data_rather_than_zero_wear(self):
        WearableDevice.objects.filter(user=self.user).update(last_synced_at=None)
        # Creating the device populated the reverse one-to-one cache on the user,
        # so the instance has to be reloaded for the update to be visible.
        self.user = User.objects.get(pk=self.user.pk)
        metrics = self._compute()
        self.assertIsNone(metrics['wear_valid_pct'])
        self.assertIsNone(metrics['hr_minutes_valid'])
        self.assertIsNone(metrics['last_sync_age_h_eod'])

    def test_synced_device_with_no_samples_is_genuine_non_wear(self):
        metrics = self._compute()
        self.assertEqual(metrics['wear_valid_pct'], 0.0)
        self.assertEqual(metrics['gaps_gt2h_n'], 1)

    def test_clock_skew_is_signed(self):
        log = make_decision(self.user, self._at(10, 0))
        event = EngagementLog.objects.create(
            user=self.user, jitai_log=log, event_type='notification_tapped',
            occurred_at=self._at(10, 5),
        )
        # Device clock running ahead of the server: occurred_at after recorded_at.
        EngagementLog.objects.filter(pk=event.pk).update(
            recorded_at=self._at(10, 5) - timedelta(seconds=2))
        self.assertEqual(self._compute()['clock_skew_p95_ms'], -2000)

    def test_silent_active_day_is_zeros_not_nulls(self):
        metrics = self._compute()
        self.assertTrue(metrics['is_active_day'])
        for field in ('ema_scheduled_n', 'slots_covered', 'sent_n', 'decision_points_n'):
            self.assertEqual(metrics[field], 0, field)
        self.assertEqual(metrics['slots_expected'], 6)
        self.assertEqual(metrics['slots_silent'], 6)
        self.assertFalse(metrics['cap_hit'])
        # Nothing to average over stays absent rather than becoming zero.
        self.assertIsNone(metrics['completeness_mean'])
        self.assertIsNone(metrics['reminders_per_checkin_median'])
        self.assertIsNone(metrics['min_gap_min'])

    def test_days_outside_the_study_window_are_blank(self):
        for local_date in (date(2026, 8, 30), date(2026, 11, 1)):
            metrics = compute_daily(self.user, local_date, now=self.NOW)
            self.assertFalse(metrics['is_active_day'])
            for field in METRIC_FIELDS:
                self.assertIsNone(metrics[field], f'{local_date} {field}')

    def test_withdrawal_day_keeps_its_coverage(self):
        make_ema(self.user, self._at(10, 15), answers={'B1_valence': 5})
        withdrawn = datetime(2026, 9, 4, 16, 0, tzinfo=UTC)
        same_day = self._compute(withdrawn_at=withdrawn)
        self.assertTrue(same_day['is_active_day'])
        self.assertEqual(same_day['slots_covered'], 1)

        after = compute_daily(self.user, date(2026, 9, 6), withdrawn_at=withdrawn, now=self.NOW)
        self.assertFalse(after['is_active_day'])


# ---------------------------------------------------------------------------
# compute_participant
# ---------------------------------------------------------------------------

class ComputeParticipantTests(TestCase):
    NOW = datetime(2026, 9, 11, 16, 0, tzinfo=UTC)
    ENROLLED = datetime(2026, 9, 1, 14, 0, tzinfo=UTC)

    def test_phases_and_retention(self):
        cases = [
            ('pre_enrollment', None, False, None),
            ('run_in', datetime(2026, 9, 8, 14, 0, tzinfo=UTC), True, True),
            ('mrt', self.ENROLLED, True, True),
            ('withdrawn', self.ENROLLED, False, False),
            ('complete', datetime(2026, 7, 1, tzinfo=UTC), False, True),
        ]
        for index, (phase, enrolled_at, is_enrolled, retention) in enumerate(cases):
            user = make_participant(f'ph{index}@x.test', enrolled_at,
                                    is_enrolled=is_enrolled, device=False)
            payload = compute_participant(user, now=self.NOW)
            self.assertEqual(payload['phase'], phase)
            self.assertIs(payload['active_retention'], retention, phase)

    def test_study_close_out_is_not_a_withdrawal(self):
        """Regression: unenrolling someone who finished is how the study ends,
        and it was being recorded as a dropout."""
        user = make_participant('c@x.test', datetime(2026, 7, 1, tzinfo=UTC),
                                is_enrolled=False, device=False)
        payload = compute_participant(user, now=self.NOW)
        self.assertIsNone(payload['first_seen_not_enrolled_at'])
        self.assertEqual(payload['phase'], 'complete')

    def test_withdrawal_stays_withdrawn_past_the_last_study_day(self):
        """Regression: checking 'past day 34' before 'withdrawn' flipped someone
        who left on day 10 to complete once the window closed."""
        user = make_participant('l@x.test', datetime(2026, 7, 1, tzinfo=UTC),
                                is_enrolled=False, device=False)
        row = MetricsParticipant.objects.create(
            user=user, **compute_participant(user, now=self.NOW))
        row.first_seen_not_enrolled_at = datetime(2026, 7, 15, tzinfo=UTC)
        row.save()
        payload = compute_participant(user, existing=row, now=self.NOW)
        self.assertEqual(payload['phase'], 'withdrawn')
        self.assertFalse(payload['active_retention'])

    def test_withdrawal_timestamp_is_sticky(self):
        user = make_participant('w@x.test', self.ENROLLED, device=False)
        row = MetricsParticipant.objects.create(
            user=user, **compute_participant(user, now=self.NOW))
        self.assertIsNone(row.first_seen_not_enrolled_at)

        user.is_enrolled = False
        user.save()
        first_seen = self.NOW + timedelta(hours=1)
        payload = compute_participant(user, existing=row, now=first_seen)
        self.assertEqual(payload['first_seen_not_enrolled_at'], first_seen)

        for field, value in payload.items():
            setattr(row, field, value)
        row.save()
        later = compute_participant(user, existing=row, now=self.NOW + timedelta(hours=7))
        self.assertEqual(later['first_seen_not_enrolled_at'], first_seen)

    def _seed_days(self, user, rows):
        for index, fields in enumerate(rows):
            MetricsDaily.objects.create(
                user=user, study_day=index,
                local_date=date(2026, 9, 1) + timedelta(days=index),
                is_run_in=True, is_active_day=True, item_bank_version='v1', **fields)

    def test_cumulative_rates_exclude_inactive_and_null_days(self):
        user = make_participant('r@x.test', self.ENROLLED, device=False)
        self._seed_days(user, [
            {'slots_expected': 6, 'slots_covered': 4, 'wear_valid_pct': 1.0},
            {'slots_expected': 6, 'slots_covered': 6, 'wear_valid_pct': 0.5},
            {'slots_expected': 6, 'slots_covered': 2, 'wear_valid_pct': None},
        ])
        MetricsDaily.objects.create(user=user, study_day=9, local_date=date(2026, 9, 10),
                                    is_run_in=True, is_active_day=False,
                                    item_bank_version='v1')
        payload = compute_participant(user, now=self.NOW)
        self.assertEqual((payload['slot_coverage_num'], payload['slot_coverage_den']), (12, 18))
        self.assertEqual((payload['wear_num'], payload['wear_den']), (1260, 1680))
        self.assertAlmostEqual(payload['wear_rate'], 0.75)

    def test_delivery_failures_leave_the_prompt_response_denominator(self):
        """Documented failures are excluded and reported separately, per
        JITAI-analysis-plan.md. Not the same as counting only confirmed
        receipts, which would drop real deliveries whose receipt never
        reported."""
        user = make_participant('p@x.test', self.ENROLLED, device=False)
        self._seed_days(user, [
            {'sent_n': 4, 'delivery_failures_n': 1, 'outcome_captured_n': 2},
            {'sent_n': 3, 'delivery_failures_n': 0, 'outcome_captured_n': 1},
            {'sent_n': 2, 'delivery_failures_n': 2, 'outcome_captured_n': 0},
        ])
        payload = compute_participant(user, now=self.NOW)
        self.assertEqual(
            (payload['prompt_response_num'], payload['prompt_response_den']), (3, 6))
        self.assertAlmostEqual(payload['prompt_response_rate'], 0.5)

    def test_no_data_gives_none_rates_not_zero(self):
        user = make_participant('n@x.test', self.ENROLLED, device=False)
        payload = compute_participant(user, now=self.NOW)
        self.assertIsNone(payload['slot_coverage_rate'])
        self.assertIsNone(payload['wear_rate'])
        self.assertEqual(payload['slot_coverage_den'], 0)

    TODAY = date(2026, 9, 11)

    def _healthy_days(self, user, n=7):
        """A trailing week ending on the class's NOW that scores zero on every
        term, so a test can move one term at a time and read the delta as that
        term's contribution."""
        for offset in range(n):
            MetricsDaily.objects.create(
                user=user, study_day=10 - offset,
                local_date=self.TODAY - timedelta(days=offset),
                is_run_in=False, is_active_day=True, item_bank_version='v1',
                ema_scheduled_n=6, slots_expected=6, slots_covered=6,
                wear_valid_pct=1.0, ema_missing_b1b2_n=0)

    def _score(self, user, **kwargs):
        kwargs.setdefault('phase', 'mrt')
        kwargs.setdefault('last_sync_at', self.NOW)
        kwargs.setdefault('now', self.NOW)
        # These tests are about the arithmetic of each term, so sync is asserted
        # measurable. The unmeasurable case has its own test below.
        kwargs.setdefault('sync_measurable', True)
        return compute_risk_score(user, **kwargs)

    def test_risk_score_is_zero_for_a_fully_compliant_participant(self):
        user = make_participant('k@x.test', self.ENROLLED, device=False)
        self._healthy_days(user)
        score, components = self._score(user)
        self.assertEqual(score, 0)
        self.assertEqual(set(components), set(RISK_WEIGHTS))
        self.assertEqual(sum(components.values()), 0)

    def test_each_risk_term_contributes_its_own_weight(self):
        user = make_participant('t@x.test', self.ENROLLED, device=False)
        self._healthy_days(user)

        MetricsDaily.objects.filter(user=user, local_date__gte=date(2026, 9, 9)).update(
            ema_scheduled_n=0, slots_covered=1, wear_valid_pct=0.1, ema_missing_b1b2_n=1)
        Alert.objects.create(user=user, rule_id='wear_low', severity='critical')
        score, components = self._score(user, last_sync_at=self.NOW - timedelta(hours=30))

        self.assertEqual(components, {
            'ema_stale': 3 * 3,       # last scheduled check-in was 2026-09-08
            'low_coverage': 2 * 3,    # 1/6 covered on three days
            'sync_stale': 2 * 1,
            'low_wear': 1 * 3,
            'missing_signal': 1 * 3,
            'open_critical': 4 * 1,
        })
        self.assertEqual(score, sum(components.values()))

    def test_risk_score_caps_hold_at_the_documented_ceiling(self):
        user = make_participant('c@x.test', self.ENROLLED, device=False)
        for offset in range(7):
            MetricsDaily.objects.create(
                user=user, study_day=10 - offset,
                local_date=self.TODAY - timedelta(days=offset),
                is_run_in=False, is_active_day=True, item_bank_version='v1',
                ema_scheduled_n=0, slots_expected=6, slots_covered=0,
                wear_valid_pct=0.0, ema_missing_b1b2_n=6)
        Alert.objects.create(user=user, rule_id='wear_low', severity='critical')
        score, components = self._score(user, last_sync_at=None)

        self.assertEqual(score, RISK_SCORE_MAX)
        self.assertEqual(score, 47)
        self.assertEqual(components['ema_stale'], 3 * 5)
        self.assertEqual(components['missing_signal'], 1 * 5)

    def test_trailing_window_is_the_last_seven_active_days_not_rows(self):
        """A gap in the middle must not pull an eighth day into the window, and
        an inactive row must not occupy a slot in it."""
        user = make_participant('w@x.test', self.ENROLLED, device=False)
        for offset in range(10):
            MetricsDaily.objects.create(
                user=user, study_day=10 - offset,
                local_date=self.TODAY - timedelta(days=offset),
                is_run_in=False, is_active_day=offset != 3, item_bank_version='v1',
                ema_scheduled_n=6, slots_expected=6,
                slots_covered=0 if offset >= 7 else 6,
                wear_valid_pct=1.0, ema_missing_b1b2_n=0)
        _, components = self._score(user)
        self.assertEqual(components['low_coverage'], 2 * 1)

    def test_null_metrics_contribute_nothing_rather_than_counting_as_failure(self):
        user = make_participant('nl@x.test', self.ENROLLED, device=False)
        for offset in range(7):
            MetricsDaily.objects.create(
                user=user, study_day=10 - offset,
                local_date=self.TODAY - timedelta(days=offset),
                is_run_in=False, is_active_day=True, item_bank_version='v1',
                ema_scheduled_n=6, slots_expected=None, slots_covered=None,
                wear_valid_pct=None, ema_missing_b1b2_n=None)
        _, components = self._score(user)
        self.assertEqual(components['low_coverage'], 0)
        self.assertEqual(components['low_wear'], 0)
        self.assertEqual(components['missing_signal'], 0)

    def test_risk_score_is_null_outside_the_active_phases(self):
        """Withdrawn, complete and pre-enrollment participants score every term
        as maximally bad, so scoring them would put people the study is not
        asking anything of at the top of the RA's call list."""
        user = make_participant('ph@x.test', self.ENROLLED, device=False)
        for phase in ('pre_enrollment', 'complete', 'withdrawn'):
            self.assertEqual(self._score(user, phase=phase), (None, None), phase)
        self.assertIsNotNone(self._score(user, phase='run_in')[0])

    def test_warning_alerts_do_not_move_the_score(self):
        """Only critical counts. The spec names severity=high, which no alert
        carries, so the term is scored against critical or it is dead."""
        user = make_participant('wa@x.test', self.ENROLLED, device=False)
        self._healthy_days(user)
        Alert.objects.create(user=user, rule_id='sync_stale', severity='warning')
        self.assertEqual(self._score(user)[0], 0)
        Alert.objects.create(user=user, rule_id='wear_low', severity='critical')
        self.assertEqual(self._score(user)[0], 4)
        Alert.objects.filter(user=user, severity='critical').update(resolved_at=self.NOW)
        self.assertEqual(self._score(user)[0], 0)

    def test_refresh_risk_scores_updates_stored_rows(self):
        user = make_participant('rs@x.test', self.ENROLLED, device=False)
        self._healthy_days(user)
        MetricsParticipant.objects.create(user=user, **compute_participant(user, now=self.NOW))
        # 0, not 2: nobody in this fixture has ever reported a sync, so the
        # signal is unmeasurable and its term contributes nothing rather than
        # putting the same 2 points on everyone.
        self.assertEqual(MetricsParticipant.objects.get(user=user).risk_score, 0)

        Alert.objects.create(user=user, rule_id='wear_low', severity='critical')
        self.assertEqual(refresh_risk_scores(now=self.NOW), 1)
        row = MetricsParticipant.objects.get(user=user)
        self.assertEqual(row.risk_score, 4)
        self.assertEqual(row.risk_components['open_critical'], 4)
        self.assertEqual(row.risk_score, sum(row.risk_components.values()))
        self.assertEqual(refresh_risk_scores(now=self.NOW), 0)

    def test_sync_term_is_zero_while_nothing_writes_the_sync_clock(self):
        """Unmeasurable is not late. Nothing writes last_synced_at today, so
        scoring 'never synced' as stale would put an identical 2 points on every
        participant and flatten the ordering the score exists to produce."""
        user = make_participant('sm@x.test', self.ENROLLED, device=False)
        self._healthy_days(user)
        _, unmeasurable = self._score(user, last_sync_at=None, sync_measurable=False)
        self.assertEqual(unmeasurable['sync_stale'], 0)
        _, measurable = self._score(user, last_sync_at=None, sync_measurable=True)
        self.assertEqual(measurable['sync_stale'], 2)

    def test_a_high_alert_moves_the_score_like_a_critical(self):
        user = make_participant('hi@x.test', self.ENROLLED, device=False)
        self._healthy_days(user)
        Alert.objects.create(user=user, rule_id='wear_low', severity='high')
        self.assertEqual(self._score(user)[0], 4)
        Alert.objects.filter(user=user).update(severity='warning')
        self.assertEqual(self._score(user)[0], 0)
        Alert.objects.filter(user=user).update(severity='critical')
        self.assertEqual(self._score(user)[0], 4)


# ---------------------------------------------------------------------------
# Cohort snapshot
# ---------------------------------------------------------------------------

class CohortTests(TestCase):
    NOW = datetime(2026, 9, 20, 16, 0, tzinfo=UTC)
    ENROLLED = datetime(2026, 9, 1, 14, 0, tzinfo=UTC)

    def test_ks_uniform_matches_scipy(self):
        """The KS test is hand-rolled because scipy is not in the backend
        requirements and the Celery worker cannot have it. These are the values
        scipy.stats.kstest(sample, 'uniform', method='asymp') returns, checked
        in the analytics environment where scipy does exist."""
        cases = [
            ([(i + 0.5) / 100 for i in range(100)], 0.005, 1.0),
            ([0.3 + 0.7 * (i / 299) for i in range(300)], 0.3, 7.065257144401614e-24),
            ([0.42], 0.58, 0.8896056376475567),
            ([0.1, 0.9], 0.4, 0.9062063895703105),
        ]
        for sample, statistic, p_value in cases:
            got_d, got_p = ks_uniform(sample)
            self.assertAlmostEqual(got_d, statistic, places=9)
            self.assertAlmostEqual(got_p, p_value, places=9)

    def test_ks_uniform_has_nothing_to_say_about_an_empty_sample(self):
        self.assertEqual(ks_uniform([]), (None, None))

    def test_wilson_matches_the_score_equation(self):
        expected = {
            (8, 10): (0.4901625, 0.9433178),
            (17, 20): (0.6395811, 0.9476312),
            (30, 100): (0.2189489, 0.3958486),
        }
        for (k, n), (low, high) in expected.items():
            got_low, got_high = wilson_interval(k, n)
            self.assertAlmostEqual(got_low, low, places=6)
            self.assertAlmostEqual(got_high, high, places=6)

    def test_wilson_stays_inside_the_unit_interval(self):
        self.assertEqual(wilson_interval(0, 20)[0], 0.0)
        self.assertEqual(wilson_interval(20, 20)[1], 1.0)
        self.assertEqual(wilson_interval(0, 0), (None, None))

    def test_wilson_has_nothing_to_say_when_the_numerator_exceeds_its_denominator(self):
        # Production carries prompts sent on decision points that were never
        # marked eligible, so the send_rate gauge really is handed k > n. The
        # interval is undefined there; it used to raise a math domain error and
        # take the whole cohort recompute down with it.
        self.assertEqual(wilson_interval(25, 1), (None, None))
        self.assertEqual(wilson_interval(2, 1), (None, None))
        self.assertEqual(wilson_interval(1, 0), (None, None))
        self.assertEqual(wilson_interval(-1, 10), (None, None))

    def test_gauge_survives_the_contradiction_it_exists_to_report(self):
        entry = _gauge('send_rate', 25, 1)
        self.assertTrue(entry['contradiction'])
        self.assertEqual(entry['numerator'], 25)
        self.assertEqual(entry['denominator'], 1)
        self.assertIsNone(entry['wilson_low'])
        self.assertIsNone(entry['wilson_high'])

    def test_suppression_needs_participants_and_units(self):
        self.assertIsNone(suppress_rate(20, 40, 9))
        self.assertIsNone(suppress_rate(20, 29, 12))
        self.assertAlmostEqual(suppress_rate(20, 40, 12), 0.5)

    def _seed_cohort(self, count, prefix, wear=0.9, retained=True, days=6):
        for index in range(count):
            user = make_participant(f'{prefix}{index}@x.test', self.ENROLLED,
                                    is_enrolled=retained, device=False)
            for day in range(days):
                MetricsDaily.objects.create(
                    user=user, study_day=day,
                    local_date=date(2026, 9, 15) + timedelta(days=day),
                    is_run_in=False, is_active_day=True, item_bank_version='v1',
                    slots_expected=6, slots_covered=5, sent_n=3, delivered_n=3,
                    outcome_captured_n=2, wear_valid_pct=wear,
                    decision_points_n=5, eligible_n=3, cap_hit=(day == 0),
                    cooldown_violations_n=(1 if day == 1 else 0), runin_violation_n=0)
            MetricsParticipant.objects.create(
                user=user, phase='mrt', is_enrolled_snapshot=retained,
                active_retention=retained,
                slot_coverage_num=5 * days, slot_coverage_den=6 * days,
                prompt_response_num=2 * days, prompt_response_den=3 * days,
                wear_num=round(wear * 840 * days), wear_den=840 * days)

    def test_small_cohort_is_suppressed_but_keeps_counts(self):
        self._seed_cohort(5, 'small')
        snapshot = compute_cohort('all', now=self.NOW)
        self.assertEqual(snapshot['n_participants'], 5)
        for name, entry in snapshot['benchmarks'].items():
            self.assertTrue(entry['suppressed'], name)
            self.assertIsNone(entry['value'], name)
        self.assertEqual(snapshot['benchmarks']['slot_coverage']['numerator'], 150)

    def test_large_cohort_publishes_rates_with_bounds(self):
        self._seed_cohort(12, 'big')
        entry = compute_cohort('all', now=self.NOW)['benchmarks']['slot_coverage']
        self.assertFalse(entry['suppressed'])
        self.assertAlmostEqual(entry['value'], 5 / 6)
        self.assertLess(entry['wilson_low'], entry['value'])
        self.assertGreater(entry['wilson_high'], entry['value'])

    def test_hair_is_not_reported(self):
        self._seed_cohort(12, 'h')
        self.assertEqual(
            set(compute_cohort('all', now=self.NOW)['benchmarks']),
            {'slot_coverage', 'prompt_response', 'wear', 'retention'},
        )

    def test_wear_is_scored_per_participant_day(self):
        self._seed_cohort(12, 'w', wear=0.9)
        self._seed_cohort(3, 'lw', wear=0.2)
        entry = compute_cohort('all', now=self.NOW)['benchmarks']['wear']
        self.assertEqual(entry['denominator'], 15 * 6)
        self.assertEqual(entry['numerator'], 12 * 6)
        self.assertIn('participant-days', entry['unit'])

    def test_integrity_counters_and_series(self):
        self._seed_cohort(12, 'i')
        snapshot = compute_cohort('all', now=self.NOW)
        self.assertEqual(snapshot['sent_n'], 12 * 6 * 3)
        self.assertEqual(snapshot['cap_hit_days'], 12)
        self.assertEqual(snapshot['cooldown_violations_n'], 12)
        self.assertEqual(len(snapshot['series_14d']), 6)
        self.assertEqual(snapshot['series_14d'][0]['participants'], 12)

    def test_snapshot_payload_is_small(self):
        self._seed_cohort(12, 's')
        snapshot = compute_cohort('all', now=self.NOW)
        encoded = json.dumps(
            {'benchmarks': snapshot['benchmarks'], 'series_14d': snapshot['series_14d'],
             'integrity': snapshot['integrity'], 'funnel': snapshot['funnel']},
            default=str)
        self.assertLess(len(encoded), 200 * 1024)

    def test_pooled_and_participant_mean_differ_when_day_counts_differ(self):
        """Pooling weights each participant by how many days they contributed, so
        someone who lasted 2 days counts a fifteenth of someone who lasted 30.
        The benchmark is stated about participants, so both readings go on the
        tile."""
        self._seed_cohort(11, 'long', days=30)
        self._seed_cohort(1, 'short', days=2)
        MetricsParticipant.objects.filter(user__email__startswith='short').update(
            slot_coverage_num=0, slot_coverage_den=12, slot_coverage_rate=0.0)
        for row in MetricsParticipant.objects.exclude(user__email__startswith='short'):
            row.slot_coverage_rate = row.slot_coverage_num / row.slot_coverage_den
            row.save(update_fields=['slot_coverage_rate'])

        entry = compute_cohort('all', now=self.NOW)['benchmarks']['slot_coverage']
        self.assertTrue(entry['proxy'])
        # Pooled barely notices the short participant; the participant mean does.
        self.assertAlmostEqual(entry['value'], 11 * 150 / (11 * 180 + 12), places=4)
        self.assertAlmostEqual(entry['participant_mean'], 11 * (5 / 6) / 12, places=4)
        self.assertLess(entry['participant_mean'], entry['value'])

    def test_integrity_excludes_run_in_days(self):
        self._seed_cohort(12, 'r')
        MetricsDaily.objects.filter(study_day__lt=2).update(is_run_in=True)
        integrity = compute_cohort('all', now=self.NOW)['integrity']
        # 6 seeded days each, 2 now run-in, so 4 MRT days x 12 participants.
        self.assertEqual(integrity['cap_hit_rate']['denominator'], 12 * 4)
        self.assertEqual(integrity['eligibility_rate']['denominator'], 12 * 4 * 5)

    def test_eligibility_gauge_carries_the_compliance_confound(self):
        """A decision point only exists when an EMA is submitted, so
        availability is confounded with compliance. The ratio has to stay on
        screen or the gauge reads as a property of the engine alone."""
        self._seed_cohort(12, 'c')
        MetricsDaily.objects.update(ema_scheduled_n=6)
        gauge = compute_cohort('all', now=self.NOW)['integrity']['eligibility_rate']
        self.assertAlmostEqual(gauge['decision_points_per_scheduled_ema'], 5 / 6)
        self.assertEqual(gauge['ema_scheduled_n'], 12 * 6 * 6)

    def test_randomization_audit_catches_a_contradicted_draw(self):
        self._seed_cohort(12, 'ra')
        user = MetricsParticipant.objects.first().user
        make_decision(user, self.NOW, send=True, reason='prompt sent')
        clean = compute_cohort('all', now=self.NOW)['integrity']['randomization_audit']
        self.assertEqual(clean['mismatches'], 0)

        # draw 0.9 against p 0.5 cannot have produced send_prompt=True
        JITAILog.objects.update(randomization_draw=0.9, randomization_probability=0.5)
        audited = compute_cohort('all', now=self.NOW)['integrity']['randomization_audit']
        self.assertEqual(audited['mismatches'], 1)
        self.assertEqual(audited['draws'], 1)

    def test_suppressed_decisions_do_not_trip_the_eligibility_cross_check(self):
        self._seed_cohort(12, 'sup')
        user = MetricsParticipant.objects.first().user
        for reason, label in (('run_in', 'run-in period'), ('distress_baseline', 'distress override (baseline)')):
            JITAILog.objects.create(
                user=user, prompt_id='', trigger_reason=label, send_prompt=False,
                suppression_reason=reason, decision_made_at=self.NOW,
                status='not_sent', delivery_status='not_sent',
            )
        gauge = compute_cohort('all', now=self.NOW)['integrity']['eligibility_rate']
        self.assertEqual(gauge['reason_eligible_but_no_draw'], 0)

        JITAILog.objects.create(
            user=user, prompt_id='', trigger_reason='prompt sent', send_prompt=False,
            decision_made_at=self.NOW, status='not_sent', delivery_status='not_sent',
        )
        gauge = compute_cohort('all', now=self.NOW)['integrity']['eligibility_rate']
        self.assertEqual(gauge['reason_eligible_but_no_draw'], 1)

    def test_active_retention_moves_when_formal_retention_cannot(self):
        """Formal retention only asks whether someone withdrew, so it cannot
        move before Day 35. Active retention moves the week someone goes quiet,
        which is the whole reason both are on the tile."""
        self._seed_cohort(12, 'ar')
        MetricsDaily.objects.update(local_date=date(2026, 9, 20), ema_scheduled_n=3)
        silent = MetricsParticipant.objects.first().user
        MetricsDaily.objects.filter(user=silent).update(
            local_date=date(2026, 9, 1), ema_scheduled_n=0)

        retention = compute_cohort('all', now=self.NOW)['benchmarks']['retention']
        self.assertEqual(retention['numerator'], 12)          # nobody withdrew
        self.assertEqual(retention['active']['numerator'], 11)  # one went quiet
        self.assertEqual(retention['active']['denominator'], 12)

    def test_wear_tile_carries_both_readings_and_flags_burst_charging(self):
        """wear_valid_pct is (840 - gaps>2h)/840 and drives the benchmark;
        hr_minutes_valid counts minutes that actually carry a sample. A day high
        on the first and low on the second is a watch worn in short bursts, and
        the gap definition is flattering it."""
        self._seed_cohort(12, 'bc', wear=0.95)
        MetricsDaily.objects.update(hr_minutes_valid=int(0.5 * 840))
        entry = compute_cohort('all', now=self.NOW)['benchmarks']['wear']
        self.assertAlmostEqual(entry['mean_coverage'], 0.95)
        self.assertAlmostEqual(entry['mean_minute_coverage'], 0.5, places=3)
        self.assertEqual(entry['burst_charging_days'], 12 * 6)
        self.assertEqual(entry['divergence_threshold'], WEAR_DIVERGENCE)

    def test_every_series_rate_obeys_the_same_suppression_as_its_tile(self):
        """A sparkline is a sequence of rates. Publishing one from five
        participants while withholding the tile above it would let a trend in
        through the back door."""
        self._seed_cohort(9, 'thin')
        thin = compute_cohort('all', now=self.NOW)
        self.assertTrue(all(entry['suppressed'] for entry in thin['benchmarks'].values()))
        for day in thin['series_14d']:
            for field in ('slot_coverage', 'prompt_response', 'wear_pass_rate'):
                self.assertIsNone(day[field], f"{field} on {day['date']}")
            # The raw counts stay, so the tile can print them.
            self.assertIsNotNone(day['slots_covered'])

        self._seed_cohort(3, 'more')
        wide = compute_cohort('all', now=self.NOW)
        self.assertFalse(wide['benchmarks']['slot_coverage']['suppressed'])
        self.assertIsNotNone(wide['series_14d'][0]['slot_coverage'])

    def test_a_gauge_numerator_above_its_denominator_is_flagged(self):
        """Production sends prompts on decision points that carry no
        randomization draw, which makes sent_n/eligible_n read 1/0. Rendering
        that as 'no data' would hide the very thing the strip exists to catch."""
        self._seed_cohort(12, 'cd')
        MetricsDaily.objects.update(eligible_n=0, sent_n=3, is_run_in=False)
        gauge = compute_cohort('all', now=self.NOW)['integrity']['send_rate']
        self.assertEqual((gauge['numerator'], gauge['denominator']), (12 * 6 * 3, 0))
        self.assertTrue(gauge['contradiction'])
        self.assertIsNone(gauge['value'])

        MetricsDaily.objects.update(eligible_n=3)
        healthy = compute_cohort('all', now=self.NOW)['integrity']['send_rate']
        self.assertFalse(healthy['contradiction'])

    def test_funnel_marks_consent_as_having_no_source(self):
        self._seed_cohort(12, 'f')
        funnel = {row['stage']: row for row in compute_cohort('all', now=self.NOW)['funnel']}
        self.assertFalse(funnel['consented']['measurable'])
        self.assertIsNone(funnel['consented']['n'])
        self.assertEqual(funnel['started day 1']['n'], 12)
        self.assertEqual(funnel['active today']['n'], 12)


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------

class AlertTests(TestCase):
    NOW = datetime(2026, 9, 20, 20, 0, tzinfo=UTC)      # 16:00 Eastern
    ENROLLED = datetime(2026, 8, 25, 14, 0, tzinfo=UTC)

    def _participant(self, email, last_sync=None, last_ema=None, phase='mrt'):
        user = make_participant(email, self.ENROLLED, device=False)
        MetricsParticipant.objects.create(
            user=user, phase=phase, is_enrolled_snapshot=True, enrolled_at=self.ENROLLED,
            last_sync_at=last_sync, last_ema_at=last_ema, active_retention=True)
        return user

    def _day(self, user, days_ago, **fields):
        defaults = dict(slots_expected=6, slots_covered=6, sent_n=1,
                        delivery_failures_n=0, cooldown_violations_n=0, runin_violation_n=0)
        defaults.update(fields)
        return MetricsDaily.objects.create(
            user=user, study_day=26 - days_ago,
            local_date=date(2026, 9, 20) - timedelta(days=days_ago),
            is_run_in=False, is_active_day=True, item_bank_version='v1', **defaults)

    def _open_rules(self):
        return {(a.user_id, a.rule_id) for a in Alert.objects.filter(resolved_at__isnull=True)}

    def test_each_rule_fires_for_its_own_fault(self):
        healthy = self._participant('ok@x.test', last_sync=self.NOW - timedelta(hours=2),
                                    last_ema=self.NOW - timedelta(hours=3))
        for day in range(14):
            self._day(healthy, day, wear_valid_pct=0.95)

        faults = {}
        faults['runin'] = self._participant('runin@x.test', self.NOW, self.NOW)
        self._day(faults['runin'], 5, runin_violation_n=2)
        faults['cool'] = self._participant('cool@x.test', self.NOW, self.NOW)
        self._day(faults['cool'], 3, cooldown_violations_n=1, min_gap_min=20)
        faults['cap'] = self._participant('cap@x.test', self.NOW, self.NOW)
        self._day(faults['cap'], 2, sent_n=DAILY_PROMPT_CAP + 2)
        faults['stale'] = self._participant('stale@x.test',
                                            self.NOW - timedelta(hours=30), self.NOW)
        faults['dead'] = self._participant('dead@x.test',
                                           self.NOW - timedelta(hours=100), self.NOW)
        faults['lowcov'] = self._participant('lowcov@x.test', self.NOW, self.NOW)
        for day in range(3):
            self._day(faults['lowcov'], day, slots_covered=1)
        faults['silent'] = self._participant('silent@x.test', self.NOW,
                                             self.NOW - timedelta(hours=60))
        faults['fail'] = self._participant('fail@x.test', self.NOW, self.NOW)
        self._day(faults['fail'], 1, delivery_failures_n=3)
        faults['nowear'] = self._participant('nowear@x.test', self.NOW, self.NOW)
        for day in range(3):
            self._day(faults['nowear'], day, wear_valid_pct=0.4)
        faults['quiet'] = self._participant('quiet@x.test', self.NOW, self.NOW)
        for day in range(7, 14):
            self._day(faults['quiet'], day, sent_n=2)
        for day in range(7):
            self._day(faults['quiet'], day, sent_n=0)

        make_decision(healthy, self.NOW - timedelta(hours=1))
        evaluate_alerts(now=self.NOW)

        self.assertEqual(self._open_rules(), {
            # Cohort-scoped: a run-in violation is a fact about the engine, not
            # a participant. One alert, not one each.
            (None, 'runin_violation'),
            (faults['cool'].pk, 'cooldown_violation'),
            (faults['cap'].pk, 'cap_exceeded'),
            (faults['stale'].pk, 'sync_stale'),
            (faults['dead'].pk, 'sync_stale'),
            (faults['lowcov'].pk, 'slot_coverage_low'),
            (faults['silent'].pk, 'no_ema_48h'),
            (faults['fail'].pk, 'delivery_failures'),
            (faults['nowear'].pk, 'wear_low'),
            (faults['quiet'].pk, 'dosage_collapse'),
            (None, 'no_wearable_data'),
            (None, 'no_hrv_data'),
        })
        self.assertEqual(
            Alert.objects.get(user=faults['stale'], rule_id='sync_stale').severity, 'warning')
        self.assertEqual(
            Alert.objects.get(user=faults['dead'], rule_id='sync_stale').severity, 'high')

    def test_re_running_changes_nothing(self):
        """Regression: payloads once held elapsed hours, so every poll looked
        like a change and rewrote the row 144 times a day."""
        user = self._participant('s@x.test', last_sync=self.NOW - timedelta(hours=30),
                                 last_ema=self.NOW - timedelta(hours=60))
        evaluate_alerts(now=self.NOW)
        opened, updated, resolved = evaluate_alerts(now=self.NOW + timedelta(minutes=10))
        self.assertEqual((opened, updated, resolved), (0, 0, 0))
        self.assertEqual(
            Alert.objects.filter(user=user, resolved_at__isnull=True).count(), 2)

    def test_escalation_keeps_the_incident(self):
        user = self._participant('e@x.test', last_sync=self.NOW - timedelta(hours=30),
                                 last_ema=self.NOW)
        evaluate_alerts(now=self.NOW)
        fired_at = Alert.objects.get(user=user, rule_id='sync_stale').fired_at

        evaluate_alerts(now=self.NOW + timedelta(hours=50))
        alert = Alert.objects.get(user=user, rule_id='sync_stale', resolved_at__isnull=True)
        # High, not critical: one silent device is data being lost now, but the
        # trial is intact and the pipeline is up. Critical is reserved for those.
        self.assertEqual(alert.severity, 'high')
        self.assertEqual(alert.fired_at, fired_at)

    def test_resolve_then_refire_opens_a_new_incident(self):
        user = self._participant('h@x.test', last_sync=self.NOW, last_ema=self.NOW)
        make_decision(user, self.NOW - timedelta(hours=1))
        evaluate_alerts(now=self.NOW)
        self.assertTrue(Alert.objects.filter(rule_id='no_wearable_data',
                                             resolved_at__isnull=True).exists())

        HeartRateSample.objects.create(user=user, timestamp=self.NOW, bpm=70)
        evaluate_alerts(now=self.NOW + timedelta(minutes=10))
        self.assertIsNotNone(Alert.objects.get(rule_id='no_wearable_data').resolved_at)

        HeartRateSample.objects.all().delete()
        evaluate_alerts(now=self.NOW + timedelta(minutes=20))
        self.assertEqual(Alert.objects.filter(rule_id='no_wearable_data').count(), 2)
        self.assertEqual(Alert.objects.filter(rule_id='no_wearable_data',
                                              resolved_at__isnull=True).count(), 1)

    def test_sync_stale_is_unmeasurable_when_nothing_writes_the_clock(self):
        """Nothing writes last_synced_at today: the mobile app does not call
        /wearable/ and ingest_wearable_data is a stub. Measuring staleness from
        enrollment would turn every participant critical 72 hours in and keep
        them there all study, which is a fact about the pipeline wearing a
        per-participant costume."""
        for index in range(4):
            self._participant(f'um{index}@x.test', last_sync=None, last_ema=self.NOW)
        evaluate_alerts(now=self.NOW)

        self.assertEqual(
            Alert.objects.filter(rule_id='sync_stale', user__isnull=False).count(), 0)
        cohort = Alert.objects.get(rule_id='sync_stale', user__isnull=True)
        self.assertEqual(cohort.severity, 'critical')
        self.assertIn('no writer', cohort.payload['detail'])

    def test_one_reported_sync_restores_the_per_participant_rule(self):
        stale = self._participant('sy@x.test', last_sync=self.NOW - timedelta(hours=30),
                                  last_ema=self.NOW)
        evaluate_alerts(now=self.NOW)
        self.assertFalse(
            Alert.objects.filter(rule_id='sync_stale', user__isnull=True,
                                 resolved_at__isnull=True).exists())
        self.assertTrue(
            Alert.objects.filter(rule_id='sync_stale', user=stale,
                                 resolved_at__isnull=True).exists())

    def test_runin_violation_is_one_alert_however_many_participants(self):
        for index in range(3):
            user = self._participant(f'rv{index}@x.test', last_sync=self.NOW, last_ema=self.NOW)
            self._day(user, index, runin_violation_n=2)
        evaluate_alerts(now=self.NOW)

        alerts = Alert.objects.filter(rule_id='runin_violation', resolved_at__isnull=True)
        self.assertEqual(alerts.count(), 1)
        alert = alerts.get()
        self.assertIsNone(alert.user_id)
        self.assertEqual(alert.payload['participants'], 3)
        self.assertEqual(
            alert.payload['prompts'],
            MetricsDaily.objects.aggregate(n=Sum('runin_violation_n'))['n'])

    def test_open_alert_cannot_duplicate(self):
        user = self._participant('u@x.test', last_sync=self.NOW, last_ema=self.NOW)
        Alert.objects.create(user=user, rule_id='sync_stale', severity='warning')
        with self.assertRaises(IntegrityError), transaction.atomic():
            Alert.objects.create(user=user, rule_id='sync_stale', severity='warning')

    def test_open_cohort_alert_cannot_duplicate(self):
        """A null user makes SQL treat the rows as distinct, so the cohort case
        needs its own partial constraint."""
        Alert.objects.create(rule_id='pipeline_stalled', severity='critical')
        with self.assertRaises(IntegrityError), transaction.atomic():
            Alert.objects.create(rule_id='pipeline_stalled', severity='critical')

    def test_pipeline_stall_is_not_reported_overnight(self):
        user = self._participant('n@x.test', last_sync=self.NOW, last_ema=self.NOW)
        self.assertTrue(user)
        for hour, expected in [(3, False), (8, False), (12, False), (14, False),
                               (15, True), (20, True), (21, False), (23, False)]:
            moment = datetime(2026, 9, 20, hour, 0, tzinfo=EASTERN)
            self.assertEqual(bool(pipeline_stalled(Context(moment))), expected, f'{hour}:00')

    def test_liveness_rules_stay_quiet_with_nobody_enrolled(self):
        self.assertEqual(pipeline_stalled(Context(
            datetime(2026, 9, 20, 16, 0, tzinfo=EASTERN))), [])


# ---------------------------------------------------------------------------
# Recompute task
# ---------------------------------------------------------------------------

class RecomputeTests(TestCase):
    def setUp(self):
        self.today = w.today_local()
        self.day1 = self.today - timedelta(days=5)
        enrolled = datetime(self.day1.year, self.day1.month, self.day1.day,
                            10, 0, tzinfo=EASTERN)
        self.users = [make_participant(f'rc{i}@x.test', enrolled) for i in range(3)]

    def test_trailing_window_and_idempotence(self):
        summary = recompute_metrics()
        self.assertEqual(summary['participants'], 3)
        self.assertEqual(summary['daily_rows'], 9)          # 3 participants x 3 days
        self.assertEqual(summary['failed'], 0)
        self.assertEqual(MetricsCohort.objects.count(), 3)

        recompute_metrics()
        self.assertEqual(MetricsDaily.objects.count(), 9)

    def test_all_days_stops_at_today(self):
        recompute_metrics(all_days=True)
        self.assertEqual(MetricsDaily.objects.count(), 3 * 6)
        self.assertFalse(MetricsDaily.objects.filter(local_date__gt=self.today).exists())

    def test_one_bad_participant_does_not_stop_the_run(self):
        import dashboard.tasks as tasks_module
        original = tasks_module.compute_daily
        broken = self.users[0]

        def explode(user, *args, **kwargs):
            if user.pk == broken.pk:
                raise ValueError('synthetic failure')
            return original(user, *args, **kwargs)

        tasks_module.compute_daily = explode
        try:
            with self.assertLogs('dashboard.tasks', level='ERROR'):
                summary = recompute_metrics()
        finally:
            tasks_module.compute_daily = original

        self.assertEqual(summary['failed'], 1)
        self.assertEqual(summary['participants'], 2)
        self.assertEqual(MetricsCohort.objects.count(), 3)

    def test_old_cohort_snapshots_are_pruned(self):
        recompute_metrics()
        snapshot = MetricsCohort.objects.filter(phase_filter='all').first()
        stale = MetricsCohort.objects.create(**{
            **{f.name: getattr(snapshot, f.name)
               for f in MetricsCohort._meta.fields if f.name != 'id'},
            'as_of': snapshot.as_of - timedelta(days=45),
        })
        recompute_metrics()
        self.assertFalse(MetricsCohort.objects.filter(pk=stale.pk).exists())

    def test_management_command_accepts_a_user_filter(self):
        out = StringIO()
        call_command('recompute_metrics', '--user', str(self.users[0].pk),
                     '--days', '2', stdout=out)
        self.assertEqual(MetricsDaily.objects.filter(user=self.users[0]).count(), 2)
        self.assertEqual(MetricsDaily.objects.exclude(user=self.users[0]).count(), 0)

    def test_management_command_rejects_an_unknown_user(self):
        with self.assertRaises(CommandError):
            call_command('recompute_metrics', '--user', '999999', stdout=StringIO())


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

class TestEnvironmentTests(TestCase):
    def test_api_keys_are_neutralised_for_the_whole_run(self):
        """Regression: exporting API_KEY for a local server or the dress
        rehearsal used to fail ~97 tests on a healthy tree, because
        APIKeyMiddleware starts enforcing X-API-Key and all but two test classes
        assume it is off. ReactTestRunner blanks both keys; this is what makes
        that fix visible instead of silent."""
        self.assertEqual(settings.API_KEY, '')
        self.assertEqual(settings.DASHBOARD_API_KEY, '')

    @override_settings(API_KEY='layered-on-top')
    def test_override_settings_still_wins(self):
        self.assertEqual(settings.API_KEY, 'layered-on-top')


@override_settings(API_KEY='test-api-key', DASHBOARD_API_KEY=DASHBOARD_KEY)
class MonitorEndpointTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.headers = {'HTTP_X_DASHBOARD_API_KEY': DASHBOARD_KEY}
        self.today = w.today_local()
        day1 = self.today - timedelta(days=4)
        enrolled = datetime(day1.year, day1.month, day1.day, 10, 0, tzinfo=EASTERN)
        self.user = make_participant('api@x.test', enrolled)
        make_ema(self.user, datetime(self.today.year, self.today.month, self.today.day,
                                     10, 15, tzinfo=EASTERN), answers={'B1_valence': 5})
        make_reminder(self.user, 0, datetime(self.today.year, self.today.month,
                                             self.today.day, 9, 30, tzinfo=EASTERN))
        recompute_metrics(all_days=True)

    def _paths(self):
        return [
            '/api/monitor/cohort',
            '/api/monitor/grid',
            '/api/monitor/alerts',
            f'/api/monitor/participant/{self.user.pk}',
            f'/api/monitor/participant/{self.user.pk}/timeline',
        ]

    def test_dashboard_key_is_required_and_sufficient(self):
        for path in self._paths():
            self.assertEqual(self.client.get(path).status_code, 403, path)
            self.assertEqual(self.client.get(path, **self.headers).status_code, 200, path)

    def test_wrong_key_is_rejected(self):
        response = self.client.get('/api/monitor/cohort',
                                   HTTP_X_DASHBOARD_API_KEY='nope')
        self.assertEqual(response.status_code, 403)

    def test_existing_dashboard_routes_still_work(self):
        self.assertEqual(
            self.client.get('/dashboard/participants/', **self.headers).status_code, 200)

    def test_cohort_reports_absence_without_a_404(self):
        MetricsCohort.objects.all().delete()
        body = self.client.get('/api/monitor/cohort', **self.headers).json()
        self.assertFalse(body['available'])
        self.assertIn('recompute_metrics', body['detail'])

    def test_cohort_rejects_an_unknown_phase(self):
        self.assertEqual(
            self.client.get('/api/monitor/cohort?phase=bogus', **self.headers).status_code, 400)

    def test_grid_keeps_null_and_zero_distinct(self):
        body = self.client.get('/api/monitor/grid?metric=slots_covered',
                               **self.headers).json()
        row = body['rows'][0]
        self.assertEqual(len(row['values']), STUDY_DAYS)
        # Five days lived, the rest not yet reached.
        self.assertTrue(all(value is not None for value in row['values'][:5]))
        self.assertTrue(all(value is None for value in row['values'][5:]))
        self.assertEqual(row['values'][0], 0)

    def test_grid_overlays_align_with_values_and_null_out_together(self):
        body = self.client.get('/api/monitor/grid?metric=wear_valid_pct',
                               **self.headers).json()
        row = body['rows'][0]
        overlays = ['local_dates', 'run_in', 'covered', 'reminded', 'silent', 'alert']
        for key in overlays:
            self.assertEqual(len(row[key]), STUDY_DAYS, key)
            # An overlay may only carry a value where the participant lived the
            # day. Structural blank has to reach the wire on every array, not
            # just on values, or the chart draws marks on days that never were.
            for index, entry in enumerate(row[key]):
                lived = index < 5
                self.assertEqual(entry is not None, lived, f'{key}[{index}]')

    def test_grid_three_way_split_sums_to_the_slots_expected(self):
        body = self.client.get('/api/monitor/grid?metric=slots_expected',
                               **self.headers).json()
        row = body['rows'][0]
        for index in range(5):
            self.assertEqual(
                row['covered'][index] + row['reminded'][index] + row['silent'][index],
                row['values'][index], f'day {index}')

    def test_grid_run_in_flag_marks_the_first_seven_days(self):
        body = self.client.get('/api/monitor/grid', **self.headers).json()
        self.assertTrue(all(body['rows'][0]['run_in'][:5]))

    def test_grid_reports_the_daily_prompt_cap_for_the_ordinal_scale(self):
        body = self.client.get('/api/monitor/grid', **self.headers).json()
        self.assertEqual(body['daily_prompt_cap'], DAILY_PROMPT_CAP)

    def test_grid_places_an_open_alert_on_its_own_firing_day(self):
        # The recompute in setUp opens its own alerts on today's cell, so clear
        # them first and let this test speak only to the date mapping.
        Alert.objects.all().delete()
        fired = datetime(self.today.year, self.today.month, self.today.day,
                         12, 0, tzinfo=EASTERN) - timedelta(days=2)
        Alert.objects.create(user=self.user, rule_id='wear_low', severity='critical',
                             fired_at=fired)
        row = self.client.get('/api/monitor/grid', **self.headers).json()['rows'][0]
        self.assertEqual([index for index, flag in enumerate(row['alert']) if flag], [2])

    def test_grid_ignores_cohort_alerts_which_belong_to_no_cell(self):
        Alert.objects.all().delete()
        Alert.objects.create(user=None, rule_id='pipeline_stalled', severity='critical')
        row = self.client.get('/api/monitor/grid', **self.headers).json()['rows'][0]
        self.assertFalse(any(flag for flag in row['alert'] if flag))

    def test_grid_serves_every_metric_field(self):
        """slots_covered, slots_reminded_uncovered and slots_silent are both
        selectable metrics and overlay columns, so the underlying values_list
        asks for the same field twice. Every metric has to survive that."""
        for metric in sorted(METRIC_FIELDS):
            response = self.client.get(f'/api/monitor/grid?metric={metric}', **self.headers)
            self.assertEqual(response.status_code, 200, metric)
            row = response.json()['rows'][0]
            self.assertEqual(len(row['values']), STUDY_DAYS, metric)
            self.assertEqual(len(row['covered']), STUDY_DAYS, metric)

    def test_grid_rejects_an_unknown_metric(self):
        response = self.client.get('/api/monitor/grid?metric=nope', **self.headers)
        self.assertEqual(response.status_code, 400)
        self.assertIn('available', response.json())

    def test_participant_detail_and_404(self):
        body = self.client.get(f'/api/monitor/participant/{self.user.pk}',
                               **self.headers).json()
        self.assertEqual(body['participant']['phase'], 'run_in')
        self.assertEqual(len(body['daily']), 5)
        self.assertEqual(
            self.client.get('/api/monitor/participant/999999', **self.headers).status_code, 404)

    def test_timeline_is_chronological_and_slot_aware(self):
        body = self.client.get(
            f'/api/monitor/participant/{self.user.pk}/timeline?date={self.today}',
            **self.headers).json()
        self.assertEqual(len(body['slots']), 6)
        self.assertTrue(body['slots'][0]['covered'])
        self.assertTrue(body['slots'][0]['reminded'])
        self.assertFalse(body['slots'][5]['covered'])
        moments = [event['at'] for event in body['events']]
        self.assertEqual(moments, sorted(moments))

    def test_timeline_rejects_a_bad_date(self):
        response = self.client.get(
            f'/api/monitor/participant/{self.user.pk}/timeline?date=04/09/2026',
            **self.headers)
        self.assertEqual(response.status_code, 400)

    def test_alerts_summarise_by_severity(self):
        Alert.objects.create(user=self.user, rule_id='wear_low', severity='warning')
        body = self.client.get('/api/monitor/alerts', **self.headers).json()
        for tier in ('critical', 'high', 'warning'):
            self.assertEqual(body[tier], sum(
                1 for alert in body['alerts'] if alert['severity'] == tier), tier)
        self.assertEqual(body['open'], len(body['alerts']))

    def test_alert_link_date_prefers_the_offending_day(self):
        """Rules that know which days offended say so in the payload, and that is
        the day someone wants the timeline to open on. fired_at is the fallback,
        not the answer."""
        Alert.objects.all().delete()
        Alert.objects.create(user=self.user, rule_id='wear_low', severity='high',
                             payload={'days': {'2026-09-03': 0.1, '2026-09-02': 0.2}})
        Alert.objects.create(user=self.user, rule_id='no_ema_48h', severity='high',
                             payload={'last_ema_at': None})
        body = self.client.get('/api/monitor/alerts', **self.headers).json()
        links = {alert['rule_id']: alert['link_date'] for alert in body['alerts']}
        self.assertEqual(links['wear_low'], '2026-09-02')
        self.assertEqual(links['no_ema_48h'], w.today_local().isoformat())

    def test_alerts_are_ordered_by_the_severity_ladder(self):
        """Ordering on the column itself puts critical before warning only by
        accident of spelling, and drops high between them rather than after
        critical."""
        Alert.objects.all().delete()
        for rule_id, severity in (('wear_low', 'warning'), ('no_ema_48h', 'critical'),
                                  ('delivery_failures', 'high')):
            Alert.objects.create(user=self.user, rule_id=rule_id, severity=severity)
        body = self.client.get('/api/monitor/alerts', **self.headers).json()
        self.assertEqual([alert['severity'] for alert in body['alerts']],
                         ['critical', 'high', 'warning'])


# ---------------------------------------------------------------------------
# Participant timeline (Stage 3)
# ---------------------------------------------------------------------------

class TimelineTests(TestCase):
    LOCAL = date(2026, 9, 15)

    def setUp(self):
        enrolled = datetime(2026, 9, 11, 10, 0, tzinfo=EASTERN)
        self.user = make_participant('tl@x.test', enrolled)
        self.day_start, _ = w.participant_day_bounds(self.LOCAL)

    def _at(self, hour, minute=0):
        # hour=24 means the next midnight, so a span can reach the end of day.
        midnight = datetime(self.LOCAL.year, self.LOCAL.month, self.LOCAL.day,
                            0, 0, tzinfo=EASTERN)
        return midnight + timedelta(hours=hour, minutes=minute)

    def _wear(self, spans):
        for start_hour, end_hour in spans:
            moment = self._at(start_hour)
            while moment < self._at(end_hour):
                HeartRateSample.objects.create(user=self.user, timestamp=moment, bpm=70)
                moment += timedelta(minutes=1)

    def test_wear_runs_reconstruct_the_covered_minutes(self):
        self._wear([(9, 10), (12, 13)])
        lane = tl.wear_lane(self.user, self.LOCAL)
        self.assertEqual(
            [(span['start'], span['end']) for span in lane['covered']],
            [(9 * 60, 10 * 60), (12 * 60, 13 * 60)])
        self.assertEqual(lane['covered_minutes'], 120)
        self.assertTrue(lane['has_data'])

    def test_a_gap_is_measured_only_inside_the_waking_window(self):
        """A gap that straddles the window edge must not be counted whole: only
        the part inside 08:00-22:00 belongs to the wear denominator."""
        self._wear([(6, 7), (23, 24)])
        lane = tl.wear_lane(self.user, self.LOCAL)
        straddling = next(g for g in lane['gaps'] if g['start'] == 7 * 60)
        self.assertEqual(straddling['minutes'], 16 * 60)        # 07:00 to 23:00
        self.assertEqual(straddling['waking_minutes'], 14 * 60)  # 08:00 to 22:00
        self.assertTrue(straddling['over_threshold'])

    def test_an_overnight_gap_is_not_a_compliance_problem(self):
        self._wear([(8, 22)])
        lane = tl.wear_lane(self.user, self.LOCAL)
        self.assertTrue(all(not gap['over_threshold'] for gap in lane['gaps']))

    def test_no_samples_reports_absence_rather_than_zero_wear(self):
        lane = tl.wear_lane(self.user, self.LOCAL)
        self.assertFalse(lane['has_data'])
        self.assertEqual(lane['covered'], [])

    def test_sync_lane_separates_no_history_from_a_quiet_device(self):
        empty = tl.sync_lane(self.user, self.LOCAL)
        self.assertFalse(empty['observed'])

        WearableSync.objects.create(
            user=self.user, observed_at=self._at(9), source='ingest',
            last_synced_at=self._at(9), samples_written=40)
        WearableSync.objects.create(
            user=self.user, observed_at=self._at(11), source='client',
            last_synced_at=self._at(11))
        lane = tl.sync_lane(self.user, self.LOCAL)
        self.assertTrue(lane['observed'])
        self.assertEqual(len(lane['advances']), 2)
        self.assertEqual([a['source'] for a in lane['advances']], ['ingest', 'client'])

    def test_sync_lane_carries_in_the_value_from_before_midnight(self):
        WearableSync.objects.create(
            user=self.user, observed_at=self._at(9) - timedelta(days=1),
            source='ingest', last_synced_at=self._at(9) - timedelta(days=1))
        lane = tl.sync_lane(self.user, self.LOCAL)
        self.assertIsNotNone(lane['carried_in'])
        self.assertEqual(lane['advances'], [])
        # Observed but silent: the step trace starts somewhere and stays flat,
        # which is what an outage looks like.
        self.assertTrue(lane['observed'])

    def test_record_sync_only_appends_when_the_clock_advances(self):
        self.assertIsNotNone(record_sync(self.user, self._at(9), 'ingest'))
        self.assertIsNone(record_sync(self.user, self._at(9), 'ingest'))
        self.assertIsNone(record_sync(self.user, self._at(8), 'ingest'))
        self.assertIsNotNone(record_sync(self.user, self._at(10), 'client'))
        self.assertEqual(WearableSync.objects.filter(user=self.user).count(), 2)

    def test_completeness_matrix_agrees_with_the_daily_metric(self):
        """If these disagree the grid and the timeline tell different stories
        about one number, so both score through _askable_sub_item_ids."""
        make_ema(self.user, self._at(9, 30),
                 answers={'B1_valence': 5, 'B1_arousal': 4},
                 served=['B1_valence', 'B1_arousal', 'B2_stress'])
        emas = list(EMA.objects.filter(user=self.user).prefetch_related('item_responses'))
        row = tl.completeness_matrix(emas)[0]

        self.assertEqual(sorted(row['answered']), ['B1_arousal', 'B1_valence'])
        self.assertAlmostEqual(row['completeness'], 2 / 3)
        daily = compute_daily(self.user, self.LOCAL, study_day=4)
        self.assertAlmostEqual(daily['completeness_mean'], row['completeness'])

    def test_a_check_in_without_b1_and_b2_is_flagged_as_a_hole_in_the_mrt(self):
        make_ema(self.user, self._at(9, 30), answers={'B3_sleep': 5},
                 served=['B3_sleep'])
        make_ema(self.user, self._at(13, 30),
                 answers={'B1_valence': 5, 'B1_arousal': 4, 'B2_stress': 3},
                 served=['B1_valence', 'B1_arousal', 'B2_stress'])
        emas = list(EMA.objects.filter(user=self.user)
                    .prefetch_related('item_responses').order_by('sent_at'))
        flags = [row['missing_signal'] for row in tl.completeness_matrix(emas)]
        self.assertEqual(flags, [True, False])

    def test_mssd_lane_flags_an_engine_row_that_should_have_been_eligible(self):
        make_decision(self.user, self._at(10), send=False,
                      reason='below within-person threshold',
                      observed_mssd=9.0, threshold_at_decision=4.0,
                      threshold_source='engine')
        point = tl.mssd_lane(self.user, self.LOCAL)[0]
        self.assertTrue(point['unexplained'])

    def test_mssd_lane_will_not_accuse_the_engine_on_a_reconstructed_threshold(self):
        """A replayed threshold can disagree with the engine for reasons that are
        not the engine's fault, so it is never grounds for the accusation."""
        make_decision(self.user, self._at(10), send=False,
                      reason='below within-person threshold',
                      observed_mssd=9.0, threshold_at_decision=4.0,
                      threshold_source='reconstructed')
        point = tl.mssd_lane(self.user, self.LOCAL)[0]
        self.assertFalse(point['unexplained'])

    def test_mssd_lane_carries_the_suppression_label(self):
        make_decision(self.user, self._at(10), send=False, reason='run-in period',
                      suppression_reason='run_in', observed_mssd=9.0,
                      threshold_at_decision=4.0, threshold_source='engine')
        make_decision(self.user, self._at(11), send=False, reason='cooldown active')
        suppressed, ordinary = tl.mssd_lane(self.user, self.LOCAL)
        self.assertEqual(suppressed['suppression_reason'], 'run_in')
        self.assertFalse(suppressed['unexplained'])
        self.assertIsNone(ordinary['suppression_reason'])

    def test_mssd_lane_leaves_a_missing_threshold_as_a_gap(self):
        make_decision(self.user, self._at(10), send=False,
                      reason='insufficient within-person history', observed_mssd=9.0)
        point = tl.mssd_lane(self.user, self.LOCAL)[0]
        self.assertIsNone(point['threshold'])
        self.assertFalse(point['unexplained'])

    def test_delivery_funnel_stages_never_increase(self):
        make_decision(self.user, self._at(10), send=True,
                      device_received_at=self._at(10, 1),
                      receipt_reported_at=self._at(10, 2),
                      receipt_platform='ios', receipt_app_state='background')
        make_decision(self.user, self._at(12), send=True)
        make_decision(self.user, self._at(14), send=False, reason='cooldown active')

        funnel = tl.delivery_funnel(self.user)
        counts = [stage['n'] for stage in funnel['stages']]
        self.assertEqual(counts, sorted(counts, reverse=True))
        self.assertEqual(counts[0], 2)
        self.assertEqual(dict(zip([s['stage'] for s in funnel['stages']], counts))['device_received'], 1)
        self.assertEqual(funnel['by_platform'], [{'receipt_platform': 'ios', 'n': 1}])

    def test_a_prompt_that_skips_a_stage_cannot_inflate_a_later_one(self):
        """Real production data carries 20 prompts with a device receipt and only
        4 with a push timestamp. Filtering each stage independently made
        device_received larger than the push_sent above it, which is not a
        waterfall. Stages nest; the skipped rows are reported as anomalies."""
        log = make_decision(self.user, self._at(10), send=True,
                            device_received_at=self._at(10, 1),
                            receipt_reported_at=self._at(10, 2))
        JITAILog.objects.filter(pk=log.pk).update(push_sent_at=None)
        EngagementLog.objects.create(user=self.user, jitai_log=log,
                                     event_type='notification_tapped',
                                     occurred_at=self._at(10, 3))

        funnel = tl.delivery_funnel(self.user)
        stages = {stage['stage']: stage['n'] for stage in funnel['stages']}
        self.assertEqual(stages['eligible_sent'], 1)
        self.assertEqual(stages['push_sent'], 0)
        # Everything downstream of the skipped stage is 0, not 1.
        self.assertEqual(stages['device_received'], 0)
        self.assertEqual(stages['receipt_reported'], 0)
        self.assertEqual(stages['engaged'], 0)
        counts = [stage['n'] for stage in funnel['stages']]
        self.assertEqual(counts, sorted(counts, reverse=True))
        # The prompt is not lost: it is counted where it actually went wrong.
        self.assertEqual(funnel['anomalies']['received_without_push'], 1)
        self.assertEqual(funnel['anomalies']['engaged_without_receipt'], 1)


@override_settings(API_KEY='test-api-key', DASHBOARD_API_KEY=DASHBOARD_KEY)
class TimelineEndpointTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.headers = {'HTTP_X_DASHBOARD_API_KEY': DASHBOARD_KEY}
        self.today = w.today_local()
        enrolled = datetime(self.today.year, self.today.month, self.today.day,
                            10, 0, tzinfo=EASTERN) - timedelta(days=4)
        self.user = make_participant('tle@x.test', enrolled)

    def test_decision_events_carry_the_suppression_label(self):
        make_decision(self.user, datetime(self.today.year, self.today.month, self.today.day,
                                          10, 0, tzinfo=EASTERN),
                      send=False, reason='distress override (momentary)',
                      suppression_reason='distress_momentary', status='suppressed',
                      delivery_status='suppressed')
        body = self.client.get(
            f'/api/monitor/participant/{self.user.pk}/timeline', **self.headers).json()
        decisions = [event for event in body['events'] if event['kind'] == 'decision']
        self.assertEqual(decisions[0]['suppression_reason'], 'distress_momentary')
        self.assertFalse(decisions[0]['eligible'])

    def test_grid_serves_the_suppressed_metric(self):
        response = self.client.get('/api/monitor/grid?metric=suppressed_n', **self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertIn('suppressed_n', METRIC_FIELDS)

    def test_multi_day_returns_oldest_first(self):
        body = self.client.get(
            f'/api/monitor/participant/{self.user.pk}/timeline?days=7',
            **self.headers).json()
        dates = [day['local_date'] for day in body['days']]
        self.assertEqual(len(dates), 7)
        self.assertEqual(dates, sorted(dates))
        self.assertEqual(dates[-1], self.today.isoformat())

    def test_single_day_keeps_the_original_flat_shape(self):
        body = self.client.get(
            f'/api/monitor/participant/{self.user.pk}/timeline', **self.headers).json()
        self.assertNotIn('days', body)
        for key in ('slots', 'wear', 'sync', 'mssd', 'completeness', 'events'):
            self.assertIn(key, body)

    def test_days_is_bounded(self):
        for value in ('0', '99', 'many'):
            response = self.client.get(
                f'/api/monitor/participant/{self.user.pk}/timeline?days={value}',
                **self.headers)
            self.assertEqual(response.status_code, 400, value)

    def test_funnel_endpoint_is_served_and_gated(self):
        path = f'/api/monitor/participant/{self.user.pk}/funnel'
        self.assertEqual(self.client.get(path).status_code, 403)
        body = self.client.get(path, **self.headers).json()
        self.assertEqual([s['stage'] for s in body['stages']],
                         ['eligible_sent', 'push_sent', 'device_received',
                          'receipt_reported', 'engaged'])

    def test_funnel_rejects_an_unknown_participant(self):
        self.assertEqual(
            self.client.get('/api/monitor/participant/999999/funnel',
                            **self.headers).status_code, 404)


# ---------------------------------------------------------------------------
# Withdrawal backfill
# ---------------------------------------------------------------------------

class EnrollmentStampTests(TestCase):
    """enrolled_at is what every study day is counted from, and nothing used to
    set it: it is exposed on the serializer and nowhere else, so ticking the box
    in Django Admin left it NULL and the participant vanished from every metric.
    """

    ADMIN_TICK = datetime(2026, 8, 27, 4, 2, tzinfo=UTC)
    FIRST_EMA = datetime(2026, 8, 21, 4, 57, tzinfo=UTC)

    def setUp(self):
        self.staff = AuthUser.objects.create_user(username='ra2', password='pw', is_staff=True)
        self.content_type = ContentType.objects.get_for_model(User)

    def _user(self, email, is_enrolled=False):
        return User.objects.create(email=email, birthdate=date(2005, 1, 1),
                                   gender='other', is_enrolled=is_enrolled)

    def _tick(self, user, when):
        entry = LogEntry.objects.create(
            user=self.staff, content_type=self.content_type, object_id=str(user.pk),
            object_repr=str(user), action_flag=CHANGE,
            change_message='[{"changed": {"fields": ["Is enrolled"]}}]')
        LogEntry.objects.filter(pk=entry.pk).update(action_time=when)

    def test_enrolling_stamps_the_timestamp(self):
        user = self._user('e1@x.test')
        self.assertIsNone(user.enrolled_at)
        user.is_enrolled = True
        user.save()
        self.assertIsNotNone(User.objects.get(pk=user.pk).enrolled_at)

    def test_a_user_created_already_enrolled_gets_one(self):
        user = self._user('e2@x.test', is_enrolled=True)
        self.assertIsNotNone(user.enrolled_at)

    def test_day_zero_never_moves_once_set(self):
        """Re-saving, or unenrolling and re-enrolling, must not renumber the
        study days of data already collected."""
        user = self._user('e3@x.test', is_enrolled=True)
        original = user.enrolled_at
        user.save()
        user.is_enrolled = False
        user.save()
        user.is_enrolled = True
        user.save()
        self.assertEqual(User.objects.get(pk=user.pk).enrolled_at, original)

    def test_update_fields_still_writes_the_stamp(self):
        user = self._user('e4@x.test')
        user.is_enrolled = True
        user.save(update_fields=['is_enrolled'])
        self.assertIsNotNone(User.objects.get(pk=user.pk).enrolled_at)

    def test_backfill_prefers_earliest_activity_over_a_later_admin_tick(self):
        """The production case: user 430's admin tick is 2026-08-27 but their
        first EMA is 2026-08-21, six days earlier. Stamping the tick would put
        those submissions at study day -6, where is_active_day is False and they
        disappear from every metric."""
        user = self._user('e5@x.test')
        User.objects.filter(pk=user.pk).update(is_enrolled=True)
        make_ema(user, self.FIRST_EMA)
        self._tick(user, self.ADMIN_TICK)

        call_command('backfill_enrollment', stdout=StringIO())
        self.assertEqual(User.objects.get(pk=user.pk).enrolled_at, self.FIRST_EMA)

    def test_backfill_leaves_unenrolled_users_alone_even_with_data(self):
        """Three production users have EMAs but are not enrolled. Stamping them
        would fabricate the denominator every benchmark divides by."""
        user = self._user('e6@x.test')
        make_ema(user, self.FIRST_EMA)
        call_command('backfill_enrollment', stdout=StringIO())
        self.assertIsNone(User.objects.get(pk=user.pk).enrolled_at)

    def test_backfill_dry_run_writes_nothing(self):
        user = self._user('e7@x.test')
        User.objects.filter(pk=user.pk).update(is_enrolled=True)
        make_ema(user, self.FIRST_EMA)
        call_command('backfill_enrollment', '--dry-run', stdout=StringIO())
        self.assertIsNone(User.objects.get(pk=user.pk).enrolled_at)


class BackfillWithdrawalsTests(TestCase):
    NOW = datetime(2026, 9, 20, 16, 0, tzinfo=UTC)
    ENROLLED = datetime(2026, 9, 1, 14, 0, tzinfo=UTC)
    WITHDRAWN_AT = datetime(2026, 9, 9, 11, 30, tzinfo=UTC)

    def setUp(self):
        self.staff = AuthUser.objects.create_user(username='ra', password='pw', is_staff=True)
        self.content_type = ContentType.objects.get_for_model(User)

    def _log(self, user, when, message):
        entry = LogEntry.objects.create(
            user=self.staff, content_type=self.content_type, object_id=str(user.pk),
            object_repr=str(user), action_flag=CHANGE, change_message=message)
        LogEntry.objects.filter(pk=entry.pk).update(action_time=when)

    def test_backdates_to_the_last_is_enrolled_toggle(self):
        user = make_participant('a@x.test', self.ENROLLED, is_enrolled=False, device=False)
        self._log(user, datetime(2026, 9, 5, 10, 0, tzinfo=UTC),
                  '[{"changed": {"fields": ["Push token"]}}]')
        self._log(user, self.WITHDRAWN_AT, '[{"changed": {"fields": ["Is enrolled"]}}]')
        MetricsParticipant.objects.create(user=user, **compute_participant(user, now=self.NOW))

        call_command('backfill_withdrawals', stdout=StringIO())
        self.assertEqual(
            MetricsParticipant.objects.get(user=user).first_seen_not_enrolled_at,
            self.WITHDRAWN_AT)

    def test_never_moves_a_timestamp_forward(self):
        user = make_participant('b@x.test', self.ENROLLED, is_enrolled=False, device=False)
        self._log(user, self.WITHDRAWN_AT, '[{"changed": {"fields": ["Is enrolled"]}}]')
        row = MetricsParticipant.objects.create(
            user=user, **compute_participant(user, now=self.NOW))
        earlier = datetime(2026, 9, 2, tzinfo=UTC)
        row.first_seen_not_enrolled_at = earlier
        row.save()

        call_command('backfill_withdrawals', stdout=StringIO())
        self.assertEqual(
            MetricsParticipant.objects.get(user=user).first_seen_not_enrolled_at, earlier)

    def test_ignores_unrelated_edits_and_enrolled_participants(self):
        noise = make_participant('c@x.test', self.ENROLLED, is_enrolled=False, device=False)
        self._log(noise, self.WITHDRAWN_AT, '[{"changed": {"fields": ["First name"]}}]')
        MetricsParticipant.objects.create(user=noise, **compute_participant(noise, now=self.NOW))

        still_in = make_participant('d@x.test', self.ENROLLED, device=False)
        self._log(still_in, self.WITHDRAWN_AT, '[{"changed": {"fields": ["Is enrolled"]}}]')
        MetricsParticipant.objects.create(
            user=still_in, **compute_participant(still_in, now=self.NOW))

        call_command('backfill_withdrawals', stdout=StringIO())
        self.assertNotEqual(
            MetricsParticipant.objects.get(user=noise).first_seen_not_enrolled_at,
            self.WITHDRAWN_AT)
        self.assertIsNone(
            MetricsParticipant.objects.get(user=still_in).first_seen_not_enrolled_at)

    def test_dry_run_writes_nothing(self):
        user = make_participant('e@x.test', self.ENROLLED, is_enrolled=False, device=False)
        self._log(user, self.WITHDRAWN_AT, '[{"changed": {"fields": ["Is enrolled"]}}]')
        row = MetricsParticipant.objects.create(
            user=user, **compute_participant(user, now=self.NOW))
        before = row.first_seen_not_enrolled_at

        call_command('backfill_withdrawals', '--dry-run', stdout=StringIO())
        self.assertEqual(
            MetricsParticipant.objects.get(user=user).first_seen_not_enrolled_at, before)



class RandomizationAuditRuleTests(TestCase):
    NOW = datetime(2026, 9, 20, 15, 0, tzinfo=EASTERN)

    def setUp(self):
        self.user = make_participant('audit-rule@x.test', datetime(2026, 9, 1, 10, 0, tzinfo=EASTERN))

    def _rule(self):
        return randomization_audit(Context(self.NOW))

    def test_suppressed_decisions_do_not_trip_the_rule(self):
        for reason, label in (('run_in', 'run-in period'),
                              ('distress_baseline', 'distress override (baseline)'),
                              ('distress_momentary', 'distress override (momentary)')):
            make_decision(self.user, self.NOW, send=False, reason=label,
                          suppression_reason=reason, status='suppressed',
                          delivery_status='suppressed')
        self.assertEqual(self._rule(), [])

    def test_an_unlabeled_drawless_decision_with_an_eligible_reason_still_trips_it(self):
        make_decision(self.user, self.NOW, send=False, reason='run-in period')
        findings = self._rule()
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].payload['eligibility_disagreements'], 1)

    def test_a_suppressed_row_carrying_a_draw_is_still_a_contradiction(self):
        JITAILog.objects.create(
            user=self.user, prompt_id='', trigger_reason='run-in period', send_prompt=False,
            suppression_reason='run_in', randomization_draw=0.3, randomization_probability=0.5,
            decision_made_at=self.NOW, status='suppressed', delivery_status='suppressed')
        self.assertEqual(len(self._rule()), 1)


# ---------------------------------------------------------------------------
# Account classification
# ---------------------------------------------------------------------------

class AccountClassificationTests(TestCase):
    ENROLLED = datetime(2026, 9, 1, 14, 0, tzinfo=UTC)

    def _account(self, email, study_id, enrolled=True):
        user = make_participant(email, self.ENROLLED if enrolled else None,
                                is_enrolled=enrolled, device=False)
        User.objects.filter(pk=user.pk).update(study_id=study_id)
        return User.objects.get(pk=user.pk)

    def test_classify(self):
        cases = {
            'RS01': accounts.STUDY,
            ' rs07 ': accounts.STUDY,
            'ST02': accounts.TEST,
            None: accounts.UNCLASSIFIED,
            '': accounts.UNCLASSIFIED,
            'XX9': accounts.UNCLASSIFIED,
            'TEST1': accounts.UNCLASSIFIED,
        }
        for study_id, expected in cases.items():
            with self.subTest(study_id=study_id):
                self.assertEqual(accounts.classify(study_id), expected)

    def test_querysets_partition_every_account(self):
        study = self._account('a@x.test', 'RS01')
        test = self._account('b@x.test', 'ST01')
        unlabelled = self._account('c@x.test', None)
        other = self._account('d@x.test', 'XX9')

        self.assertEqual(set(accounts.study_users()), {study})
        self.assertEqual(set(accounts.test_users()), {test})
        self.assertEqual(set(accounts.unclassified_users()), {unlabelled, other})
        self.assertEqual(accounts.account_counts(), {
            accounts.STUDY: 1, accounts.TEST: 1, accounts.UNCLASSIFIED: 2,
        })

    def test_lowercase_study_id_written_without_save_is_still_a_participant(self):
        user = self._account('a@x.test', 'rs03')
        self.assertEqual(list(accounts.study_users()), [user])
        self.assertFalse(accounts.unclassified_users().exists())

    def test_queryset_argument_narrows_the_result(self):
        enrolled = self._account('a@x.test', 'RS01')
        self._account('b@x.test', 'RS02', enrolled=False)
        only_enrolled = User.objects.filter(enrolled_at__isnull=False)
        self.assertEqual(list(accounts.study_users(only_enrolled)), [enrolled])
        self.assertEqual(accounts.account_counts(only_enrolled)[accounts.STUDY], 1)


# ---------------------------------------------------------------------------
# PI report sections
# ---------------------------------------------------------------------------

class ReportFixture:
    ENROLLED = datetime(2026, 9, 1, 14, 0, tzinfo=UTC)
    DAY = date(2026, 9, 10)
    NOW = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)

    def setUp(self):
        self.rs1 = self._account('a@x.test', 'RS01')
        self.rs2 = self._account('b@x.test', 'RS02')
        self.staff = self._account('s@x.test', 'ST01')
        self.sequence = 0

    def _account(self, email, study_id):
        user = make_participant(email, self.ENROLLED, device=False)
        User.objects.filter(pk=user.pk).update(study_id=study_id)
        return User.objects.get(pk=user.pk)

    def _at(self, hour, minute=0, day=None):
        local = day or self.DAY
        return datetime(local.year, local.month, local.day, hour, minute, tzinfo=EASTERN)

    def _decision(self, user, when, draw=0.3, send=None, p=0.5, **kwargs):
        self.sequence += 1
        if send is None:
            send = draw is not None and draw < p
        fields = {
            'user': user, 'prompt_id': 'P1' if send else '',
            'trigger_reason': kwargs.pop('trigger_reason', 'prompt sent'),
            'decision_point_id': kwargs.pop('decision_point_id', f'ema_{self.sequence}'),
            'randomization_draw': draw,
            'randomization_probability': p if draw is not None else None,
            'send_prompt': send, 'decision_made_at': when,
            'push_sent_at': when if send else None,
            'eligible_prompt_ids': ['P1', 'P2'] if draw is not None else None,
            'message_arm': 'coping' if send else None,
            'arm_randomization_draw': 0.2 if send else None,
        }
        fields.update(kwargs)
        return JITAILog.objects.create(**fields)

    def _rows(self, start=None, end=None):
        users = list(report.study_participants())
        return report.decision_rows(users, start or self.DAY, end or self.DAY)

    def _status(self, checks, name):
        return next(check['status'] for check in checks if check['name'] == name)


class ReportSectionTests(ReportFixture, TestCase):
    def test_test_accounts_are_left_out_of_every_count(self):
        self._decision(self.rs1, self._at(10))
        self._decision(self.staff, self._at(11))
        decisions = report.decision_points(self._rows())
        self.assertEqual(decisions['evaluated'], 1)

    def test_decision_counts_and_availability(self):
        self._decision(self.rs1, self._at(10), draw=0.3)
        self._decision(self.rs1, self._at(12), draw=0.8)
        self._decision(self.rs1, self._at(14), draw=None,
                       trigger_reason='below within-person threshold')
        self._decision(self.rs1, self._at(16), draw=None, trigger_reason='distress override (momentary)',
                       suppression_reason='distress_momentary')

        decisions = report.decision_points(self._rows())
        self.assertEqual(decisions['evaluated'], 4)
        self.assertEqual(decisions['passed_mssd_gate'], 3)
        self.assertEqual(decisions['available'], 2)
        self.assertEqual(decisions['unavailable_by_reason'], {
            'below within-person threshold': 1, 'Distress override (check-in)': 1})
        self.assertNotIn('eligible', decisions)
        self.assertEqual(decisions['sent'], 1)
        self.assertEqual(decisions['not_sent'], 1)
        self.assertEqual(decisions['suppressed_by_reason'], {'distress_momentary': 1})
        self.assertEqual(decisions['availability_rate'], 0.5)
        self.assertEqual(decisions['availability_status'], report.WATCH)

    def test_run_in_decisions_stay_out_of_the_availability_rate(self):
        run_in_day = date(2026, 9, 3)
        self._decision(self.rs1, self._at(10, day=run_in_day), draw=None,
                       trigger_reason='run-in period', suppression_reason='run_in')
        decisions = report.decision_points(self._rows(run_in_day, run_in_day))
        self.assertEqual(decisions['evaluated'], 1)
        self.assertEqual(decisions['mrt_evaluated'], 0)
        self.assertEqual(decisions['availability_status'], report.NO_DATA)

    def test_integrity_passes_on_clean_rows(self):
        self._decision(self.rs1, self._at(10), draw=0.3)
        self._decision(self.rs1, self._at(12), draw=0.8)
        checks = report.randomization_integrity(self._rows())
        self.assertEqual({check['status'] for check in checks}, {report.PASS})

    def test_integrity_fails_on_a_send_the_draw_did_not_allow(self):
        self._decision(self.rs1, self._at(10), draw=0.9, send=True)
        checks = report.randomization_integrity(self._rows())
        self.assertEqual(self._status(checks, 'Randomization executed'), report.FAIL)

    def test_no_prompt_available_is_a_message_gap_not_a_randomization_failure(self):
        self._decision(self.rs1, self._at(10), draw=0.3, send=False)
        integrity = report.randomization_integrity(self._rows())
        self.assertEqual(self._status(integrity, 'Randomization executed'), report.PASS)
        messages = report.message_eligibility(self._rows())
        self.assertEqual(
            self._status(messages['checks'], 'Message available when randomized to send'), report.WATCH)

    def test_prompt_outside_its_pool_fails(self):
        self._decision(self.rs1, self._at(10), draw=0.3, prompt_id='P9')
        messages = report.message_eligibility(self._rows())
        self.assertEqual(
            self._status(messages['checks'], 'Prompt drawn from its eligible pool'), report.FAIL)

    def test_coping_repeat_within_last_five_fails_even_across_the_period_boundary(self):
        earlier_day = self.DAY - timedelta(days=1)
        self._decision(self.rs1, self._at(15, day=earlier_day), draw=0.3, prompt_id='P2')
        self._decision(self.rs1, self._at(10), draw=0.3, prompt_id='P2')
        messages = report.message_eligibility(self._rows())
        self.assertEqual(
            self._status(messages['checks'], 'Last-5 exclusion honored (coping)'), report.FAIL)

    def test_coping_prompt_older_than_the_last_five_may_repeat(self):
        for hour, prompt_id in zip(range(9, 15), ['P2', 'P3', 'P4', 'P5', 'P6', 'P7']):
            self._decision(self.rs1, self._at(hour, day=self.DAY - timedelta(days=1)), draw=0.3,
                           prompt_id=prompt_id, eligible_prompt_ids=[prompt_id])
        self._decision(self.rs1, self._at(10), draw=0.3, prompt_id='P2', eligible_prompt_ids=['P2'])
        messages = report.message_eligibility(self._rows())
        self.assertEqual(
            self._status(messages['checks'], 'Last-5 exclusion honored (coping)'), report.PASS)

    def test_control_repeats_are_allowed_and_routing_is_counted(self):
        self._decision(self.rs1, self._at(9), draw=0.3, prompt_id='C001',
                       eligible_prompt_ids=['C001'], message_arm='control')
        self._decision(self.rs1, self._at(11), draw=0.3, prompt_id='C001',
                       eligible_prompt_ids=['C001'], message_arm='control')
        self._decision(self.rs1, self._at(13), draw=0.3, prompt_id='P020',
                       eligible_prompt_ids=['P020'], fallback_reason='closest_fit',
                       category_drawn='Urge / craving')
        messages = report.message_eligibility(self._rows())
        self.assertEqual(
            self._status(messages['checks'], 'Last-5 exclusion honored (coping)'), report.PASS)
        self.assertEqual(messages['arms'], {'control': 2, 'coping': 1})
        self.assertEqual(messages['routing'], {'Alcohol severity override (P020)': 1})
        self.assertEqual(messages['categories'], {'Urge / craving': 1})

    def test_integrity_flags_a_missing_pool_and_a_probability_on_a_suppressed_row(self):
        self._decision(self.rs1, self._at(10), draw=0.8, eligible_prompt_ids=None)
        self._decision(self.rs1, self._at(12), draw=None, randomization_probability=0.5,
                       trigger_reason='run-in period', suppression_reason='run_in')
        checks = report.randomization_integrity(self._rows())
        self.assertEqual(self._status(checks, 'Message pool recorded'), report.FAIL)
        self.assertEqual(self._status(checks, 'Randomization probability logged'), report.FAIL)

    def test_ks_needs_enough_draws(self):
        self._decision(self.rs1, self._at(10), draw=0.3)
        checks = report.randomization_integrity(self._rows(), include_ks=True)
        self.assertEqual(self._status(checks, 'Draws uniform on [0, 1] (KS)'), report.NO_DATA)

    def test_outcome_windows_split_by_arm(self):
        sent = self._decision(self.rs1, self._at(10), draw=0.3)
        held = self._decision(self.rs2, self._at(10), draw=0.8)
        make_ema(self.rs1, self._at(11, 10), ema_type='post_prompt', source=sent)
        reminder = CheckinReminder.objects.create(user=self.rs2, jitai_log=held)
        CheckinReminder.objects.filter(pk=reminder.pk).update(sent_at=self._at(11))

        outcome = report.outcome_windows(self._rows(), self.NOW)
        self.assertEqual(outcome['arms']['A']['closed'], 1)
        self.assertEqual(outcome['arms']['A']['captured'], 1)
        self.assertEqual(outcome['arms']['B']['closed'], 1)
        self.assertEqual(outcome['arms']['B']['reminded'], 1)
        self.assertEqual(outcome['arms']['B']['captured'], 0)
        self.assertEqual(self._status(outcome['checks'], 'Check-in within 2-hour window'), report.PASS)

    def test_assigned_to_send_with_no_message_is_in_neither_arm(self):
        self._decision(self.rs1, self._at(10), draw=0.3, send=False)
        outcome = report.outcome_windows(self._rows(), self.NOW)
        self.assertEqual(outcome['arms']['A']['windows'], 0)
        self.assertEqual(outcome['arms']['B']['windows'], 0)
        self.assertEqual(outcome['assigned_no_message'], 1)

    def _outcome_check_in(self, log, answered, start, end):
        ema = make_ema(self.rs1, answered, ema_type='extra_check_in', source=log)
        EMA.objects.filter(pk=ema.pk).update(outcome_window_start=start, outcome_window_end=end)

    def test_stored_window_bounds_are_decision_time_and_two_hours(self):
        log = self._decision(self.rs1, self._at(10), draw=0.8)
        self._outcome_check_in(log, self._at(11, 5), self._at(10), self._at(12))
        outcome = report.outcome_windows(self._rows(), self.NOW)
        self.assertEqual(self._status(outcome['checks'], 'Check-in within 2-hour window'), report.PASS)
        self.assertEqual(outcome['arms']['B']['captured'], 1)

    def test_window_anchored_on_the_push_is_flagged(self):
        log = self._decision(self.rs1, self._at(10), draw=0.3, push_sent_at=self._at(10, 20))
        self._outcome_check_in(log, self._at(11, 25), self._at(10, 20), self._at(12, 20))
        outcome = report.outcome_windows(self._rows(), self.NOW)
        self.assertEqual(self._status(outcome['checks'], 'Check-in within 2-hour window'), report.FAIL)

    def test_outcome_check_in_before_the_window_opens_is_flagged(self):
        sent = self._decision(self.rs1, self._at(10), draw=0.3)
        make_ema(self.rs1, self._at(10, 20), ema_type='post_prompt', source=sent)
        outcome = report.outcome_windows(self._rows(), self.NOW)
        self.assertEqual(outcome['arms']['A']['invalid_early'], 1)
        self.assertEqual(outcome['arms']['A']['missing'], 1)
        self.assertEqual(self._status(outcome['checks'], 'Check-in within 2-hour window'), report.FAIL)

    def test_an_open_window_is_not_scored(self):
        self._decision(self.rs1, self._at(10), draw=0.3)
        outcome = report.outcome_windows(self._rows(), self._at(11))
        self.assertEqual(outcome['arms']['A']['open'], 1)
        self.assertEqual(outcome['arms']['A']['closed'], 0)

    def test_cooldown_violation_names_the_participant(self):
        self._decision(self.rs1, self._at(10), draw=0.3)
        self._decision(self.rs1, self._at(10, 30), draw=0.3)
        self._decision(self.rs2, self._at(10, 15), draw=0.3)
        users = list(report.study_participants())
        cooldown = report.cooldown(self._rows(), report.labels_for(users))
        self.assertEqual(cooldown['status'], report.FAIL)
        self.assertEqual(cooldown['closest_gap_min'], 30)
        self.assertEqual([v['study_id'] for v in cooldown['violations']], ['RS01'])

    def test_distress_breakdown_and_follow_up(self):
        DistressFlag.objects.create(user=self.rs1, source='baseline', signals=['phq9_self_harm'],
                                    raised_at=self._at(9))
        DistressFlag.objects.create(user=self.rs2, source='momentary',
                                    signals=['b1_low_valence', 'b1_affect_sad'],
                                    raised_at=self._at(12), expires_at=self._at(12) + timedelta(hours=24))
        self._decision(self.rs2, self._at(13), draw=None, suppression_reason='distress_momentary',
                       trigger_reason='distress override (momentary)')

        users = list(report.study_participants())
        result = report.distress(users, self._rows(), self.DAY, self.DAY, self.NOW)
        rows = {row['label']: row for row in result['breakdown']}
        self.assertEqual(result['override_events'], 2)
        self.assertEqual(result['randomization_suppressed'], 1)
        self.assertEqual(rows['Self-harm item']['count'], 1)
        self.assertTrue(rows['Self-harm item']['follow_up'])
        self.assertEqual(rows['Other distress signal']['count'], 2)
        self.assertFalse(rows['Other distress signal']['follow_up'])
        self.assertEqual(result['resource_cards_status'], report.NOT_MEASURED)
        self.assertEqual(result['momentary_review'][0]['study_id'], 'RS02')
        self.assertEqual(result['momentary_review'][0]['signals'], [
            'Check-in valence at the scale floor', 'Check-in sadness at the scale ceiling'])

    def test_alert_only_baseline_flag_still_needs_follow_up(self):
        DistressFlag.objects.create(user=self.rs1, source='baseline', signals=['audit_c'],
                                    raised_at=self._at(9))
        users = list(report.study_participants())
        result = report.distress(users, self._rows(), self.DAY, self.DAY, self.NOW)
        rows = {row['label']: row for row in result['breakdown']}
        self.assertTrue(rows['High-risk alcohol']['follow_up'])
        self.assertEqual(result['active_now'], 0)

    def test_momentary_flags_never_become_issues(self):
        DistressFlag.objects.create(user=self.rs1, source='momentary', signals=['b2_high_stress'],
                                    raised_at=self._at(12), expires_at=self._at(12) + timedelta(hours=24))
        daily = report_build.build_daily(self.DAY, now=self.NOW)
        self.assertEqual(len(daily['distress']['momentary_review']), 1)
        self.assertFalse(any('distress' in issue['description'].lower() for issue in daily['issues']))

    def test_data_quality_flags_unlinked_outcomes_and_unlabelled_accounts(self):
        make_ema(self.rs1, self._at(11), ema_type='post_prompt')
        self._account('u@x.test', None)
        users = list(report.study_participants())
        checks = report.data_quality(users, self._rows(), self.DAY, self.DAY)
        self.assertEqual(self._status(checks, 'Check-in linked to decision point'), report.WATCH)
        self.assertEqual(self._status(checks, 'Missing participant IDs'), report.WATCH)
        self.assertEqual(self._status(checks, 'Duplicate records'), report.PASS)

    def test_save_order_jitter_is_not_a_timestamp_problem(self):
        ema = make_ema(self.rs1, self._at(10))
        EMA.objects.filter(pk=ema.pk).update(responded_at=self._at(10) - timedelta(microseconds=300))
        users = list(report.study_participants())
        checks = report.data_quality(users, self._rows(), self.DAY, self.DAY)
        self.assertEqual(self._status(checks, 'Timestamp consistency'), report.PASS)

        EMA.objects.filter(pk=ema.pk).update(responded_at=self._at(9, 50))
        checks = report.data_quality(users, self._rows(), self.DAY, self.DAY)
        self.assertEqual(self._status(checks, 'Timestamp consistency'), report.WATCH)

    def _crosswalk(self):
        return report.labfront_crosswalk(list(report.study_participants()))

    def _device(self, user, labfront_id):
        WearableDevice.objects.update_or_create(user=user, defaults={'labfront_participant_id': labfront_id})

    def test_labfront_id_matching_the_study_id_passes(self):
        self._device(self.rs1, 'RS01')
        self._device(self.rs2, ' rs02 ')
        self._device(self.staff, 'TEST-9')
        self.assertEqual(self._crosswalk()['status'], report.PASS)

    def test_labfront_placeholder_mismatch_and_missing_device_are_flagged(self):
        self._device(self.rs1, 'TEST-101')
        result = self._crosswalk()
        self.assertEqual(result['status'], report.WATCH)
        self.assertIn('placeholder ID: RS01 (TEST-101)', result['detail'])
        self.assertIn('no device: RS02', result['detail'])

        self._device(self.rs1, 'RS09')
        self._device(self.rs2, 'RS02')
        result = self._crosswalk()
        self.assertEqual(result['detail'], 'different ID: RS01 (RS09)')

    def _seen(self, now=None):
        users = list(report.study_participants())
        return report.prompt_seen(self._rows(), now or self.NOW, report.labels_for(users))

    def _rate(self, log, when):
        return make_ema(self.rs1, when, ema_type='prompt_feedback', answers={'C0_helpful': 'Yes'}, source=log)

    def test_a_receipted_prompt_is_seen_at_the_receipt(self):
        self._decision(self.rs1, self._at(10), draw=0.3, device_received_at=self._at(10, 2))
        seen = self._seen()
        self.assertEqual(seen['counts']['receipt'], 1)
        self.assertEqual(seen['median_minutes_to_seen'], 2)
        self.assertEqual(self._status(seen['checks'], 'Sent prompts seen'), report.PASS)

    def test_a_rating_without_a_receipt_is_the_earliest_evidence(self):
        log = self._decision(self.rs1, self._at(10), draw=0.3)
        self._rate(log, self._at(14))
        seen = self._seen()
        self.assertEqual(seen['counts']['rating only'], 1)
        self.assertEqual(seen['flagged'][0]['minutes_to_seen'], 240)
        self.assertEqual(self._status(seen['checks'], 'Device receipts reported'), report.WATCH)
        self.assertEqual(self._status(seen['checks'], 'Sent prompts seen'), report.PASS)

    def test_earliest_of_receipt_and_rating_wins(self):
        log = self._decision(self.rs1, self._at(10), draw=0.3, device_received_at=self._at(10, 30))
        self._rate(log, self._at(10, 10))
        seen = self._seen()
        self.assertEqual(seen['counts']['receipt'], 1)
        self.assertEqual(seen['median_minutes_to_seen'], 10)

    def test_no_evidence_waits_for_the_outcome_window_to_close(self):
        self._decision(self.rs1, self._at(10), draw=0.3)
        self.assertEqual(self._seen(now=self._at(11))['counts']['pending'], 1)
        seen = self._seen(now=self._at(13))
        self.assertEqual(seen['counts']['no evidence'], 1)
        self.assertEqual(self._status(seen['checks'], 'Sent prompts seen'), report.WATCH)
        self.assertEqual(seen['flagged'][0]['study_id'], 'RS01')

    def test_missing_push_token_is_flagged_without_printing_tokens(self):
        User.objects.filter(pk=self.rs1.pk).update(push_token='ExponentPushToken[secret]')
        users = list(report.study_participants())
        result = report.push_tokens(users)
        self.assertEqual(result['status'], report.WATCH)
        self.assertIn('RS02', result['detail'])
        self.assertNotIn('RS01', result['detail'])
        self.assertNotIn('secret', result['detail'])

    def test_scheduled_check_in_without_a_decision_row_is_missing(self):
        make_ema(self.rs1, self._at(10), answers={'B1_valence': 4})
        users = list(report.study_participants())
        checks = report.data_quality(users, self._rows(), self.DAY, self.DAY)
        self.assertEqual(self._status(checks, 'Missing decision logs'), report.WATCH)

    def test_benchmarks_are_suppressed_below_ten_participants(self):
        MetricsDaily.objects.create(
            user=self.rs1, study_day=9, local_date=self.DAY, is_run_in=False, is_active_day=True,
            item_bank_version='v1', slots_expected=6, slots_covered=5)
        entry = report.benchmarks(list(report.study_participants()), self.DAY, self.DAY)[0]
        self.assertEqual((entry['numerator'], entry['denominator']), (5, 6))
        self.assertIsNone(entry['value'])
        self.assertEqual(entry['status'], report.SUPPRESSED)

    def test_retention_is_provisional_and_kept_out_of_issues(self):
        for user in (self.rs1, self.rs2):
            MetricsParticipant.objects.create(user=user, phase='withdrawn', is_enrolled_snapshot=False,
                                              active_retention=False)
        daily = report_build.build_daily(self.DAY, now=self.NOW)
        retention = next(entry for entry in daily['benchmarks'] if entry['name'] == 'retention')
        self.assertTrue(retention['provisional'])
        self.assertFalse(any(issue['description'].startswith('Retention') for issue in daily['issues']))

    def test_benchmark_verdict_uses_the_wilson_interval(self):
        self.assertEqual(report.rate_entry('x', 'X', 95, 100, 20, target=0.75)['status'], report.PASS)
        self.assertEqual(report.rate_entry('x', 'X', 40, 100, 20, target=0.75)['status'], report.FAIL)
        self.assertEqual(report.rate_entry('x', 'X', 75, 100, 20, target=0.75)['status'], report.WATCH)
        self.assertEqual(report.rate_entry('x', 'X', 0, 0, 20, target=0.75)['status'], report.NO_DATA)

    def test_participant_status_counts_each_account_class(self):
        self._account('u@x.test', None)
        User.objects.create(email='n@x.test', birthdate=date(2005, 1, 1), gender='other', study_id='RS09')
        status = report.participant_status()
        self.assertEqual(status['study'], 3)
        self.assertEqual(status['test'], 1)
        self.assertEqual(status['unclassified'], 1)
        self.assertEqual(status['not_enrolled'], 1)

    def test_daily_and_weekly_builds(self):
        self._decision(self.rs1, self._at(10), draw=0.9, send=True)
        daily = report_build.build_daily(self.DAY, now=self.NOW)
        self.assertEqual(daily['kind'], 'daily')
        self.assertEqual(daily['decisions']['evaluated'], 1)
        self.assertTrue(any(issue['severity'] == 'critical' for issue in daily['issues']))

        weekly = report_build.build_weekly(self.DAY, now=self.NOW)
        self.assertEqual(weekly['start'], self.DAY - timedelta(days=6))
        self.assertEqual(len(weekly['trend']), 7)
        self.assertIn('Randomization', weekly['assessment']['risks'])
        self.assertIn('1 critical issue', weekly['summary'])
        self.assertEqual([stage['stage'] for stage in weekly['flow']][:3],
                         ['Screened', 'Eligible', 'Consented'])


class ComposeTelemetryTests(ReportFixture, TestCase):
    def _event(self, user, event_type, when, session_id, **metadata):
        return PhoneTelemetry.objects.create(
            user=user, session_id=session_id, event_type=event_type, occurred_at=when,
            screen_name='compose', metadata=metadata or None)

    def _draft(self, user, started, session_id, end_type=None, ended=None, keystrokes=10,
               deletions=2, seconds=30):
        self._event(user, 'draft_started', started, session_id)
        if end_type:
            self._event(user, end_type, ended or started + timedelta(minutes=1), session_id,
                        keystroke_count=keystrokes, delete_count=deletions,
                        time_on_compose=seconds * 1000)

    def _visit(self, user, opened, session_id, seconds=None, had_draft=False):
        self._event(user, 'session_start', opened, session_id, had_draft=False)
        if seconds is not None:
            self._event(user, 'session_end', opened + timedelta(seconds=seconds), session_id,
                        had_draft=had_draft, visit_ms=seconds * 1000)

    def _users(self):
        return list(report.study_participants())

    def _bounds(self):
        return report.period_bounds(self.DAY, self.DAY)

    def test_draft_outcomes(self):
        self._draft(self.rs1, self._at(9), 'd1', 'draft_submitted', keystrokes=40, deletions=10)
        self._draft(self.rs1, self._at(10), 'd2', 'draft_deleted')
        self._draft(self.rs1, self._at(11), 'd3')
        self._draft(self.rs1, self._at(23), 'd4')
        self._draft(self.rs1, self._at(12), 'd5', 'draft_submitted',
                    ended=self._at(12) + timedelta(hours=25))

        drafts = {d['session_id']: d for d in compose_data.compose_drafts(
            self._users(), *self._bounds(), self._at(12, day=self.DAY + timedelta(days=1)))}
        self.assertEqual(drafts['d1']['outcome'], compose_data.SUBMITTED)
        self.assertEqual(drafts['d1']['keystrokes'], 40)
        self.assertEqual(drafts['d1']['seconds'], 30)
        self.assertEqual(drafts['d2']['outcome'], compose_data.DELETED)
        self.assertEqual(drafts['d3']['outcome'], compose_data.ABANDONED)
        self.assertEqual(drafts['d4']['outcome'], compose_data.PENDING)
        self.assertEqual(drafts['d5']['outcome'], compose_data.ABANDONED)

    def test_visits_pair_by_session_and_keep_an_unclosed_one(self):
        self._visit(self.rs1, self._at(9), 'v1', seconds=90, had_draft=True)
        self._visit(self.rs1, self._at(10), 'v2')
        visits = compose_data.compose_visits(self._users(), *self._bounds())
        self.assertEqual([v['seconds'] for v in visits], [90, None])
        self.assertEqual([v['closed'] for v in visits], [True, False])
        self.assertTrue(visits[0]['had_draft_at_close'])

    def test_opens_are_not_measured_before_the_new_build(self):
        self._visit(self.rs1, self._at(9), 'v1', seconds=60)
        self._draft(self.rs2, self._at(9), 'd1', 'draft_submitted')
        rows = {r['study_id']: r for r in compose_data.compose_by_participant(
            self._users(), *self._bounds(), self.NOW)}
        self.assertEqual(rows['RS01']['opens'], 1)
        self.assertIsNone(rows['RS02']['opens'])
        self.assertEqual(rows['RS02']['drafts'], 1)
        self.assertEqual(rows['RS02']['submit_rate'], 1)

    def test_submit_rate_leaves_pending_drafts_out(self):
        self._draft(self.rs1, self._at(9), 'd1', 'draft_submitted')
        self._draft(self.rs1, self._at(10), 'd2')
        self._draft(self.rs1, self._at(23), 'd3')
        now = self._at(12, day=self.DAY + timedelta(days=1))
        row = compose_data.compose_by_participant(self._users(), *self._bounds(), now)[0]
        self.assertEqual((row['submitted'], row['abandoned'], row['pending']), (1, 1, 1))
        self.assertEqual(row['submit_rate'], 0.5)

    def test_an_event_goes_to_the_latest_overlapping_window_only(self):
        first = self._decision(self.rs1, self._at(10), draw=0.3)
        second = self._decision(self.rs1, self._at(10, 30), draw=0.8)
        self._draft(self.rs1, self._at(10, 45), 'd1', 'draft_submitted')
        windows = [(first.pk, self.rs1.pk, first.decision_made_at, 'A'),
                   (second.pk, self.rs1.pk, second.decision_made_at, 'B')]
        scored = {u['decision_id']: u for u in compose_data.compose_after_decisions(
            self._users(), windows, self.NOW)}
        self.assertEqual(scored[first.pk]['drafts'], 0)
        self.assertEqual(scored[second.pk]['drafts'], 1)
        self.assertEqual(scored[second.pk]['early_drafts'], 1)
        self.assertEqual(scored[second.pk]['submitted'], 1)

    def test_assigned_to_send_with_no_message_is_left_out(self):
        self._decision(self.rs1, self._at(10), draw=0.3, send=False)
        self._decision(self.rs1, self._at(13), draw=0.8)
        result = report.compose(self._users(), self._rows(), self.DAY, self.DAY, self.NOW)
        self.assertEqual(result['excluded_no_message'], 1)
        self.assertEqual(result['comparison']['windows'], {'A': 0, 'B': 1})

    def test_an_open_window_is_not_scored(self):
        self._decision(self.rs1, self._at(10), draw=0.3)
        result = report.compose(self._users(), self._rows(), self.DAY, self.DAY, self._at(11))
        self.assertEqual(result['comparison']['open_windows'], 1)
        self.assertEqual(result['comparison']['windows'], {'A': 0, 'B': 0})

    def test_within_person_difference_and_suppression(self):
        self._decision(self.rs1, self._at(10), draw=0.3)
        self._decision(self.rs1, self._at(14), draw=0.8)
        self._decision(self.rs2, self._at(10), draw=0.3)
        self._decision(self.rs2, self._at(14), draw=0.8)
        self._draft(self.rs1, self._at(10, 30), 'a1', 'draft_submitted')
        self._draft(self.rs1, self._at(11, 30), 'a2', 'draft_submitted')
        self._draft(self.rs2, self._at(10, 30), 'b1', 'draft_submitted')
        self._draft(self.rs2, self._at(14, 30), 'b2', 'draft_submitted')

        result = report.compose(self._users(), self._rows(), self.DAY, self.DAY, self.NOW, with_ci=True)
        drafts = next(m for m in result['comparison']['metrics'] if m['metric'] == 'drafts')
        self.assertEqual(drafts['difference_by_participant'], {'RS01': 2, 'RS02': 0})
        self.assertEqual(drafts['mean_difference'], 1)
        self.assertEqual(drafts['sent_mean'], 1.5)
        self.assertTrue(drafts['suppressed'])
        self.assertIsNone(drafts['ci_low'])
        opened = next(m for m in result['comparison']['metrics'] if m['metric'] == 'opened')
        self.assertIsNone(opened['sent_mean'])

    def test_background_blocks_skip_any_block_touching_a_decision_window(self):
        self._decision(self.rs1, self._at(10), draw=0.3)
        self._draft(self.rs1, self._at(17, 15), 'd1', 'draft_submitted')
        users = [self.rs1]
        windows = [(1, self.rs1.pk, self._at(10), 'A')]
        blocks = compose_data.background_blocks(users, windows, self.DAY, self.DAY, self.NOW)
        self.assertEqual(len(blocks), 4)
        self.assertEqual(sum(block['drafts'] for block in blocks), 1)

    def test_ci_is_published_with_enough_participants(self):
        scored = []
        for user_id in range(12):
            for index in range(3):
                for arm, drafts in (('A', 1 + user_id % 2), ('B', 0)):
                    scored.append({'user_id': user_id, 'arm': arm, 'closed': True,
                                   **{name: 0 for name in compose_data.WINDOW_METRICS},
                                   **{f'{h}_{n}': 0 for h in ('early', 'late')
                                      for n in compose_data.HALF_METRICS},
                                   'drafts': drafts})
        drafts = next(m for m in compose_data.compare_arms(scored, with_ci=True)['metrics']
                      if m['metric'] == 'drafts')
        self.assertFalse(drafts['suppressed'])
        self.assertLessEqual(drafts['ci_low'], drafts['mean_difference'])
        self.assertGreaterEqual(drafts['ci_high'], drafts['mean_difference'])
        self.assertEqual(drafts['mean_difference'], 1.5)


class ReportRenderTests(ReportFixture, TestCase):
    DAILY_HEADINGS = (
        '1.1 Participant status', '1.2 Feasibility benchmarks', '1.3 Check-in compliance',
        '1.4 Decision points', '1.5 Randomization integrity', '1.5b Message eligibility',
        '1.6 Outcome window', '1.7 Cooldown and daily cap', '1.8 Distress override monitoring',
        '1.9 Data quality', '1.10 Issues requiring attention', '1.11 Compose use',
    )
    WEEKLY_HEADINGS = (
        '2.1 Executive summary', '2.2 Participant flow', '2.3 Feasibility benchmarks',
        '2.4 Decision points', '2.5 Protocol compliance assessment', '2.6 Game days vs non-game days',
        '2.7 Garmin signals at check-in', '2.8 Key actions for next week',
        'A.1 Participant status', 'A.11 Compose use', 'Prompt seen',
    )

    def setUp(self):
        super().setUp()
        sent = self._decision(self.rs1, self._at(10), draw=0.3)
        self._decision(self.staff, self._at(10), draw=0.3)
        make_ema(self.rs1, self._at(11, 10), ema_type='post_prompt', source=sent)
        DistressFlag.objects.create(user=self.rs2, source='momentary', signals=['b2_high_stress'],
                                    raised_at=self._at(12), expires_at=self._at(12) + timedelta(hours=24))
        PhoneTelemetry.objects.create(user=self.rs1, session_id='v1', event_type='session_start',
                                      occurred_at=self._at(10, 20), metadata={'had_draft': False})

    def test_daily_page_has_every_section_and_no_identifying_detail(self):
        html = report_render.render_report(report_build.build_daily(self.DAY, now=self.NOW))
        for heading in self.DAILY_HEADINGS:
            self.assertIn(heading, html)
        self.assertIn('RS01', html)
        self.assertNotIn('ST01', html)
        self.assertNotIn('@x.test', html)
        self.assertIn('2026-09-10 12:00', html)

    def test_weekly_page_has_every_section(self):
        html = report_render.render_report(report_build.build_weekly(self.DAY, now=self.NOW))
        for heading in self.WEEKLY_HEADINGS:
            self.assertIn(heading, html)
        self.assertNotIn('@x.test', html)

    def test_a_suppressed_rate_is_never_drawn(self):
        entry = report.rate_entry('slot_coverage', 'Check-in completion', 5, 6, 1, target=0.75)
        chart = report_svg.benchmark_intervals([entry])
        self.assertIn('5/6, rate suppressed', chart)
        self.assertNotIn('<circle', chart)

    def test_sparkline_leaves_gaps_and_needs_two_points(self):
        self.assertEqual(report_svg.sparkline([('d1', 0.5), ('d2', None)]), '')
        line = report_svg.sparkline([('d1', 0.5), ('d2', None), ('d3', 0.7), ('d4', 0.8)])
        self.assertEqual(line.count('<polyline'), 1)
        self.assertEqual(line.count('<circle'), 3)


class BuildReportCommandTests(ReportFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.out = tempfile.TemporaryDirectory()
        self.addCleanup(self.out.cleanup)

    def _run(self, *args):
        stdout = StringIO()
        call_command('build_report', *args, '--out', self.out.name, stdout=stdout)
        return stdout.getvalue()

    def test_daily_report_is_written_for_the_given_date(self):
        self._decision(self.rs1, self._at(10), draw=0.3)
        output = self._run('--daily', '--date', '2026-09-10')
        path = Path(self.out.name) / 'react_daily_2026-09-10.html'
        self.assertTrue(path.exists())
        self.assertIn('1.11 Compose use', path.read_text())
        self.assertIn('No daily metrics have been computed', output)

    def test_weekly_report_names_the_last_day(self):
        self._run('--weekly', '--end', '2026-09-10')
        self.assertTrue((Path(self.out.name) / 'react_weekly_2026-09-10.html').exists())

    def test_recompute_fills_the_metrics_first(self):
        output = self._run('--daily', '--date', '2026-09-10', '--recompute')
        self.assertIn('Recomputed', output)
        self.assertNotIn('No daily metrics have been computed', output)
        self.assertTrue(MetricsDaily.objects.filter(user=self.rs1).exists())
        self.assertFalse(MetricsDaily.objects.filter(user=self.staff).exists())

    def test_weekly_report_lines_up_a_labfront_export(self):
        export = tempfile.TemporaryDirectory()
        self.addCleanup(export.cleanup)
        when = datetime(2026, 9, 10, 10, tzinfo=EASTERN)
        write_export_file(export.name, 'RS01_abc', lf.HEART_RATE, '260910', HR_HEADER, [(when, 60)])
        make_ema(self.rs1, when + timedelta(minutes=5))
        output = self._run('--weekly', '--end', '2026-09-10', '--labfront-export', export.name)
        html = (Path(self.out.name) / 'react_weekly_2026-09-10.html').read_text()
        self.assertIn('2.7 Garmin signals at check-in', html)
        self.assertIn('1 of 1 check-in(s) aligned', output)
        self.assertIn('data through 2026-09-10 10:00 Eastern', output)

    def test_labfront_export_is_weekly_only_and_must_exist(self):
        with self.assertRaises(CommandError):
            self._run('--daily', '--date', '2026-09-10', '--labfront-export', self.out.name)
        with self.assertRaises(CommandError):
            self._run('--weekly', '--end', '2026-09-10', '--labfront-export', '/no/such/folder')

    def test_mismatched_and_future_dates_are_refused(self):
        with self.assertRaises(CommandError):
            self._run('--weekly', '--date', '2026-09-10')
        with self.assertRaises(CommandError):
            self._run('--daily', '--end', '2026-09-10')
        with self.assertRaises(CommandError):
            self._run('--daily', '--date', '2999-01-01')
        with self.assertRaises(CommandError):
            self._run('--daily', '--date', '10/09/2026')


# ---------------------------------------------------------------------------
# Weekly report: game days and the Labfront export
# ---------------------------------------------------------------------------

def make_study_participant(study_id, enrolled_at):
    user = make_participant(f'{study_id.lower()}@x.test', enrolled_at, device=False)
    User.objects.filter(pk=user.pk).update(study_id=study_id)
    return User.objects.get(pk=user.pk)


class GameDayComparisonTests(TestCase):
    ENROLLED = datetime(2026, 9, 20, 9, tzinfo=EASTERN)

    def setUp(self):
        self.rs01 = make_study_participant('RS01', self.ENROLLED)
        self.rs02 = make_study_participant('RS02', self.ENROLLED)
        self.users = [self.rs01, self.rs02]

    def _at(self, day, hour=14):
        return datetime(2026, 10, day, hour, tzinfo=EASTERN)

    def test_no_data_before_homecoming_weekend(self):
        result = report.game_days(self.users, date(2026, 10, 8))
        self.assertEqual(result['status'], report.NO_DATA)
        self.assertIn('2026-10-09', result['detail'])

    def test_no_data_without_a_game_day_in_range(self):
        result = report.game_days(self.users, date(2026, 10, 9))
        self.assertEqual(result['status'], report.NO_DATA)
        self.assertIn('config.GAME_DAYS', result['detail'])

    def test_homecoming_saturday_is_the_game_day(self):
        result = report.game_days(self.users, date(2026, 10, 18))
        self.assertIsNone(result['status'])
        self.assertEqual(result['game_dates'], [date(2026, 10, 10)])

    def test_game_days_come_from_config_not_event_day(self):
        EventDay.objects.create(date=date(2026, 10, 11), sport='football')
        listed = (date(2026, 10, 3), date(2026, 10, 10), date(2026, 10, 17))
        with mock.patch.object(report, 'GAME_DAYS', listed):
            result = report.game_days(self.users, date(2026, 10, 18))
        self.assertEqual(result['game_dates'], [date(2026, 10, 10), date(2026, 10, 17)])

    def test_completion_split_by_game_day_and_suppressed_below_threshold(self):
        for day, covered in ((9, 3), (10, 4), (11, 6)):
            MetricsDaily.objects.create(
                user=self.rs01, study_day=day + 10, local_date=date(2026, 10, day), is_run_in=False,
                is_active_day=True, item_bank_version='v1', slots_expected=6, slots_covered=covered)
        MetricsDaily.objects.create(
            user=self.rs01, study_day=13, local_date=date(2026, 10, 3), is_run_in=False,
            is_active_day=True, item_bank_version='v1', slots_expected=6, slots_covered=0)
        game, other = report.game_days(self.users, date(2026, 10, 11))['completion']
        self.assertEqual((game['numerator'], game['denominator']), (4, 6))
        self.assertEqual((other['numerator'], other['denominator']), (9, 12))
        self.assertEqual(game['status'], report.SUPPRESSED)
        self.assertIsNone(game['value'])

    def test_difference_is_within_person_over_scheduled_check_ins(self):
        make_ema(self.rs01, self._at(10), answers={'B1_valence': 6, 'B2_stress': 2})
        make_ema(self.rs01, self._at(11), answers={'B1_valence': 4, 'B2_stress': 4})
        make_ema(self.rs01, self._at(12), answers={'B1_valence': 2, 'B2_stress': 6})
        make_ema(self.rs01, self._at(10, 16), ema_type='post_prompt', answers={'B1_valence': 1})
        make_ema(self.rs01, self._at(8), answers={'B1_valence': 1})
        make_ema(self.rs02, self._at(10), answers={'B1_valence': 5})

        result = report.game_days(self.users, date(2026, 10, 18))
        valence = next(item for item in result['items'] if item['item'] == 'B1_valence')
        self.assertEqual((valence['game_n'], valence['other_n']), (2, 2))
        self.assertEqual(valence['game_mean'], 5.5)
        self.assertEqual(valence['other_mean'], 3)
        self.assertEqual(valence['mean_difference'], 3)
        self.assertEqual(valence['paired_participants'], 1)
        self.assertEqual(valence['difference_by_participant'], {'RS01': 3, 'RS02': None})

        rows = {row['study_id']: row for row in result['participants']}
        self.assertEqual((rows['RS01']['game_checkins'], rows['RS01']['other_checkins']), (1, 2))
        self.assertEqual(rows['RS01']['differences'], {'B1_valence': 3, 'B2_stress': -3})
        self.assertIsNone(rows['RS02']['differences']['B1_valence'])


HR_HEADER = ['dailySummaryId', 'timezoneOffsetInMs', 'unixTimestampInMs', 'isoDate', 'beatsPerMinute']
STRESS_HEADER = ['timezoneOffsetInMs', 'unixTimestampInMs', 'isoDate', 'stressLevel', 'bodyBattery']


def write_export_file(root, folder, stream, day, header, samples):
    path = Path(root) / folder / stream
    path.mkdir(parents=True, exist_ok=True)
    study_id = folder.split('_')[0]
    lines = [
        'Header Length,5', 'Powered by Labfront', 'Documentation,https://help.labfront.com/data-documentation',
        'projectId,projectTitle,participantId,participantInsignia', f'p,REACT_DEMO,x,{study_id}', '',
        ','.join(header),
    ]
    for moment, value in samples:
        stamp = str(int(moment.timestamp() * 1000))
        if stream == lf.HEART_RATE:
            lines.append(f'id,-14400000,{stamp},{moment.isoformat()},{value}')
        else:
            lines.append(f'-14400000,{stamp},{moment.isoformat()},{value},50')
    (path / f'{day}_{stream}_{folder}.csv').write_text('\n'.join(lines) + '\n')


class LabfrontExportTests(TestCase):
    ENROLLED = datetime(2026, 9, 20, 9, tzinfo=EASTERN)

    def setUp(self):
        self.root = tempfile.TemporaryDirectory()
        self.addCleanup(self.root.cleanup)
        self.folder = Path(self.root.name) / 'RS01_abc'

    def _at(self, day, hour, minute=0):
        return datetime(2026, 10, day, hour, minute, tzinfo=EASTERN)

    def test_reader_skips_the_preamble_and_drops_stress_codes(self):
        write_export_file(self.root.name, 'RS01_abc', lf.STRESS, '261009', STRESS_HEADER, [
            (self._at(9, 10), -1), (self._at(9, 10, 3), -2), (self._at(9, 10, 6), 30)])
        times, values = lf.read_stream(self.folder, lf.STRESS)
        self.assertEqual(values, [30.0])
        self.assertEqual(times, [self._at(9, 10, 6)])
        self.assertEqual(lf.coverage(self.folder), (self._at(9, 10), self._at(9, 10, 6)))

    def test_reader_opens_only_files_near_the_period(self):
        write_export_file(self.root.name, 'RS01_abc', lf.HEART_RATE, '261001', HR_HEADER, [(self._at(1, 10), 60)])
        write_export_file(self.root.name, 'RS01_abc', lf.HEART_RATE, '261009', HR_HEADER, [(self._at(9, 10), 90)])
        _, values = lf.read_stream(self.folder, lf.HEART_RATE, date(2026, 10, 8), date(2026, 10, 9))
        self.assertEqual(values, [90.0])

    def test_participant_folders_key_on_the_study_id(self):
        (Path(self.root.name) / 'rs02_def').mkdir()
        (Path(self.root.name) / '.hidden').mkdir()
        self.assertEqual(set(lf.participant_folders(self.root.name)), {'RS02'})

    def test_check_ins_line_up_with_the_thirty_minutes_before(self):
        rs01 = make_study_participant('RS01', self.ENROLLED)
        rs02 = make_study_participant('RS02', self.ENROLLED)
        write_export_file(self.root.name, 'RS01_abc', lf.HEART_RATE, '261009', HR_HEADER, [
            (self._at(9, 13, 20), 100), (self._at(9, 13, 50), 70), (self._at(9, 13, 55), 80)])
        write_export_file(self.root.name, 'RS01_abc', lf.STRESS, '261009', STRESS_HEADER, [
            (self._at(9, 13, 45), 40), (self._at(9, 15, 10), -1)])
        (Path(self.root.name) / 'RS09_zzz').mkdir()
        aligned = make_ema(rs01, self._at(9, 14), answers={'B1_valence': 5, 'B2_stress': 3})
        make_ema(rs01, self._at(9, 15))
        make_ema(rs01, self._at(10, 14))
        make_ema(rs02, self._at(9, 14))
        make_ema(rs01, self._at(9, 14, 30), ema_type='post_prompt')

        result = report.garmin_at_checkins(
            [rs01, rs02], date(2026, 10, 5), date(2026, 10, 11), self.root.name)
        by_time = {(row['study_id'], row['at']): row for row in result['checkins']}
        first = by_time[('RS01', aligned.responded_at)]
        self.assertEqual(first['status'], report.GARMIN_ALIGNED)
        self.assertEqual((first['hr_mean'], first['hr_n']), (75, 2))
        self.assertEqual((first['stress_mean'], first['stress_n']), (40, 1))
        self.assertEqual((first['valence'], first['self_stress']), (5, 3))
        self.assertEqual(by_time[('RS01', self._at(9, 15))]['status'], report.GARMIN_NO_SIGNAL)
        self.assertEqual(by_time[('RS01', self._at(10, 14))]['status'], report.GARMIN_OUTSIDE)
        self.assertEqual(by_time[('RS02', self._at(9, 14))]['status'], report.GARMIN_NO_FOLDER)
        self.assertEqual(len(result['checkins']), 4)
        self.assertEqual(result['unmatched_folders'], ['RS09'])
        self.assertEqual(result['coverage_end'], self._at(9, 15, 10))

        rows = {row['study_id']: row for row in result['participants']}
        self.assertEqual(rows['RS01']['pairs'], 1)
        self.assertIsNone(rows['RS01']['spearman'])
        self.assertFalse(rows['RS02']['in_export'])

    def test_not_measured_without_an_export(self):
        result = report.garmin_at_checkins([], date(2026, 10, 5), date(2026, 10, 11), None)
        self.assertEqual(result['status'], report.NOT_MEASURED)

    def test_spearman_ranks_ties_and_rejects_constant_input(self):
        self.assertEqual(report._ranks([3, 1, 3, 2]), [3.5, 1.0, 3.5, 2.0])
        self.assertAlmostEqual(report.spearman([1, 2, 3, 4], [10, 20, 30, 50]), 1.0)
        self.assertIsNone(report.spearman([1, 1, 1], [1, 2, 3]))
