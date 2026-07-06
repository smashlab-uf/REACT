import os
from datetime import timedelta
from unittest.mock import patch, MagicMock
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework import status as http_status
from django.contrib.auth.models import User as AuthUser
from rest_framework_simplejwt.tokens import RefreshToken

from app.models import (
    EMA, EngagementLog, HeartRateSample, JITAILog, PhoneTelemetry,
    StressSample, User, WearableDevice,
)
from app.serializers import (
    UserSerializer,
    WearableDeviceSerializer, HeartRateSampleSerializer,
    StressSampleSerializer, EMASerializer, JITAILogSerializer,
)
FAST_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']


def make_user(email='test@example.com', password='testpass123', **kwargs):
    defaults = {'birthdate': '2000-01-01', 'gender': 'male'}
    defaults.update(kwargs)
    user = User(email=email, **defaults)
    user.set_password(password)
    user.save()
    return user


def authenticated_client(app_user):
    auth_user, _ = AuthUser.objects.get_or_create(
        username=app_user.email,
        defaults={'email': app_user.email},
    )
    client = APIClient()
    refresh = RefreshToken.for_user(auth_user)
    client.credentials(HTTP_AUTHORIZATION=f'Bearer {str(refresh.access_token)}')
    return client


# ---------------------------------------------------------------------------
# Model: password hashing
# ---------------------------------------------------------------------------

@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class UserPasswordTests(TestCase):

    def test_set_password_does_not_store_plaintext(self):
        user = User(email='a@b.com', birthdate='2000-01-01', gender='male')
        user.set_password('mypassword')
        self.assertNotEqual(user.password, 'mypassword')

    def test_correct_password_returns_true(self):
        user = User(email='a@b.com', birthdate='2000-01-01', gender='male')
        user.set_password('mypassword')
        self.assertTrue(user.check_password('mypassword'))

    def test_wrong_password_returns_false(self):
        user = User(email='a@b.com', birthdate='2000-01-01', gender='male')
        user.set_password('mypassword')
        self.assertFalse(user.check_password('wrongpassword'))

    def test_no_password_set_returns_false(self):
        user = User(email='a@b.com', birthdate='2000-01-01', gender='male')
        self.assertFalse(user.check_password('anything'))


# ---------------------------------------------------------------------------
# Model: legacy User fields removed
# ---------------------------------------------------------------------------

class UserLegacyFieldsTest(TestCase):

    def test_no_legacy_health_fields_on_model(self):
        field_names = {f.name for f in User._meta.get_fields()}
        for legacy in ('height_feet', 'height_inches', 'goal_weight',
                       'goal_to_lose_weight', 'goal_to_feel_better'):
            self.assertNotIn(legacy, field_names,
                             msg=f"Legacy field '{legacy}' still on User model")

    def test_no_legacy_health_fields_on_serializer(self):
        serializer_fields = set(UserSerializer().fields.keys())
        for legacy in ('height_feet', 'height_inches', 'goal_weight',
                       'goal_to_lose_weight', 'goal_to_feel_better'):
            self.assertNotIn(legacy, serializer_fields,
                             msg=f"Legacy field '{legacy}' still in UserSerializer")


# ---------------------------------------------------------------------------
# API: POST /user/ — CreateUserView
# ---------------------------------------------------------------------------

@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class CreateUserViewTests(TestCase):

    def setUp(self):
        self.client = APIClient()

    def test_valid_payload_creates_user_and_returns_201(self):
        response = self.client.post('/user/', {
            'email': 'new@example.com',
            'password': 'securepass123',
        }, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_201_CREATED)
        self.assertIn('user_id', response.data)
        self.assertTrue(User.objects.filter(email='new@example.com').exists())

    def test_duplicate_email_returns_400(self):
        make_user(email='taken@example.com')
        response = self.client.post('/user/', {
            'email': 'taken@example.com',
            'password': 'securepass123',
        }, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_400_BAD_REQUEST)

    def test_missing_email_returns_400(self):
        response = self.client.post('/user/', {'password': 'securepass123'}, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_400_BAD_REQUEST)

    def test_password_shorter_than_8_chars_returns_400(self):
        response = self.client.post('/user/', {
            'email': 'new@example.com',
            'password': 'short',
        }, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_400_BAD_REQUEST)

    def test_password_is_not_stored_in_plaintext(self):
        self.client.post('/user/', {
            'email': 'new@example.com',
            'password': 'securepass123',
        }, format='json')
        user = User.objects.get(email='new@example.com')
        self.assertNotEqual(user.password, 'securepass123')

    def test_new_user_is_not_enrolled_by_default(self):
        self.client.post('/user/', {
            'email': 'new@example.com',
            'password': 'securepass123',
        }, format='json')
        user = User.objects.get(email='new@example.com')
        self.assertFalse(user.is_enrolled)
        self.assertIsNone(user.enrolled_at)


# ---------------------------------------------------------------------------
# API: PUT /user/<id>/ — UserUpdateView
# ---------------------------------------------------------------------------

