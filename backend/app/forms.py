from django import forms
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.db import transaction

from .models import User, WearableDevice


class ParticipantEnrollmentForm(forms.Form):
    email = forms.EmailField(max_length=254)
    first_name = forms.CharField(max_length=100, required=False)
    last_name = forms.CharField(max_length=100, required=False)
    birthdate = forms.DateField(widget=forms.DateInput(attrs={'type': 'date'}))
    gender = forms.ChoiceField(choices=User._meta.get_field('gender').choices, initial='other')
    password1 = forms.CharField(
        label='Password', strip=False, min_length=8,
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
    )
    password2 = forms.CharField(
        label='Confirm password', strip=False,
        widget=forms.PasswordInput(attrs={'autocomplete': 'new-password'}),
    )
    labfront_participant_id = forms.CharField(
        label='Labfront participant ID', max_length=64,
        help_text='Copy the actual participant ID from Labfront after setting up their Garmin connection.',
    )

    def __init__(self, *args, participant=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.participant = participant
        if participant is not None:
            for name in ('first_name', 'last_name', 'birthdate', 'gender', 'password1', 'password2'):
                self.fields.pop(name)
            self.fields['email'].initial = participant.email
            self.fields['email'].disabled = True
            device = WearableDevice.objects.filter(user=participant).first()
            if device and not device.labfront_participant_id.upper().startswith('TEST-'):
                self.fields['labfront_participant_id'].initial = device.labfront_participant_id

    def clean_email(self):
        email = self.cleaned_data['email']
        if self.participant is None and User.objects.filter(email__iexact=email).exists():
            raise ValidationError('This account already exists. Open it under Users and choose Enroll with Labfront.')
        return email

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

    def clean(self):
        data = super().clean()
        if self.participant is None:
            password = data.get('password1')
            if password and data.get('password2') and password != data['password2']:
                self.add_error('password2', 'The passwords do not match.')
            if password:
                candidate = User(**{name: data.get(name, '') for name in ('email', 'first_name', 'last_name')})
                try:
                    validate_password(password, candidate)
                except ValidationError as error:
                    self.add_error('password1', error)
        return data

    @transaction.atomic
    def save(self):
        if not self.is_valid():
            raise ValueError('Cannot save an invalid enrollment form.')
        data = self.cleaned_data
        if self.participant is None:
            participant = User(**{name: data[name] for name in (
                'email', 'first_name', 'last_name', 'birthdate', 'gender',
            )})
            participant.set_password(data['password1'])
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
        return participant, device, created
