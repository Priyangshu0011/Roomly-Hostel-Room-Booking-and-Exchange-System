import json
from functools import wraps

from django.core.exceptions import PermissionDenied, ValidationError
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from accounts.models import StudentProfile
from rooms.models import Room
from .models import ExchangeProposal, ExchangeRequest, TransferRecord
from .services import (
    approve_proposal,
    create_exchange_request,
    respond_to_proposal,
)


def api_authenticated(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return JsonResponse({'error': 'Authentication is required.'}, status=401)
        return view(request, *args, **kwargs)

    return wrapped


def read_json_body(request):
    try:
        payload = json.loads(request.body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None, JsonResponse({'error': 'Request body must be valid JSON.'}, status=400)
    if not isinstance(payload, dict):
        return None, JsonResponse({'error': 'JSON request body must be an object.'}, status=400)
    return payload, None


def validation_error_response(error):
    return JsonResponse({'errors': error.messages}, status=400)


def serialize_request(exchange_request):
    return {
        'id': exchange_request.pk,
        'status': exchange_request.status,
        'from_room_id': exchange_request.from_room_id,
        'preferred_room_ids': list(
            exchange_request.preferences.order_by('priority').values_list('room_id', flat=True)
        ),
        'reason': exchange_request.reason,
        'created_at': exchange_request.created_at.isoformat(),
        'expires_at': exchange_request.expires_at.isoformat(),
    }


def serialize_proposal(proposal):
    return {
        'id': proposal.pk,
        'status': proposal.status,
        'cycle_length': proposal.cycle_length,
        'expires_at': proposal.expires_at.isoformat(),
        'participants': [
            {
                'student_id': participant.student_id,
                'student': participant.student.user.get_username(),
                'from_room_id': participant.from_room_id,
                'to_room_id': participant.to_room_id,
                'response': participant.response,
            }
            for participant in proposal.participants.select_related('student__user').all()
        ],
    }


@api_authenticated
@require_http_methods(['GET', 'POST'])
def exchange_requests_api(request):
    profile, _ = StudentProfile.objects.get_or_create(user=request.user)
    if request.method == 'GET':
        requests = ExchangeRequest.objects.filter(
            requester=profile,
        ).prefetch_related('preferences').order_by('-created_at')
        return JsonResponse({'results': [serialize_request(item) for item in requests]})

    payload, error_response = read_json_body(request)
    if error_response:
        return error_response
    preferred_room_ids = payload.get('preferred_room_ids')
    reason = payload.get('reason', '')
    if not isinstance(preferred_room_ids, list) or not all(
        isinstance(room_id, int) and not isinstance(room_id, bool)
        for room_id in preferred_room_ids
    ):
        return JsonResponse(
            {'error': 'preferred_room_ids must be a list of room IDs.'},
            status=400,
        )
    if len(set(preferred_room_ids)) != len(preferred_room_ids):
        return JsonResponse({'error': 'preferred_room_ids cannot contain duplicates.'}, status=400)
    if not isinstance(reason, str) or len(reason) > 500:
        return JsonResponse({'error': 'reason must be a string of at most 500 characters.'}, status=400)
    rooms = list(Room.objects.filter(pk__in=preferred_room_ids).select_related('block'))
    if len(rooms) != len(set(preferred_room_ids)):
        return JsonResponse({'error': 'One or more preferred rooms do not exist.'}, status=400)
    try:
        exchange_request = create_exchange_request(
            profile.pk,
            rooms,
            reason,
            request.user,
        )
    except ValidationError as error:
        return validation_error_response(error)
    return JsonResponse({'request': serialize_request(exchange_request)}, status=201)


@api_authenticated
@require_GET
def proposals_api(request):
    profile, _ = StudentProfile.objects.get_or_create(user=request.user)
    proposals = ExchangeProposal.objects.filter(
        participants__student=profile,
    ).distinct().order_by('-created_at')
    return JsonResponse({'results': [serialize_proposal(item) for item in proposals]})


@api_authenticated
@require_POST
def proposal_respond_api(request, proposal_id):
    payload, error_response = read_json_body(request)
    if error_response:
        return error_response
    decision = payload.get('decision')
    if decision not in ('accept', 'decline'):
        return JsonResponse({'error': 'decision must be "accept" or "decline".'}, status=400)
    profile, _ = StudentProfile.objects.get_or_create(user=request.user)
    try:
        proposal = respond_to_proposal(
            proposal_id,
            profile.pk,
            decision == 'accept',
            request.user,
        )
    except ExchangeProposal.DoesNotExist:
        return JsonResponse({'error': 'Proposal not found.'}, status=404)
    except PermissionDenied as error:
        return JsonResponse({'error': str(error)}, status=403)
    except ValidationError as error:
        return validation_error_response(error)
    return JsonResponse({'proposal': serialize_proposal(proposal)})


@api_authenticated
@require_POST
def proposal_approve_api(request, proposal_id):
    payload, error_response = read_json_body(request)
    if error_response:
        return error_response
    decision = payload.get('decision')
    remarks = payload.get('remarks', '')
    if not isinstance(remarks, str):
        return JsonResponse({'error': 'remarks must be a string.'}, status=400)
    try:
        proposal = approve_proposal(
            proposal_id,
            request.user,
            decision,
            remarks,
        )
    except PermissionDenied as error:
        return JsonResponse({'error': str(error)}, status=403)
    except ExchangeProposal.DoesNotExist:
        return JsonResponse({'error': 'Proposal not found.'}, status=404)
    except ValidationError as error:
        return validation_error_response(error)
    response = {'proposal': serialize_proposal(proposal)}
    if proposal.status == ExchangeProposal.Status.EXECUTED:
        response['transfer_id'] = str(proposal.transfer_record.transfer_id)
    return JsonResponse(response)


@api_authenticated
@require_GET
def transfers_api(request):
    profile, _ = StudentProfile.objects.get_or_create(user=request.user)
    transfers = TransferRecord.objects.filter(
        proposal__participants__student=profile,
    ).distinct().order_by('-completed_at')
    return JsonResponse({
        'results': [
            {
                'transfer_id': str(transfer.transfer_id),
                'completed_at': transfer.completed_at.isoformat(),
                'proposal_id': transfer.proposal_id,
                'participants': transfer.participant_snapshot,
                'approval_remarks': transfer.approval_remarks,
            }
            for transfer in transfers
        ],
    })


@api_authenticated
@require_GET
def transfer_detail_api(request, transfer_id):
    profile, _ = StudentProfile.objects.get_or_create(user=request.user)
    try:
        transfer = TransferRecord.objects.get(
            transfer_id=transfer_id,
            proposal__participants__student=profile,
        )
    except TransferRecord.DoesNotExist:
        return JsonResponse({'error': 'Transfer record not found.'}, status=404)
    return JsonResponse({
        'transfer_id': str(transfer.transfer_id),
        'completed_at': transfer.completed_at.isoformat(),
        'proposal_id': transfer.proposal_id,
        'participants': transfer.participant_snapshot,
        'approval_remarks': transfer.approval_remarks,
    })
