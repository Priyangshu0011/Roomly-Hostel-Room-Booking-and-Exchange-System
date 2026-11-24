import uuid

from django.conf import settings
from django.db import models
from django.db.models import Q
from django.utils import timezone


class ExchangeRequest(models.Model):
    class Status(models.TextChoices):
        OPEN = 'OPEN', 'Open'
        MATCHED = 'MATCHED', 'In a proposal'
        WITHDRAWN = 'WITHDRAWN', 'Withdrawn'
        EXPIRED = 'EXPIRED', 'Expired'
        COMPLETED = 'COMPLETED', 'Completed'

    requester = models.ForeignKey(
        'accounts.StudentProfile',
        on_delete=models.PROTECT,
        related_name='exchange_requests',
    )
    from_room = models.ForeignKey(
        'rooms.Room',
        on_delete=models.PROTECT,
        related_name='outgoing_exchange_requests',
    )
    preferred_rooms = models.ManyToManyField(
        'rooms.Room',
        through='RequestPreference',
        related_name='exchange_preferences',
    )
    status = models.CharField(max_length=12, choices=Status.choices, default=Status.OPEN)
    reason = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    expires_at = models.DateTimeField()

    class Meta:
        ordering = ('-created_at',)
        constraints = [
            models.UniqueConstraint(
                fields=('requester',),
                condition=Q(status__in=('OPEN', 'MATCHED')),
                name='one_active_exchange_request_per_student',
            ),
        ]
        indexes = [
            models.Index(fields=('status', 'expires_at'), name='exchange_status_expiry_idx'),
        ]

    def __str__(self):
        return f'Exchange request #{self.pk} · {self.requester.user.username} · {self.status}'


class RequestPreference(models.Model):
    exchange_request = models.ForeignKey(
        ExchangeRequest,
        on_delete=models.CASCADE,
        related_name='preferences',
    )
    room = models.ForeignKey(
        'rooms.Room',
        on_delete=models.PROTECT,
        related_name='request_preferences',
    )
    priority = models.PositiveSmallIntegerField(default=1)

    class Meta:
        ordering = ('priority', 'room_id')
        constraints = [
            models.UniqueConstraint(
                fields=('exchange_request', 'room'),
                name='unique_room_preference_per_exchange_request',
            ),
            models.UniqueConstraint(
                fields=('exchange_request', 'priority'),
                name='unique_exchange_preference_priority',
            ),
        ]


class PolicyRule(models.Model):
    name = models.CharField(max_length=100, default='Default hostel exchange policy')
    is_active = models.BooleanField(default=True)
    cooling_off_days = models.PositiveSmallIntegerField(default=30)
    request_validity_days = models.PositiveSmallIntegerField(default=30)
    proposal_validity_hours = models.PositiveSmallIntegerField(default=48)
    max_chain_length = models.PositiveSmallIntegerField(default=5)
    max_preferences = models.PositiveSmallIntegerField(default=5)
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='updated_exchange_policies',
    )

    class Meta:
        ordering = ('-updated_at',)
        permissions = [
            ('manage_policyrule', 'Can configure exchange policy'),
        ]

    def clean(self):
        from django.core.exceptions import ValidationError

        errors = {}
        if not 2 <= self.max_chain_length <= 5:
            errors['max_chain_length'] = 'The maximum exchange chain must be between 2 and 5.'
        if not 1 <= self.max_preferences <= 10:
            errors['max_preferences'] = 'The maximum number of room preferences must be between 1 and 10.'
        if self.proposal_validity_hours < 1:
            errors['proposal_validity_hours'] = 'Proposal validity must be at least one hour.'
        if errors:
            raise ValidationError(errors)

    @classmethod
    def current(cls):
        return cls.objects.filter(is_active=True).order_by('-updated_at', '-pk').first()

    def __str__(self):
        return self.name


