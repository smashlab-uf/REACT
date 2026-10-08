"""Notification-based deadlines, including Dr. Chang's five-minute reopen case."""
from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from app.models import CheckinReminder, EMA, JITAILog
from app.tests import FAST_HASHERS, authenticated_client, make_user


@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class CheckinDeadlineTests(TestCase):
    def setUp(self):
        self.user = make_user(email='deadline@test.com')
        self.client = authenticated_client(self.user)
        self.reminder = CheckinReminder.objects.create(user=self.user, daily_count_at_send=0)
        self.sent = self.reminder.sent_at
        self.deadline = self.sent + timedelta(minutes=30)

    def at(self, minutes):
        return patch('app.views.django_timezone.now', return_value=self.sent + timedelta(minutes=minutes))

    def submit(self, **overrides):
        payload = {
            'prompt_id': self.reminder.prompt_id,
            'ema_type': 'scheduled_check_in',
            'responses': [{'sub_item_id': 'B1_valence', 'value': 4}],
        }
        payload.update(overrides)
        return self.client.post('/ema/responses/', payload, format='json')

    def test_open_at_five_minutes_has_twenty_five_minutes_left(self):
        with self.at(5):
            data = self.client.get('/ema/next/').json()
        self.assertTrue(data['should_show'])
        self.assertEqual(data['prompt_id'], self.reminder.prompt_id)
        self.assertEqual(parse_datetime(data['expires_at']), self.deadline)
        self.assertEqual((parse_datetime(data['expires_at']) - (self.sent + timedelta(minutes=5))).total_seconds(), 1500)

    def test_repeated_opens_never_extend_deadline_or_create_survey_rows(self):
        for minutes in (0, 0.5, 5, 20, 29.99):
            with self.at(minutes):
                data = self.client.get('/ema/next/').json()
            self.assertTrue(data['should_show'])
            self.assertEqual(data['prompt_id'], self.reminder.prompt_id)
            self.assertEqual(parse_datetime(data['expires_at']), self.deadline)
        self.assertEqual(EMA.objects.count(), 0)

    def test_exact_deadline_and_later_hide_and_reject_survey(self):
        for minutes in (30, 31, 120):
            with self.at(minutes):
                self.assertFalse(self.client.get('/ema/next/').json()['should_show'])
                self.assertEqual(self.submit().status_code, 410)
        self.assertFalse(EMA.objects.exists())

    def test_submit_keeps_original_deadline_and_completed_survey_disappears(self):
        with self.at(25):
            response = self.submit()
            self.assertEqual(response.status_code, 201)
            self.assertEqual(parse_datetime(response.json()['expires_at']), self.deadline)
            self.assertFalse(self.client.get('/ema/next/').json()['should_show'])
            self.assertEqual(self.submit().status_code, 409)
        self.assertEqual(EMA.objects.count(), 1)

    def test_notification_required(self):
        self.reminder.delete()
        self.assertFalse(self.client.get('/ema/next/').json()['should_show'])
        self.assertEqual(self.submit(prompt_id='EMA-invented').status_code, 400)

    def test_other_users_notification_cannot_be_read_or_submitted(self):
        other = make_user(email='other-deadline@test.com')
        self.client = authenticated_client(other)
        self.assertFalse(self.client.get('/ema/next/').json()['should_show'])
        self.assertEqual(self.submit().status_code, 404)

    def test_alias_prompt_id_cannot_bypass_duplicate_checks(self):
        alias = f'EMA-REMINDER-0{self.reminder.pk}'
        self.assertEqual(self.submit(prompt_id=alias).status_code, 400)
        self.assertFalse(EMA.objects.exists())

    def test_feedback_type_cannot_bypass_deadline(self):
        with self.at(31):
            self.assertEqual(self.submit(ema_type='prompt_feedback').status_code, 400)
        self.assertFalse(EMA.objects.exists())

    def test_new_notification_does_not_extend_old_survey(self):
        with self.at(31):
            new = CheckinReminder.objects.create(user=self.user, daily_count_at_send=1)
            data = self.client.get('/ema/next/').json()
            self.assertEqual(data['prompt_id'], new.prompt_id)
            self.assertEqual(parse_datetime(data['expires_at']), new.expires_at)
            self.assertEqual(self.submit().status_code, 410)

    def test_future_notification_is_not_available(self):
        with self.at(-1):
            self.assertFalse(self.client.get('/ema/next/').json()['should_show'])
            self.assertEqual(self.submit().status_code, 410)

    def test_outcome_checkin_uses_its_own_reminder_in_both_arms(self):
        for draw in (0.2, 0.9):
            with self.subTest(draw=draw):
                log = JITAILog.objects.create(
                    user=self.user, trigger_reason='test', decision_point_id=f'ema_{draw}',
                    decision_made_at=self.sent - timedelta(minutes=65),
                    randomization_draw=draw, randomization_probability=0.5,
                    send_prompt=draw < 0.5,
                )
                reminder = CheckinReminder.objects.create(user=self.user, jitai_log=log)
                with self.at(5):
                    data = self.client.get('/ema/next/').json()
                    self.assertTrue(data['should_show'])
                    self.assertEqual(data['prompt_id'], reminder.prompt_id)
                    self.assertEqual(parse_datetime(data['expires_at']), reminder.expires_at)
                    response = self.submit(
                        prompt_id=reminder.prompt_id, ema_type='extra_check_in', jitai_log_id=log.pk,
                        outcome_window_start=self.sent.isoformat(), outcome_window_end=self.deadline.isoformat(),
                    )
                    self.assertEqual(response.status_code, 201)
                    self.assertEqual(parse_datetime(response.json()['outcome_window_start']), log.decision_made_at)
                    self.assertEqual(parse_datetime(response.json()['expires_at']), reminder.expires_at)
                    self.assertFalse(self.client.get('/ema/next/').json()['should_show'])
                log.delete()

    def test_outcome_window_without_sent_reminder_does_not_create_checkin(self):
        JITAILog.objects.create(
            user=self.user, trigger_reason='test', decision_point_id='ema_pending',
            decision_made_at=self.sent - timedelta(minutes=65), randomization_draw=0.9,
        )
        with self.at(5):
            self.assertFalse(self.client.get('/ema/next/').json()['should_show'])

    def _old_feedback(self):
        return JITAILog.objects.create(
            user=self.user, trigger_reason='earlier intervention', send_prompt=True,
            push_sent_at=self.sent - timedelta(days=7),
        )

    def test_checkin_only_skips_old_feedback_and_preserves_deadline_on_open(self):
        feedback = self._old_feedback()
        with self.at(5):
            default = self.client.get('/ema/next/').json()
            self.assertEqual(default['ema_type'], 'prompt_feedback')
            self.assertEqual(default['jitai_log_id'], feedback.pk)
            # Availability and opening the card both use this request mode.
            for _ in range(2):
                checkin = self.client.get('/ema/next/', {'checkin_only': '1'}).json()
                self.assertEqual(checkin['ema_type'], 'scheduled_check_in')
                self.assertEqual(checkin['prompt_id'], self.reminder.prompt_id)
                self.assertEqual(parse_datetime(checkin['expires_at']), self.deadline)
            self.assertEqual(self.submit().status_code, 201)
            self.assertFalse(self.client.get('/ema/next/', {'checkin_only': '1'}).json()['should_show'])
            # Bypassing feedback neither answers nor dismisses it.
            self.assertEqual(self.client.get('/ema/next/').json()['jitai_log_id'], feedback.pk)
            self.assertFalse(EMA.objects.filter(source_jitai_log=feedback).exists())

    def test_old_feedback_alone_never_becomes_home_screen_checkin(self):
        self._old_feedback()
        for minutes in (30, 60):
            with self.at(minutes):
                self.assertTrue(self.client.get('/ema/next/').json()['should_show'])
                self.assertFalse(self.client.get('/ema/next/', {'checkin_only': '1'}).json()['should_show'])
        self.reminder.delete()
        self.assertFalse(self.client.get('/ema/next/', {'checkin_only': '1'}).json()['should_show'])

    def test_old_feedback_does_not_block_outcome_checkin_card(self):
        self._old_feedback()
        log = JITAILog.objects.create(
            user=self.user, trigger_reason='outcome', decision_point_id='ema_outcome',
            decision_made_at=self.sent - timedelta(minutes=65), randomization_draw=0.9,
        )
        reminder = CheckinReminder.objects.create(user=self.user, jitai_log=log)
        with self.at(5):
            data = self.client.get('/ema/next/', {'checkin_only': '1'}).json()
            self.assertEqual(data['ema_type'], 'extra_check_in')
            self.assertEqual(data['prompt_id'], reminder.prompt_id)
            self.assertEqual(parse_datetime(data['expires_at']), reminder.expires_at)
