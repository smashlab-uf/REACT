import secrets
import uuid

from django import forms
from django.core.exceptions import ValidationError
from django.db import transaction

from .models import User, WearableDevice


class ParticipantEnrollmentForm(forms.Form):
    email = forms.EmailField(label='Account ID', max_length=254, disabled=True, required=False)
    first_name = forms.CharField(max_length=100, required=False)
    last_name = forms.CharField(max_length=100, required=False)
    gender = forms.ChoiceField(choices=User._meta.get_field('gender').choices, initial='other')
    labfront_participant_id = forms.CharField(
        label='Labfront participant ID', max_length=64,
        help_text='Copy the actual participant ID from Labfront after setting up their Garmin connection.',
    )

    def __init__(self, *args, participant=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.participant = participant
        if participant is not None:
            for name in ('first_name', 'last_name', 'gender'):
                self.fields.pop(name)
            self.fields['email'].initial = participant.email
            self.fields['email'].disabled = True
            device = WearableDevice.objects.filter(user=participant).first()
            if device and not device.labfront_participant_id.upper().startswith('TEST-'):
                self.fields['labfront_participant_id'].initial = device.labfront_participant_id
        else:
            self.fields.pop('email')
        self.generated_credentials = None

    def clean_labfront_participant_id(self):
        participant_id = self.cleaned_data['labfront_participant_id']
        if participant_id.upper().startswith('TEST-'):
            raise ValidationError('Enter the real Labfront participant ID, not a TEST placeholder.')
        devices = WearableDevice.objects.filter(labfront_participant_id=participant_id)
        if self.participant is not None:
            devices = devices.exclude(user=self.participant)
            current = WearableDevice.objects.filter(user=self.participant).first()
            if (current and current.labfront_participant_id != participant_id
                    and not current.labfront_participant_id.upper().startswith('TEST-')):
                raise ValidationError('This account already has a different Labfront ID. Correct its wearable record first.')
        if devices.exists():
            raise ValidationError('This Labfront participant ID is already linked to another account.')
        return participant_id

    def generate_credentials(self):
        while True:
            email = f'{uuid.uuid4().hex}@react.user'
            if not User.objects.filter(email__iexact=email).exists():
                break
        password = secrets.token_urlsafe(24)
        return email, password

    @transaction.atomic
    def save(self):
        if not self.is_valid():
            raise ValueError('Cannot save an invalid enrollment form.')
        data = self.cleaned_data
        if self.participant is None:
            email, password = self.generate_credentials()
            participant = User(
                email=email, birthdate='2000-01-01',
                **{name: data[name] for name in ('first_name', 'last_name', 'gender')},
            )
            participant.set_password(password)
            participant.save()
        else:
            participant = User.objects.select_for_update().get(pk=self.participant.pk)

        device = WearableDevice.objects.select_for_update().filter(user=participant).first()
        created = device is None
        if device is None:
            device = WearableDevice(user=participant)
        elif (device.labfront_participant_id != data['labfront_participant_id']
              and not device.labfront_participant_id.upper().startswith('TEST-')):
            raise ValidationError('The wearable link changed. Reload the form before enrolling.')
        device.labfront_participant_id = data['labfront_participant_id']
        device.is_active = True
        device.save()
        participant.is_enrolled = True
        # User.save stamps the first enrollment date without resetting an existing one.
        participant.save(update_fields=['is_enrolled'])
        if self.participant is None:
            self.generated_credentials = {'email': email, 'password': password}
        return participant, device, created
