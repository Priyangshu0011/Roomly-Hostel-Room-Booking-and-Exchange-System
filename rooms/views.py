from django.shortcuts import render, get_object_or_404
from django.db.models import Count, F
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from accounts.models import WardenAssignment
from swaps.models import ExchangeProposal, ExchangeRequest, PolicyRule
from .models import HostelBlock, Room


def room_list_view(request):
    rooms = Room.objects.select_related('block').annotate(
        occupancy=Count('occupants')
    ).annotate(
        available_spots=F('capacity') - F('occupancy')
    ).order_by('block__name', 'room_number')
    can_manage_inventory = request.user.has_perm('rooms.manage_room_inventory')
    if not can_manage_inventory:
        rooms = rooms.filter(is_verified=True, is_active=True)

    selected_block = request.GET.get('block', '').strip()
    selected_capacity = request.GET.get('capacity', '').strip()
    available_only = request.GET.get('available') == '1'

    if selected_block:
        rooms = rooms.filter(block__name__icontains=selected_block)

    if selected_capacity.isdigit():
        rooms = rooms.filter(capacity=int(selected_capacity))

    if available_only:
        rooms = rooms.filter(occupancy__lt=F('capacity'))

    context = {
        'rooms': rooms,
        'selected_block': selected_block,
        'selected_capacity': selected_capacity,
        'selected_capacity_int': int(selected_capacity) if selected_capacity.isdigit() else None,
        'capacity_options': range(1, 11),
        'available_only': available_only,
        'total_rooms_count': rooms.count(),
        'available_rooms_count': Room.objects.annotate(
            occupancy=Count('occupants')
        ).filter(occupancy__lt=F('capacity'), is_verified=True, is_active=True).count(),
        'total_blocks_count': rooms.values('block_id').distinct().count(),
        'can_manage_inventory': can_manage_inventory,
    }
    return render(request, 'rooms/room_list.html', context)


def room_detail_view(request, room_id):
    can_manage_inventory = request.user.has_perm('rooms.manage_room_inventory')
    rooms = Room.objects.select_related('block').annotate(occupant_count=Count('occupants'))
    if not can_manage_inventory:
        rooms = rooms.filter(is_verified=True, is_active=True)
    room = get_object_or_404(rooms, pk=room_id)
    available_spots = max(0, room.capacity - room.occupant_count)

    profile = getattr(request.user, 'student_profile', None) if request.user.is_authenticated else None
    context = {
        'room': room,
        'occupant_count': room.occupant_count,
        'available_spots': available_spots,
        'can_request_exchange': bool(profile and profile.current_room),
    }
    return render(request, 'rooms/room_detail.html', context)


@login_required(login_url='accounts:login')
def operations_view(request):
    if not request.user.has_perm('rooms.manage_room_inventory'):
        raise PermissionDenied
    return render(request, 'rooms/operations.html', {
        'unverified_rooms': Room.objects.filter(is_verified=False).select_related('block').count(),
        'active_blocks': HostelBlock.objects.count(),
        'active_requests': ExchangeRequest.objects.filter(
            status__in=(ExchangeRequest.Status.OPEN, ExchangeRequest.Status.MATCHED),
        ).count(),
        'approval_queue': ExchangeProposal.objects.filter(
            status=ExchangeProposal.Status.AWAITING_APPROVAL,
        ).count(),
        'policy': PolicyRule.current(),
        'wardens_without_assignments': request.user.is_superuser and not WardenAssignment.objects.exists(),
    })
