from unittest.mock import patch, MagicMock
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework import status as http_status
from django.contrib.auth.models import User as AuthUser
from rest_framework_simplejwt.tokens import RefreshToken

from app.models import User, UserData, WearableDevice, HeartRateSample, StressSample, EMA, JITAILog
from app.serializers import (
    UserSerializer, UserDataSerializer,
    WearableDeviceSerializer, HeartRateSampleSerializer,
    StressSampleSerializer, EMASerializer, JITAILogSerializer,
)
from app.utils import check_game_status, send_notification

FAST_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']


def make_user(email='test@example.com', password='testpass123', **kwargs):
    defaults = {'birthdate': '2000-01-01', 'gender': 'male'}
    defaults.update(kwargs)
    user = User(email=email, **defaults)
    user.set_password(password)
    user.save()
    return user


def make_game(home_name, home_pts, away_name, away_pts, game_status):
    game = MagicMock()
    game.home_team.name = home_name
    game.home_team.points = home_pts
    game.away_team.name = away_name
    game.away_team.points = away_pts
    game.status = game_status
    return game


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
# Utils: check_game_status
#
# NOTE: Tests for Florida as the HOME team (winning/losing while home) will
# FAIL due to a bug in utils.py line 57:
#   `if curr_game.home_team == curr_team:`
# should be:
#   `if curr_game.home_team.name == curr_team:`
# The tests are written for the correct expected behavior to document the bug.
# ---------------------------------------------------------------------------

