from datetime import timedelta

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count
from django.utils import timezone

from accounts.models import StudentProfile
from rooms.models import Room
from .matching import detect_exchange_cycles, open_exchange_requests
from .models import (
    Approval,
    AuditEntry,
    ExchangeProposal,
    ExchangeRequest,
    PolicyRule,
    ProposalParticipant,
    RequestPreference,
    TransferRecord,
)
from .permissions import can_approve_proposal
from .policies import eligibility_reasons


def audit(actor, action, instance, details=None):
    AuditEntry.objects.create(
        actor=actor,
        action=action,
        object_type=type(instance).__name__,
        object_id=str(instance.pk),
        details=details or {},
    )


def active_policy():
    policy = PolicyRule.current()
    if policy is None:
        policy = PolicyRule.objects.create()
    return policy


def validate_request(profile, target_rooms, now=None, policy=None):
    now = now or timezone.now()
    policy = policy or active_policy()
    reasons = eligibility_reasons(profile, now=now, policy=policy)
    if not target_rooms:
        reasons.append('Choose at least one preferred room.')
    if len(target_rooms) > policy.max_preferences:
        reasons.append(f'Choose no more than {policy.max_preferences} preferred rooms.')
    if len({room.pk for room in target_rooms}) != len(target_rooms):
        reasons.append('A preferred room cannot be selected more than once.')
    for room in target_rooms:
        reasons.extend(eligibility_reasons(profile, room, now=now, policy=policy))
    return list(dict.fromkeys(reasons))


def create_exchange_request(profile_id, target_rooms, reason='', actor=None):
    now = timezone.now()
    policy = active_policy()
    target_rooms = list(target_rooms)
    with transaction.atomic():
        profile = StudentProfile.objects.select_for_update().select_related(
            'current_room__block',
        ).get(pk=profile_id)
        reasons = validate_request(profile, target_rooms, now=now, policy=policy)
        if reasons:
            raise ValidationError(reasons)
        if ExchangeRequest.objects.filter(
            requester=profile,
            status__in=(ExchangeRequest.Status.OPEN, ExchangeRequest.Status.MATCHED),
        ).exists():
            raise ValidationError('You already have an active exchange request.')
        exchange_request = ExchangeRequest.objects.create(
            requester=profile,
            from_room=profile.current_room,
            status=ExchangeRequest.Status.OPEN,
            reason=reason,
            expires_at=now + timedelta(days=policy.request_validity_days),
        )
        RequestPreference.objects.bulk_create([
            RequestPreference(exchange_request=exchange_request, room=room, priority=index)
            for index, room in enumerate(target_rooms, start=1)
        ])
        audit(actor, 'request.created', exchange_request, {
            'from_room_id': exchange_request.from_room_id,
            'preferred_room_ids': [room.pk for room in target_rooms],
        })
    return exchange_request


def edit_exchange_request(exchange_request_id, profile_id, target_rooms, reason, actor):
    now = timezone.now()
    policy = active_policy()
    target_rooms = list(target_rooms)
    expired = False
    with transaction.atomic():
        profile = StudentProfile.objects.select_for_update().select_related(
            'current_room__block',
        ).get(pk=profile_id)
        exchange_request = ExchangeRequest.objects.select_for_update().select_related(
            'requester__current_room__block',
        ).get(pk=exchange_request_id, requester_id=profile_id)
        if exchange_request.status != ExchangeRequest.Status.OPEN:
            raise ValidationError('Only open requests can be edited.')
        if exchange_request.expires_at <= now:
            exchange_request.status = ExchangeRequest.Status.EXPIRED
            exchange_request.save(update_fields=('status', 'updated_at'))
            audit(actor, 'request.expired', exchange_request)
            expired = True
        else:
            reasons = validate_request(profile, target_rooms, now, policy)
            if reasons:
                raise ValidationError(reasons)
            exchange_request.from_room = profile.current_room
            exchange_request.reason = reason
            exchange_request.expires_at = now + timedelta(days=policy.request_validity_days)
            exchange_request.save(update_fields=('from_room', 'reason', 'expires_at', 'updated_at'))
            exchange_request.preferences.all().delete()
            RequestPreference.objects.bulk_create([
                RequestPreference(exchange_request=exchange_request, room=room, priority=index)
                for index, room in enumerate(target_rooms, start=1)
            ])
            audit(actor, 'request.updated', exchange_request, {
                'preferred_room_ids': [room.pk for room in target_rooms],
            })
    if expired:
        raise ValidationError('This exchange request has expired.')
    return exchange_request


