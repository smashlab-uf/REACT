from unittest.mock import patch

from django.test import TestCase, override_settings
from rest_framework.test import APIClient

from app.models import JITAILog, User
from app.notification_service import send_checkin_reminder, send_jitai_prompt
from app.tests import authenticated_client, make_user


@override_settings(API_KEY='test-key', PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class PushLogoutTests(TestCase):
    def setUp(self):
        self.user = make_user(push_token='ExponentPushToken[device-a]')
        self.client = APIClient()
        self.client.credentials(HTTP_X_API_KEY='test-key')

    def unregister(self, **overrides):
        body = {'user_id': self.user.pk, 'push_token': self.user.push_token}
        body.update(overrides)
        return self.client.post('/notifications/unregister/', body, format='json')

    def test_expired_jwt_can_revoke_and_repeated_cleanup_is_idempotent(self):
        self.client.credentials(HTTP_X_API_KEY='test-key', HTTP_AUTHORIZATION='Bearer expired')
        self.assertEqual(self.unregister().status_code, 204)
        self.assertEqual(self.unregister().status_code, 204)
        self.user.refresh_from_db()
        self.assertIsNone(self.user.push_token)
        with patch('app.notification_service.PushClient') as expo:
            self.assertFalse(send_checkin_reminder(self.user))
            log = JITAILog.objects.create(user=self.user, prompt_id='TEMPLATE_001')
            self.assertFalse(send_jitai_prompt(self.user, log))
            expo.assert_not_called()

    def test_wrong_token_cannot_revoke_another_device(self):
        self.assertEqual(self.unregister(push_token='ExponentPushToken[other]').status_code, 204)
        self.user.refresh_from_db()
        self.assertEqual(self.user.push_token, 'ExponentPushToken[device-a]')

    def test_worker_with_stale_user_rechecks_token_before_sending(self):
        stale_user = User.objects.get(pk=self.user.pk)
        self.assertEqual(self.unregister().status_code, 204)
        with patch('app.notification_service.PushClient') as expo:
            self.assertFalse(send_checkin_reminder(stale_user))
            log = JITAILog.objects.create(user=self.user, prompt_id='TEMPLATE_001')
            self.assertFalse(send_jitai_prompt(self.user, log))
            expo.assert_not_called()

    def test_stale_logout_does_not_remove_new_device_registration(self):
        User.objects.filter(pk=self.user.pk).update(push_token='ExponentPushToken[device-b]')
        self.assertEqual(self.unregister().status_code, 204)
        self.user.refresh_from_db()
        self.assertEqual(self.user.push_token, 'ExponentPushToken[device-b]')

    def test_api_key_is_required(self):
        self.client.credentials()
        self.assertEqual(self.unregister().status_code, 403)

    def test_invalid_payloads_are_rejected(self):
        for token in [None, '', ' ', 123, 'x' * 129]:
            self.assertEqual(self.unregister(push_token=token).status_code, 400)
        for user_id in [None, True, '1', -1, 10**100]:
            self.assertEqual(self.unregister(user_id=user_id).status_code, 400)

    def test_login_can_register_again(self):
        self.assertEqual(self.unregister().status_code, 204)
        client = authenticated_client(self.user)
        result = client.put(f'/user/{self.user.pk}/', {'push_token': self.user.push_token},
                            format='json', HTTP_X_API_KEY='test-key')
        self.assertEqual(result.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.push_token, 'ExponentPushToken[device-a]')
