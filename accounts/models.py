from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone


class StudentProfile(models.Model):
    class Gender(models.TextChoices):
        UNDISCLOSED = 'U', 'Prefer not to say'
        FEMALE = 'F', 'Female'
        MALE = 'M', 'Male'
        OTHER = 'X', 'Another identity'

    user = models.OneToOneField(
        User,
        on_delete=models.CASCADE,
        related_name='student_profile',
    )
    current_room = models.ForeignKey(
        'rooms.Room',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='occupants',
    )
    current_room_assigned_at = models.DateTimeField(null=True, blank=True)
    gender = models.CharField(max_length=1, choices=Gender.choices, default=Gender.UNDISCLOSED)
    fees_clear = models.BooleanField(default=True)
    disciplinary_hold = models.BooleanField(default=False)
    fee_status_updated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Student Profile'
        verbose_name_plural = 'Student Profiles'
        permissions = [
            ('manage_student_eligibility', 'Can update student eligibility'),
        ]

    def __str__(self):
        room_info = self.current_room.room_number if self.current_room else "Unallocated"
        return f"{self.user.username} ({room_info})"

    def save(self, *args, **kwargs):
        update_fields = kwargs.get('update_fields')
        room_is_being_saved = update_fields is None or 'current_room' in update_fields or 'current_room_id' in update_fields
        if room_is_being_saved:
            previous_room_id = None
            if self.pk:
                previous_room_id = type(self).objects.filter(pk=self.pk).values_list(
                    'current_room_id', flat=True
                ).first()
            if previous_room_id != self.current_room_id:
                self.current_room_assigned_at = timezone.now() if self.current_room_id else None
                if update_fields is not None:
                    kwargs['update_fields'] = set(update_fields) | {'current_room_assigned_at'}
        return super().save(*args, **kwargs)


class WardenAssignment(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='warden_assignments')
    block = models.ForeignKey(
        'rooms.HostelBlock',
        on_delete=models.CASCADE,
        related_name='warden_assignments',
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=('user', 'block'), name='unique_warden_block_assignment'),
        ]
        ordering = ('block__name', 'user__username')

    def __str__(self):
        return f'{self.user.username} · {self.block.name}'
