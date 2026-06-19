from django.contrib import admin

from .models import (
    ActivitySummary,
    EMA,
    HeartRateSample,
    JITAILog,
    NotificationData,
    User,
    UserData,
    WearableDevice,
)


admin.site.site_header = "HealthyGatorSportsFan Admin"
admin.site.site_title = "HealthyGatorSportsFan"
admin.site.index_title = "Backend Data Management"


class ReadableAdminMixin:
    class Media:
        css = {
            "all": ("app/admin_readability.css",),
        }


@admin.register(User)
class UserAdmin(ReadableAdminMixin, admin.ModelAdmin):
    list_display = (
        "user_id",
        "email",
        "first_name",
        "last_name",
        "gender",
        "goal_weight",
        "goal_to_lose_weight",
        "goal_to_feel_better",
        "has_push_token",
        "has_fitbit_connection",
    )
    list_filter = ("gender", "goal_to_lose_weight", "goal_to_feel_better")
    search_fields = ("email", "first_name", "last_name", "fitbit_user_id")
    ordering = ("email",)
    readonly_fields = (
        "password",
        "fitbit_access_token",
        "fitbit_refresh_token",
        "fitbit_token_expires",
    )
    fieldsets = (
        ("Profile", {
            "fields": ("email", "first_name", "last_name", "birthdate", "gender"),
        }),
        ("Body Metrics", {
            "fields": ("height_feet", "height_inches", "goal_weight"),
        }),
        ("Goals", {
            "fields": ("goal_to_lose_weight", "goal_to_feel_better"),
        }),
        ("Notifications", {
            "fields": ("push_token",),
        }),
        ("Credentials and Fitbit Tokens", {
            "classes": ("collapse",),
            "fields": (
                "password",
                "fitbit_user_id",
                "fitbit_access_token",
                "fitbit_refresh_token",
                "fitbit_token_expires",
            ),
        }),
    )

    @admin.display(boolean=True, description="Push token")
    def has_push_token(self, obj):
        return bool(obj.push_token)

    @admin.display(boolean=True, description="Fitbit")
    def has_fitbit_connection(self, obj):
        return bool(obj.fitbit_user_id or obj.fitbit_access_token)


@admin.register(UserData)
class UserDataAdmin(ReadableAdminMixin, admin.ModelAdmin):
    list_display = ("data_id", "user", "timestamp", "goal_type", "weight_value", "feel_better_value")
    list_filter = ("goal_type", "timestamp")
    search_fields = ("user__email", "user__first_name", "user__last_name")
    date_hierarchy = "timestamp"
    ordering = ("-timestamp",)
    autocomplete_fields = ("user",)


@admin.register(NotificationData)
class NotificationDataAdmin(ReadableAdminMixin, admin.ModelAdmin):
    list_display = (
        "notification_id",
        "user",
        "notification_title",
        "read_status",
        "timestamp",
    )
    list_filter = ("read_status", "timestamp")
    search_fields = (
        "user__email",
        "notification_title",
        "notification_message",
    )
    date_hierarchy = "timestamp"
    ordering = ("-timestamp",)
    autocomplete_fields = ("user",)


@admin.register(WearableDevice)
class WearableDeviceAdmin(ReadableAdminMixin, admin.ModelAdmin):
    list_display = (
        "device_id",
        "user",
        "device_name",
        "device_type",
        "fitbit_device_id",
        "is_active",
        "last_synced_at",
        "created_at",
    )
    list_filter = ("device_type", "is_active", "created_at", "last_synced_at")
    search_fields = ("user__email", "device_name", "device_type", "fitbit_device_id")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    readonly_fields = ("created_at",)
    autocomplete_fields = ("user",)


@admin.register(HeartRateSample)
class HeartRateSampleAdmin(ReadableAdminMixin, admin.ModelAdmin):
    list_display = ("sample_id", "device", "user_email", "timestamp", "bpm", "zone")
    list_filter = ("zone", "timestamp")
    search_fields = ("device__user__email", "device__device_name", "device__fitbit_device_id")
    date_hierarchy = "timestamp"
    ordering = ("-timestamp",)
    autocomplete_fields = ("device",)

    @admin.display(description="User email", ordering="device__user__email")
    def user_email(self, obj):
        return obj.device.user.email


@admin.register(ActivitySummary)
class ActivitySummaryAdmin(ReadableAdminMixin, admin.ModelAdmin):
    list_display = (
        "summary_id",
        "device",
        "user_email",
        "date",
        "steps",
        "active_minutes",
        "calories_burned",
        "distance_km",
    )
    list_filter = ("date",)
    search_fields = ("device__user__email", "device__device_name", "device__fitbit_device_id")
    date_hierarchy = "date"
    ordering = ("-date",)
    autocomplete_fields = ("device",)

    @admin.display(description="User email", ordering="device__user__email")
    def user_email(self, obj):
        return obj.device.user.email


@admin.register(EMA)
class EMAAdmin(ReadableAdminMixin, admin.ModelAdmin):
    list_display = (
        "ema_id",
        "user",
        "timestamp",
        "mood",
        "energy",
        "stress",
        "physical_activity",
        "weight_lbs",
    )
    list_filter = ("physical_activity", "timestamp")
    search_fields = ("user__email", "user__first_name", "user__last_name", "notes")
    date_hierarchy = "timestamp"
    ordering = ("-timestamp",)
    autocomplete_fields = ("user",)
    fieldsets = (
        ("Respondent", {
            "fields": ("user", "timestamp"),
        }),
        ("Response", {
            "fields": ("mood", "energy", "stress", "physical_activity", "weight_lbs", "notes"),
        }),
    )
    readonly_fields = ("timestamp",)


@admin.register(JITAILog)
class JITAILogAdmin(ReadableAdminMixin, admin.ModelAdmin):
    list_display = (
        "log_id",
        "user",
        "timestamp",
        "title",
        "trigger_reason",
        "prompt_status",
        "prompt_count",
    )
    list_filter = ("prompt_status", "trigger_reason", "timestamp")
    search_fields = ("user__email", "title", "message", "trigger_reason")
    date_hierarchy = "timestamp"
    ordering = ("-timestamp",)
    autocomplete_fields = ("user",)
    readonly_fields = ("timestamp",)
    fieldsets = (
        ("Prompt", {
            "fields": ("user", "timestamp", "title", "message"),
        }),
        ("Trigger Context", {
            "fields": ("trigger_reason", "volatility_score", "threshold_used"),
        }),
        ("Engagement", {
            "fields": ("prompt_status", "prompt_count", "opened_at", "interacted_at"),
        }),
    )
