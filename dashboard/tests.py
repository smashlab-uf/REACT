"""Tests for the monitoring data layer.

Several of these are regression tests for bugs found while building it, and are
labelled as such: the DST subtraction shortcut, study close-out being read as a
withdrawal, phase ordering after day 34, alert payloads that churned on every
poll, and delivery failures sitting in the prompt-response denominator.
"""

import json
from datetime import date, datetime, timedelta
from io import StringIO
from zoneinfo import ZoneInfo

from app.models import (
    CheckinReminder,
    EMA,
    EMAItemResponse,
    EngagementLog,
    HeartRateSample,
    JITAILog,
    User,
    WearableDevice,
)
from django.contrib.admin.models import CHANGE, LogEntry
from django.contrib.auth.models import User as AuthUser
from django.contrib.contenttypes.models import ContentType
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import IntegrityError, transaction
from django.test import Client, TestCase, override_settings

from dashboard.data import windows as w
from dashboard.data.alerts import Context, evaluate_alerts, pipeline_stalled
from dashboard.data.cohort import compute_cohort, suppress_rate, wilson_interval
from dashboard.data.config import DAILY_PROMPT_CAP, RUN_IN_DAYS, STUDY_DAYS
from dashboard.data.daily import METRIC_FIELDS, compute_daily
from dashboard.data.item_bank import load_item_bank, sub_item_index
from dashboard.data.participant import compute_participant, compute_risk_score, refresh_risk_scores
from dashboard.models import Alert, MetricsCohort, MetricsDaily, MetricsParticipant
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
        # Study day 3 is inside the run-in, and no gate exists in the engine.
        self.assertEqual(metrics['runin_violation_n'], 2)

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

    def test_risk_score_weights_and_cap(self):
        user = make_participant('k@x.test', self.ENROLLED, device=False)
        self.assertEqual(compute_risk_score(user), 0)
        Alert.objects.create(user=user, rule_id='sync_stale', severity='warning')
        Alert.objects.create(user=user, rule_id='no_ema_48h', severity='warning')
        self.assertEqual(compute_risk_score(user), 30)
        Alert.objects.create(user=user, rule_id='runin_violation', severity='critical')
        self.assertEqual(compute_risk_score(user), 70)
        Alert.objects.create(user=user, rule_id='cooldown_violation', severity='critical')
        self.assertEqual(compute_risk_score(user), 100)

        Alert.objects.filter(user=user, severity='critical').update(resolved_at=self.NOW)
        self.assertEqual(compute_risk_score(user), 30)

    def test_refresh_risk_scores_updates_stored_rows(self):
        user = make_participant('rs@x.test', self.ENROLLED, device=False)
        MetricsParticipant.objects.create(user=user, **compute_participant(user, now=self.NOW))
        Alert.objects.create(user=user, rule_id='wear_low', severity='critical')
        self.assertEqual(refresh_risk_scores(), 1)
        self.assertEqual(MetricsParticipant.objects.get(user=user).risk_score, 40)


# ---------------------------------------------------------------------------
# Cohort snapshot
# ---------------------------------------------------------------------------

class CohortTests(TestCase):
    NOW = datetime(2026, 9, 20, 16, 0, tzinfo=UTC)
    ENROLLED = datetime(2026, 9, 1, 14, 0, tzinfo=UTC)

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
            {'benchmarks': snapshot['benchmarks'], 'series_14d': snapshot['series_14d']},
            default=str)
        self.assertLess(len(encoded), 200 * 1024)


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
            (faults['runin'].pk, 'runin_violation'),
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
        })
        self.assertEqual(
            Alert.objects.get(user=faults['stale'], rule_id='sync_stale').severity, 'warning')
        self.assertEqual(
            Alert.objects.get(user=faults['dead'], rule_id='sync_stale').severity, 'critical')

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
        self.assertEqual(alert.severity, 'critical')
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
        self.assertEqual(body['warning'], sum(
            1 for alert in body['alerts'] if alert['severity'] == 'warning'))
        self.assertEqual(body['open'], len(body['alerts']))


# ---------------------------------------------------------------------------
# Withdrawal backfill
# ---------------------------------------------------------------------------

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
