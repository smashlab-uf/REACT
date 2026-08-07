from datetime import timedelta

from django.contrib.auth.models import User as AuthUser
from rest_framework_simplejwt.tokens import RefreshToken
from django.shortcuts import render
from django.db.models import OuterRef, Subquery
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
    JITAIReceiptSerializer,
    JITAILogSerializer,
    PhoneTelemetrySerializer,
    StressSampleSerializer,
    TelemetryIngestSerializer,
    UserSerializer,
    WearableDeviceSerializer,
)
from django.utils import timezone as django_timezone
from django.conf import settings
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi


import logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)
from rest_framework.permissions import AllowAny, BasePermission, IsAuthenticated


def _get_app_user(request):
    email = getattr(request.user, 'email', None)
    if not email:
        return None
    try:
        return User.objects.get(email=email)
    except User.DoesNotExist:
        return None


class IsAdminUserOrDashboardAPIKey(BasePermission):
    def has_permission(self, request, view):
        if request.user and request.user.is_staff:
            return True

        expected_key = getattr(settings, 'DASHBOARD_API_KEY', '')
        provided_key = request.headers.get('X-Dashboard-API-Key', '')
        return bool(expected_key) and provided_key == expected_key


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


class MeView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary="Get current user's profile",
        operation_description="Returns the authenticated participant's profile, resolved from the JWT.",
        responses={200: UserSerializer(many=False)}
    )
    def get(self, request):
        app_user = _get_app_user(request)
        if app_user is None:
            return Response(status=status.HTTP_404_NOT_FOUND)
        serializer = UserSerializer(app_user)
        return Response(serializer.data)


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


class JITAIReceiptView(APIView):
    permission_classes = [IsAuthenticated]

    @swagger_auto_schema(
        operation_summary="Record JITAI notification receipt",
        operation_description=(
            "Mobile calls this when the device receives a JITAI push. "
            "The endpoint records device_received_at and server receipt time."
        ),
        request_body=JITAIReceiptSerializer,
    )
    def post(self, request):
        app_user = _get_app_user(request)
        if app_user is None:
            return Response({"error": "User not found."}, status=status.HTTP_403_FORBIDDEN)

        serializer = JITAIReceiptSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

        data = serializer.validated_data
        try:
            jitai_log = JITAILog.objects.get(id=data["jitai_log_id"], user=app_user)
        except JITAILog.DoesNotExist:
            return Response({"error": "JITAI log not found."}, status=status.HTTP_404_NOT_FOUND)

        now = django_timezone.now()
        jitai_log.device_received_at = data["device_received_at"]
        jitai_log.receipt_reported_at = now
        jitai_log.receipt_platform = data.get("platform", "")
        jitai_log.receipt_app_state = data.get("app_state", "")
        jitai_log.delivery_status = "received_on_device"
        jitai_log.delivery_error = ""
        jitai_log.save(update_fields=[
            "device_received_at",
            "receipt_reported_at",
            "receipt_platform",
            "receipt_app_state",
            "delivery_status",
            "delivery_error",
        ])

        delivery_latency_ms = None
        total_latency_ms = None
        server_observed_latency_ms = None
        if jitai_log.push_sent_at:
            delivery_latency_ms = int(
                (jitai_log.device_received_at - jitai_log.push_sent_at).total_seconds() * 1000
            )
            server_observed_latency_ms = int(
                (jitai_log.receipt_reported_at - jitai_log.push_sent_at).total_seconds() * 1000
            )
        if jitai_log.decision_made_at:
            total_latency_ms = int(
                (jitai_log.device_received_at - jitai_log.decision_made_at).total_seconds() * 1000
            )

        return Response({
            "message": "Receipt recorded.",
            "jitai_log_id": jitai_log.id,
            "delivery_status": jitai_log.delivery_status,
            "device_received_at": jitai_log.device_received_at,
            "receipt_reported_at": jitai_log.receipt_reported_at,
            "delivery_latency_ms": delivery_latency_ms,
            "server_observed_latency_ms": server_observed_latency_ms,
            "total_latency_ms": total_latency_ms,
        }, status=status.HTTP_200_OK)


