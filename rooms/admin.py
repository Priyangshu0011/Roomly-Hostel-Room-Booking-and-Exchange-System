from django.contrib import admin
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from .models import HostelBlock, Room


@admin.register(HostelBlock)
class HostelBlockAdmin(admin.ModelAdmin):
    list_display = ('name', 'gender_restriction', 'room_count')
    list_filter = ('gender_restriction',)
    search_fields = ('name',)

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_room_count=Count('rooms'))

    @admin.display(description='Rooms')
    def room_count(self, obj):
        return obj._room_count

    def has_module_permission(self, request):
        return request.user.has_perm('rooms.view_hostelblock')

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm('rooms.view_hostelblock')

    def has_add_permission(self, request):
        return request.user.has_perm('rooms.add_hostelblock')

    def has_change_permission(self, request, obj=None):
        return request.user.has_perm('rooms.change_hostelblock')

    def has_delete_permission(self, request, obj=None):
        return request.user.has_perm('rooms.delete_hostelblock')


@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ('room_number', 'block', 'capacity', 'occupancy', 'is_active', 'is_verified', 'verified_at')
    list_filter = ('block', 'is_active', 'is_verified', 'capacity')
    search_fields = ('room_number', 'block__name')
    readonly_fields = ('verified_at', 'verified_by')
    list_editable = ('is_active',)

    def get_queryset(self, request):
        return super().get_queryset(request).annotate(_occupancy=Count('occupants'))

    @admin.display(description='Occupancy')
    def occupancy(self, obj):
        return f'{obj._occupancy} / {obj.capacity}'

    def has_module_permission(self, request):
        return request.user.has_perm('rooms.view_room')

    def has_view_permission(self, request, obj=None):
        return request.user.has_perm('rooms.view_room')

    def has_add_permission(self, request):
        return request.user.has_perm('rooms.add_room')

    def has_change_permission(self, request, obj=None):
        return request.user.has_perm('rooms.change_room')

    def has_delete_permission(self, request, obj=None):
        return request.user.has_perm('rooms.delete_room')

    @transaction.atomic
    def save_model(self, request, obj, form, change):
        previous = None
        if change:
            previous = Room.objects.select_for_update().get(pk=obj.pk)
        occupancy = obj.occupants.count() if obj.pk else 0
        if obj.capacity < occupancy:
            raise ValidationError('Room capacity cannot be less than its current occupancy.')
        if obj.is_verified and (previous is None or not previous.is_verified):
            obj.verified_at = timezone.now()
            obj.verified_by = request.user
        elif not obj.is_verified:
            obj.verified_at = None
            obj.verified_by = None
        super().save_model(request, obj, form, change)
        verification_changed = (
            previous is None and obj.is_verified
            or previous is not None and previous.is_verified != obj.is_verified
        )
        if verification_changed:
            from swaps.models import AuditEntry

            AuditEntry.objects.create(
                actor=request.user,
                action='room.verified' if obj.is_verified else 'room.verification_revoked',
                object_type='Room',
                object_id=str(obj.pk),
                details={
                    'block_id': obj.block_id,
                    'capacity': obj.capacity,
                    'is_verified': obj.is_verified,
                },
            )
