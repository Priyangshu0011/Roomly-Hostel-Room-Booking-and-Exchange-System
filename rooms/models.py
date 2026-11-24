from django.db import models
from django.db.models import Q
from django.conf import settings


class HostelBlock(models.Model):
    class GenderRestriction(models.TextChoices):
        MALE = 'M', 'Male'
        FEMALE = 'F', 'Female'
        COED = 'C', 'Co-ed'

    name = models.CharField(max_length=50, unique=True)
    gender_restriction = models.CharField(
        max_length=1,
        choices=GenderRestriction.choices,
        default=GenderRestriction.COED,
    )

    class Meta:
        verbose_name = 'Hostel Block'
        verbose_name_plural = 'Hostel Blocks'
        ordering = ['name']
        permissions = [
            ('manage_room_inventory', 'Can configure and verify room inventory'),
        ]

    def __str__(self):
        return f"{self.name} ({self.get_gender_restriction_display()})"


class Room(models.Model):
    block = models.ForeignKey(
        HostelBlock,
        on_delete=models.CASCADE,
        related_name='rooms',
    )
    room_number = models.CharField(max_length=10)
    capacity = models.PositiveSmallIntegerField(default=1)
    is_active = models.BooleanField(default=True)
    is_verified = models.BooleanField(default=False)
    verified_at = models.DateTimeField(null=True, blank=True)
    verified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='verified_hostel_rooms',
    )

    class Meta:
        verbose_name = 'Room'
        verbose_name_plural = 'Rooms'
        constraints = [
            models.UniqueConstraint(fields=('block', 'room_number'), name='unique_room_number_per_block'),
            models.CheckConstraint(condition=Q(capacity__gte=1), name='room_capacity_at_least_one'),
        ]
        ordering = ['block', 'room_number']

    def __str__(self):
        return f"{self.block.name} - Room {self.room_number}"
