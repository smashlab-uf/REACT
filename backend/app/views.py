from django.contrib.auth.models import User as AuthUser
from rest_framework_simplejwt.tokens import RefreshToken
from django.shortcuts import render
from .models import (
    EMA,
    EngagementLog,
    HeartRateSample,
    JITAILog,
    PhoneTelemetry,
    StressSample,
    User,
    WearableDevice,
)
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, generics
from .serializers import (
    EMASerializer,
    EngagementLogSerializer,
    HeartRateSampleSerializer,
    JITAILogSerializer,
    PhoneTelemetrySerializer,
    StressSampleSerializer,
    TelemetryIngestSerializer,
    UserSerializer,
    WearableDeviceSerializer,
)
from django.utils import timezone as django_timezone
from .utils import get_game_clock_state
from django.conf import settings
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi


import logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
from rest_framework.permissions import AllowAny, IsAuthenticated


def _get_app_user(request):
    email = getattr(request.user, 'email', None)
    if not email:
        return None
    try:
        return User.objects.get(email=email)
    except User.DoesNotExist:
        return None


# Create your views here.

# Best practice is one view per page

# 'request' is the entire HTTP object (headers, request method like GET, POST, others), etc...)
# 'request.data' is used to access parsed data like the JSON or form data
# 'request.body' is used to access raw data that is not parsed
# 'self' refers to the current instance

def index(request):
    return render(request, "index.html")

# API view to handle POST requests for user creation
class CreateUserView(APIView):
    permission_classes = [AllowAny]
    @swagger_auto_schema(
            operation_summary="Add user",
            operation_description="Create a new user to add to the database.",
            request_body=UserSerializer
        )
    def post(self, request):
        serializer = UserSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
class UserUpdateView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(operation_summary="Update user", operation_description="Update an existing user in the database", request_body=UserSerializer)
    def put(self, request, user_id):
        if not request.user.is_staff:
            app_user = _get_app_user(request)
            if app_user is None or app_user.user_id != user_id:
                return Response(status=status.HTTP_403_FORBIDDEN)
        try:
            user = User.objects.get(user_id=user_id)
        except User.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)
        serializer = UserSerializer(user, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class CheckEmailView(APIView):
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        operation_summary="Check if email is already used", operation_description="Checks all users in the database to determine whether an email is already in user.",
        responses={200: UserSerializer(many=False)}  # Define response schema
    )
    def post(self, request):
        email = request.data.get('email')
        if User.objects.filter(email=email).exists():
            return Response({'exists': True}, status=status.HTTP_200_OK)
        return Response({'exists': False}, status=status.HTTP_200_OK)

