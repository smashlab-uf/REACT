from rest_framework import serializers
from .models import (
    EMA, EngagementLog, HeartRateSample, JITAILog, PhoneTelemetry,
    SleepSummary, StressSample, User, UserData, WearableDevice,
)
from django.contrib.auth.hashers import make_password

import logging


class UserSerializer(serializers.ModelSerializer):

    password = serializers.CharField(write_only=True, required=False, allow_blank=False, min_length=8)

    class Meta:
        model = User
        fields = [
            'user_id', 'email', 'password', 'first_name', 'last_name',
            'birthdate', 'gender', 'height_feet', 'height_inches',
            'goal_weight', 'goal_to_lose_weight', 'goal_to_feel_better',
            'push_token', 'is_enrolled', 'enrolled_at',
        ]
        read_only_fields = ('user_id',)
        extra_kwargs = {
            'birthdate': {'required': False, 'default': "2000-01-01"},
            'gender': {'required': False, 'default': "Other"},
            'first_name': {'required': False, 'default': ''},
            'last_name': {'required': False, 'default': ''},
            'height_feet': {'required': False, 'default': 0},
            'height_inches': {'required': False, 'default': 0},
            'goal_weight': {'required': False, 'default': 0.0},
            'goal_to_lose_weight': {'required': False, 'default': 0.0},
            'goal_to_feel_better': {'required': False, 'default': 0.0},
        }

    def create(self, validated_data):
        pwd = validated_data.pop('password', None)
        user = User(**validated_data)
        if pwd:
            user.set_password(pwd)
        user.save()
        return user

    def update(self, instance, validated_data):
        pwd = validated_data.pop('password', None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        if pwd:
            instance.set_password(pwd)
        instance.save()
        return instance


class UserDataSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserData
        fields = ['data_id', 'user', 'timestamp', 'goal_type', 'weight_value', 'feel_better_value']
        read_only_fields = ('data_id', 'timestamp')
        extra_kwargs = {
            'goal_type': {'required': False},
            'weight_value': {'required': False, 'default': 0.0},
            'feel_better_value': {'required': False, 'default': 0.0}
        }

    def create(self, validated_data):
        return UserData.objects.create(
            goal_type=validated_data['goal_type'],
            weight_value=validated_data['weight_value'],
            feel_better_value=validated_data['feel_better_value'],
        )

    def update(self, instance, validated_data):
        instance.goal_type = validated_data.get('goal_type', instance.goal_type)
        instance.weight_value = validated_data.get('weight_value', instance.weight_value)
        instance.feel_better_value = validated_data.get('feel_better_value', instance.feel_better_value)
        instance.save()
        return instance


class WearableDeviceSerializer(serializers.ModelSerializer):
    class Meta:
        model = WearableDevice
        fields = ['id', 'user', 'fitabase_participant_id', 'device_name', 'is_active', 'last_synced_at']
        read_only_fields = ('id',)


class HeartRateSampleSerializer(serializers.ModelSerializer):
    class Meta:
        model = HeartRateSample
        fields = ['id', 'user', 'timestamp', 'bpm', 'source']
        read_only_fields = ('id',)


class StressSampleSerializer(serializers.ModelSerializer):
    class Meta:
        model = StressSample
        fields = ['id', 'user', 'timestamp', 'stress_score', 'source']
        read_only_fields = ('id',)



class EMASerializer(serializers.ModelSerializer):
    class Meta:
        model = EMA
        fields = ['id', 'user', 'prompt_id', 'sent_at', 'responded_at', 'status', 'mood', 'stress', 'energy']
        read_only_fields = ('id', 'sent_at')
        extra_kwargs = {
            'mood':   {'min_value': 1, 'max_value': 7},
            'stress': {'min_value': 1, 'max_value': 7},
            'energy': {'min_value': 1, 'max_value': 7},
        }


class JITAILogSerializer(serializers.ModelSerializer):
    class Meta:
        model = JITAILog
        fields = [
            'id', 'user', 'prompt_id', 'triggered_at', 'trigger_reason',
            'hr_at_trigger', 'stress_at_trigger', 'ema', 'observed_mssd',
            'send_prompt', 'status',
        ]
        read_only_fields = ('id', 'triggered_at')


class TelemetryWearableDeviceSerializer(serializers.Serializer):
    fitabase_participant_id = serializers.CharField(max_length=64)
    device_name = serializers.CharField(max_length=100, required=False, allow_null=True)
    last_synced_at = serializers.DateTimeField(required=False, allow_null=True)
    is_active = serializers.BooleanField(required=False, default=True)


class TelemetryHeartRateSampleSerializer(serializers.Serializer):
    timestamp = serializers.DateTimeField()
    bpm = serializers.IntegerField(min_value=1, max_value=300)
    source = serializers.CharField(max_length=32, required=False, default='garmin_fitabase')


class TelemetryStressSampleSerializer(serializers.Serializer):
    timestamp = serializers.DateTimeField()
    stress_score = serializers.IntegerField(min_value=0, max_value=100)
    source = serializers.CharField(max_length=32, required=False, default='garmin_fitabase')


class TelemetryEMASerializer(serializers.Serializer):
    prompt_id = serializers.CharField(max_length=64)
    responded_at = serializers.DateTimeField(required=False, allow_null=True)
    status = serializers.ChoiceField(choices=EMA.STATUS_CHOICES, required=False, default='completed')
    mood = serializers.IntegerField(min_value=1, max_value=7, required=False, allow_null=True)
    energy = serializers.IntegerField(min_value=1, max_value=7, required=False, allow_null=True)
    stress = serializers.IntegerField(min_value=1, max_value=7, required=False, allow_null=True)


class TelemetryJITAILogSerializer(serializers.Serializer):
    prompt_id = serializers.CharField(max_length=64)
    trigger_reason = serializers.CharField(max_length=128)
    hr_at_trigger = serializers.IntegerField(min_value=0, required=False, allow_null=True)
    stress_at_trigger = serializers.IntegerField(min_value=0, max_value=100, required=False, allow_null=True)
    status = serializers.ChoiceField(choices=JITAILog.STATUS_CHOICES, required=False, default='delivered')


class TelemetryIngestSerializer(serializers.Serializer):
    user_id = serializers.IntegerField()
    wearable_device = TelemetryWearableDeviceSerializer(required=False)
    heart_rate_samples = TelemetryHeartRateSampleSerializer(many=True, required=False, default=list)
    stress_samples = TelemetryStressSampleSerializer(many=True, required=False, default=list)
    emas = TelemetryEMASerializer(many=True, required=False, default=list)
    jitai_logs = TelemetryJITAILogSerializer(many=True, required=False, default=list)


class SleepSummarySerializer(serializers.ModelSerializer):
    class Meta:
        model = SleepSummary
        fields = [
            'id', 'user', 'date', 'total_minutes', 'light_minutes',
            'deep_minutes', 'rem_minutes', 'awake_minutes', 'sleep_score', 'source',
        ]
        read_only_fields = ('id', 'source')


class PhoneTelemetrySerializer(serializers.ModelSerializer):
    class Meta:
        model = PhoneTelemetry
        fields = [
            'id', 'user', 'session_id', 'event_type', 'occurred_at',
            'recorded_at', 'game_clock_state', 'screen_name', 'latency_ms', 'metadata',
        ]
        read_only_fields = ('id', 'recorded_at', 'game_clock_state')

    def validate_metadata(self, value):
        if value is None:
            return value
        for v in value.values():
            if isinstance(v, str) and len(v) > 50:
                raise serializers.ValidationError(
                    "metadata string values must not exceed 50 characters"
                )
        return value


class EngagementLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = EngagementLog
        fields = [
            'id', 'user', 'jitai_log', 'event_type', 'occurred_at',
            'recorded_at', 'game_clock_state',
        ]
        read_only_fields = ('id', 'recorded_at', 'game_clock_state')
