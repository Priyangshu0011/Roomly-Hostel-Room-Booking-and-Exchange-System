from collections import defaultdict

from django.db.models import F

from .models import ExchangeRequest


def detect_exchange_cycles(requests, max_length=5, max_cycles=250, max_expansions=200_000):
    """Return bounded, deterministic simple room cycles with distinct students."""
    adjacency = defaultdict(list)
    for exchange_request in requests:
        target_rooms = getattr(exchange_request, '_eligible_target_rooms', None)
        if target_rooms is None:
            target_rooms = [preference.room_id for preference in exchange_request.preferences.all()]
        for target_room_id in target_rooms:
            adjacency[exchange_request.from_room_id].append((target_room_id, exchange_request))

    for edges in adjacency.values():
        edges.sort(key=lambda edge: (edge[0], edge[1].requester_id, edge[1].pk))

    cycles = []
    visited_work = 0
    start_rooms = sorted(adjacency)

    for start_room in start_rooms:
        path = []
        visited_rooms = {start_room}
        visited_students = set()

        def walk(current_room):
            nonlocal visited_work
            if len(cycles) >= max_cycles or visited_work >= max_expansions:
                return

            for target_room, exchange_request in adjacency.get(current_room, ()):
                visited_work += 1
                if visited_work >= max_expansions:
                    return
                if exchange_request.requester_id in visited_students:
                    continue
                if target_room == start_room:
                    if len(path) + 1 >= 2:
                        cycles.append(tuple(path + [exchange_request]))
                        if len(cycles) >= max_cycles:
                            return
                    continue
                if target_room <= start_room or target_room in visited_rooms:
                    continue
                if len(path) + 1 >= max_length:
                    continue

                visited_rooms.add(target_room)
                visited_students.add(exchange_request.requester_id)
                path.append(exchange_request)
                walk(target_room)
                path.pop()
                visited_students.remove(exchange_request.requester_id)
                visited_rooms.remove(target_room)
                if len(cycles) >= max_cycles or visited_work >= max_expansions:
                    return

        walk(start_room)
        if len(cycles) >= max_cycles or visited_work >= max_expansions:
            break

    return cycles


def open_exchange_requests(now):
    return ExchangeRequest.objects.filter(
        status=ExchangeRequest.Status.OPEN,
        expires_at__gt=now,
        requester__current_room=F('from_room'),
    ).select_related(
        'requester__user',
        'requester__current_room__block',
        'from_room__block',
    ).prefetch_related(
        'preferences__room__block',
    ).order_by('from_room_id', 'requester_id', 'pk')
