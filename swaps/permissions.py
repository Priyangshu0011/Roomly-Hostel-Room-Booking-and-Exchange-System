from accounts.models import WardenAssignment


STUDENT_GROUP = 'Students'
WARDEN_GROUP = 'Wardens'
CHIEF_WARDEN_GROUP = 'Chief Wardens'
HOSTEL_ADMIN_GROUP = 'Hostel Administrators'
DEAN_GROUP = 'Student Welfare Deans'


def has_role(user, role):
    return user.is_authenticated and (
        user.is_superuser or user.groups.filter(name=role).exists()
    )


def is_hostel_administrator(user):
    return has_role(user, HOSTEL_ADMIN_GROUP)


def can_manage_inventory(user):
    return has_role(user, HOSTEL_ADMIN_GROUP)


def can_view_analytics(user):
    return user.is_authenticated and user.has_perm('swaps.view_exchangeanalytics')


def can_approve_proposal(user, proposal):
    if not user.is_authenticated:
        return False
    if user.is_superuser or has_role(user, CHIEF_WARDEN_GROUP):
        return True
    if not user.has_perm('swaps.review_exchangeproposal') or not has_role(user, WARDEN_GROUP):
        return False

    participant_block_ids = set()
    for participant in proposal.participants.select_related('from_room', 'to_room'):
        participant_block_ids.add(participant.from_room.block_id)
        participant_block_ids.add(participant.to_room.block_id)
    if len(participant_block_ids) != 1:
        return False

    return WardenAssignment.objects.filter(
        user=user,
        block_id=next(iter(participant_block_ids)),
    ).exists()