class CheckGameStatusTests(TestCase):

    def _api(self, games):
        api = MagicMock()
        api.get_scoreboard.return_value = games
        return api

    def test_no_florida_game_returns_no_game_found(self):
        api = self._api([make_game('Alabama', 7, 'Georgia', 3, 'in_progress')])
        result, *_ = check_game_status(api)
        self.assertEqual(result, 'No game found')

    def test_empty_scoreboard_returns_no_game_found(self):
        api = self._api([])
        result, *_ = check_game_status(api)
        self.assertEqual(result, 'No game found')

    def test_scheduled_game_returns_game_not_started(self):
        api = self._api([make_game('Florida Gators', 0, 'Alabama', 0, 'scheduled')])
        result, *_ = check_game_status(api)
        self.assertEqual(result, 'Game not started')

    # Florida as AWAY team — these all pass (bug does not affect away branch)

    def test_florida_away_winning_by_14_or_more_returns_winning_decisive(self):
        api = self._api([make_game('Alabama', 10, 'Florida Gators', 24, 'in_progress')])
        result, *_ = check_game_status(api)
        self.assertEqual(result, 'winning_decisive')

    def test_florida_away_winning_by_1_to_13_returns_winning_close(self):
        api = self._api([make_game('Alabama', 10, 'Florida Gators', 17, 'in_progress')])
        result, *_ = check_game_status(api)
        self.assertEqual(result, 'winning_close')

    def test_florida_away_tied_returns_tied(self):
        api = self._api([make_game('Alabama', 7, 'Florida Gators', 7, 'in_progress')])
        result, *_ = check_game_status(api)
        self.assertEqual(result, 'tied')

    def test_florida_away_losing_by_1_to_13_returns_losing_close(self):
        api = self._api([make_game('Alabama', 17, 'Florida Gators', 10, 'in_progress')])
        result, *_ = check_game_status(api)
        self.assertEqual(result, 'losing_close')

    def test_florida_away_losing_by_14_or_more_returns_losing_decisive(self):
        api = self._api([make_game('Alabama', 35, 'Florida Gators', 7, 'in_progress')])
        result, *_ = check_game_status(api)
        self.assertEqual(result, 'losing_decisive')

    def test_florida_away_completed_winning_by_14_returns_won_decisive(self):
        api = self._api([make_game('Alabama', 10, 'Florida Gators', 35, 'completed')])
        result, *_ = check_game_status(api)
        self.assertEqual(result, 'won_decisive')

    def test_florida_away_completed_winning_by_1_to_13_returns_won_close(self):
        api = self._api([make_game('Alabama', 14, 'Florida Gators', 21, 'completed')])
        result, *_ = check_game_status(api)
        self.assertEqual(result, 'won_close')

    def test_florida_away_completed_losing_by_1_to_13_returns_lost_close(self):
        api = self._api([make_game('Alabama', 21, 'Florida Gators', 14, 'completed')])
        result, *_ = check_game_status(api)
        self.assertEqual(result, 'lost_close')

    def test_florida_away_completed_losing_by_14_or_more_returns_lost_decisive(self):
        api = self._api([make_game('Alabama', 35, 'Florida Gators', 10, 'completed')])
        result, *_ = check_game_status(api)
        self.assertEqual(result, 'lost_decisive')

    # Florida as HOME team — these FAIL due to the home_team == curr_team bug

    def test_florida_home_winning_by_14_or_more_returns_winning_decisive(self):
        api = self._api([make_game('Florida Gators', 24, 'Alabama', 10, 'in_progress')])
        result, *_ = check_game_status(api)
        self.assertEqual(result, 'winning_decisive')

    def test_florida_home_winning_by_1_to_13_returns_winning_close(self):
        api = self._api([make_game('Florida Gators', 17, 'Alabama', 10, 'in_progress')])
        result, *_ = check_game_status(api)
        self.assertEqual(result, 'winning_close')

    def test_florida_home_losing_by_1_to_13_returns_losing_close(self):
        api = self._api([make_game('Florida Gators', 10, 'Alabama', 17, 'in_progress')])
        result, *_ = check_game_status(api)
        self.assertEqual(result, 'losing_close')

    def test_florida_home_losing_by_14_or_more_returns_losing_decisive(self):
        api = self._api([make_game('Florida Gators', 3, 'Alabama', 21, 'in_progress')])
        result, *_ = check_game_status(api)
        self.assertEqual(result, 'losing_decisive')

    def test_florida_home_completed_won_decisive(self):
        api = self._api([make_game('Florida Gators', 35, 'Alabama', 14, 'completed')])
        result, *_ = check_game_status(api)
        self.assertEqual(result, 'won_decisive')

    def test_florida_home_completed_lost_decisive(self):
        api = self._api([make_game('Florida Gators', 10, 'Alabama', 35, 'completed')])
        result, *_ = check_game_status(api)
        self.assertEqual(result, 'lost_decisive')

    # Result structure

    def test_result_includes_team_names_and_scores(self):
        api = self._api([make_game('Alabama', 10, 'Florida Gators', 24, 'in_progress')])
        _, home_team, home_score, away_team, away_score, _ = check_game_status(api)
        self.assertEqual(home_team, 'Alabama')
        self.assertEqual(home_score, 10)
        self.assertEqual(away_team, 'Florida Gators')
        self.assertEqual(away_score, 24)

    def test_live_game_returns_in_progress_completion_status(self):
        api = self._api([make_game('Alabama', 7, 'Florida Gators', 7, 'in_progress')])
        *_, completion = check_game_status(api)
        self.assertEqual(completion, 'in_progress')

    def test_finished_game_returns_completed_completion_status(self):
        api = self._api([make_game('Alabama', 14, 'Florida Gators', 21, 'completed')])
        *_, completion = check_game_status(api)
        self.assertEqual(completion, 'completed')


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
        self.client = APIClient()
        self.user = make_user()

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

    def test_nonexistent_user_returns_404(self):
        response = self.client.put('/user/99999/', {'first_name': 'Ghost'}, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_404_NOT_FOUND)


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
# API: POST /userdata/<id>/ — CreateUserDataView
# ---------------------------------------------------------------------------