def withdraw_exchange_request(exchange_request_id, profile_id, actor):
    with transaction.atomic():
        exchange_request = ExchangeRequest.objects.select_for_update().get(
            pk=exchange_request_id,
            requester_id=profile_id,
        )
        if exchange_request.status != ExchangeRequest.Status.OPEN:
            raise ValidationError('Only open exchange requests can be withdrawn.')
        exchange_request.status = ExchangeRequest.Status.WITHDRAWN
        exchange_request.save(update_fields=('status', 'updated_at'))
        audit(actor, 'request.withdrawn', exchange_request)
    return exchange_request


def _expire_proposal(proposal, now, actor=None):
    if proposal.status not in (
        ExchangeProposal.Status.AWAITING_ACCEPTANCE,
        ExchangeProposal.Status.AWAITING_APPROVAL,
    ):
        return False
    proposal.status = ExchangeProposal.Status.EXPIRED
    proposal.resolved_at = now
    proposal.save(update_fields=('status', 'resolved_at'))
    participants = proposal.participants.select_related('exchange_request').all()
    for participant in participants:
        request = participant.exchange_request
        if request.status == ExchangeRequest.Status.MATCHED:
            request.status = (
                ExchangeRequest.Status.OPEN
                if request.expires_at > now
                else ExchangeRequest.Status.EXPIRED
            )
            request.save(update_fields=('status', 'updated_at'))
    audit(actor, 'proposal.expired', proposal)
    return True


def expire_exchange_workflows(now=None):
    now = now or timezone.now()
    expired_request_ids = list(ExchangeRequest.objects.filter(
        status=ExchangeRequest.Status.OPEN,
        expires_at__lte=now,
    ).order_by('pk').values_list('pk', flat=True))
    request_count = 0
    for request_id in expired_request_ids:
        with transaction.atomic():
            exchange_request = ExchangeRequest.objects.select_for_update().get(pk=request_id)
            if exchange_request.status != ExchangeRequest.Status.OPEN or exchange_request.expires_at > now:
                continue
            exchange_request.status = ExchangeRequest.Status.EXPIRED
            exchange_request.save(update_fields=('status', 'updated_at'))
            audit(None, 'request.expired', exchange_request)
            request_count += 1
    for proposal_id in ExchangeProposal.objects.filter(
        status__in=(
            ExchangeProposal.Status.AWAITING_ACCEPTANCE,
            ExchangeProposal.Status.AWAITING_APPROVAL,
        ),
        expires_at__lte=now,
    ).values_list('pk', flat=True):
        with transaction.atomic():
            proposal = ExchangeProposal.objects.select_for_update().get(pk=proposal_id)
            _expire_proposal(proposal, now)
    return request_count


def create_matching_proposals(requested_by=None, now=None, max_proposals=250):
    now = now or timezone.now()
    policy = active_policy()
    eligible_requests = []
    for exchange_request in open_exchange_requests(now):
        if eligibility_reasons(exchange_request.requester, now=now, policy=policy):
            continue
        valid_targets = [
            preference.room
            for preference in exchange_request.preferences.all()
            if not eligibility_reasons(
                exchange_request.requester,
                preference.room,
                now,
                policy,
            )
        ]
        if valid_targets:
            exchange_request._eligible_target_rooms = [room.pk for room in valid_targets]
            eligible_requests.append(exchange_request)

    cycles = detect_exchange_cycles(
        eligible_requests,
        max_length=policy.max_chain_length,
        max_cycles=max_proposals,
    )
    proposals = []

    for cycle in cycles:
        request_ids = [exchange_request.pk for exchange_request in cycle]
        room_ids = sorted({
            room_id
            for exchange_request in cycle
            for room_id in (
                exchange_request.from_room_id,
                next(
                    target_id for target_id in exchange_request._eligible_target_rooms
                    if target_id == cycle[(cycle.index(exchange_request) + 1) % len(cycle)].from_room_id
                ),
            )
        })
        profile_ids = sorted({exchange_request.requester_id for exchange_request in cycle})
        cycle_key = ':'.join(str(request_id) for request_id in request_ids)
        try:
            with transaction.atomic():
                locked_rooms = list(
                    Room.objects.select_for_update().filter(pk__in=room_ids).order_by('pk')
                )
                locked_profiles = list(
                    StudentProfile.objects.select_for_update().filter(
                        pk__in=profile_ids,
                    ).select_related('current_room__block').order_by('pk')
                )
                locked_requests = list(
                    ExchangeRequest.objects.select_for_update().filter(
                        pk__in=request_ids,
                        status=ExchangeRequest.Status.OPEN,
                        expires_at__gt=now,
                    ).select_related('requester__current_room__block').order_by('pk')
                )
                if len(locked_rooms) != len(room_ids) or len(locked_profiles) != len(profile_ids):
                    continue
                if len(locked_requests) != len(request_ids):
                    continue
                requests_by_id = {item.pk: item for item in locked_requests}
                cycle_requests = [requests_by_id.get(request_id) for request_id in request_ids]
                if any(item is None for item in cycle_requests):
                    continue
                if any(
                    item.requester.current_room_id != item.from_room_id
                    or eligibility_reasons(
                        item.requester,
                        target_room=Room.objects.get(
                            pk=cycle[(index + 1) % len(cycle)].from_room_id
                        ),
                        now=now,
                        policy=policy,
                    )
                    for index, item in enumerate(cycle_requests)
                ):
                    continue
                if ExchangeProposal.objects.filter(cycle_key=cycle_key).exists():
                    continue

                proposal_expiry = min(
                    [now + timedelta(hours=policy.proposal_validity_hours)]
                    + [item.expires_at for item in cycle_requests]
                )
                proposal = ExchangeProposal.objects.create(
                    cycle_length=len(cycle_requests),
                    expires_at=proposal_expiry,
                    created_by=requested_by,
                    cycle_key=cycle_key,
                )
                ProposalParticipant.objects.bulk_create([
                    ProposalParticipant(
                        proposal=proposal,
                        exchange_request=item,
                        student=item.requester,
                        from_room=item.from_room,
                        to_room=cycle_requests[(index + 1) % len(cycle_requests)].from_room,
                    )
                    for index, item in enumerate(cycle_requests)
                ])
                for item in cycle_requests:
                    item.status = ExchangeRequest.Status.MATCHED
                    item.save(update_fields=('status', 'updated_at'))
                audit(requested_by, 'proposal.created', proposal, {
                    'cycle_length': proposal.cycle_length,
                    'request_ids': request_ids,
                    'expires_at': proposal.expires_at.isoformat(),
                })
                proposals.append(proposal)
        except IntegrityError:
            if ExchangeProposal.objects.filter(cycle_key=cycle_key).exists():
                continue
            raise

    return proposals


