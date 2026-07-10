"""
URL configuration for project project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path
from app.views import index, CreateUserView, UserLoginView, UserUpdateView, CheckEmailView, TelemetryIngestView, WearableDeviceView, EMAView, JITAILogView, HeartRateListView, StressListView, PhoneTelemetryView, EngagementLogView

# Import drf-yasg components
from drf_yasg.views import get_schema_view
from drf_yasg import openapi
from rest_framework import permissions

schema_view = get_schema_view(
    openapi.Info(title="REACT API", default_version="v1",),
    public=True,
    # This can be adjusted later if needed ...
    permission_classes=(permissions.AllowAny,), 
)

# Used to define API endpoints that our mobile app will interact with, rather than returning HTML pages for a web app

# Best practice is one route per page, but multipe routes can be implemented as the app gets more complex

urlpatterns = [
    # API endpoints for testing
    path('admin/', admin.site.urls), # Django Admin page (http://127.0.0.1:8000/admin)
    path('', index, name = "index"), # to see database contents for testing (http://127.0.0.1:8000/), see templates -> index.html
    
    # API endpoints for app
    path('user/', CreateUserView.as_view(), name='user-create'), # endpoint for user creation screen
    path('user/<int:user_id>/', UserUpdateView.as_view(), name='update-user'),
    path('user/login/', UserLoginView.as_view(), name='user-login'),
    path('user/checkemail/', CheckEmailView.as_view(), name='check-user-email'),
    path('wearable/', WearableDeviceView.as_view(), name='wearable-create'),
    path('wearable/<int:user_id>/', WearableDeviceView.as_view(), name='wearable-detail'),
    path('ema/', EMAView.as_view(), name='ema-create'),
    path('ema/<int:user_id>/', EMAView.as_view(), name='ema-list'),
    path('jitai/', JITAILogView.as_view(), name='jitai-create'),
    path('jitai/<int:user_id>/', JITAILogView.as_view(), name='jitai-list'),
    path('telemetry/ingest/', TelemetryIngestView.as_view(), name='telemetry-ingest'),
    path('telemetry/hr/<int:user_id>/', HeartRateListView.as_view(), name='telemetry-hr'),
    path('telemetry/stress/<int:user_id>/', StressListView.as_view(), name='telemetry-stress'),
    path('telemetry/phone/', PhoneTelemetryView.as_view(), name='telemetry-phone'),
    path('telemetry/engagement/', EngagementLogView.as_view(), name='telemetry-engagement'),

    # API endpoint for Swagger
    path('swagger/', schema_view.with_ui('swagger', cache_timeout=0), name='schema-swagger-ui'),
]