@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class UserUpdateViewTests(TestCase):

    def setUp(self):
        self.user = make_user()
        self.client = authenticated_client(self.user)

    def test_update_first_name_returns_200_and_persists(self):
        response = self.client.put(
            f'/user/{self.user.user_id}/',
            {'first_name': 'Gator'},
            format='json',
        )
        self.assertEqual(response.status_code, http_status.HTTP_200_OK)
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, 'Gator')

    def test_update_password_rehashes_it(self):
        self.client.put(
            f'/user/{self.user.user_id}/',
            {'password': 'newpassword99'},
            format='json',
        )
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('newpassword99'))

    def test_cannot_update_other_users_profile_returns_403(self):
        other = make_user(email='other@example.com')
        response = self.client.put(f'/user/{other.user_id}/', {'first_name': 'Hacker'}, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_403_FORBIDDEN)

    def test_staff_can_update_any_profile(self):
        other = make_user(email='target@example.com')
        auth_user, _ = AuthUser.objects.get_or_create(
            username=self.user.email, defaults={'email': self.user.email}
        )
        auth_user.is_staff = True
        auth_user.save()
        response = self.client.put(f'/user/{other.user_id}/', {'first_name': 'Admin'}, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# API: POST /user/checkemail/ — CheckEmailView
# ---------------------------------------------------------------------------

@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class CheckEmailViewTests(TestCase):

    def setUp(self):
        self.client = APIClient()

    def test_existing_email_returns_exists_true(self):
        make_user(email='existing@example.com')
        response = self.client.post(
            '/user/checkemail/', {'email': 'existing@example.com'}, format='json'
        )
        self.assertEqual(response.status_code, http_status.HTTP_200_OK)
        self.assertTrue(response.data['exists'])

    def test_new_email_returns_exists_false(self):
        response = self.client.post(
            '/user/checkemail/', {'email': 'brand_new@example.com'}, format='json'
        )
        self.assertEqual(response.status_code, http_status.HTTP_200_OK)
        self.assertFalse(response.data['exists'])


# ---------------------------------------------------------------------------
# API: POST /user/login/ — UserLoginView
# ---------------------------------------------------------------------------

@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class UserLoginViewTests(TestCase):

    def setUp(self):
        self.client = APIClient()
        self.user = make_user(email='gator@ufl.edu', password='chomp1234')

    def test_valid_credentials_return_200_with_access_and_refresh_tokens(self):
        response = self.client.post('/user/login/', {
            'email': 'gator@ufl.edu',
            'password': 'chomp1234',
        }, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_200_OK)
        self.assertIn('access', response.data)
        self.assertIn('refresh', response.data)

    def test_valid_credentials_return_user_data_in_response(self):
        response = self.client.post('/user/login/', {
            'email': 'gator@ufl.edu',
            'password': 'chomp1234',
        }, format='json')
        self.assertIn('data', response.data)
        self.assertEqual(response.data['data']['email'], 'gator@ufl.edu')

    def test_wrong_password_returns_401(self):
        response = self.client.post('/user/login/', {
            'email': 'gator@ufl.edu',
            'password': 'wrongpassword',
        }, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_401_UNAUTHORIZED)

    def test_nonexistent_email_returns_401(self):
        response = self.client.post('/user/login/', {
            'email': 'nobody@ufl.edu',
            'password': 'chomp1234',
        }, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_401_UNAUTHORIZED)

    def test_missing_email_returns_400(self):
        response = self.client.post('/user/login/', {'password': 'chomp1234'}, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_400_BAD_REQUEST)

    def test_missing_password_returns_400(self):
        response = self.client.post('/user/login/', {'email': 'gator@ufl.edu'}, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_400_BAD_REQUEST)


# ---------------------------------------------------------------------------
# Model: User enrollment fields
# ---------------------------------------------------------------------------

@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class UserEnrollmentFieldTests(TestCase):

    def test_is_enrolled_defaults_to_false(self):
        user = make_user()
        self.assertFalse(user.is_enrolled)

    def test_enrolled_at_defaults_to_null(self):
        user = make_user()
        self.assertIsNone(user.enrolled_at)

    def test_can_set_enrollment(self):
        user = make_user()
        now = timezone.now()
        user.is_enrolled = True
        user.enrolled_at = now
        user.save()
        user.refresh_from_db()
        self.assertTrue(user.is_enrolled)
        self.assertIsNotNone(user.enrolled_at)


# ---------------------------------------------------------------------------
# Model: WearableDevice
# ---------------------------------------------------------------------------

@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class WearableDeviceModelTests(TestCase):

    def test_device_is_linked_to_user_via_onetoone(self):
        user = make_user()
        device = WearableDevice.objects.create(
            user=user,
            labfront_participant_id='LABFRONT001',
        )
        self.assertEqual(device.user, user)

    def test_is_active_defaults_to_true(self):
        user = make_user()
        device = WearableDevice.objects.create(
            user=user,
            labfront_participant_id='LABFRONT001',
        )
        self.assertTrue(device.is_active)

    def test_last_synced_at_is_nullable(self):
        user = make_user()
        device = WearableDevice.objects.create(
            user=user,
            labfront_participant_id='LABFRONT001',
        )
        self.assertIsNone(device.last_synced_at)

    def test_deleting_user_deletes_device(self):
        user = make_user()
        WearableDevice.objects.create(user=user, labfront_participant_id='LABFRONT001')
        user.delete()
        self.assertEqual(WearableDevice.objects.count(), 0)

    def test_labfront_participant_id_is_unique(self):
        from django.db import IntegrityError
        user1 = make_user(email='u1@example.com')
        user2 = make_user(email='u2@example.com')
        WearableDevice.objects.create(user=user1, labfront_participant_id='SAME_ID')
        with self.assertRaises(IntegrityError):
            WearableDevice.objects.create(user=user2, labfront_participant_id='SAME_ID')

    def test_one_user_cannot_have_two_devices(self):
        from django.db import IntegrityError
        user = make_user()
        WearableDevice.objects.create(user=user, labfront_participant_id='ID_A')
        with self.assertRaises(IntegrityError):
            WearableDevice.objects.create(user=user, labfront_participant_id='ID_B')


# ---------------------------------------------------------------------------
# Model: HeartRateSample
# ---------------------------------------------------------------------------

@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class HeartRateSampleModelTests(TestCase):

    def test_sample_links_to_user(self):
        user = make_user()
        sample = HeartRateSample.objects.create(
            user=user,
            timestamp=timezone.now(),
            bpm=72,
        )
        self.assertEqual(sample.user, user)
        self.assertEqual(sample.bpm, 72)

    def test_source_defaults_to_garmin_labfront(self):
        user = make_user()
        sample = HeartRateSample.objects.create(
            user=user,
            timestamp=timezone.now(),
            bpm=80,
        )
        self.assertEqual(sample.source, 'garmin_labfront')

    def test_deleting_user_deletes_samples(self):
        user = make_user()
        HeartRateSample.objects.create(user=user, timestamp=timezone.now(), bpm=80)
        user.delete()
        self.assertEqual(HeartRateSample.objects.count(), 0)


# ---------------------------------------------------------------------------
# Model: StressSample
# ---------------------------------------------------------------------------

@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class StressSampleModelTests(TestCase):

    def test_sample_links_to_user(self):
        user = make_user()
        sample = StressSample.objects.create(
            user=user,
            timestamp=timezone.now(),
            stress_score=55,
        )
        self.assertEqual(sample.user, user)
        self.assertEqual(sample.stress_score, 55)

    def test_source_defaults_to_garmin_labfront(self):
        user = make_user()
        sample = StressSample.objects.create(
            user=user,
            timestamp=timezone.now(),
            stress_score=40,
        )
        self.assertEqual(sample.source, 'garmin_labfront')

    def test_deleting_user_deletes_stress_samples(self):
        user = make_user()
        StressSample.objects.create(user=user, timestamp=timezone.now(), stress_score=30)
        user.delete()
        self.assertEqual(StressSample.objects.count(), 0)



# ---------------------------------------------------------------------------
# Model: EMA
# ---------------------------------------------------------------------------

@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class EMAModelTests(TestCase):

    def test_ema_stores_survey_response(self):
        user = make_user()
        ema = EMA.objects.create(
            user=user,
            prompt_id='PROMPT_001',
            mood=5,
            energy=4,
            stress=3,
        )
        self.assertEqual(ema.mood, 5)
        self.assertEqual(ema.energy, 4)
        self.assertEqual(ema.stress, 3)
        self.assertEqual(ema.prompt_id, 'PROMPT_001')

    def test_sent_at_is_set_automatically(self):
        user = make_user()
        ema = EMA.objects.create(user=user, prompt_id='P1')
        self.assertIsNotNone(ema.sent_at)

    def test_status_defaults_to_pending(self):
        user = make_user()
        ema = EMA.objects.create(user=user, prompt_id='P1')
        self.assertEqual(ema.status, 'pending')

    def test_responded_at_is_nullable(self):
        user = make_user()
        ema = EMA.objects.create(user=user, prompt_id='P1')
        self.assertIsNone(ema.responded_at)

    def test_likert_fields_are_nullable(self):
        user = make_user()
        ema = EMA.objects.create(user=user, prompt_id='P1')
        self.assertIsNone(ema.mood)
        self.assertIsNone(ema.energy)
        self.assertIsNone(ema.stress)

    def test_deleting_user_deletes_ema_records(self):
        user = make_user()
        EMA.objects.create(user=user, prompt_id='P1', mood=5)
        user.delete()
        self.assertEqual(EMA.objects.count(), 0)

    def test_mood_rejects_out_of_range_values(self):
        from django.core.exceptions import ValidationError
        user = make_user()
        ema_low = EMA(user=user, prompt_id='P1', mood=0)
        with self.assertRaises(ValidationError):
            ema_low.full_clean()
        ema_high = EMA(user=user, prompt_id='P1', mood=8)
        with self.assertRaises(ValidationError):
            ema_high.full_clean()

    def test_mood_accepts_boundary_values(self):
        user = make_user()
        ema_min = EMA(user=user, prompt_id='P1', mood=1)
        ema_min.full_clean()
        ema_max = EMA(user=user, prompt_id='P1', mood=7)
        ema_max.full_clean()


# ---------------------------------------------------------------------------
# Model: JITAILog
# ---------------------------------------------------------------------------

@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class JITAILogModelTests(TestCase):

    def test_jitai_log_stores_intervention(self):
        user = make_user()
        log = JITAILog.objects.create(
            user=user,
            prompt_id='TEMPLATE_HR_HIGH',
            trigger_reason='hr_elevated+stress_high',
            hr_at_trigger=105,
            stress_at_trigger=72,
        )
        self.assertEqual(log.prompt_id, 'TEMPLATE_HR_HIGH')
        self.assertEqual(log.trigger_reason, 'hr_elevated+stress_high')
        self.assertEqual(log.hr_at_trigger, 105)
        self.assertEqual(log.stress_at_trigger, 72)

    def test_status_defaults_to_pending(self):
        user = make_user()
        log = JITAILog.objects.create(
            user=user,
            prompt_id='TEMPLATE_001',
            trigger_reason='hr_elevated',
        )
        self.assertEqual(log.status, 'pending')

    def test_triggered_at_is_set_automatically(self):
        user = make_user()
        log = JITAILog.objects.create(
            user=user,
            prompt_id='TEMPLATE_001',
            trigger_reason='hr_elevated',
        )
        self.assertIsNotNone(log.triggered_at)

    def test_hr_and_stress_at_trigger_are_nullable(self):
        user = make_user()
        log = JITAILog.objects.create(
            user=user,
            prompt_id='TEMPLATE_001',
            trigger_reason='ema_low_mood',
        )
        self.assertIsNone(log.hr_at_trigger)
        self.assertIsNone(log.stress_at_trigger)

    def test_deleting_user_deletes_jitai_logs(self):
        user = make_user()
        JITAILog.objects.create(
            user=user, prompt_id='TEMPLATE_001', trigger_reason='hr_elevated'
        )
        user.delete()
        self.assertEqual(JITAILog.objects.count(), 0)


# ---------------------------------------------------------------------------
# Serializer: WearableDevice
# ---------------------------------------------------------------------------

@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class WearableDeviceSerializerTests(TestCase):

    def test_serializer_creates_device(self):
        user = make_user()
        data = {
            'user': user.user_id,
            'labfront_participant_id': 'LABFRONT001',
        }
        serializer = WearableDeviceSerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        device = serializer.save()
        self.assertEqual(device.labfront_participant_id, 'LABFRONT001')

    def test_id_is_read_only(self):
        user = make_user()
        data = {
            'id': 999,
            'user': user.user_id,
            'labfront_participant_id': 'LABFRONT002',
        }
        serializer = WearableDeviceSerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        device = serializer.save()
        self.assertNotEqual(device.id, 999)


# ---------------------------------------------------------------------------
# Serializer: HeartRateSample
# ---------------------------------------------------------------------------

@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class HeartRateSampleSerializerTests(TestCase):

    def test_serializer_creates_sample(self):
        user = make_user()
        data = {
            'user': user.user_id,
            'timestamp': '2026-01-01T10:00:00Z',
            'bpm': 72,
        }
        serializer = HeartRateSampleSerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        sample = serializer.save()
        self.assertEqual(sample.bpm, 72)
        self.assertEqual(sample.source, 'garmin_labfront')


# ---------------------------------------------------------------------------
# Serializer: StressSample
# ---------------------------------------------------------------------------

@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class StressSampleSerializerTests(TestCase):

    def test_serializer_creates_stress_sample(self):
        user = make_user()
        data = {
            'user': user.user_id,
            'timestamp': '2026-01-01T10:00:00Z',
            'stress_score': 55,
        }
        serializer = StressSampleSerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        sample = serializer.save()
        self.assertEqual(sample.stress_score, 55)
        self.assertEqual(sample.source, 'garmin_labfront')



# ---------------------------------------------------------------------------
# Serializer: EMA
# ---------------------------------------------------------------------------

@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class EMASerializerTests(TestCase):

    def test_serializer_creates_ema(self):
        user = make_user()
        data = {
            'prompt_id': 'PROMPT_001',
            'mood': 5,
            'energy': 4,
            'stress': 3,
        }
        serializer = EMASerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        ema = serializer.save(user=user)
        self.assertEqual(ema.mood, 5)
        self.assertEqual(ema.status, 'pending')

    def test_sent_at_is_read_only(self):
        user = make_user()
        data = {
            'prompt_id': 'PROMPT_001',
            'sent_at': '2020-01-01T00:00:00Z',
        }
        serializer = EMASerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        ema = serializer.save(user=user)
        self.assertNotEqual(str(ema.sent_at.year), '2020')


# ---------------------------------------------------------------------------
# Serializer: JITAILog
# ---------------------------------------------------------------------------

@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class JITAILogSerializerTests(TestCase):

    def test_serializer_creates_jitai_log(self):
        user = make_user()
        data = {
            'user': user.user_id,
            'prompt_id': 'TEMPLATE_HR_HIGH',
            'trigger_reason': 'hr_elevated+stress_high',
            'hr_at_trigger': 105,
            'stress_at_trigger': 72,
        }
        serializer = JITAILogSerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        log = serializer.save()
        self.assertEqual(log.trigger_reason, 'hr_elevated+stress_high')
        self.assertEqual(log.status, 'pending')

    def test_triggered_at_is_read_only(self):
        user = make_user()
        data = {
            'user': user.user_id,
            'prompt_id': 'TEMPLATE_001',
            'trigger_reason': 'hr_elevated',
            'triggered_at': '2020-01-01T00:00:00Z',
        }
        serializer = JITAILogSerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        log = serializer.save()
        self.assertNotEqual(str(log.triggered_at.year), '2020')


# ---------------------------------------------------------------------------
# API: POST /telemetry/ingest/ — TelemetryIngestView
# ---------------------------------------------------------------------------

@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class TelemetryIngestViewTests(TestCase):

    def setUp(self):
        self.user = make_user(email='telemetry@example.com')
        self.client = authenticated_client(self.user)

    def _payload(self):
        return {
            'user_id': self.user.user_id,
            'wearable_device': {
                'labfront_participant_id': 'LABFRONT-001',
                'is_active': True,
            },
            'heart_rate_samples': [
                {
                    'timestamp': '2026-06-12T14:00:00Z',
                    'bpm': 82,
                }
            ],
            'stress_samples': [
                {
                    'timestamp': '2026-06-12T14:03:00Z',
                    'stress_score': 45,
                }
            ],
            'emas': [
                {
                    'prompt_id': 'EMA_TEMPLATE_01',
                    'mood': 5,
                    'energy': 6,
                    'stress': 3,
                    'status': 'completed',
                }
            ],
            'jitai_logs': [
                {
                    'prompt_id': 'JITAI_TEMPLATE_01',
                    'trigger_reason': 'hr_elevated+stress_high',
                    'hr_at_trigger': 105,
                    'stress_at_trigger': 72,
                    'observed_mssd': 0.72,
                    'send_prompt': True,
                }
            ],
            'phone_events': [
                {
                    'session_id': 'SESSION_ABC',
                    'event_type': 'draft_started',
                    'occurred_at': '2026-06-12T14:04:00Z',
                    'screen_name': 'game_thread',
                    'latency_ms': 120,
                    'metadata': {'source': 'demo'},
                }
            ],
            'engagement_events': [
                {
                    'event_type': 'notification_tapped',
                    'occurred_at': '2026-06-12T14:05:00Z',
                }
            ],
        }

    def test_valid_payload_creates_telemetry_records(self):
        response = self.client.post('/telemetry/ingest/', self._payload(), format='json')

        self.assertEqual(response.status_code, http_status.HTTP_201_CREATED)
        self.assertEqual(WearableDevice.objects.filter(user=self.user).count(), 1)
        self.assertEqual(HeartRateSample.objects.count(), 1)
        self.assertEqual(StressSample.objects.count(), 1)
        self.assertEqual(EMA.objects.count(), 1)
        self.assertEqual(JITAILog.objects.count(), 1)
        self.assertEqual(PhoneTelemetry.objects.count(), 1)
        self.assertEqual(EngagementLog.objects.count(), 1)
        self.assertEqual(response.data['counts']['heart_rate_samples'], 1)
        self.assertEqual(response.data['counts']['stress_samples'], 1)
        self.assertEqual(response.data['counts']['phone_events'], 1)
        self.assertEqual(response.data['counts']['engagement_events'], 1)
        self.assertAlmostEqual(JITAILog.objects.get().observed_mssd, 0.72)
        self.assertTrue(JITAILog.objects.get().send_prompt)

    def test_missing_user_id_returns_400(self):
        payload = self._payload()
        payload.pop('user_id')

        response = self.client.post('/telemetry/ingest/', payload, format='json')

        self.assertEqual(response.status_code, http_status.HTTP_400_BAD_REQUEST)

    def test_unknown_user_returns_404(self):
        payload = self._payload()
        payload['user_id'] = 99999

        response = self.client.post('/telemetry/ingest/', payload, format='json')

        self.assertEqual(response.status_code, http_status.HTTP_404_NOT_FOUND)

    def test_bpm_out_of_range_returns_400(self):
        payload = self._payload()
        payload['heart_rate_samples'][0]['bpm'] = 999

        response = self.client.post('/telemetry/ingest/', payload, format='json')

        self.assertEqual(response.status_code, http_status.HTTP_400_BAD_REQUEST)
        self.assertEqual(HeartRateSample.objects.count(), 0)

    def test_repeated_device_upsert_does_not_duplicate(self):
        self.client.post('/telemetry/ingest/', self._payload(), format='json')
        response = self.client.post('/telemetry/ingest/', self._payload(), format='json')

        self.assertEqual(response.status_code, http_status.HTTP_201_CREATED)
        self.assertEqual(WearableDevice.objects.filter(user=self.user).count(), 1)


# ---------------------------------------------------------------------------
# Model: PhoneTelemetry
# ---------------------------------------------------------------------------

@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class PhoneTelemetryModelTests(TestCase):

    def test_creates_phone_telemetry_event(self):
        from app.models import PhoneTelemetry
        user = make_user()
        event = PhoneTelemetry.objects.create(
            user=user,
            session_id='SESSION_ABC',
            event_type='draft_started',
            occurred_at=timezone.now(),
        )
        self.assertEqual(event.event_type, 'draft_started')
        self.assertIsNotNone(event.recorded_at)

    def test_metadata_is_nullable(self):
        from app.models import PhoneTelemetry
        user = make_user()
        event = PhoneTelemetry.objects.create(
            user=user,
            session_id='SESSION_ABC',
            event_type='session_start',
            occurred_at=timezone.now(),
        )
        self.assertIsNone(event.metadata)

    def test_irb_validator_rejects_long_string_in_metadata(self):
        from django.core.exceptions import ValidationError
        from app.models import PhoneTelemetry
        user = make_user()
        event = PhoneTelemetry(
            user=user,
            session_id='SESSION_ABC',
            event_type='draft_submitted',
            occurred_at=timezone.now(),
            metadata={'text': 'x' * 51},
        )
        with self.assertRaises(ValidationError):
            event.full_clean()

    def test_irb_validator_accepts_short_string_in_metadata(self):
        from app.models import PhoneTelemetry
        user = make_user()
        event = PhoneTelemetry(
            user=user,
            session_id='SESSION_ABC',
            event_type='draft_submitted',
            occurred_at=timezone.now(),
            metadata={'label': 'submit'},
        )
        event.full_clean()

    def test_irb_validator_accepts_integer_metadata_values(self):
        from app.models import PhoneTelemetry
        user = make_user()
        event = PhoneTelemetry(
            user=user,
            session_id='SESSION_ABC',
            event_type='draft_submitted',
            occurred_at=timezone.now(),
            metadata={'keystroke_count': 42, 'delete_count': 5},
        )
        event.full_clean()

    def test_deleting_user_deletes_phone_telemetry(self):
        from app.models import PhoneTelemetry
        user = make_user()
        PhoneTelemetry.objects.create(
            user=user, session_id='S1', event_type='session_start', occurred_at=timezone.now()
        )
        user.delete()
        self.assertEqual(PhoneTelemetry.objects.count(), 0)


# ---------------------------------------------------------------------------
# Model: EngagementLog
# ---------------------------------------------------------------------------

@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class EngagementLogModelTests(TestCase):

    def test_creates_engagement_event(self):
        from app.models import EngagementLog
        user = make_user()
        log = EngagementLog.objects.create(
            user=user,
            event_type='ema_completed',
            occurred_at=timezone.now(),
        )
        self.assertEqual(log.event_type, 'ema_completed')
        self.assertIsNone(log.jitai_log)
        self.assertIsNotNone(log.recorded_at)

    def test_jitai_log_is_nullable(self):
        from app.models import EngagementLog
        user = make_user()
        log = EngagementLog.objects.create(
            user=user, event_type='ema_opened', occurred_at=timezone.now()
        )
        self.assertIsNone(log.jitai_log)

    def test_jitai_log_deletion_sets_null(self):
        from app.models import EngagementLog
        user = make_user()
        jitai = JITAILog.objects.create(
            user=user, prompt_id='T1', trigger_reason='hr_elevated'
        )
        log = EngagementLog.objects.create(
            user=user,
            jitai_log=jitai,
            event_type='notification_tapped',
            occurred_at=timezone.now(),
        )
        jitai.delete()
        log.refresh_from_db()
        self.assertIsNone(log.jitai_log)

    def test_deleting_user_deletes_engagement_logs(self):
        from app.models import EngagementLog
        user = make_user()
        EngagementLog.objects.create(
            user=user, event_type='ema_completed', occurred_at=timezone.now()
        )
        user.delete()
        self.assertEqual(EngagementLog.objects.count(), 0)


# ---------------------------------------------------------------------------
# Serializer: PhoneTelemetry
# ---------------------------------------------------------------------------

@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class PhoneTelemetrySerializerTests(TestCase):

    def _base_data(self):
        return {
            'session_id': 'SESSION_001',
            'event_type': 'draft_submitted',
            'occurred_at': '2026-09-01T15:00:00Z',
            'metadata': {'keystroke_count': 42, 'delete_count': 5},
        }

    def test_serializer_creates_event_with_integer_metadata(self):
        from app.serializers import PhoneTelemetrySerializer
        user = make_user()
        serializer = PhoneTelemetrySerializer(data=self._base_data())
        self.assertTrue(serializer.is_valid(), serializer.errors)
        event = serializer.save(user=user)
        self.assertEqual(event.event_type, 'draft_submitted')
        self.assertEqual(event.metadata['keystroke_count'], 42)

    def test_irb_violation_long_string_returns_validation_error(self):
        from app.serializers import PhoneTelemetrySerializer
        data = self._base_data()
        data['metadata'] = {'text': 'x' * 51}
        serializer = PhoneTelemetrySerializer(data=data)
        self.assertFalse(serializer.is_valid())
        self.assertIn('metadata', serializer.errors)

    def test_irb_allows_string_under_50_chars(self):
        from app.serializers import PhoneTelemetrySerializer
        data = self._base_data()
        data['metadata'] = {'label': 'submit'}
        serializer = PhoneTelemetrySerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_recorded_at_is_read_only(self):
        from app.serializers import PhoneTelemetrySerializer
        user = make_user()
        data = self._base_data()
        data['recorded_at'] = '2020-01-01T00:00:00Z'
        serializer = PhoneTelemetrySerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        event = serializer.save(user=user)
        self.assertNotEqual(str(event.recorded_at.year), '2020')


# ---------------------------------------------------------------------------
# Serializer: EngagementLog
# ---------------------------------------------------------------------------

@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class EngagementLogSerializerTests(TestCase):

    def test_serializer_creates_engagement_event(self):
        from app.serializers import EngagementLogSerializer
        user = make_user()
        data = {
            'event_type': 'ema_completed',
            'occurred_at': '2026-09-01T15:05:00Z',
        }
        serializer = EngagementLogSerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        log = serializer.save(user=user)
        self.assertEqual(log.event_type, 'ema_completed')
        self.assertIsNone(log.jitai_log)

    def test_serializer_accepts_nullable_jitai_log(self):
        from app.serializers import EngagementLogSerializer
        user = make_user()
        jitai = JITAILog.objects.create(
            user=user, prompt_id='T1', trigger_reason='hr_elevated'
        )
        data = {
            'jitai_log': jitai.id,
            'event_type': 'notification_tapped',
            'occurred_at': '2026-09-01T15:05:00Z',
        }
        serializer = EngagementLogSerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        log = serializer.save(user=user)
        self.assertEqual(log.jitai_log, jitai)


# ---------------------------------------------------------------------------
# Serializer: JITAILog — verify new fields present
# ---------------------------------------------------------------------------

@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class JITAILogSerializerFieldsTests(TestCase):

    def test_serializer_includes_ema_observed_mssd_send_prompt(self):
        user = make_user()
        ema = EMA.objects.create(user=user, prompt_id='P1', mood=5, stress=3, energy=4)
        data = {
            'user': user.user_id,
            'prompt_id': 'TEMPLATE_001',
            'trigger_reason': 'hr_elevated',
            'ema': ema.id,
            'observed_mssd': 12.5,
            'send_prompt': True,
        }
        serializer = JITAILogSerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        log = serializer.save()
        self.assertEqual(log.observed_mssd, 12.5)
        self.assertTrue(log.send_prompt)


# ---------------------------------------------------------------------------
# API: POST /wearable/, GET /wearable/<id>/, PATCH /wearable/<id>/
# ---------------------------------------------------------------------------

@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class WearableEndpointTests(TestCase):

    def setUp(self):
        self.user = make_user(email='wearable@ufl.edu')
        self.client = authenticated_client(self.user)

    def test_post_creates_device_and_returns_201(self):
        response = self.client.post('/wearable/', {
            'user': self.user.user_id,
            'labfront_participant_id': 'LABFRONT_001',
        }, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_201_CREATED)
        self.assertTrue(WearableDevice.objects.filter(user=self.user).exists())

    def test_post_without_auth_returns_401(self):
        response = APIClient().post('/wearable/', {
            'user': self.user.user_id,
            'labfront_participant_id': 'LABFRONT_001',
        }, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_401_UNAUTHORIZED)

    def test_get_returns_device_for_enrolled_user(self):
        WearableDevice.objects.create(
            user=self.user, labfront_participant_id='LABFRONT_001'
        )
        response = self.client.get(f'/wearable/{self.user.user_id}/')
        self.assertEqual(response.status_code, http_status.HTTP_200_OK)
        self.assertEqual(response.data['labfront_participant_id'], 'LABFRONT_001')

    def test_get_returns_404_when_no_device(self):
        response = self.client.get(f'/wearable/{self.user.user_id}/')
        self.assertEqual(response.status_code, http_status.HTTP_404_NOT_FOUND)

    def test_patch_updates_is_active(self):
        WearableDevice.objects.create(
            user=self.user, labfront_participant_id='LABFRONT_001'
        )
        response = self.client.patch(
            f'/wearable/{self.user.user_id}/',
            {'is_active': False},
            format='json',
        )
        self.assertEqual(response.status_code, http_status.HTTP_200_OK)
        device = WearableDevice.objects.get(user=self.user)
        self.assertFalse(device.is_active)

    def test_patch_nonexistent_device_returns_404(self):
        response = self.client.patch(
            f'/wearable/{self.user.user_id}/',
            {'is_active': False},
            format='json',
        )
        self.assertEqual(response.status_code, http_status.HTTP_404_NOT_FOUND)


# ---------------------------------------------------------------------------
# API: POST /ema/, GET /ema/<user_id>/
# ---------------------------------------------------------------------------

@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class EMAEndpointTests(TestCase):

    def setUp(self):
        self.user = make_user(email='ema@ufl.edu')
        self.client = authenticated_client(self.user)

    def test_post_creates_ema_and_returns_201(self):
        response = self.client.post('/ema/', {
            'user': self.user.user_id,
            'prompt_id': 'EMA_TEMPLATE_01',
            'mood': 5,
            'stress': 3,
            'energy': 6,
        }, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_201_CREATED)
        self.assertTrue(EMA.objects.filter(user=self.user).exists())

    def test_post_with_likert_responses_sets_completed_and_responded_at(self):
        response = self.client.post('/ema/', {
            'user': self.user.user_id,
            'prompt_id': 'EMA_TEMPLATE_01',
            'mood': 4,
            'stress': 2,
            'energy': 7,
        }, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_201_CREATED)
        ema = EMA.objects.get(user=self.user)
        self.assertEqual(ema.status, 'completed')
        self.assertIsNotNone(ema.responded_at)

    def test_post_without_likert_responses_leaves_status_pending(self):
        response = self.client.post('/ema/', {
            'user': self.user.user_id,
            'prompt_id': 'EMA_TEMPLATE_01',
        }, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_201_CREATED)
        ema = EMA.objects.get(user=self.user)
        self.assertEqual(ema.status, 'pending')
        self.assertIsNone(ema.responded_at)

    def test_post_without_auth_returns_401(self):
        response = APIClient().post('/ema/', {
            'user': self.user.user_id,
            'prompt_id': 'EMA_TEMPLATE_01',
        }, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_401_UNAUTHORIZED)

    def test_get_returns_ema_history_for_user(self):
        EMA.objects.create(user=self.user, prompt_id='P1', mood=5, stress=3, energy=4)
        EMA.objects.create(user=self.user, prompt_id='P2', mood=3, stress=6, energy=2)
        response = self.client.get(f'/ema/{self.user.user_id}/')
        self.assertEqual(response.status_code, http_status.HTTP_200_OK)
        self.assertEqual(len(response.data), 2)

    def test_get_returns_empty_list_for_user_with_no_emas(self):
        response = self.client.get(f'/ema/{self.user.user_id}/')
        self.assertEqual(response.status_code, http_status.HTTP_200_OK)
        self.assertEqual(len(response.data), 0)


# ---------------------------------------------------------------------------
# API: POST /jitai/, GET /jitai/<user_id>/
# ---------------------------------------------------------------------------

@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class JITAIEndpointTests(TestCase):

    def setUp(self):
        self.user = make_user(email='jitai@ufl.edu')
        self.client = authenticated_client(self.user)

    def test_post_creates_jitai_log_and_returns_201(self):
        response = self.client.post('/jitai/', {
            'user': self.user.user_id,
            'prompt_id': 'TEMPLATE_HR_HIGH',
            'trigger_reason': 'hr_elevated+stress_high',
            'hr_at_trigger': 110,
            'stress_at_trigger': 75,
            'observed_mssd': 18.4,
            'send_prompt': True,
        }, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_201_CREATED)
        self.assertEqual(JITAILog.objects.count(), 1)

    def test_post_without_auth_returns_401(self):
        response = APIClient().post('/jitai/', {
            'user': self.user.user_id,
            'prompt_id': 'T1',
            'trigger_reason': 'hr_elevated',
        }, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_401_UNAUTHORIZED)

    def test_get_returns_jitai_history(self):
        JITAILog.objects.create(
            user=self.user, prompt_id='T1', trigger_reason='hr_elevated', send_prompt=True
        )
        JITAILog.objects.create(
            user=self.user, prompt_id='T2', trigger_reason='ema_low_mood', send_prompt=False
        )
        response = self.client.get(f'/jitai/{self.user.user_id}/')
        self.assertEqual(response.status_code, http_status.HTTP_200_OK)
        self.assertEqual(len(response.data), 2)

    def test_get_returns_empty_list_for_user_with_no_logs(self):
        response = self.client.get(f'/jitai/{self.user.user_id}/')
        self.assertEqual(response.status_code, http_status.HTTP_200_OK)
        self.assertEqual(len(response.data), 0)

    def test_send_prompt_false_is_stored(self):
        self.client.post('/jitai/', {
            'user': self.user.user_id,
            'prompt_id': 'TEMPLATE_001',
            'trigger_reason': 'cooldown',
            'send_prompt': False,
        }, format='json')
        log = JITAILog.objects.get(user=self.user)
        self.assertFalse(log.send_prompt)


# ---------------------------------------------------------------------------
# API: GET /telemetry/hr/<user_id>/, GET /telemetry/stress/<user_id>/
# ---------------------------------------------------------------------------

@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class TelemetryReadEndpointTests(TestCase):

    def setUp(self):
        self.user = make_user(email='telread@ufl.edu')
        self.client = authenticated_client(self.user)

    def test_hr_returns_samples_for_user(self):
        HeartRateSample.objects.create(user=self.user, timestamp=timezone.now(), bpm=72)
        HeartRateSample.objects.create(user=self.user, timestamp=timezone.now(), bpm=85)
        response = self.client.get(f'/telemetry/hr/{self.user.user_id}/')
        self.assertEqual(response.status_code, http_status.HTTP_200_OK)
        self.assertEqual(len(response.data), 2)

    def test_hr_returns_empty_list_when_no_samples(self):
        response = self.client.get(f'/telemetry/hr/{self.user.user_id}/')
        self.assertEqual(response.status_code, http_status.HTTP_200_OK)
        self.assertEqual(response.data, [])

    def test_hr_limit_param_caps_results(self):
        for bpm in range(1, 6):
            HeartRateSample.objects.create(user=self.user, timestamp=timezone.now(), bpm=bpm * 10)
        response = self.client.get(f'/telemetry/hr/{self.user.user_id}/?limit=2')
        self.assertEqual(response.status_code, http_status.HTTP_200_OK)
        self.assertEqual(len(response.data), 2)

    def test_hr_requires_auth(self):
        response = APIClient().get(f'/telemetry/hr/{self.user.user_id}/')
        self.assertEqual(response.status_code, http_status.HTTP_401_UNAUTHORIZED)

    def test_stress_returns_samples_for_user(self):
        StressSample.objects.create(user=self.user, timestamp=timezone.now(), stress_score=55)
        response = self.client.get(f'/telemetry/stress/{self.user.user_id}/')
        self.assertEqual(response.status_code, http_status.HTTP_200_OK)
        self.assertEqual(len(response.data), 1)

    def test_stress_limit_param_caps_results(self):
        for score in range(1, 6):
            StressSample.objects.create(user=self.user, timestamp=timezone.now(), stress_score=score * 10)
        response = self.client.get(f'/telemetry/stress/{self.user.user_id}/?limit=3')
        self.assertEqual(response.status_code, http_status.HTTP_200_OK)
        self.assertEqual(len(response.data), 3)


# ---------------------------------------------------------------------------
# API: POST /telemetry/phone/
# ---------------------------------------------------------------------------

@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class PhoneTelemetryEndpointTests(TestCase):

    def setUp(self):
        self.user = make_user(email='phone@ufl.edu')
        self.client = authenticated_client(self.user)

    def _payload(self):
        return {
            'user': self.user.user_id,
            'session_id': 'SESSION_001',
            'event_type': 'draft_submitted',
            'occurred_at': '2026-09-01T15:00:00Z',
            'metadata': {'keystroke_count': 42, 'delete_count': 5},
        }

    def test_irb_violation_long_string_metadata_returns_400(self):
        payload = self._payload()
        payload['metadata'] = {'text': 'x' * 51}
        response = self.client.post('/telemetry/phone/', payload, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_400_BAD_REQUEST)

    def test_post_without_auth_returns_401(self):
        response = APIClient().post('/telemetry/phone/', self._payload(), format='json')
        self.assertEqual(response.status_code, http_status.HTTP_401_UNAUTHORIZED)

    def test_invalid_event_type_returns_400(self):
        payload = self._payload()
        payload['event_type'] = 'not_a_real_event'
        response = self.client.post('/telemetry/phone/', payload, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_400_BAD_REQUEST)


# ---------------------------------------------------------------------------
# API: POST /telemetry/engagement/
# ---------------------------------------------------------------------------

@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class EngagementEndpointTests(TestCase):

    def setUp(self):
        self.user = make_user(email='engage@ufl.edu')
        self.client = authenticated_client(self.user)

    def test_post_with_jitai_log_links_correctly(self):
        from app.models import EngagementLog
        jitai = JITAILog.objects.create(
            user=self.user, prompt_id='T1', trigger_reason='hr_elevated'
        )
        response = self.client.post('/telemetry/engagement/', {
            'user': self.user.user_id,
            'jitai_log': jitai.id,
            'event_type': 'notification_tapped',
            'occurred_at': '2026-09-01T15:10:00Z',
        }, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_201_CREATED)
        log = EngagementLog.objects.get(user=self.user)
        self.assertEqual(log.jitai_log, jitai)

    def test_post_without_auth_returns_401(self):
        response = APIClient().post('/telemetry/engagement/', {
            'user': self.user.user_id,
            'event_type': 'ema_completed',
            'occurred_at': '2026-09-01T15:10:00Z',
        }, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_401_UNAUTHORIZED)

    def test_invalid_event_type_returns_400(self):
        response = self.client.post('/telemetry/engagement/', {
            'user': self.user.user_id,
            'event_type': 'not_a_real_event',
            'occurred_at': '2026-09-01T15:10:00Z',
        }, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_400_BAD_REQUEST)


# ---------------------------------------------------------------------------
# IDOR: cross-user access blocked for WearableDevice
# ---------------------------------------------------------------------------

@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class WearableEndpointIDORTests(TestCase):

    def setUp(self):
        self.user = make_user(email='wearable_idor@ufl.edu')
        self.client = authenticated_client(self.user)

    def test_cannot_read_other_users_data(self):
        other_user = make_user(email='other_wearable@ufl.edu')
        WearableDevice.objects.create(user=other_user, labfront_participant_id='LABFRONT_OTHER')
        response = self.client.get(f'/wearable/{other_user.user_id}/')
        self.assertEqual(response.status_code, http_status.HTTP_403_FORBIDDEN)

    def test_cannot_patch_other_users_data(self):
        other_user = make_user(email='other_wearable2@ufl.edu')
        WearableDevice.objects.create(user=other_user, labfront_participant_id='LABFRONT_OTHER2')
        response = self.client.patch(
            f'/wearable/{other_user.user_id}/',
            {'is_active': False},
            format='json',
        )
        self.assertEqual(response.status_code, http_status.HTTP_403_FORBIDDEN)

    def test_staff_can_read_any_users_data(self):
        other_user = make_user(email='other_wearable3@ufl.edu')
        WearableDevice.objects.create(user=other_user, labfront_participant_id='LABFRONT_OTHER3')
        auth_user, _ = AuthUser.objects.get_or_create(
            username=self.user.email,
            defaults={'email': self.user.email},
        )
        auth_user.is_staff = True
        auth_user.save()
        response = self.client.get(f'/wearable/{other_user.user_id}/')
        self.assertEqual(response.status_code, http_status.HTTP_200_OK)


# ---------------------------------------------------------------------------
# IDOR: cross-user access blocked for EMA
# ---------------------------------------------------------------------------

@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class EMAEndpointIDORTests(TestCase):

    def setUp(self):
        self.user = make_user(email='ema_idor@ufl.edu')
        self.client = authenticated_client(self.user)

    def test_cannot_read_other_users_data(self):
        other_user = make_user(email='other_ema@ufl.edu')
        EMA.objects.create(user=other_user, prompt_id='P1', mood=5, stress=3, energy=4)
        response = self.client.get(f'/ema/{other_user.user_id}/')
        self.assertEqual(response.status_code, http_status.HTTP_403_FORBIDDEN)


# ---------------------------------------------------------------------------
# IDOR: cross-user access blocked for JITAILog
# ---------------------------------------------------------------------------

@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class JITAIEndpointIDORTests(TestCase):

    def setUp(self):
        self.user = make_user(email='jitai_idor@ufl.edu')
        self.client = authenticated_client(self.user)

    def test_cannot_read_other_users_data(self):
        other_user = make_user(email='other_jitai@ufl.edu')
        JITAILog.objects.create(user=other_user, prompt_id='T1', trigger_reason='hr_elevated')
        response = self.client.get(f'/jitai/{other_user.user_id}/')
        self.assertEqual(response.status_code, http_status.HTTP_403_FORBIDDEN)


# ---------------------------------------------------------------------------
# IDOR: cross-user access blocked for telemetry reads
# ---------------------------------------------------------------------------

@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class TelemetryReadIDORTests(TestCase):

    def setUp(self):
        self.user = make_user(email='telread_idor@ufl.edu')
        self.client = authenticated_client(self.user)

    def test_cannot_read_other_users_hr_data(self):
        from django.utils import timezone as tz
        other_user = make_user(email='other_hr@ufl.edu')
        HeartRateSample.objects.create(user=other_user, timestamp=tz.now(), bpm=72)
        response = self.client.get(f'/telemetry/hr/{other_user.user_id}/')
        self.assertEqual(response.status_code, http_status.HTTP_403_FORBIDDEN)

    def test_cannot_read_other_users_stress_data(self):
        from django.utils import timezone as tz
        other_user = make_user(email='other_stress@ufl.edu')
        StressSample.objects.create(user=other_user, timestamp=tz.now(), stress_score=55)
        response = self.client.get(f'/telemetry/stress/{other_user.user_id}/')
        self.assertEqual(response.status_code, http_status.HTTP_403_FORBIDDEN)


# ---------------------------------------------------------------------------
# Limit validation: non-integer limit returns 400
# ---------------------------------------------------------------------------

@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class TelemetryLimitValidationTests(TestCase):

    def setUp(self):
        self.user = make_user(email='limit_test@ufl.edu')
        self.client = authenticated_client(self.user)

    def test_hr_non_integer_limit_returns_400(self):
        response = self.client.get(f'/telemetry/hr/{self.user.user_id}/?limit=abc')
        self.assertEqual(response.status_code, http_status.HTTP_400_BAD_REQUEST)

    def test_stress_non_integer_limit_returns_400(self):
        response = self.client.get(f'/telemetry/stress/{self.user.user_id}/?limit=abc')
        self.assertEqual(response.status_code, http_status.HTTP_400_BAD_REQUEST)


# ---------------------------------------------------------------------------
# Serializer: EMA Likert range validation
# ---------------------------------------------------------------------------

@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class EMASerializerLikertValidationTests(TestCase):

    def _base_data(self, user):
        return {
            'user': user.user_id,
            'prompt_id': 'EMA_TEST',
            'mood': 5,
            'stress': 3,
            'energy': 4,
        }

    def test_mood_below_1_returns_invalid(self):
        user = make_user(email='ema_likert1@ufl.edu')
        data = self._base_data(user)
        data['mood'] = 0
        serializer = EMASerializer(data=data)
        self.assertFalse(serializer.is_valid())
        self.assertIn('mood', serializer.errors)

    def test_mood_above_7_returns_invalid(self):
        user = make_user(email='ema_likert2@ufl.edu')
        data = self._base_data(user)
        data['mood'] = 8
        serializer = EMASerializer(data=data)
        self.assertFalse(serializer.is_valid())
        self.assertIn('mood', serializer.errors)

    def test_stress_below_1_returns_invalid(self):
        user = make_user(email='ema_likert3@ufl.edu')
        data = self._base_data(user)
        data['stress'] = 0
        serializer = EMASerializer(data=data)
        self.assertFalse(serializer.is_valid())
        self.assertIn('stress', serializer.errors)

    def test_energy_above_7_returns_invalid(self):
        user = make_user(email='ema_likert4@ufl.edu')
        data = self._base_data(user)
        data['energy'] = 8
        serializer = EMASerializer(data=data)
        self.assertFalse(serializer.is_valid())
        self.assertIn('energy', serializer.errors)

    def test_boundary_values_1_and_7_are_valid(self):
        user = make_user(email='ema_likert5@ufl.edu')
        data = self._base_data(user)
        data['mood'] = 1
        data['stress'] = 7
        data['energy'] = 1
        serializer = EMASerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)


