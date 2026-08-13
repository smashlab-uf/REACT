from django.contrib import admin

from .models import (
    EMA, EMAItemResponse, EngagementLog, HeartRateSample, JITAILog,
    PhoneTelemetry, StressSample, User, WearableDevice,
)


admin.site.site_header = "REACT Admin"
admin.site.site_title = "REACT"
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
        "is_enrolled",
        "has_push_token",
    )
    list_filter = ("gender", "is_enrolled")
    search_fields = ("email", "first_name", "last_name")
    ordering = ("email",)
    readonly_fields = ("password", "enrolled_at")
    fieldsets = (
        ("Profile", {
            "fields": ("email", "first_name", "last_name", "birthdate", "gender"),
        }),
        ("Enrollment", {
            "fields": ("is_enrolled", "enrolled_at"),
        }),
        ("Notifications", {
            "fields": ("push_token",),
        }),
        ("Credentials", {
            "classes": ("collapse",),
            "fields": ("password",),
        }),
    )

    @admin.display(boolean=True, description="Push token")
    def has_push_token(self, obj):
        return bool(obj.push_token)


@admin.register(WearableDevice)
class WearableDeviceAdmin(ReadableAdminMixin, admin.ModelAdmin):
    list_display = (
        "user",
        "labfront_participant_id",
        "is_active",
        "last_synced_at",
    )
    list_filter = ("is_active", "last_synced_at")
    search_fields = ("user__email", "labfront_participant_id")
    ordering = ("user__email",)
    autocomplete_fields = ("user",)


@admin.register(HeartRateSample)
class HeartRateSampleAdmin(ReadableAdminMixin, admin.ModelAdmin):
    list_display = ("id", "user", "timestamp", "bpm", "source")
    list_filter = ("source", "timestamp")
    search_fields = ("user__email",)
    date_hierarchy = "timestamp"
    ordering = ("-timestamp",)
    autocomplete_fields = ("user",)


@admin.register(StressSample)
class StressSampleAdmin(ReadableAdminMixin, admin.ModelAdmin):
    list_display = ("id", "user", "timestamp", "stress_score", "source")
    list_filter = ("source", "timestamp")
    search_fields = ("user__email",)
    date_hierarchy = "timestamp"
    ordering = ("-timestamp",)
    autocomplete_fields = ("user",)


class EMAItemResponseInline(admin.TabularInline):
    model = EMAItemResponse
    extra = 0


@admin.register(EMA)
class EMAAdmin(ReadableAdminMixin, admin.ModelAdmin):
    inlines = (EMAItemResponseInline,)
    list_display = (
        "id",
        "user",
        "prompt_id",
        "sent_at",
        "responded_at",
        "status",
        "ema_type",
        "mood",
        "energy",
        "stress",
    )
    list_filter = ("status", "ema_type", "sent_at")
    search_fields = ("user__email", "user__first_name", "user__last_name", "prompt_id")
    date_hierarchy = "sent_at"
    ordering = ("-sent_at",)
    autocomplete_fields = ("user",)
    readonly_fields = ("sent_at",)
    fieldsets = (
        ("Respondent", {
            "fields": ("user", "prompt_id", "sent_at", "responded_at", "status", "ema_type"),
        }),
        ("Outcome Window", {
            "fields": ("source_jitai_log", "outcome_window_start", "outcome_window_end", "expires_at"),
        }),
        ("Response", {
            "fields": ("mood", "energy", "stress"),
        }),
    )


@admin.register(JITAILog)
class JITAILogAdmin(ReadableAdminMixin, admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "prompt_id",
        "triggered_at",
        "trigger_reason",
        "hr_at_trigger",
        "stress_at_trigger",
        "status",
        "delivery_status",
        "decision_made_at",
        "push_sent_at",
        "device_received_at",
    )
    list_filter = ("status", "delivery_status", "trigger_reason", "triggered_at")
    search_fields = ("user__email", "prompt_id", "trigger_reason")
    date_hierarchy = "triggered_at"
    ordering = ("-triggered_at",)
    autocomplete_fields = ("user",)
    readonly_fields = ("triggered_at", "decision_made_at", "push_sent_at", "device_received_at", "receipt_reported_at")
    fieldsets = (
        ("Prompt", {
            "fields": ("user", "prompt_id", "triggered_at", "status"),
        }),
        ("Trigger Context", {
            "fields": ("trigger_reason", "hr_at_trigger", "stress_at_trigger"),
        }),
        ("Delivery Latency", {
            "fields": (
                "delivery_status", "decision_made_at", "push_sent_at",
                "device_received_at", "receipt_reported_at",
                "receipt_platform", "receipt_app_state", "delivery_error",
            ),
        }),
    )



@admin.register(PhoneTelemetry)
class PhoneTelemetryAdmin(ReadableAdminMixin, admin.ModelAdmin):
    list_display = ("id", "user", "session_id", "event_type", "occurred_at", "screen_name")
    list_filter = ("event_type", "occurred_at")
    search_fields = ("user__email", "session_id")
    date_hierarchy = "occurred_at"
    ordering = ("-occurred_at",)
    autocomplete_fields = ("user",)
    readonly_fields = ("recorded_at",)


@admin.register(EngagementLog)
class EngagementLogAdmin(ReadableAdminMixin, admin.ModelAdmin):
    list_display = ("id", "user", "event_type", "occurred_at", "jitai_log")
    list_filter = ("event_type", "occurred_at")
    search_fields = ("user__email",)
    date_hierarchy = "occurred_at"
    ordering = ("-occurred_at",)
    autocomplete_fields = ("user",)
    readonly_fields = ("recorded_at",)
