from django.contrib import admin, messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from rooms.models import Room
from .models import StudentProfile, WardenAssignment


@admin.register(StudentProfile)
class StudentProfileAdmin(admin.ModelAdmin):
    list_display = (
        'user',
        'current_room',
        'gender',
        'fees_clear',
        'disciplinary_hold',
        'current_room_assigned_at',
    )
    list_filter = (
        'current_room__block',
        'gender',
        'fees_clear',
        'disciplinary_hold',
    )
    search_fields = (
        'user__username',
        'user__first_name',
        'user__last_name',
        'user__email',
        'current_room__room_number',
    )
    readonly_fields = ('current_room_assigned_at', 'fee_status_updated_at')
    fieldsets = (
        ('Student and room', {'fields': ('user', 'gender', 'current_room', 'current_room_assigned_at')}),
        ('Eligibility', {'fields': ('fees_clear', 'fee_status_updated_at', 'disciplinary_hold')}),
    )

    def has_module_permission(self, request):
        return request.user.has_perm('accounts.view_studentprofile')

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm('accounts.view_studentprofile')

    def has_add_permission(self, request):
        return request.user.has_perm('accounts.add_studentprofile')

    def has_change_permission(self, request, obj=None):
        return request.user.has_perm('accounts.change_studentprofile')

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

    @transaction.atomic
    def save_model(self, request, obj, form, change):
        previous = None
        previous_room_id = None
        if change:
            previous_room_id = StudentProfile.objects.filter(pk=obj.pk).values_list(
                'current_room_id',
                flat=True,
            ).get()
        room_ids = {
            room_id
            for room_id in (previous_room_id, obj.current_room_id)
            if room_id is not None
        }
        locked_room_ids = set(
            Room.objects.select_for_update()
            .filter(pk__in=room_ids)
            .order_by('pk')
            .values_list('pk', flat=True)
        )
        if change:
            previous = StudentProfile.objects.select_for_update().get(pk=obj.pk)
            if previous.current_room_id not in locked_room_ids and previous.current_room_id is not None:
                raise ValidationError('The student allocation changed during this edit. Reload and retry.')
        if obj.current_room_id:
            room = Room.objects.get(pk=obj.current_room_id)
            occupied = StudentProfile.objects.filter(current_room=room).exclude(pk=obj.pk).count()
            if occupied >= room.capacity:
                raise ValidationError(f'{room} has no remaining capacity.')
            if not room.is_active or not room.is_verified:
                raise ValidationError('Students can only be allocated to active, verified rooms.')
            if room.block.gender_restriction != 'C' and room.block.gender_restriction != obj.gender:
                raise ValidationError('This student does not meet the destination block policy.')

        eligibility_changed = previous and (
            previous.fees_clear != obj.fees_clear
            or previous.disciplinary_hold != obj.disciplinary_hold
        )
        if eligibility_changed:
            obj.fee_status_updated_at = timezone.now()
        super().save_model(request, obj, form, change)

        from swaps.models import AuditEntry

        AuditEntry.objects.create(
            actor=request.user,
            action='student.eligibility_updated' if eligibility_changed else 'student.profile_updated',
            object_type='StudentProfile',
            object_id=str(obj.pk),
            details={
                'room_id': obj.current_room_id,
                'fees_clear': obj.fees_clear,
                'disciplinary_hold': obj.disciplinary_hold,
            },
        )
        if eligibility_changed:
            messages.success(request, 'Student eligibility changes were recorded in the audit trail.')


@admin.register(WardenAssignment)
class WardenAssignmentAdmin(admin.ModelAdmin):
    list_display = ('user', 'block')
    list_filter = ('block',)
    search_fields = ('user__username', 'user__first_name', 'user__last_name', 'block__name')

    def has_module_permission(self, request):
        return request.user.has_perm('accounts.manage_student_eligibility')

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm('accounts.manage_student_eligibility')

    def has_add_permission(self, request):
        return request.user.has_perm('accounts.manage_student_eligibility')

    def has_change_permission(self, request, obj=None):
        return request.user.has_perm('accounts.manage_student_eligibility')

    def has_delete_permission(self, request, obj=None):
        return request.user.has_perm('accounts.manage_student_eligibility')