# ---------------------------------------------------------------------------
# Ownership: EMA user derived from JWT, not body
# ---------------------------------------------------------------------------

@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class EMAOwnershipAndReadOnlyTests(TestCase):

    def setUp(self):
        self.user = make_user(email='ema_own@ufl.edu')
        self.other = make_user(email='ema_other@ufl.edu')
        self.client = authenticated_client(self.user)

    def test_user_derived_from_token_not_body(self):
        response = self.client.post('/ema/', {
            'user': self.other.user_id,
            'prompt_id': 'ema_v1',
            'mood': 5,
            'stress': 3,
            'energy': 6,
        }, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_201_CREATED)
        ema = EMA.objects.get(id=response.data['id'])
        self.assertEqual(ema.user, self.user)

    def test_post_without_user_in_body_succeeds(self):
        response = self.client.post('/ema/', {
            'prompt_id': 'ema_v1',
            'mood': 5,
            'stress': 3,
            'energy': 6,
        }, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_201_CREATED)
        self.assertEqual(response.data['user'], self.user.user_id)

    def test_cannot_override_status_via_body(self):
        response = self.client.post('/ema/', {
            'prompt_id': 'ema_v1',
            'status': 'expired',
            'mood': 5,
            'stress': 3,
            'energy': 4,
        }, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_201_CREATED)
        self.assertEqual(response.data['status'], 'completed')

    def test_cannot_override_responded_at_via_body(self):
        response = self.client.post('/ema/', {
            'prompt_id': 'ema_v1',
            'responded_at': '2020-01-01T00:00:00Z',
        }, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_201_CREATED)
        self.assertIsNone(response.data['responded_at'])


