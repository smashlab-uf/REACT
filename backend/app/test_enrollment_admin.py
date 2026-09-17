from unittest.mock import patch

from django.contrib.admin.models import LogEntry
from django.contrib.auth.models import Permission, User as AuthUser
from django.db import IntegrityError
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import User, WearableDevice


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class ParticipantEnrollmentAdminTests(TestCase):
    def setUp(self):
        self.staff = AuthUser.objects.create_superuser('enroller', 'staff@example.com', 'staff-password')
        self.client.force_login(self.staff)
        self.url = reverse('admin:app_user_create_and_enroll')
        self.data = {
            'email': 'participant@example.com',
            'first_name': 'Alex', 'last_name': 'Participant',
            'birthdate': '2000-01-01', 'gender': 'other',
            'password1': 'Lab-Visit!7392', 'password2': 'Lab-Visit!7392',
            'labfront_participant_id': 'labfront-participant-123',
        }

    def make_participant(self):
        user = User(email='existing@example.com', birthdate='2000-01-01', gender='other')
        user.set_password('original-password')
        user.save()
        return user

    def test_create_enroll_and_login(self):
        response = self.client.post(self.url, self.data, follow=True)
        self.assertEqual(response.status_code, 200)
        user = User.objects.get(email=self.data['email'])
        self.assertTrue(user.check_password(self.data['password1']))
        self.assertNotEqual(user.password, self.data['password1'])
        self.assertTrue(user.is_enrolled)
        self.assertIsNotNone(user.enrolled_at)
        self.assertEqual(user.wearabledevice.labfront_participant_id, self.data['labfront_participant_id'])
        self.assertTrue(user.wearabledevice.is_active)
        self.assertContains(response, 'push token is missing')
        login = self.client.post('/user/login/', {'email': user.email, 'password': self.data['password1']})
        self.assertEqual(login.status_code, 200)
        self.assertIn('access', login.json())
        self.assertNotIn(self.data['password1'], LogEntry.objects.latest('id').change_message)

    def test_existing_account_replaces_placeholder_without_changing_credentials_or_date(self):
        user = self.make_participant()
        original_password = user.password
        original_date = timezone.now()
        user.enrolled_at = original_date
        user.save()
        device = WearableDevice.objects.create(user=user, labfront_participant_id=f'TEST-{user.pk}', is_active=False)
        url = reverse('admin:app_user_enroll_labfront', args=[user.pk])
        response = self.client.post(url, {
            'labfront_participant_id': 'real-labfront-id', 'email': 'replacement@example.com',
            'password1': 'replacement-password', 'password2': 'replacement-password',
        })
        self.assertEqual(response.status_code, 302)
        user.refresh_from_db()
        device.refresh_from_db()
        self.assertEqual(user.email, 'existing@example.com')
        self.assertEqual(user.password, original_password)
        self.assertEqual(user.enrolled_at, original_date)
        self.assertEqual(device.labfront_participant_id, 'real-labfront-id')
        self.assertTrue(device.is_active)
        self.assertTrue(user.is_enrolled)
        self.client.post(url, {'labfront_participant_id': 'real-labfront-id'})
        self.assertEqual(WearableDevice.objects.filter(user=user).count(), 1)
        self.assertEqual(User.objects.get(pk=user.pk).enrolled_at, original_date)

    def test_existing_account_without_wearable_can_enroll(self):
        user = self.make_participant()
        response = self.client.post(reverse('admin:app_user_enroll_labfront', args=[user.pk]), {
            'labfront_participant_id': 'real-labfront-id',
        })
        self.assertEqual(response.status_code, 302)
        user.refresh_from_db()
        self.assertTrue(user.is_enrolled)
        self.assertIsNotNone(user.enrolled_at)
        self.assertEqual(user.wearabledevice.labfront_participant_id, 'real-labfront-id')

    def test_duplicate_email_is_rejected_without_modifying_existing_account(self):
        user = self.make_participant()
        response = self.client.post(self.url, {**self.data, 'email': user.email.upper()})
        self.assertContains(response, 'This account already exists')
        user.refresh_from_db()
        self.assertFalse(user.is_enrolled)
        self.assertTrue(user.check_password('original-password'))
        self.assertFalse(WearableDevice.objects.exists())

    def test_duplicate_labfront_id_does_not_create_account(self):
        user = self.make_participant()
        WearableDevice.objects.create(user=user, labfront_participant_id=self.data['labfront_participant_id'])
        response = self.client.post(self.url, self.data)
        self.assertContains(response, 'already linked to another account')
        self.assertFalse(User.objects.filter(email=self.data['email']).exists())

    def test_existing_real_labfront_link_is_not_overwritten(self):
        user = self.make_participant()
        WearableDevice.objects.create(user=user, labfront_participant_id='original-real-id')
        response = self.client.post(reverse('admin:app_user_enroll_labfront', args=[user.pk]), {
            'labfront_participant_id': 'different-real-id',
        })
        self.assertContains(response, 'already has a different Labfront ID')
        self.assertEqual(WearableDevice.objects.get(user=user).labfront_participant_id, 'original-real-id')

    def test_invalid_input_creates_nothing_and_never_redisplays_password(self):
        for fields, message in [
            ({'password2': 'mismatch'}, 'The passwords do not match'),
            ({'password1': '12345678', 'password2': '12345678'}, 'too common'),
            ({'labfront_participant_id': 'TEST-999'}, 'real Labfront participant ID'),
            ({'labfront_participant_id': ''}, 'This field is required'),
        ]:
            with self.subTest(fields=fields):
                response = self.client.post(self.url, {**self.data, **fields})
                self.assertContains(response, message)
                self.assertNotContains(response, self.data['password1'])
                self.assertFalse(User.objects.exists())
                self.assertFalse(WearableDevice.objects.exists())

    def test_wearable_failure_rolls_back_new_user_and_admin_log(self):
        with patch('app.forms.WearableDevice.save', side_effect=IntegrityError('duplicate')):
            response = self.client.post(self.url, self.data)
        self.assertContains(response, 'email or Labfront ID is already in use')
        self.assertFalse(User.objects.exists())
        self.assertFalse(WearableDevice.objects.exists())
        self.assertFalse(LogEntry.objects.exists())

    def test_staff_without_model_permissions_cannot_enroll(self):
        staff = AuthUser.objects.create_user('limited', is_staff=True)
        self.client.force_login(staff)
        user = self.make_participant()
        for url in [self.url, reverse('admin:app_user_enroll_labfront', args=[user.pk])]:
            self.assertEqual(self.client.get(url).status_code, 403)
            self.assertEqual(self.client.post(url, self.data).status_code, 403)
        self.assertFalse(WearableDevice.objects.exists())

    def test_user_permissions_alone_do_not_grant_wearable_permissions(self):
        staff = AuthUser.objects.create_user('user-editor', is_staff=True)
        staff.user_permissions.add(*Permission.objects.filter(content_type__app_label='app', codename__in=['add_user', 'change_user']))
        self.client.force_login(staff)
        self.assertNotContains(self.client.get(reverse('admin:index')), self.url)
        self.assertEqual(self.client.post(self.url, self.data).status_code, 403)
        self.assertFalse(User.objects.exists())

    def test_staff_with_required_permissions_can_create_and_enroll(self):
        staff = AuthUser.objects.create_user('lab-staff', is_staff=True)
        staff.user_permissions.add(*Permission.objects.filter(
            content_type__app_label='app',
            codename__in=['add_user', 'change_user', 'add_wearabledevice', 'change_wearabledevice'],
        ))
        self.client.force_login(staff)
        self.assertContains(self.client.get(reverse('admin:index')), self.url)
        response = self.client.post(self.url, self.data)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(User.objects.get(email=self.data['email']).is_enrolled)

    def test_anonymous_and_csrf_protection(self):
        self.client.logout()
        self.assertEqual(self.client.post(self.url, self.data).status_code, 302)
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.staff)
        self.assertEqual(client.post(self.url, self.data).status_code, 403)
        self.assertFalse(User.objects.exists())

    def test_forms_are_discoverable_and_get_does_not_enroll(self):
        homepage = self.client.get(reverse('admin:index'))
        self.assertContains(homepage, self.url)
        self.assertContains(homepage, 'Participant onboarding')
        self.assertContains(homepage, reverse('admin:app_user_changelist'))
        self.assertContains(self.client.get(reverse('admin:app_user_changelist')), self.url)
        self.assertContains(self.client.get(self.url), 'Create account and enroll')
        self.assertFalse(User.objects.exists())
        user = self.make_participant()
        url = reverse('admin:app_user_enroll_labfront', args=[user.pk])
        self.assertContains(self.client.get(reverse('admin:app_user_change', args=[user.pk])), url)
        response = self.client.get(url)
        self.assertNotContains(response, 'name="password1"')
        user.refresh_from_db()
        self.assertFalse(user.is_enrolled)