def respond_to_proposal(proposal_id, profile_id, accept, actor):
    now = timezone.now()
    expired = False
    with transaction.atomic():
        proposal = ExchangeProposal.objects.select_for_update().get(pk=proposal_id)
        if proposal.expires_at <= now:
            _expire_proposal(proposal, now, actor)
            expired = True
        else:
            if proposal.status != ExchangeProposal.Status.AWAITING_ACCEPTANCE:
                raise ValidationError('This proposal is no longer accepting responses.')
            try:
                participant = ProposalParticipant.objects.select_for_update().get(
                    proposal=proposal,
                    student_id=profile_id,
                )
            except ProposalParticipant.DoesNotExist as error:
                raise PermissionDenied('You are not a participant in this proposal.') from error
            if participant.response != ProposalParticipant.Response.PENDING:
                raise ValidationError('You have already responded to this proposal.')
            participant.response = (
                ProposalParticipant.Response.ACCEPTED
                if accept else ProposalParticipant.Response.DECLINED
            )
            participant.responded_at = now
            participant.save(update_fields=('response', 'responded_at'))
            audit(actor, 'proposal.accepted' if accept else 'proposal.declined', proposal, {
                'student_id': profile_id,
            })
            if not accept:
                proposal.status = ExchangeProposal.Status.DECLINED
                proposal.resolved_at = now
                proposal.save(update_fields=('status', 'resolved_at'))
                for item in proposal.participants.select_related('exchange_request').all():
                    request = item.exchange_request
                    if request.status == ExchangeRequest.Status.MATCHED:
                        request.status = (
                            ExchangeRequest.Status.WITHDRAWN
                            if request.requester_id == profile_id
                            else ExchangeRequest.Status.OPEN
                            if request.expires_at > now
                            else ExchangeRequest.Status.EXPIRED
                        )
                        request.save(update_fields=('status', 'updated_at'))
            elif not proposal.participants.exclude(
                response=ProposalParticipant.Response.ACCEPTED
            ).exists():
                proposal.status = ExchangeProposal.Status.AWAITING_APPROVAL
                proposal.save(update_fields=('status',))
    if expired:
        raise ValidationError('This proposal has expired.')
    return proposal