# ---------------------------------------------------------------------------
# Ownership: PhoneTelemetry user derived from JWT, not body
# ---------------------------------------------------------------------------

@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class PhoneTelemetryOwnershipTests(TestCase):

    def setUp(self):
        self.user = make_user(email='phone_own@ufl.edu')
        self.other = make_user(email='phone_other@ufl.edu')
        self.client = authenticated_client(self.user)

    def test_user_derived_from_token_not_body(self):
        from app.models import PhoneTelemetry
        response = self.client.post('/telemetry/phone/', {
            'user': self.other.user_id,
            'session_id': 'S1',
            'event_type': 'session_start',
            'occurred_at': '2025-09-06T20:00:00Z',
        }, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_201_CREATED)
        event = PhoneTelemetry.objects.get(id=response.data['id'])
        self.assertEqual(event.user, self.user)

    def test_post_without_user_in_body_succeeds(self):
        response = self.client.post('/telemetry/phone/', {
            'session_id': 'S2',
            'event_type': 'session_end',
            'occurred_at': '2025-09-06T20:01:00Z',
        }, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_201_CREATED)
        self.assertEqual(response.data['user'], self.user.user_id)


# ---------------------------------------------------------------------------
# Ownership: EngagementLog user derived from JWT, not body
# ---------------------------------------------------------------------------