class DashboardParticipantStatusView(APIView):
    permission_classes = [IsAdminUserOrDashboardAPIKey]

    def get(self, request):
        try:
            stale_after_hours = max(1, int(request.query_params.get('stale_after_hours', 24)))
        except (TypeError, ValueError):
            return Response(
                {'error': 'stale_after_hours must be a positive integer'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        stale_cutoff = django_timezone.now() - timedelta(hours=stale_after_hours)

        latest_participant_id = (
            WearableDevice.objects
            .filter(user=OuterRef('pk'))
            .order_by('-last_synced_at', '-id')
            .values('labfront_participant_id')[:1]
        )
        latest_device_sync = (
            WearableDevice.objects
            .filter(user=OuterRef('pk'))
            .order_by('-last_synced_at', '-id')
            .values('last_synced_at')[:1]
        )
        latest_push = (
            JITAILog.objects
            .filter(user=OuterRef('pk'), push_sent_at__isnull=False)
            .order_by('-push_sent_at')
            .values('push_sent_at')[:1]
        )
        latest_receipt = (
            JITAILog.objects
            .filter(user=OuterRef('pk'), device_received_at__isnull=False)
            .order_by('-device_received_at')
            .values('device_received_at')[:1]
        )

        users = (
            User.objects
            .annotate(
                labfront_participant_id=Subquery(latest_participant_id),
                last_sync_timestamp=Subquery(latest_device_sync),
                last_push_timestamp=Subquery(latest_push),
                last_receipt_timestamp=Subquery(latest_receipt),
            )
            .order_by('user_id')
        )

        data = []
        for user in users:
            last_sync = user.last_sync_timestamp
            is_stale = last_sync is None or last_sync < stale_cutoff
            data.append({
                'participant_id': user.labfront_participant_id or str(user.user_id),
                'user_id': user.user_id,
                'email': user.email,
                'last_sync_timestamp': last_sync,
                'last_push_timestamp': user.last_push_timestamp,
                'last_receipt_timestamp': user.last_receipt_timestamp,
                'current_status': 'stale' if is_stale else 'active',
                'is_stale': is_stale,
                'stale_after_hours': stale_after_hours,
            })

        return Response(data)


class DashboardLatencyEventsView(APIView):
    permission_classes = [IsAdminUserOrDashboardAPIKey]

    def get(self, request):
        try:
            limit = max(1, min(int(request.query_params.get('limit', 200)), 1000))
        except (TypeError, ValueError):
            return Response(
                {'error': 'limit must be a positive integer'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        logs = (
            JITAILog.objects
            .select_related('user', 'user__wearabledevice')
            .prefetch_related('engagementlog_set')
            .order_by('-decision_made_at', '-push_sent_at', '-id')[:limit]
        )

        data = []
        for log in logs:
            delivery_latency_ms = None
            if log.push_sent_at and log.device_received_at:
                delivery_latency_ms = int(
                    (log.device_received_at - log.push_sent_at).total_seconds() * 1000
                )

            total_latency_ms = None
            if log.decision_made_at and log.device_received_at:
                total_latency_ms = int(
                    (log.device_received_at - log.decision_made_at).total_seconds() * 1000
                )

            server_observed_latency_ms = None
            if log.push_sent_at and log.receipt_reported_at:
                server_observed_latency_ms = int(
                    (log.receipt_reported_at - log.push_sent_at).total_seconds() * 1000
                )

            engagement_events = [
                {
                    'event_id': event.id,
                    'event_type': event.event_type,
                    'occurred_at': event.occurred_at,
                    'recorded_at': event.recorded_at,
                }
                for event in log.engagementlog_set.all().order_by('occurred_at')
            ]

            device = getattr(log.user, 'wearabledevice', None)
            participant_id = (
                device.labfront_participant_id
                if device is not None
                else str(log.user.user_id)
            )

            data.append({
                'event_id': log.id,
                'message_id': log.prompt_id,
                'participant_id': participant_id,
                'user_id': log.user.user_id,
                'decision_point_id': log.decision_point_id,
                'decision_made_at': log.decision_made_at,
                'push_sent_timestamp': log.push_sent_at,
                'receipt_timestamp': log.device_received_at,
                'receipt_reported_at': log.receipt_reported_at,
                'delivery_status': log.delivery_status,
                'delivery_error': log.delivery_error,
                'receipt_platform': log.receipt_platform,
                'receipt_app_state': log.receipt_app_state,
                'delivery_latency_ms': delivery_latency_ms,
                'server_observed_latency_ms': server_observed_latency_ms,
                'total_latency_ms': total_latency_ms,
                'engagement_events': engagement_events,
            })

        return Response(data)


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
        event = serializer.save(user=app_user)
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
        log = serializer.save(user=app_user)
        return Response(EngagementLogSerializer(log).data, status=status.HTTP_201_CREATED)