def approve_proposal(proposal_id, approver, decision, remarks):
    if decision not in Approval.Decision.values:
        raise ValidationError('Choose a valid approval decision.')
    remarks = remarks.strip()
    if not remarks:
        raise ValidationError('Approval remarks are required.')
    if len(remarks) > 1000:
        raise ValidationError('Approval remarks cannot exceed 1000 characters.')
    now = timezone.now()
    expired = False

    with transaction.atomic():
        proposal = ExchangeProposal.objects.select_for_update().get(pk=proposal_id)
        if not can_approve_proposal(approver, proposal):
            raise PermissionDenied('You are not authorised to approve this proposal for these hostel blocks.')
        if proposal.expires_at <= now:
            _expire_proposal(proposal, now, approver)
            expired = True
        else:
            if proposal.status != ExchangeProposal.Status.AWAITING_APPROVAL:
                raise ValidationError('All participants must accept before a warden can approve this proposal.')

            participants = list(
                ProposalParticipant.objects.filter(proposal=proposal).order_by('student_id', 'pk')
            )
            if len(participants) != proposal.cycle_length or any(
                item.response != ProposalParticipant.Response.ACCEPTED for item in participants
            ):
                raise ValidationError('Every affected student must accept before the transfer can proceed.')

            room_ids = sorted({
                room_id for item in participants for room_id in (item.from_room_id, item.to_room_id)
            })
            student_ids = sorted({item.student_id for item in participants})
            request_ids = sorted({item.exchange_request_id for item in participants})

            # Lock every affected room in ascending primary-key order before allocations.
            locked_rooms = list(Room.objects.select_for_update().filter(pk__in=room_ids).order_by('pk'))
            locked_students = list(
                StudentProfile.objects.select_for_update().filter(pk__in=student_ids).order_by('pk')
            )
            locked_requests = list(
                ExchangeRequest.objects.select_for_update().filter(pk__in=request_ids).order_by('pk')
            )
            if len(locked_rooms) != len(room_ids) or len(locked_students) != len(student_ids):
                raise ValidationError('A room or resident in this proposal no longer exists.')
            profiles_by_id = {profile.pk: profile for profile in locked_students}
            requests_by_id = {exchange_request.pk: exchange_request for exchange_request in locked_requests}
            if len(locked_requests) != len(request_ids):
                raise ValidationError('An exchange request in this proposal no longer exists.')

            Approval.objects.create(
                proposal=proposal,
                approver=approver,
                decision=decision,
                remarks=remarks,
            )
            if decision == Approval.Decision.REJECTED:
                proposal.status = ExchangeProposal.Status.REJECTED
                proposal.resolved_at = now
                proposal.save(update_fields=('status', 'resolved_at'))
                for exchange_request in requests_by_id.values():
                    exchange_request.status = (
                        ExchangeRequest.Status.OPEN
                        if exchange_request.expires_at > now
                        else ExchangeRequest.Status.EXPIRED
                    )
                    exchange_request.save(update_fields=('status', 'updated_at'))
                audit(approver, 'proposal.rejected', proposal, {'remarks': remarks})
                return proposal

            for item in participants:
                profile = profiles_by_id[item.student_id]
                exchange_request = requests_by_id[item.exchange_request_id]
                if profile.current_room_id != item.from_room_id:
                    raise ValidationError('A participant’s room allocation changed after the proposal was created.')
                if exchange_request.status != ExchangeRequest.Status.MATCHED:
                    raise ValidationError('An exchange request in this proposal is no longer active.')
                target_room = next(room for room in locked_rooms if room.pk == item.to_room_id)
                reasons = eligibility_reasons(profile, target_room=target_room, now=now)
                if reasons:
                    raise ValidationError(reasons)

            counts = dict(
                StudentProfile.objects.filter(current_room_id__in=room_ids)
                .values('current_room_id')
                .annotate(total=Count('pk'))
                .values_list('current_room_id', 'total')
            )
            incoming = {room_id: 0 for room_id in room_ids}
            outgoing = {room_id: 0 for room_id in room_ids}
            for item in participants:
                outgoing[item.from_room_id] += 1
                incoming[item.to_room_id] += 1
            for room in locked_rooms:
                projected_occupancy = counts.get(room.pk, 0) + incoming[room.pk] - outgoing[room.pk]
                if projected_occupancy > room.capacity:
                    raise ValidationError(f'{room} does not have enough capacity for this transfer.')

            for item in participants:
                StudentProfile.objects.filter(pk=item.student_id).update(
                    current_room_id=item.to_room_id,
                    current_room_assigned_at=now,
                )
                profile = profiles_by_id[item.student_id]
                profile.current_room_id = item.to_room_id
                profile.current_room_assigned_at = now
                exchange_request = requests_by_id[item.exchange_request_id]
                exchange_request.status = ExchangeRequest.Status.COMPLETED
                exchange_request.save(update_fields=('status', 'updated_at'))

            proposal.status = ExchangeProposal.Status.EXECUTED
            proposal.resolved_at = now
            proposal.save(update_fields=('status', 'resolved_at'))
            TransferRecord.objects.create(
                proposal=proposal,
                participant_snapshot=[
                    {
                        'student_id': item.student_id,
                        'student': profiles_by_id[item.student_id].user.get_username(),
                        'from_room_id': item.from_room_id,
                        'from_room': str(item.from_room),
                        'to_room_id': item.to_room_id,
                        'to_room': str(item.to_room),
                    }
                    for item in participants
                ],
                approval_remarks=remarks,
            )
            audit(approver, 'transfer.completed', proposal, {
                'transfer_id': str(proposal.transfer_record.transfer_id),
                'participant_count': len(participants),
            })
    if expired:
        raise ValidationError('This proposal has expired.')
    return proposal