@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class CreateUserDataViewTests(TestCase):

    def setUp(self):
        self.client = APIClient()
        self.user = make_user()

    def test_valid_payload_returns_201_with_data_id(self):
        response = self.client.post(f'/userdata/{self.user.user_id}/', {
            'goal_type': 'loseWeight',
            'weight_value': '185.0',
            'feel_better_value': 3,
        }, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_201_CREATED)
        self.assertIn('data_id', response.data)

    def test_entry_is_persisted_to_database(self):
        self.client.post(f'/userdata/{self.user.user_id}/', {
            'goal_type': 'loseWeight',
            'weight_value': '185.0',
        }, format='json')
        self.assertEqual(UserData.objects.filter(user=self.user).count(), 1)


# ---------------------------------------------------------------------------
# API: GET /userdata/latest/<id>/ — LatestUserDataView
# ---------------------------------------------------------------------------

@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class LatestUserDataViewTests(TestCase):

    def setUp(self):
        self.client = APIClient()
        self.user = make_user()

    def test_returns_most_recent_entry(self):
        UserData.objects.create(user=self.user, goal_type='loseWeight', weight_value=200)
        UserData.objects.create(user=self.user, goal_type='loseWeight', weight_value=185)
        response = self.client.get(f'/userdata/latest/{self.user.user_id}/')
        self.assertEqual(response.status_code, http_status.HTTP_200_OK)
        self.assertEqual(str(response.data['weight_value']), '185.0')

    def test_user_with_no_data_returns_404(self):
        response = self.client.get(f'/userdata/latest/{self.user.user_id}/')
        self.assertEqual(response.status_code, http_status.HTTP_404_NOT_FOUND)


# ---------------------------------------------------------------------------
# API: GET /auth/me/ — me_view
# ---------------------------------------------------------------------------

@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class MeViewTests(TestCase):

    def test_authenticated_request_returns_user_profile(self):
        app_user = make_user(email='me@ufl.edu')
        client = authenticated_client(app_user)
        response = client.get('/auth/me/')
        self.assertEqual(response.status_code, http_status.HTTP_200_OK)
        self.assertEqual(response.data['email'], 'me@ufl.edu')

    def test_unauthenticated_request_returns_401(self):
        response = APIClient().get('/auth/me/')
        self.assertEqual(response.status_code, http_status.HTTP_401_UNAUTHORIZED)


# ---------------------------------------------------------------------------
# Utils: send_notification — score deduplication via cache
# ---------------------------------------------------------------------------

