from django.utils import timezone

from .models import PolicyRule


def eligibility_reasons(profile, target_room=None, now=None, policy=None):
    now = now or timezone.now()
    reasons = []
    current_room = profile.current_room

    if current_room is None:
        reasons.append('A current room allocation is required.')
    else:
        assigned_at = profile.current_room_assigned_at
        policy = policy or PolicyRule.current()
        cooling_off_days = policy.cooling_off_days if policy else 30
        if assigned_at is None:
            reasons.append('Your room allocation date is missing; contact the hostel administrator.')
        elif (now - assigned_at).days < cooling_off_days:
            days_left = cooling_off_days - (now - assigned_at).days
            reasons.append(f'The cooling-off period is active for {days_left} more day(s).')

    if not profile.fees_clear:
        reasons.append('Outstanding hostel fees must be cleared before requesting an exchange.')
    if profile.disciplinary_hold:
        reasons.append('An active disciplinary hold prevents exchange requests.')

    if target_room is not None:
        if not target_room.is_active:
            reasons.append('This room is not accepting exchange requests.')
        if not target_room.is_verified:
            reasons.append('This room has not been verified by the hostel administrator.')
        if current_room is not None and current_room.pk == target_room.pk:
            reasons.append('Your current room cannot be selected as a destination.')
        block_policy = target_room.block.gender_restriction
        if block_policy != 'C' and profile.gender != block_policy:
            reasons.append(
                f'This room is in a {target_room.block.get_gender_restriction_display()} block '
                'and does not match your registered eligibility.'
            )

    return reasons
