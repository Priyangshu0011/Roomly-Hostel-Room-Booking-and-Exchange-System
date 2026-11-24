from django import forms
from django.core.exceptions import ValidationError

from rooms.models import Room
from .models import ExchangeRequest, PolicyRule
from .services import validate_request


class ExchangeRequestForm(forms.Form):
    preferred_rooms = forms.ModelMultipleChoiceField(
        queryset=Room.objects.none(),
        widget=forms.SelectMultiple(attrs={
            'class': 'field-control multi-select',
            'size': 7,
        }),
        label='Preferred destination rooms',
        help_text='Choose rooms in the order you prefer. You will only move if everyone in a proposed exchange accepts and an authorised warden approves it.',
    )
    reason = forms.CharField(
        max_length=500,
        required=False,
        widget=forms.Textarea(attrs={
            'class': 'field-control',
            'rows': 3,
            'placeholder': 'Optional: briefly explain your request.',
        }),
        label='Reason (optional)',
    )

    def __init__(self, *args, user, instance=None, **kwargs):
        self.user = user
        self.instance = instance
        super().__init__(*args, **kwargs)
        profile = getattr(user, 'student_profile', None)
        rooms = Room.objects.filter(is_verified=True, is_active=True).select_related('block')
        if profile and profile.current_room_id:
            rooms = rooms.exclude(pk=profile.current_room_id)
        self.fields['preferred_rooms'].queryset = rooms.order_by('block__name', 'room_number')
        self.fields['preferred_rooms'].help_text = (
            f'Choose up to {PolicyRule.current().max_preferences if PolicyRule.current() else 5} '
            'verified rooms as destinations.'
        )
        if instance is not None and not self.is_bound:
            self.initial['preferred_rooms'] = list(
                instance.preferences.order_by('priority').values_list('room_id', flat=True)
            )
            self.initial['reason'] = instance.reason

    def clean_preferred_rooms(self):
        rooms = list(self.cleaned_data['preferred_rooms'])
        profile = getattr(self.user, 'student_profile', None)
        if not profile:
            raise ValidationError('A student profile is required to request an exchange.')
        if self.instance is None and ExchangeRequest.objects.filter(
            requester=profile,
            status__in=(ExchangeRequest.Status.OPEN, ExchangeRequest.Status.MATCHED),
        ).exists():
            raise ValidationError('You already have an active exchange request.')
        reasons = validate_request(profile, rooms)
        if reasons:
            raise ValidationError(reasons)
        return rooms