class SendNotificationTests(TestCase):

    @patch('app.utils.send_push_notification_next_game')
    @patch('app.utils.cache')
    @patch('app.utils.get_users_with_push_token')
    def test_no_push_tokens_sends_nothing(self, mock_get_tokens, mock_cache, mock_send):
        mock_get_tokens.return_value = []
        send_notification('winning_decisive', 'Florida Gators', 24, 'Alabama', 10)
        mock_send.assert_not_called()

    @patch('app.utils.send_push_notification_next_game')
    @patch('app.utils.cache')
    @patch('app.utils.get_users_with_push_token')
    def test_new_score_sends_notification(self, mock_get_tokens, mock_cache, mock_send):
        mock_get_tokens.return_value = [{'user_id': 1, 'push_token': 'ExponentPushToken[abc]'}]
        mock_cache.get.return_value = None
        send_notification('winning_decisive', 'Florida Gators', 24, 'Alabama', 10)
        mock_send.assert_called_once()

    @patch('app.utils.send_push_notification_next_game')
    @patch('app.utils.cache')
    @patch('app.utils.get_users_with_push_token')
    def test_unchanged_score_does_not_resend(self, mock_get_tokens, mock_cache, mock_send):
        mock_get_tokens.return_value = [{'user_id': 1, 'push_token': 'ExponentPushToken[abc]'}]
        mock_cache.get.return_value = b'24-10'
        send_notification('winning_decisive', 'Florida Gators', 24, 'Alabama', 10)
        mock_send.assert_not_called()

    @patch('app.utils.send_push_notification_next_game')
    @patch('app.utils.cache')
    @patch('app.utils.get_users_with_push_token')
    def test_updated_score_sends_new_notification(self, mock_get_tokens, mock_cache, mock_send):
        mock_get_tokens.return_value = [{'user_id': 1, 'push_token': 'ExponentPushToken[abc]'}]
        mock_cache.get.return_value = b'17-10'
        send_notification('winning_decisive', 'Florida Gators', 24, 'Alabama', 10)
        mock_send.assert_called_once()

    @patch('app.utils.send_push_notification_next_game')
    @patch('app.utils.cache')
    @patch('app.utils.get_users_with_push_token')
    def test_no_game_found_sends_nothing(self, mock_get_tokens, mock_cache, mock_send):
        mock_get_tokens.return_value = [{'user_id': 1, 'push_token': 'ExponentPushToken[abc]'}]
        mock_cache.get.return_value = None
        send_notification('No game found', '', 0, '', 0)
        mock_send.assert_not_called()

    @patch('app.utils.send_push_notification_next_game')
    @patch('app.utils.cache')
    @patch('app.utils.get_users_with_push_token')
    def test_sending_notification_updates_cache_with_current_score(
        self, mock_get_tokens, mock_cache, mock_send
    ):
        mock_get_tokens.return_value = [{'user_id': 1, 'push_token': 'ExponentPushToken[abc]'}]
        mock_cache.get.return_value = None
        send_notification('winning_decisive', 'Florida Gators', 24, 'Alabama', 10)
        mock_cache.set.assert_called_once_with('last_score', '24-10')


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
            fitabase_participant_id='FITABASE001',
        )
        self.assertEqual(device.user, user)

    def test_is_active_defaults_to_true(self):
        user = make_user()
        device = WearableDevice.objects.create(
            user=user,
            fitabase_participant_id='FITABASE001',
        )
        self.assertTrue(device.is_active)

    def test_last_synced_at_is_nullable(self):
        user = make_user()
        device = WearableDevice.objects.create(
            user=user,
            fitabase_participant_id='FITABASE001',
        )
        self.assertIsNone(device.last_synced_at)

    def test_deleting_user_deletes_device(self):
        user = make_user()
        WearableDevice.objects.create(user=user, fitabase_participant_id='FITABASE001')
        user.delete()
        self.assertEqual(WearableDevice.objects.count(), 0)

    def test_fitabase_participant_id_is_unique(self):
        from django.db import IntegrityError
        user1 = make_user(email='u1@example.com')
        user2 = make_user(email='u2@example.com')
        WearableDevice.objects.create(user=user1, fitabase_participant_id='SAME_ID')
        with self.assertRaises(IntegrityError):
            WearableDevice.objects.create(user=user2, fitabase_participant_id='SAME_ID')

    def test_one_user_cannot_have_two_devices(self):
        from django.db import IntegrityError
        user = make_user()
        WearableDevice.objects.create(user=user, fitabase_participant_id='ID_A')
        with self.assertRaises(IntegrityError):
            WearableDevice.objects.create(user=user, fitabase_participant_id='ID_B')


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

    def test_source_defaults_to_garmin_fitabase(self):
        user = make_user()
        sample = HeartRateSample.objects.create(
            user=user,
            timestamp=timezone.now(),
            bpm=80,
        )
        self.assertEqual(sample.source, 'garmin_fitabase')

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

    def test_source_defaults_to_garmin_fitabase(self):
        user = make_user()
        sample = StressSample.objects.create(
            user=user,
            timestamp=timezone.now(),
            stress_score=40,
        )
        self.assertEqual(sample.source, 'garmin_fitabase')

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

    def test_status_defaults_to_delivered(self):
        user = make_user()
        log = JITAILog.objects.create(
            user=user,
            prompt_id='TEMPLATE_001',
            trigger_reason='hr_elevated',
        )
        self.assertEqual(log.status, 'delivered')

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
            'fitabase_participant_id': 'FITABASE001',
            'device_name': 'Garmin Vivoactive 6',
        }
        serializer = WearableDeviceSerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        device = serializer.save()
        self.assertEqual(device.fitabase_participant_id, 'FITABASE001')

    def test_id_is_read_only(self):
        user = make_user()
        data = {
            'id': 999,
            'user': user.user_id,
            'fitabase_participant_id': 'FITABASE002',
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
        self.assertEqual(sample.source, 'garmin_fitabase')


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
        self.assertEqual(sample.source, 'garmin_fitabase')



# ---------------------------------------------------------------------------
# Serializer: EMA
# ---------------------------------------------------------------------------

@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class EMASerializerTests(TestCase):

    def test_serializer_creates_ema(self):
        user = make_user()
        data = {
            'user': user.user_id,
            'prompt_id': 'PROMPT_001',
            'mood': 5,
            'energy': 4,
            'stress': 3,
        }
        serializer = EMASerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        ema = serializer.save()
        self.assertEqual(ema.mood, 5)
        self.assertEqual(ema.status, 'pending')

    def test_sent_at_is_read_only(self):
        user = make_user()
        data = {
            'user': user.user_id,
            'prompt_id': 'PROMPT_001',
            'sent_at': '2020-01-01T00:00:00Z',
        }
        serializer = EMASerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        ema = serializer.save()
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
        self.assertEqual(log.status, 'delivered')

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
        self.client = APIClient()
        self.user = make_user(email='telemetry@example.com')

    def _payload(self):
        return {
            'user_id': self.user.user_id,
            'wearable_device': {
                'fitabase_participant_id': 'FITABASE-001',
                'device_name': 'Garmin Vivoactive 6',
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
        self.assertEqual(response.data['counts']['heart_rate_samples'], 1)
        self.assertEqual(response.data['counts']['stress_samples'], 1)

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
# Model: SleepSummary
# ---------------------------------------------------------------------------

@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class SleepSummaryModelTests(TestCase):

    def test_creates_sleep_summary(self):
        from app.models import SleepSummary
        user = make_user()
        summary = SleepSummary.objects.create(
            user=user,
            date='2026-08-31',
            total_minutes=420,
            deep_minutes=90,
            sleep_score=78,
        )
        self.assertEqual(summary.total_minutes, 420)
        self.assertEqual(summary.sleep_score, 78)
        self.assertEqual(summary.source, 'garmin_fitabase')

    def test_unique_together_user_date(self):
        from django.db import IntegrityError
        from app.models import SleepSummary
        user = make_user()
        SleepSummary.objects.create(user=user, date='2026-08-31', total_minutes=420)
        with self.assertRaises(IntegrityError):
            SleepSummary.objects.create(user=user, date='2026-08-31', total_minutes=380)

    def test_all_minute_fields_are_nullable(self):
        from app.models import SleepSummary
        user = make_user()
        summary = SleepSummary.objects.create(user=user, date='2026-09-01')
        self.assertIsNone(summary.total_minutes)
        self.assertIsNone(summary.light_minutes)
        self.assertIsNone(summary.deep_minutes)
        self.assertIsNone(summary.rem_minutes)
        self.assertIsNone(summary.awake_minutes)
        self.assertIsNone(summary.sleep_score)

    def test_deleting_user_deletes_sleep_summaries(self):
        from app.models import SleepSummary
        user = make_user()
        SleepSummary.objects.create(user=user, date='2026-08-31', total_minutes=420)
        user.delete()
        self.assertEqual(SleepSummary.objects.count(), 0)


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
            game_clock_state='live',
        )
        self.assertEqual(event.event_type, 'draft_started')
        self.assertEqual(event.game_clock_state, 'live')
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
            game_clock_state='live',
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
# Serializer: SleepSummary
# ---------------------------------------------------------------------------

@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class SleepSummarySerializerTests(TestCase):

    def test_serializer_creates_sleep_summary(self):
        from app.models import SleepSummary
        from app.serializers import SleepSummarySerializer
        user = make_user()
        data = {
            'user': user.user_id,
            'date': '2026-08-31',
            'total_minutes': 420,
            'deep_minutes': 90,
            'sleep_score': 78,
        }
        serializer = SleepSummarySerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        summary = serializer.save()
        self.assertEqual(summary.total_minutes, 420)
        self.assertEqual(summary.source, 'garmin_fitabase')

    def test_id_is_read_only(self):
        from app.serializers import SleepSummarySerializer
        user = make_user()
        data = {'id': 999, 'user': user.user_id, 'date': '2026-09-01'}
        serializer = SleepSummarySerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        summary = serializer.save()
        self.assertNotEqual(summary.id, 999)


# ---------------------------------------------------------------------------
# Serializer: PhoneTelemetry
# ---------------------------------------------------------------------------

@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class PhoneTelemetrySerializerTests(TestCase):

    def _base_data(self, user):
        return {
            'user': user.user_id,
            'session_id': 'SESSION_001',
            'event_type': 'draft_submitted',
            'occurred_at': '2026-09-01T15:00:00Z',
            'metadata': {'keystroke_count': 42, 'delete_count': 5},
        }

    def test_serializer_creates_event_with_integer_metadata(self):
        from app.serializers import PhoneTelemetrySerializer
        user = make_user()
        serializer = PhoneTelemetrySerializer(data=self._base_data(user))
        self.assertTrue(serializer.is_valid(), serializer.errors)
        event = serializer.save()
        self.assertEqual(event.event_type, 'draft_submitted')
        self.assertEqual(event.metadata['keystroke_count'], 42)

    def test_irb_violation_long_string_returns_validation_error(self):
        from app.serializers import PhoneTelemetrySerializer
        user = make_user()
        data = self._base_data(user)
        data['metadata'] = {'text': 'x' * 51}
        serializer = PhoneTelemetrySerializer(data=data)
        self.assertFalse(serializer.is_valid())
        self.assertIn('metadata', serializer.errors)

    def test_irb_allows_string_under_50_chars(self):
        from app.serializers import PhoneTelemetrySerializer
        user = make_user()
        data = self._base_data(user)
        data['metadata'] = {'label': 'submit'}
        serializer = PhoneTelemetrySerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)

    def test_recorded_at_is_read_only(self):
        from app.serializers import PhoneTelemetrySerializer
        user = make_user()
        data = self._base_data(user)
        data['recorded_at'] = '2020-01-01T00:00:00Z'
        serializer = PhoneTelemetrySerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        event = serializer.save()
        self.assertNotEqual(str(event.recorded_at.year), '2020')

    def test_game_clock_state_is_read_only(self):
        from app.serializers import PhoneTelemetrySerializer
        user = make_user()
        data = self._base_data(user)
        data['game_clock_state'] = 'live'
        serializer = PhoneTelemetrySerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        event = serializer.save()
        self.assertEqual(event.game_clock_state, 'pre')


# ---------------------------------------------------------------------------
# Serializer: EngagementLog
# ---------------------------------------------------------------------------

@override_settings(PASSWORD_HASHERS=FAST_HASHERS)
class EngagementLogSerializerTests(TestCase):

    def test_serializer_creates_engagement_event(self):
        from app.serializers import EngagementLogSerializer
        user = make_user()
        data = {
            'user': user.user_id,
            'event_type': 'ema_completed',
            'occurred_at': '2026-09-01T15:05:00Z',
        }
        serializer = EngagementLogSerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        log = serializer.save()
        self.assertEqual(log.event_type, 'ema_completed')
        self.assertIsNone(log.jitai_log)

    def test_serializer_accepts_nullable_jitai_log(self):
        from app.serializers import EngagementLogSerializer
        user = make_user()
        jitai = JITAILog.objects.create(
            user=user, prompt_id='T1', trigger_reason='hr_elevated'
        )
        data = {
            'user': user.user_id,
            'jitai_log': jitai.id,
            'event_type': 'notification_tapped',
            'occurred_at': '2026-09-01T15:05:00Z',
        }
        serializer = EngagementLogSerializer(data=data)
        self.assertTrue(serializer.is_valid(), serializer.errors)
        log = serializer.save()
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
            'fitabase_participant_id': 'FITABASE_001',
            'device_name': 'Garmin Vivoactive 6',
        }, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_201_CREATED)
        self.assertTrue(WearableDevice.objects.filter(user=self.user).exists())

    def test_post_without_auth_returns_401(self):
        response = APIClient().post('/wearable/', {
            'user': self.user.user_id,
            'fitabase_participant_id': 'FITABASE_001',
        }, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_401_UNAUTHORIZED)

    def test_get_returns_device_for_enrolled_user(self):
        WearableDevice.objects.create(
            user=self.user, fitabase_participant_id='FITABASE_001'
        )
        response = self.client.get(f'/wearable/{self.user.user_id}/')
        self.assertEqual(response.status_code, http_status.HTTP_200_OK)
        self.assertEqual(response.data['fitabase_participant_id'], 'FITABASE_001')

    def test_get_returns_404_when_no_device(self):
        response = self.client.get(f'/wearable/{self.user.user_id}/')
        self.assertEqual(response.status_code, http_status.HTTP_404_NOT_FOUND)

    def test_patch_updates_device_name(self):
        WearableDevice.objects.create(
            user=self.user, fitabase_participant_id='FITABASE_001'
        )
        response = self.client.patch(
            f'/wearable/{self.user.user_id}/',
            {'device_name': 'Garmin Vivoactive 6 Pro'},
            format='json',
        )
        self.assertEqual(response.status_code, http_status.HTTP_200_OK)
        device = WearableDevice.objects.get(user=self.user)
        self.assertEqual(device.device_name, 'Garmin Vivoactive 6 Pro')

    def test_patch_nonexistent_device_returns_404(self):
        response = self.client.patch(
            f'/wearable/{self.user.user_id}/',
            {'device_name': 'X'},
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

    @patch('app.views.get_game_clock_state', return_value='live')
    def test_post_creates_event_and_stamps_game_clock_state(self, mock_gcs):
        from app.models import PhoneTelemetry
        response = self.client.post('/telemetry/phone/', self._payload(), format='json')
        self.assertEqual(response.status_code, http_status.HTTP_201_CREATED)
        event = PhoneTelemetry.objects.get(user=self.user)
        self.assertEqual(event.game_clock_state, 'live')

    @patch('app.views.get_game_clock_state', return_value='pre')
    def test_irb_violation_long_string_metadata_returns_400(self, mock_gcs):
        payload = self._payload()
        payload['metadata'] = {'text': 'x' * 51}
        response = self.client.post('/telemetry/phone/', payload, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_400_BAD_REQUEST)

    @patch('app.views.get_game_clock_state', return_value='pre')
    def test_client_supplied_game_clock_state_is_ignored(self, mock_gcs):
        from app.models import PhoneTelemetry
        payload = self._payload()
        payload['game_clock_state'] = 'live'
        response = self.client.post('/telemetry/phone/', payload, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_201_CREATED)
        event = PhoneTelemetry.objects.get(user=self.user)
        self.assertEqual(event.game_clock_state, 'pre')

    def test_post_without_auth_returns_401(self):
        response = APIClient().post('/telemetry/phone/', self._payload(), format='json')
        self.assertEqual(response.status_code, http_status.HTTP_401_UNAUTHORIZED)

    @patch('app.views.get_game_clock_state', return_value='pre')
    def test_invalid_event_type_returns_400(self, mock_gcs):
        payload = self._payload()
        payload['event_type'] = 'not_a_real_event'
        response = self.client.post('/telemetry/phone/', payload, format='json')
        self.assertEqual(response.status_code, http_status.HTTP_400_BAD_REQUEST)