@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class EngagementLogOwnershipTests(TestCase):

    def setUp(self):
        self.user = make_user(email='engage_own@ufl.edu')
        self.other = make_user(email='engage_other@ufl.edu')
        self.client = authenticated_client(self.user)

    def test_user_derived_from_token_not_body(self):
        from app.models import EngagementLog
        response = self.client.post('/telemetry/engagement/', {
            'user': self.other.user_id,
            'event_type': 'ema_opened',
            'occurred_at': '2025-09-06T20:00:00Z',
        }, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_201_CREATED)
        log = EngagementLog.objects.get(id=response.data['id'])
        self.assertEqual(log.user, self.user)

    def test_post_without_user_in_body_succeeds(self):
        response = self.client.post('/telemetry/engagement/', {
            'event_type': 'ema_dismissed',
            'occurred_at': '2025-09-06T20:01:00Z',
        }, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_201_CREATED)
        self.assertEqual(response.data['user'], self.user.user_id)


# ---------------------------------------------------------------------------
# Auth: TelemetryIngestView requires authentication
# ---------------------------------------------------------------------------

@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class TelemetryIngestAuthTests(TestCase):

    def test_ingest_without_auth_returns_401(self):
        user = make_user(email='ingest_anon@ufl.edu')
        response = APIClient().post('/telemetry/ingest/', {
            'user_id': user.user_id,
        }, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_401_UNAUTHORIZED)