# # API view to handle POST requests for data sent from the front-end (basicinfo.tsx)
# class BasicInfoView(APIView):
#     def post(self, request, user_id):
#         # Retrieve the user by ID
#         user = User.objects.get(pk=user_id) # pk is primary key
#         # Separate weight_value for UserData
#         weight_value = request.data.pop('weight_value', None) # return 'None' if no weight available
#         # Update user data with new information
#         user_serializer = UserSerializer(user, data=request.data, partial=True)
#         if user_serializer.is_valid():
#             user_serializer.save()
#             # Handle UserData creation if weight is provided
#             if weight_value is not None:
#                 user_data = UserData.objects.create(user=user)
#                 user_data_serializer = UserDataSerializer(user_data, data={'weight_value': weight_value}, partial=True)
#                 if user_data_serializer.is_valid():
#                     user_data_serializer.save()
#                 else:
#                     print("UserData errors:", user_data_serializer.errors)
#                     return Response(user_data_serializer.errors, status=status.HTTP_400_BAD_REQUEST)
#             # Return the user data with success status
#             return Response(user_serializer.data, status=status.HTTP_200_OK)
#         # Log and return errors if the user data is invalid
#         print("User errors:", user_serializer.errors)
#         return Response(user_serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
# # API view to handle POST requests for data sent from the front-end (goalcollection.tsx)
# class GoalCollectionView(APIView):
#     def post(self, request, user_id):
#         user = User.objects.get(pk=user_id)
#         user_serializer = UserSerializer(user, data=request.data, partial=True)
#         if user_serializer.is_valid():
#             user_serializer.save()
#             user_data = UserData.objects.create(user=user)
#             user_data_serializer = UserDataSerializer(user_data, data=request.data, partial=True)
#             if user_data_serializer.is_valid():
#                 user_data_serializer.save()
#                 return Response({
#                     'user': user_serializer.data,
#                     'user_data': user_data_serializer.data
#                 }, status=status.HTTP_200_OK)
#             return Response(user_data_serializer.errors, status=status.HTTP_400_BAD_REQUEST)
#         return Response(user_serializer.errors, status=status.HTTP_400_BAD_REQUEST)
class UserLoginView(APIView):
    permission_classes = (AllowAny,)
    @swagger_auto_schema(
        operation_summary="User login (POST)", operation_description="Authenticate and get user's information given email and password.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['email', 'password'],
            properties={
                'email': openapi.Schema(type=openapi.TYPE_STRING),
                'password': openapi.Schema(type=openapi.TYPE_STRING),
            },
        ),
        # manual_parameters=[
        #         openapi.Parameter(
        #             'email',  # Name of the parameter
        #             openapi.IN_QUERY,  # Location of the parameter
        #             description="Login email entered by user",
        #             type=openapi.TYPE_STRING,  # Type of the parameter
        #             required=True  # Whether the parameter is required
        #         ),
        #         openapi.Parameter(
        #             'password',  # Name of the parameter
        #             openapi.IN_QUERY,  # Location of the parameter
        #             description="Login password entered by user",
        #             type=openapi.TYPE_STRING,  # Type of the parameter
        #             required=True  # Whether the parameter is required
        #         )
        # ],
        responses={200: UserSerializer(many=False)}  # Define response schema
    )
    def post(self, request):
        email = request.data.get('email')
        password = request.data.get('password')

        if not email or not password:
            return Response({"error": "Email and password are required."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            return Response({"error": "Invalid credentials"}, status=status.HTTP_401_UNAUTHORIZED)

        if not user.check_password(password):
            return Response({"error": "Invalid credentials"}, status=status.HTTP_401_UNAUTHORIZED)

        auth_user, _ = AuthUser.objects.get_or_create(
            username=user.email,
            defaults={"email": user.email}
        )

        refresh = RefreshToken.for_user(auth_user)
        access = refresh.access_token
        refresh["app_user_id"] = user.user_id
        access["app_user_id"] = user.user_id
        
        serializer = UserSerializer(user)
        return Response({
            "access": str(access),
            "refresh": str(refresh),
            "data": serializer.data
        }, status=status.HTTP_200_OK)
    
    # def get(self, request):
    #     email = request.query_params.get('email')
    #     password = request.query_params.get('password')
    #     users = User.objects.all()  # Fetch all users from the database
    #     print("Email & password from query parameters: ", email, " & ", password)
    #     print("Count of users: ", User.objects.count())
    #     users = User.objects.all()
    #     print("Users found: ", {users})
    #     try:
    #         # Fetch the user by email
    #         user = User.objects.get(email=email)
    #         # Check if the provided password matches
    #         print("User's password from DB: ", user.password)
    #         if user.check_password(password):
    #             # If the password is correct, serialize and return user data
    #             serializer = UserSerializer(user)
    #             return Response(serializer.data, status=status.HTTP_200_OK)
    #         else:
    #             return Response({"error": "Invalid credentials"}, status=status.HTTP_401_UNAUTHORIZED)
    #     except User.DoesNotExist:
    #         return Response({"error": "User not found"}, status=status.HTTP_404_NOT_FOUND)

# # Shannon, 11/19/2024: Below is an attempt I made at a more advanced auth method using django's built-in auth. I opted for simplicity for now.
# class UserLoginView(APIView):
#     def get(self, request):
#         email = request.query_params.get('email')
#         password = request.query_params.get('password')
#         # Check if a user with the provided username exists
#         if not User.objects.filter(email=email).exists():
#             # Display an error message if the username does not exist
#             messages.error(request, 'Invalid email')
#             return Response({"error": "Invalid email"}, status=status.HTTP_404_NOT_FOUND)
#         user = authenticate(username=email, password=password)
#         if user is not None:
#             login(request, user) #login() function takes an HttpRequest object and a User object, and saves the user's ID in the session.
#             serializer = UserSerializer(user)
#             return Response(serializer.data, status=status.HTTP_200_OK)
#         else:
#             return Response({"error": "Invalid credentials"}, status=status.HTTP_401_UNAUTHORIZED)
#     def logout_view(request):
#         logout(request)
    

class TelemetryIngestView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary="Ingest telemetry",
        operation_description=(
            "Store telemetry from Fitabase polling into the wearable, heart rate, "
            "stress, EMA, and JITAI tables."
        ),
        request_body=TelemetryIngestSerializer,
    )
    def post(self, request):
        serializer = TelemetryIngestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        try:
            user = User.objects.get(user_id=data["user_id"])
        except User.DoesNotExist:
            return Response({"error": "User not found."}, status=status.HTTP_404_NOT_FOUND)

        device_payload = data.get("wearable_device") or {}
        if device_payload:
            WearableDevice.objects.update_or_create(
                user=user,
                defaults={
                    "labfront_participant_id": device_payload["labfront_participant_id"],
                    "last_synced_at": device_payload.get("last_synced_at"),
                    "is_active": device_payload.get("is_active", True),
                },
            )

        created_counts = {
            "heart_rate_samples": 0,
            "stress_samples": 0,
            "emas": 0,
            "jitai_logs": 0,
            "phone_events": 0,
            "engagement_events": 0,
        }

        for sample in data.get("heart_rate_samples", []):
            HeartRateSample.objects.create(user=user, **sample)
            created_counts["heart_rate_samples"] += 1

        for sample in data.get("stress_samples", []):
            StressSample.objects.create(user=user, **sample)
            created_counts["stress_samples"] += 1

        for ema in data.get("emas", []):
            EMA.objects.create(user=user, **ema)
            created_counts["emas"] += 1

        for log in data.get("jitai_logs", []):
            ema_id = log.pop("ema", None)
            if ema_id is not None:
                try:
                    log["ema"] = EMA.objects.get(id=ema_id, user=user)
                except EMA.DoesNotExist:
                    return Response(
                        {"error": f"EMA {ema_id} not found for user."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
            JITAILog.objects.create(user=user, **log)
            created_counts["jitai_logs"] += 1

        for event in data.get("phone_events", []):
            PhoneTelemetry.objects.create(user=user, **event)
            created_counts["phone_events"] += 1

        for event in data.get("engagement_events", []):
            jitai_log_id = event.pop("jitai_log", None)
            if jitai_log_id is not None:
                try:
                    event["jitai_log"] = JITAILog.objects.get(id=jitai_log_id, user=user)
                except JITAILog.DoesNotExist:
                    return Response(
                        {"error": f"JITAI log {jitai_log_id} not found for user."},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
            EngagementLog.objects.create(user=user, **event)
            created_counts["engagement_events"] += 1

        return Response(
            {
                "message": "Telemetry ingested.",
                "user_id": user.user_id,
                "counts": created_counts,
            },
            status=status.HTTP_201_CREATED,
        )


class WearableDeviceView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = WearableDeviceSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def get(self, request, user_id):
        if not request.user.is_staff:
            app_user = _get_app_user(request)
            if app_user is None or app_user.user_id != user_id:
                return Response(status=status.HTTP_403_FORBIDDEN)
        try:
            device = WearableDevice.objects.get(user__user_id=user_id)
        except WearableDevice.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)
        return Response(WearableDeviceSerializer(device).data)

    def patch(self, request, user_id):
        if not request.user.is_staff:
            app_user = _get_app_user(request)
            if app_user is None or app_user.user_id != user_id:
                return Response(status=status.HTTP_403_FORBIDDEN)
        try:
            device = WearableDevice.objects.get(user__user_id=user_id)
        except WearableDevice.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)
        serializer = WearableDeviceSerializer(device, data=request.data, partial=True)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        serializer.save()
        return Response(serializer.data)


# class SendNotificationView(APIView):
#     def post(self, request):
#         serializer = NotificationDataSerializer(data=request.data)
#         if serializer.is_valid():
#             serializer.save()
#             return Response(serializer.data, status=status.HTTP_201_CREATED)
#         return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
#     def send_notification(self, data):
#         expo_push_url = "https://exp.host/--/api/v2/push/send"
#         message = {
#             "to": data['user'].google_acct_id,
#             "title": "Score Update",
#             "body": data["Testing to see if this push notification works!"],
class EMAView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        app_user = _get_app_user(request)
        if app_user is None:
            return Response({"error": "User not found."}, status=status.HTTP_403_FORBIDDEN)
        serializer = EMASerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        ema = serializer.save(user=app_user)
        if ema.mood is not None and ema.stress is not None and ema.energy is not None:
            ema.status = 'completed'
            ema.responded_at = django_timezone.now()
            ema.save(update_fields=['status', 'responded_at'])
        return Response(EMASerializer(ema).data, status=status.HTTP_201_CREATED)

    def get(self, request, user_id):
        if not request.user.is_staff:
            app_user = _get_app_user(request)
            if app_user is None or app_user.user_id != user_id:
                return Response(status=status.HTTP_403_FORBIDDEN)
        emas = EMA.objects.filter(user__user_id=user_id).order_by('-sent_at')
        return Response(EMASerializer(emas, many=True).data)


class JITAILogView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        serializer = JITAILogSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        log = serializer.save()
        return Response(JITAILogSerializer(log).data, status=status.HTTP_201_CREATED)

    def get(self, request, user_id):
        if not request.user.is_staff:
            app_user = _get_app_user(request)
            if app_user is None or app_user.user_id != user_id:
                return Response(status=status.HTTP_403_FORBIDDEN)
        logs = JITAILog.objects.filter(user__user_id=user_id).order_by('-triggered_at')
        return Response(JITAILogSerializer(logs, many=True).data)


class HeartRateListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, user_id):
        if not request.user.is_staff:
            app_user = _get_app_user(request)
            if app_user is None or app_user.user_id != user_id:
                return Response(status=status.HTTP_403_FORBIDDEN)
        try:
            limit = max(1, min(int(request.query_params.get('limit', 100)), 1000))
        except (ValueError, TypeError):
            return Response({'error': 'limit must be a positive integer'}, status=status.HTTP_400_BAD_REQUEST)
        samples = HeartRateSample.objects.filter(
            user__user_id=user_id
        ).order_by('-timestamp')[:limit]
        return Response(HeartRateSampleSerializer(samples, many=True).data)


class StressListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, user_id):
        if not request.user.is_staff:
            app_user = _get_app_user(request)
            if app_user is None or app_user.user_id != user_id:
                return Response(status=status.HTTP_403_FORBIDDEN)
        try:
            limit = max(1, min(int(request.query_params.get('limit', 100)), 1000))
        except (ValueError, TypeError):
            return Response({'error': 'limit must be a positive integer'}, status=status.HTTP_400_BAD_REQUEST)
        samples = StressSample.objects.filter(
            user__user_id=user_id
        ).order_by('-timestamp')[:limit]
        return Response(StressSampleSerializer(samples, many=True).data)


class PhoneTelemetryView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        app_user = _get_app_user(request)
        if app_user is None:
            return Response({"error": "User not found."}, status=status.HTTP_403_FORBIDDEN)
        serializer = PhoneTelemetrySerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        event = serializer.save(user=app_user, game_clock_state=get_game_clock_state())
        return Response(PhoneTelemetrySerializer(event).data, status=status.HTTP_201_CREATED)


class EngagementLogView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        app_user = _get_app_user(request)
        if app_user is None:
            return Response({"error": "User not found."}, status=status.HTTP_403_FORBIDDEN)
        serializer = EngagementLogSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        log = serializer.save(user=app_user, game_clock_state=get_game_clock_state())
        return Response(EngagementLogSerializer(log).data, status=status.HTTP_201_CREATED)
