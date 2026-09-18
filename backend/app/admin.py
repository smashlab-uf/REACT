from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.http import HttpResponseNotAllowed, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils import timezone
from django.views.decorators.debug import sensitive_post_parameters

from .forms import ParticipantEnrollmentForm

from .models import (
    CheckinReminder, EMA, EMAItemResponse, EngagementLog, EventDay, HeartRateSample, JITAILog,
    PhoneTelemetry, StressSample, User, WearableDevice,
)


admin.site.site_header = "REACT Admin"
admin.site.site_title = "REACT"
admin.site.index_title = "Backend Data Management"
admin.site.index_template = "admin/react_index.html"


class ReadableAdminMixin:
    class Media:
        css = {
            "all": ("app/admin_readability.css",),
        }


def enroll_user_for_notifications(user):
    now = timezone.now()
    user.is_enrolled = True
    if user.enrolled_at is None:
        user.enrolled_at = now
    user.save(update_fields=["is_enrolled", "enrolled_at"])
    device, created = WearableDevice.objects.get_or_create(
        user=user,
        defaults={
            "labfront_participant_id": f"TEST-{user.user_id}",
            "is_active": True,
        },
    )
    if not device.is_active:
        device.is_active = True
        device.save(update_fields=["is_active"])
    return device, created


@admin.register(User)
class UserAdmin(ReadableAdminMixin, admin.ModelAdmin):
    change_form_template = "admin/app/user/change_form.html"
    change_list_template = "admin/app/user/change_list.html"
    actions = ("enroll_for_notifications",)
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

    def get_urls(self):
        urls = super().get_urls()
        custom = [
            path(
                "create-and-enroll/",
                self.admin_site.admin_view(sensitive_post_parameters('password1', 'password2')(self.onboard_view)),
                name="app_user_create_and_enroll",
            ),
            path(
                "<path:object_id>/enroll-labfront/",
                self.admin_site.admin_view(self.onboard_view),
                name="app_user_enroll_labfront",
            ),
            path(
                "<path:object_id>/enroll-notifications/",
                self.admin_site.admin_view(self.enroll_for_notifications_view),
                name="app_user_enroll_notifications",
            ),
        ]
        return custom + urls

    def onboard_view(self, request, object_id=None):
        if request.method not in ('GET', 'POST'):
            return HttpResponseNotAllowed(['GET', 'POST'])
        participant = None
        if object_id is not None:
            participant = self.get_object(request, object_id)
            if participant is None:
                return self._get_obj_does_not_exist_redirect(request, self.model._meta, object_id)
        allowed = (self.has_add_permission(request) if participant is None
                   else self.has_change_permission(request, participant))
        if not allowed or not request.user.has_perms(('app.add_wearabledevice', 'app.change_wearabledevice')):
            raise PermissionDenied
        form = ParticipantEnrollmentForm(
            request.POST if request.method == 'POST' else None, participant=participant,
        )
        if request.method == 'POST' and form.is_valid():
            try:
                with transaction.atomic():
                    user, device, created = form.save()
                    if participant is None:
                        self.log_addition(request, user, 'Created account and enrolled with Labfront.')
                    else:
                        self.log_change(request, user, 'Enrolled with Labfront.')
            except IntegrityError:
                form.add_error(None, 'The email or Labfront ID is already in use. Check the existing account and try again.')
            except ValidationError as error:
                form.add_error(None, error)
            else:
                self._enroll_message(request, user, device, created)
                if participant is not None:
                    return HttpResponseRedirect(reverse('admin:app_user_change', args=[user.pk]))
                return TemplateResponse(request, 'admin/app/user/onboard_complete.html', {
                    **self.admin_site.each_context(request),
                    'opts': self.model._meta,
                    'title': 'Account created and enrolled',
                    'credentials': form.generated_credentials,
                    'user_url': reverse('admin:app_user_change', args=[user.pk]),
                })
        context = {
            **self.admin_site.each_context(request),
            'opts': self.model._meta,
            'title': 'Create account and enroll' if participant is None else 'Enroll with Labfront',
            'form': form,
            'participant': participant,
            'media': self.media + form.media,
        }
        return TemplateResponse(request, 'admin/app/user/onboard.html', context)

    def _enroll_message(self, request, user, device, created):
        wearable_note = (
            f"created wearable {device.labfront_participant_id}"
            if created
            else f"wearable {device.labfront_participant_id} is active"
        )
        token_note = (
            "push token is present"
            if user.push_token
            else "push token is missing — they must open the app and allow notifications"
        )
        self.message_user(
            request,
            f"Enrolled {user.email}: {wearable_note}; {token_note}.",
            messages.WARNING if not user.push_token else messages.SUCCESS,
        )

    @admin.action(description="Enroll for notifications")
    def enroll_for_notifications(self, request, queryset):
        for user in queryset:
            device, created = enroll_user_for_notifications(user)
            self._enroll_message(request, user, device, created)

    def enroll_for_notifications_view(self, request, object_id):
        if request.method != "POST":
            return HttpResponseNotAllowed(["POST"])
        user = self.get_object(request, object_id)
        if user is None:
            return self._get_obj_does_not_exist_redirect(request, self.model._meta, object_id)
        device, created = enroll_user_for_notifications(user)
        self._enroll_message(request, user, device, created)
        return HttpResponseRedirect(reverse("admin:app_user_change", args=[object_id]))


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
    fields = ('sub_item_id', 'response_type', 'value_numeric', 'value_choice', 'value_choices')


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


@admin.register(EventDay)
class EventDayAdmin(ReadableAdminMixin, admin.ModelAdmin):
    list_display = ("date", "sport", "description")
    list_filter = ("sport",)
    search_fields = ("sport", "description")
    ordering = ("-date",)


@admin.register(CheckinReminder)
class CheckinReminderAdmin(ReadableAdminMixin, admin.ModelAdmin):
    list_display = ("user", "sent_at", "daily_count_at_send")
    list_filter = ("sent_at",)
    search_fields = ("user__email",)
    ordering = ("-sent_at",)
    autocomplete_fields = ("user",)


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
    readonly_fields = ("triggered_at", "decision_made_at", "push_sent_at", "device_received_at", "receipt_reported_at", "receipt_event_id")
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
                "receipt_event_id", "receipt_platform", "receipt_app_state", "delivery_error",
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