# ---------------------------------------------------------------------------
# Legacy routes: verified absent after removal
# ---------------------------------------------------------------------------

class LegacyRouteTests(TestCase):

    def setUp(self):
        self.client = APIClient()

    def test_poll_cfbd_is_gone(self):
        response = self.client.get('/poll-cfbd/')
        self.assertEqual(response.status_code, 404)

    def test_home_tile_is_gone(self):
        response = self.client.get('/home-tile/')
        self.assertEqual(response.status_code, 404)

    def test_schedule_tile_is_gone(self):
        response = self.client.get('/schedule-tile/')
        self.assertEqual(response.status_code, 404)

    def test_auth_refresh_alias_is_gone(self):
        response = self.client.post('/auth/refresh/', {'refresh': 'token'}, format='json')
        self.assertEqual(response.status_code, 404)


# ---------------------------------------------------------------------------
# Task: evaluate_jitai_triggers
# ---------------------------------------------------------------------------

@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class EvaluateJITAITriggersTests(TestCase):

    def _make_enrolled_user(self, email='jitai_task@ufl.edu'):
        user = make_user(email=email, push_token='ExponentPushToken[test123]')
        user.is_enrolled = True
        user.save()
        WearableDevice.objects.create(user=user, labfront_participant_id=f'LF_{email}')
        return user

    def _make_ema(self, user, mood, stress, energy, offset_seconds=0):
        ema = EMA.objects.create(
            user=user,
            prompt_id='P1',
            mood=mood,
            stress=stress,
            energy=energy,
            status='completed',
            responded_at=timezone.now(),
        )
        t = timezone.now() - timedelta(seconds=3600) + timedelta(seconds=offset_seconds)
        EMA.objects.filter(pk=ema.pk).update(sent_at=t)
        ema.refresh_from_db()
        return ema

    def test_no_enrolled_users_creates_no_logs(self):
        from app.tasks import evaluate_jitai_triggers
        evaluate_jitai_triggers()
        self.assertEqual(JITAILog.objects.count(), 0)

    def test_enrolled_user_with_no_emas_creates_no_log(self):
        from app.tasks import evaluate_jitai_triggers
        self._make_enrolled_user()
        evaluate_jitai_triggers()
        self.assertEqual(JITAILog.objects.count(), 0)

    def test_all_emas_already_evaluated_creates_no_new_log(self):
        from app.tasks import evaluate_jitai_triggers
        user = self._make_enrolled_user()
        ema = self._make_ema(user, 4, 4, 4, offset_seconds=0)
        JITAILog.objects.create(user=user, prompt_id='P1', trigger_reason='prior', ema=ema)
        evaluate_jitai_triggers()
        self.assertEqual(JITAILog.objects.count(), 1)

    @patch('app.tasks.send_jitai_prompt')
    def test_insufficient_history_writes_log_with_send_prompt_false(self, mock_send):
        from app.tasks import evaluate_jitai_triggers
        user = self._make_enrolled_user()
        self._make_ema(user, 4, 4, 4, offset_seconds=0)
        self._make_ema(user, 4, 4, 4, offset_seconds=60)
        evaluate_jitai_triggers()
        log = JITAILog.objects.get(user=user)
        self.assertFalse(log.send_prompt)
        self.assertEqual(log.trigger_reason, 'insufficient within-person history')
        mock_send.assert_not_called()

    @patch('app.tasks.random.uniform', return_value=0.1)
    @patch('app.tasks.send_jitai_prompt')
    def test_volatile_emas_trigger_prompt_and_call_expo(self, mock_send, mock_rand):
        # 5 stable EMAs establish a baseline MSSD near 0.
        # 1 volatile EMA (big drop in scores) pushes MSSD above the within-person
        # threshold, triggering send_prompt=True.
        from app.tasks import evaluate_jitai_triggers
        user = self._make_enrolled_user()
        for i in range(5):
            self._make_ema(user, 4, 4, 4, offset_seconds=i * 60)
        volatile = self._make_ema(user, 1, 1, 1, offset_seconds=300)
        evaluate_jitai_triggers()
        log = JITAILog.objects.get(user=user)
        self.assertEqual(log.ema, volatile)
        self.assertTrue(log.send_prompt)
        self.assertEqual(log.status, 'pending')
        mock_send.assert_called_once()

    @patch('app.tasks.send_jitai_prompt')
    def test_jitai_log_written_even_when_no_prompt_sent(self, mock_send):
        from app.tasks import evaluate_jitai_triggers
        user = self._make_enrolled_user()
        self._make_ema(user, 4, 4, 4, offset_seconds=0)
        evaluate_jitai_triggers()
        self.assertEqual(JITAILog.objects.filter(user=user).count(), 1)
        mock_send.assert_not_called()

    @patch('app.tasks.send_jitai_prompt')
    def test_hr_and_stress_snapshots_stored_on_log(self, mock_send):
        from app.tasks import evaluate_jitai_triggers
        user = self._make_enrolled_user()
        HeartRateSample.objects.create(user=user, timestamp=timezone.now(), bpm=92)
        StressSample.objects.create(user=user, timestamp=timezone.now(), stress_score=68)
        self._make_ema(user, 4, 4, 4, offset_seconds=0)
        evaluate_jitai_triggers()
        log = JITAILog.objects.get(user=user)
        self.assertEqual(log.hr_at_trigger, 92)
        self.assertEqual(log.stress_at_trigger, 68)

    @patch('app.tasks.send_jitai_prompt')
    def test_exception_in_one_user_does_not_stop_others(self, mock_send):
        from app import tasks as tasks_module
        from app.tasks import evaluate_jitai_triggers
        user1 = self._make_enrolled_user(email='task_exc1@ufl.edu')
        user2 = self._make_enrolled_user(email='task_exc2@ufl.edu')
        self._make_ema(user1, 4, 4, 4, offset_seconds=0)
        self._make_ema(user2, 4, 4, 4, offset_seconds=0)

        original = tasks_module._evaluate_user

        def raise_for_user1(user):
            if user.user_id == user1.user_id:
                raise RuntimeError("simulated failure")
            original(user)

        with patch('app.tasks._evaluate_user', side_effect=raise_for_user1):
            evaluate_jitai_triggers()

        self.assertFalse(JITAILog.objects.filter(user=user1).exists())
        self.assertTrue(JITAILog.objects.filter(user=user2).exists())

    @patch('app.tasks.send_jitai_prompt')
    def test_prompt_id_set_on_log_when_send_prompt_true(self, mock_send):
        from app.tasks import evaluate_jitai_triggers
        user = self._make_enrolled_user()
        for i in range(5):
            self._make_ema(user, 4, 4, 4, offset_seconds=i * 60)
        self._make_ema(user, 1, 1, 1, offset_seconds=300)
        evaluate_jitai_triggers()
        log = JITAILog.objects.get(user=user)
        if log.send_prompt:
            self.assertNotEqual(log.prompt_id, '')
        else:
            self.assertEqual(log.prompt_id, '')