class ExchangeProposal(models.Model):
    class Status(models.TextChoices):
        AWAITING_ACCEPTANCE = 'AWAITING_ACCEPTANCE', 'Awaiting student acceptance'
        AWAITING_APPROVAL = 'AWAITING_APPROVAL', 'Awaiting warden approval'
        DECLINED = 'DECLINED', 'Declined by a participant'
        REJECTED = 'REJECTED', 'Rejected by an approver'
        EXPIRED = 'EXPIRED', 'Expired'
        EXECUTED = 'EXECUTED', 'Completed'

    cycle_length = models.PositiveSmallIntegerField()
    status = models.CharField(
        max_length=24,
        choices=Status.choices,
        default=Status.AWAITING_ACCEPTANCE,
    )
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_exchange_proposals',
    )
    cycle_key = models.CharField(max_length=512, unique=True)

    class Meta:
        ordering = ('expires_at', 'pk')
        permissions = [
            ('review_exchangeproposal', 'Can review exchange proposals'),
        ]
        indexes = [
            models.Index(fields=('status', 'expires_at'), name='proposal_status_expiry_idx'),
        ]

    def __str__(self):
        return f'{self.cycle_length}-resident proposal #{self.pk} · {self.status}'


class ProposalParticipant(models.Model):
    class Response(models.TextChoices):
        PENDING = 'PENDING', 'Awaiting response'
        ACCEPTED = 'ACCEPTED', 'Accepted'
        DECLINED = 'DECLINED', 'Declined'

    proposal = models.ForeignKey(
        ExchangeProposal,
        on_delete=models.PROTECT,
        related_name='participants',
    )
    exchange_request = models.ForeignKey(
        ExchangeRequest,
        on_delete=models.PROTECT,
        related_name='proposal_participations',
    )
    student = models.ForeignKey(
        'accounts.StudentProfile',
        on_delete=models.PROTECT,
        related_name='proposal_participations',
    )
    from_room = models.ForeignKey(
        'rooms.Room',
        on_delete=models.PROTECT,
        related_name='proposal_departures',
    )
    to_room = models.ForeignKey(
        'rooms.Room',
        on_delete=models.PROTECT,
        related_name='proposal_arrivals',
    )
    response = models.CharField(max_length=10, choices=Response.choices, default=Response.PENDING)
    responded_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ('student_id',)
        constraints = [
            models.UniqueConstraint(
                fields=('proposal', 'student'),
                name='unique_student_per_proposal',
            ),
            models.UniqueConstraint(
                fields=('proposal', 'exchange_request'),
                name='unique_request_per_proposal',
            ),
        ]

    def __str__(self):
        return f'{self.student.user.username}: {self.from_room} → {self.to_room}'


class Approval(models.Model):
    class Decision(models.TextChoices):
        APPROVED = 'APPROVED', 'Approved'
        REJECTED = 'REJECTED', 'Rejected'

    proposal = models.OneToOneField(
        ExchangeProposal,
        on_delete=models.PROTECT,
        related_name='approval',
    )
    approver = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='exchange_approvals',
    )
    decision = models.CharField(max_length=10, choices=Decision.choices)
    remarks = models.CharField(max_length=1000)
    decided_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.get_decision_display()} · proposal #{self.proposal_id}'


class TransferRecord(models.Model):
    transfer_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    proposal = models.OneToOneField(
        ExchangeProposal,
        on_delete=models.PROTECT,
        related_name='transfer_record',
    )
    completed_at = models.DateTimeField(default=timezone.now)
    participant_snapshot = models.JSONField()
    approval_remarks = models.CharField(max_length=1000)

    class Meta:
        ordering = ('-completed_at',)

    def __str__(self):
        return f'Transfer {self.transfer_id}'


class AuditEntry(models.Model):
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='exchange_audit_entries',
    )
    action = models.CharField(max_length=80)
    object_type = models.CharField(max_length=80)
    object_id = models.CharField(max_length=80)
    details = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ('-created_at', '-pk')
        permissions = [
            ('view_exchangeanalytics', 'Can view exchange analytics'),
        ]
        indexes = [
            models.Index(fields=('object_type', 'object_id', 'created_at'), name='audit_object_time_idx'),
        ]

    def __str__(self):
        return f'{self.created_at:%Y-%m-%d %H:%M} · {self.action}'