# ---------------------------------------------------------------------------
# Notification service: send_jitai_prompt
# ---------------------------------------------------------------------------

@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class SendJITAIPromptTests(TestCase):

    def _make_user_with_token(self, email='notify@ufl.edu'):
        return make_user(email=email, push_token='ExponentPushToken[test123]')

    def _make_log(self, user, prompt_id='TEMPLATE_001'):
        return JITAILog.objects.create(
            user=user,
            prompt_id=prompt_id,
            trigger_reason='prompt sent',
        )

    @patch('app.notification_service.PushClient')
    def test_push_payload_includes_type_prompt_id_and_jitai_log_id(self, MockPushClient):
        from app.notification_service import send_jitai_prompt
        user = self._make_user_with_token()
        log = self._make_log(user)
        MockPushClient.return_value.publish.return_value = MagicMock()

        send_jitai_prompt(user, log)

        message = MockPushClient.return_value.publish.call_args[0][0]
        self.assertEqual(message.data['type'], 'ema_prompt')
        self.assertEqual(message.data['prompt_id'], log.prompt_id)
        self.assertEqual(message.data['jitai_log_id'], log.pk)

    @patch('app.notification_service.PushClient')
    def test_no_push_token_returns_false_without_calling_expo(self, MockPushClient):
        from app.notification_service import send_jitai_prompt
        user = make_user(email='notoken@ufl.edu')
        log = self._make_log(user)

        result = send_jitai_prompt(user, log)

        self.assertFalse(result)
        MockPushClient.assert_not_called()

    @patch('app.notification_service.PushClient')
    def test_successful_push_returns_true(self, MockPushClient):
        from app.notification_service import send_jitai_prompt
        user = self._make_user_with_token()
        log = self._make_log(user)
        MockPushClient.return_value.publish.return_value = MagicMock()

        result = send_jitai_prompt(user, log)

        self.assertTrue(result)

    @patch('app.notification_service.PushClient')
    def test_device_not_registered_clears_push_token_and_marks_failed(self, MockPushClient):
        from app.notification_service import send_jitai_prompt
        from exponent_server_sdk import DeviceNotRegisteredError
        user = self._make_user_with_token()
        log = self._make_log(user)
        mock_response = MagicMock()
        mock_response.validate_response.side_effect = DeviceNotRegisteredError(MagicMock())
        MockPushClient.return_value.publish.return_value = mock_response

        result = send_jitai_prompt(user, log)

        self.assertFalse(result)
        user.refresh_from_db()
        self.assertIsNone(user.push_token)
        log.refresh_from_db()
        self.assertEqual(log.status, 'failed')

    @patch('app.notification_service.PushClient')
    def test_push_error_marks_log_as_failed_and_returns_false(self, MockPushClient):
        from app.notification_service import send_jitai_prompt
        user = self._make_user_with_token()
        log = self._make_log(user)
        MockPushClient.return_value.publish.side_effect = Exception('network error')

        result = send_jitai_prompt(user, log)

        self.assertFalse(result)
        log.refresh_from_db()
        self.assertEqual(log.status, 'failed')


# ---------------------------------------------------------------------------
# Model: JITAILog MRT schema — new fields and status choices
# ---------------------------------------------------------------------------

@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class JITAILogMRTSchemaTests(TestCase):

    def _make_log(self, **kwargs):
        user = make_user(email='mrtschema@test.com')
        defaults = {
            'user': user,
            'prompt_id': 'default',
            'trigger_reason': 'test',
        }
        defaults.update(kwargs)
        return JITAILog.objects.create(**defaults)

    def test_decision_point_id_is_nullable(self):
        log = self._make_log()
        self.assertIsNone(log.decision_point_id)

    def test_decision_point_id_unique_constraint(self):
        self._make_log(decision_point_id='ema_1')
        with self.assertRaises(Exception):
            self._make_log(decision_point_id='ema_1')

    def test_multiple_null_decision_point_ids_allowed(self):
        self._make_log(decision_point_id=None)
        make_user(email='mrtschema2@test.com')
        log2 = JITAILog.objects.create(
            user=make_user(email='mrtschema3@test.com'),
            prompt_id='default',
            trigger_reason='test',
            decision_point_id=None,
        )
        self.assertIsNone(log2.decision_point_id)

    def test_randomization_probability_nullable(self):
        log = self._make_log(randomization_probability=None)
        self.assertIsNone(log.randomization_probability)

    def test_randomization_draw_nullable(self):
        log = self._make_log(randomization_draw=None)
        self.assertIsNone(log.randomization_draw)

    def test_status_pending_is_valid(self):
        log = self._make_log(status='pending')
        self.assertEqual(log.status, 'pending')

    def test_status_not_sent_is_valid(self):
        log = self._make_log(status='not_sent')
        self.assertEqual(log.status, 'not_sent')

    def test_status_default_is_pending(self):
        log = self._make_log()
        self.assertEqual(log.status, 'pending')


@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class EvaluateUserMRTTests(TestCase):

    def setUp(self):
        self.user = make_user(
            email='mrteval@test.com',
            is_enrolled=True,
            push_token='ExponentPushToken[abc123]',
        )
        WearableDevice.objects.create(
            user=self.user,
            labfront_participant_id='labfront-mrt-001',
            is_active=True,
        )
        base = timezone.now() - timedelta(hours=12)
        for i in range(5):
            EMA.objects.create(
                user=self.user,
                prompt_id='default',
                sent_at=base + timedelta(hours=i * 2),
                responded_at=base + timedelta(hours=i * 2, minutes=5),
                status='completed',
                mood=(i % 7) + 1,
                stress=((i + 2) % 7) + 1,
                energy=((i + 4) % 7) + 1,
            )

    def _latest_ema(self):
        return EMA.objects.filter(user=self.user, status='completed').order_by('-sent_at').first()

    def _eligible_df(self, ema):
        import pandas as pd
        return pd.DataFrame([{
            'user_id': self.user.user_id,
            'timestamp': pd.Timestamp(ema.sent_at),
            'ema': 4.0,
            'observed_mssd': 2.5,
            'eligible': True,
            'decision_reason': 'prompt sent',
            'user_threshold': 1.0,
        }])

    def _ineligible_df(self, ema):
        import pandas as pd
        return pd.DataFrame([{
            'user_id': self.user.user_id,
            'timestamp': pd.Timestamp(ema.sent_at),
            'ema': 4.0,
            'observed_mssd': 0.5,
            'eligible': False,
            'decision_reason': 'below within-person threshold',
            'user_threshold': 1.0,
        }])

    @patch('app.tasks.send_jitai_prompt')
    @patch('app.tasks.apply_decision_rules')
    @patch('app.tasks.calculate_mssd')
    @patch('app.tasks.random.uniform', return_value=0.3)
    def test_eligible_below_p_sends_and_logs_draw(self, mock_rand, mock_mssd, mock_rules, mock_send):
        os.environ['JITAI_RANDOMIZATION_PROBABILITY'] = '0.5'
        ema = self._latest_ema()
        mock_mssd.return_value = self._eligible_df(ema)
        mock_rules.return_value = self._eligible_df(ema)

        from app.tasks import _evaluate_user
        _evaluate_user(self.user)

        log = JITAILog.objects.get(user=self.user)
        self.assertTrue(log.send_prompt)
        self.assertEqual(log.randomization_draw, 0.3)
        self.assertEqual(log.randomization_probability, 0.5)
        self.assertIsNotNone(log.decision_point_id)
        mock_send.assert_called_once()

    @patch('app.tasks.send_jitai_prompt')
    @patch('app.tasks.apply_decision_rules')
    @patch('app.tasks.calculate_mssd')
    @patch('app.tasks.random.uniform', return_value=0.8)
    def test_eligible_above_p_does_not_send(self, mock_rand, mock_mssd, mock_rules, mock_send):
        os.environ['JITAI_RANDOMIZATION_PROBABILITY'] = '0.5'
        ema = self._latest_ema()
        mock_mssd.return_value = self._eligible_df(ema)
        mock_rules.return_value = self._eligible_df(ema)

        from app.tasks import _evaluate_user
        _evaluate_user(self.user)

        log = JITAILog.objects.get(user=self.user)
        self.assertFalse(log.send_prompt)
        self.assertEqual(log.randomization_draw, 0.8)
        self.assertEqual(log.status, 'not_sent')
        mock_send.assert_not_called()

    @patch('app.tasks.send_jitai_prompt')
    @patch('app.tasks.apply_decision_rules')
    @patch('app.tasks.calculate_mssd')
    def test_ineligible_draw_is_none_and_status_not_sent(self, mock_mssd, mock_rules, mock_send):
        ema = self._latest_ema()
        mock_mssd.return_value = self._ineligible_df(ema)
        mock_rules.return_value = self._ineligible_df(ema)

        from app.tasks import _evaluate_user
        _evaluate_user(self.user)

        log = JITAILog.objects.get(user=self.user)
        self.assertFalse(log.send_prompt)
        self.assertIsNone(log.randomization_draw)
        self.assertEqual(log.status, 'not_sent')
        mock_send.assert_not_called()

    @patch('app.tasks.send_jitai_prompt')
    @patch('app.tasks.apply_decision_rules')
    @patch('app.tasks.calculate_mssd')
    def test_idempotent_second_call_creates_no_duplicate(self, mock_mssd, mock_rules, mock_send):
        ema = self._latest_ema()
        mock_mssd.return_value = self._ineligible_df(ema)
        mock_rules.return_value = self._ineligible_df(ema)

        from app.tasks import _evaluate_user
        _evaluate_user(self.user)
        _evaluate_user(self.user)

        self.assertEqual(JITAILog.objects.filter(user=self.user).count(), 1)

    @patch('app.tasks.send_jitai_prompt')
    @patch('app.tasks.apply_decision_rules')
    @patch('app.tasks.calculate_mssd')
    def test_decision_point_id_is_ema_prefixed(self, mock_mssd, mock_rules, mock_send):
        ema = self._latest_ema()
        mock_mssd.return_value = self._ineligible_df(ema)
        mock_rules.return_value = self._ineligible_df(ema)

        from app.tasks import _evaluate_user
        _evaluate_user(self.user)

        log = JITAILog.objects.get(user=self.user)
        self.assertEqual(log.decision_point_id, f'ema_{ema.pk}')

    @patch('app.tasks.send_jitai_prompt')
    @patch('app.tasks.apply_decision_rules')
    @patch('app.tasks.calculate_mssd')
    @patch('app.tasks.random.uniform', return_value=0.3)
    def test_randomization_probability_always_logged(self, mock_rand, mock_mssd, mock_rules, mock_send):
        os.environ['JITAI_RANDOMIZATION_PROBABILITY'] = '0.4'
        ema = self._latest_ema()
        mock_mssd.return_value = self._eligible_df(ema)
        mock_rules.return_value = self._eligible_df(ema)

        from app.tasks import _evaluate_user
        _evaluate_user(self.user)

        log = JITAILog.objects.get(user=self.user)
        self.assertEqual(log.randomization_probability, 0.4)


class DecisionEngineEligibilityTests(TestCase):

    def _make_df(self):
        import pandas as pd
        from datetime import datetime, timedelta
        base = datetime(2026, 1, 1, 12, 0, 0)
        rows = [
            {
                'user_id': 1,
                'timestamp': pd.Timestamp(base + timedelta(hours=i * 2)),
                'ema': float((i * 3 % 6) + 1),
            }
            for i in range(8)
        ]
        return pd.DataFrame(rows)

    def test_output_has_eligible_column_not_send_prompt(self):
        from decision_engine.decision_engine import apply_decision_rules, calculate_mssd
        df = self._make_df()
        df = calculate_mssd(df, window=3)
        result = apply_decision_rules(df)
        self.assertIn('eligible', result.columns)
        self.assertNotIn('send_prompt', result.columns)

    def test_output_has_decision_reason_column(self):
        from decision_engine.decision_engine import apply_decision_rules, calculate_mssd
        df = self._make_df()
        df = calculate_mssd(df, window=3)
        result = apply_decision_rules(df)
        self.assertIn('decision_reason', result.columns)

    def test_eligible_column_is_boolean(self):
        from decision_engine.decision_engine import apply_decision_rules, calculate_mssd
        df = self._make_df()
        df = calculate_mssd(df, window=3)
        result = apply_decision_rules(df)
        self.assertTrue(result['eligible'].isin([True, False]).all())

    def test_eligible_true_iff_decision_reason_is_prompt_sent(self):
        from decision_engine.decision_engine import apply_decision_rules, calculate_mssd
        df = self._make_df()
        df = calculate_mssd(df, window=3)
        result = apply_decision_rules(df)
        expected = result['decision_reason'] == 'prompt sent'
        self.assertTrue((result['eligible'] == expected).all())


@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class JITAILogSerializerMRTFieldsTests(TestCase):

    def setUp(self):
        self.user = make_user(email='jitaiser@test.com')

    def _make_log(self, **kwargs):
        defaults = {
            'user': self.user,
            'prompt_id': 'default',
            'trigger_reason': 'test',
            'decision_point_id': 'ema_99',
            'randomization_probability': 0.5,
            'randomization_draw': 0.3,
            'send_prompt': True,
            'status': 'pending',
        }
        defaults.update(kwargs)
        return JITAILog.objects.create(**defaults)

    def test_jitai_log_serializer_includes_decision_point_id(self):
        from app.serializers import JITAILogSerializer
        log = self._make_log()
        data = JITAILogSerializer(log).data
        self.assertIn('decision_point_id', data)
        self.assertEqual(data['decision_point_id'], 'ema_99')

    def test_jitai_log_serializer_includes_randomization_probability(self):
        from app.serializers import JITAILogSerializer
        log = self._make_log()
        data = JITAILogSerializer(log).data
        self.assertIn('randomization_probability', data)
        self.assertAlmostEqual(float(data['randomization_probability']), 0.5)

    def test_jitai_log_serializer_includes_randomization_draw(self):
        from app.serializers import JITAILogSerializer
        log = self._make_log()
        data = JITAILogSerializer(log).data
        self.assertIn('randomization_draw', data)
        self.assertAlmostEqual(float(data['randomization_draw']), 0.3)

    def test_telemetry_jitai_serializer_accepts_new_fields(self):
        from app.serializers import TelemetryJITAILogSerializer
        data = {
            'prompt_id': 'default',
            'trigger_reason': 'test',
            'decision_point_id': 'ema_42',
            'randomization_probability': 0.5,
            'randomization_draw': 0.2,
        }
        ser = TelemetryJITAILogSerializer(data=data)
        self.assertTrue(ser.is_valid(), ser.errors)

    def test_telemetry_jitai_serializer_new_fields_are_optional(self):
        from app.serializers import TelemetryJITAILogSerializer
        data = {
            'prompt_id': 'default',
            'trigger_reason': 'test',
        }
        ser = TelemetryJITAILogSerializer(data=data)
        self.assertTrue(ser.is_valid(), ser.errors)

    def test_telemetry_jitai_serializer_probability_range_validation(self):
        from app.serializers import TelemetryJITAILogSerializer
        data = {
            'prompt_id': 'default',
            'trigger_reason': 'test',
            'randomization_probability': 1.5,
        }
        ser = TelemetryJITAILogSerializer(data=data)
        self.assertFalse(ser.is_valid())
        self.assertIn('randomization_probability', ser.errors)
