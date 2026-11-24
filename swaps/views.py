from datetime import timedelta

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Avg, Count, DurationField, ExpressionWrapper, F, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django.utils import timezone

from accounts.models import StudentProfile
from rooms.models import HostelBlock, Room
from .forms import ExchangeRequestForm
from .models import (
    Approval,
    ExchangeProposal,
    ExchangeRequest,
    PolicyRule,
    ProposalParticipant,
    TransferRecord,
)
from .permissions import can_approve_proposal, can_view_analytics
from .services import (
    approve_proposal,
    create_exchange_request,
    edit_exchange_request,
    respond_to_proposal,
    withdraw_exchange_request,
)


def _profile_for(user):
    profile, _ = StudentProfile.objects.get_or_create(user=user)
    return profile


def _show_validation_error(request, error):
    messages.error(request, ' '.join(error.messages))


@login_required(login_url='accounts:login')
def create_exchange_request_view(request):
    profile = _profile_for(request.user)
    if not profile.current_room_id:
        messages.error(request, 'A room allocation is required before requesting an exchange.')
        return redirect('accounts:dashboard')
    form = ExchangeRequestForm(
        request.POST or None,
        user=request.user,
    )
    if request.method == 'POST' and form.is_valid():
        try:
            exchange_request = create_exchange_request(
                profile.pk,
                form.cleaned_data['preferred_rooms'],
                form.cleaned_data['reason'],
                request.user,
            )
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, 'Your exchange request is now active.')
            return redirect('swaps:request_detail', request_id=exchange_request.pk)
    return render(request, 'swaps/create_swap.html', {
        'form': form,
        'profile': profile,
        'policy': PolicyRule.current(),
    })


@login_required(login_url='accounts:login')
def my_exchange_requests_view(request):
    profile = _profile_for(request.user)
    exchange_requests = ExchangeRequest.objects.filter(
        requester=profile,
    ).prefetch_related('preferences__room__block').order_by('-created_at')
    participations = ProposalParticipant.objects.filter(
        student=profile,
    ).select_related('proposal').order_by('-proposal__created_at')
    return render(request, 'swaps/my_swaps.html', {
        'profile': profile,
        'exchange_requests': exchange_requests,
        'participations': participations,
    })


@login_required(login_url='accounts:login')
def exchange_request_detail_view(request, request_id):
    profile = _profile_for(request.user)
    exchange_request = get_object_or_404(
        ExchangeRequest.objects.prefetch_related('preferences__room__block'),
        pk=request_id,
        requester=profile,
    )
    return render(request, 'swaps/request_detail.html', {
        'exchange_request': exchange_request,
        'participations': exchange_request.proposal_participations.select_related('proposal'),
    })


@login_required(login_url='accounts:login')
def edit_exchange_request_view(request, request_id):
    profile = _profile_for(request.user)
    exchange_request = get_object_or_404(
        ExchangeRequest,
        pk=request_id,
        requester=profile,
    )
    if exchange_request.status != ExchangeRequest.Status.OPEN:
        messages.error(request, 'Only open requests can be edited.')
        return redirect('swaps:request_detail', request_id=exchange_request.pk)
    form = ExchangeRequestForm(
        request.POST or None,
        user=request.user,
        instance=exchange_request,
    )
    if request.method == 'POST' and form.is_valid():
        try:
            edit_exchange_request(
                exchange_request.pk,
                profile.pk,
                form.cleaned_data['preferred_rooms'],
                form.cleaned_data['reason'],
                request.user,
            )
        except ValidationError as error:
            form.add_error(None, error)
        else:
            messages.success(request, 'Your exchange request was updated.')
            return redirect('swaps:request_detail', request_id=exchange_request.pk)
    return render(request, 'swaps/create_swap.html', {
        'form': form,
        'profile': profile,
        'exchange_request': exchange_request,
        'policy': PolicyRule.current(),
    })


@login_required(login_url='accounts:login')
@require_POST
def withdraw_exchange_request_view(request, request_id):
    profile = _profile_for(request.user)
    get_object_or_404(ExchangeRequest, pk=request_id, requester=profile)
    try:
        withdraw_exchange_request(request_id, profile.pk, request.user)
    except ValidationError as error:
        _show_validation_error(request, error)
    else:
        messages.success(request, 'Your exchange request was withdrawn.')
    return redirect('swaps:list')


@login_required(login_url='accounts:login')
def proposal_list_view(request):
    profile = _profile_for(request.user)
    proposals = ExchangeProposal.objects.filter(
        participants__student=profile,
    ).distinct().prefetch_related(
        'participants__student__user',
        'participants__from_room__block',
        'participants__to_room__block',
    ).order_by('-created_at')
    return render(request, 'swaps/proposals.html', {'proposals': proposals})


@login_required(login_url='accounts:login')
@require_POST
def respond_to_proposal_view(request, proposal_id):
    profile = _profile_for(request.user)
    get_object_or_404(ExchangeProposal, pk=proposal_id)
    decision = request.POST.get('decision')
    if decision not in ('accept', 'decline'):
        messages.error(request, 'Choose accept or decline for this proposal.')
        return redirect('swaps:proposals')
    try:
        proposal = respond_to_proposal(
            proposal_id,
            profile.pk,
            decision == 'accept',
            request.user,
        )
    except ExchangeProposal.DoesNotExist as error:
        raise Http404 from error
    except ValidationError as error:
        _show_validation_error(request, error)
    else:
        if proposal.status == ExchangeProposal.Status.AWAITING_APPROVAL:
            messages.success(request, 'Every participant has accepted. The proposal is now in the warden approval queue.')
        elif proposal.status == ExchangeProposal.Status.DECLINED:
            messages.info(request, 'The proposal was declined. No room allocations were changed.')
        else:
            messages.success(request, 'Your response has been recorded.')
    return redirect('swaps:proposals')


def _approval_queryset():
    return ExchangeProposal.objects.filter(
        status=ExchangeProposal.Status.AWAITING_APPROVAL,
    ).prefetch_related('participants__from_room__block', 'participants__to_room__block')


@login_required(login_url='accounts:login')
def approval_queue_view(request):
    if not request.user.has_perm('swaps.review_exchangeproposal'):
        raise PermissionDenied
    proposals = list(_approval_queryset())
    proposals = [proposal for proposal in proposals if can_approve_proposal(request.user, proposal)]
    return render(request, 'swaps/approval_queue.html', {'proposals': proposals})


@login_required(login_url='accounts:login')
@require_POST
def approve_proposal_view(request, proposal_id):
    get_object_or_404(ExchangeProposal, pk=proposal_id)
    decision = request.POST.get('decision')
    try:
        proposal = approve_proposal(
            proposal_id,
            request.user,
            decision,
            request.POST.get('remarks', ''),
        )
    except PermissionDenied:
        raise
    except ExchangeProposal.DoesNotExist as error:
        raise Http404 from error
    except ValidationError as error:
        _show_validation_error(request, error)
        return redirect('swaps:approval_queue')
    if proposal.status == ExchangeProposal.Status.EXECUTED:
        messages.success(request, f'Exchange completed. Transfer record {proposal.transfer_record.transfer_id} was created.')
    else:
        messages.info(request, 'The proposal was rejected and no room allocations were changed.')
    return redirect('swaps:approval_queue')


@login_required(login_url='accounts:login')
def transfer_list_view(request):
    profile = _profile_for(request.user)
    transfers = TransferRecord.objects.filter(
        proposal__participants__student=profile,
    ).distinct().prefetch_related('proposal__participants__from_room__block', 'proposal__participants__to_room__block')
    return render(request, 'swaps/transfers.html', {'transfers': transfers})


@login_required(login_url='accounts:login')
def transfer_detail_view(request, transfer_id):
    profile = _profile_for(request.user)
    transfer = get_object_or_404(
        TransferRecord.objects.select_related('proposal'),
        transfer_id=transfer_id,
        proposal__participants__student=profile,
    )
    participants = transfer.proposal.participants.select_related(
        'student__user',
        'from_room__block',
        'to_room__block',
    )
    return render(request, 'swaps/transfer_detail.html', {
        'transfer': transfer,
        'participants': participants,
    })


@login_required(login_url='accounts:login')
def analytics_view(request):
    if not can_view_analytics(request.user):
        raise PermissionDenied
    period = request.GET.get('period', '30')
    if period not in {'30', '90', '365', 'all'}:
        period = '30'
    period_start = (
        timezone.now() - timedelta(days=int(period))
        if period != 'all'
        else None
    )
    requests = ExchangeRequest.objects.all()
    proposals = ExchangeProposal.objects.all()
    approval_records = Approval.objects.all()
    if period_start:
        requests = requests.filter(created_at__gte=period_start)
        proposals = proposals.filter(created_at__gte=period_start)
        approval_records = approval_records.filter(decided_at__gte=period_start)
    completed = proposals.filter(status=ExchangeProposal.Status.EXECUTED)
    request_count = requests.count()
    matched_requests = ProposalParticipant.objects.filter(
        exchange_request__in=requests,
    )
    if period_start:
        matched_requests = matched_requests.filter(proposal__created_at__gte=period_start)
    matched_request_count = matched_requests.values('exchange_request_id').distinct().count()
    average_duration = completed.filter(resolved_at__isnull=False).aggregate(
        value=Avg(ExpressionWrapper(
            F('resolved_at') - F('created_at'),
            output_field=DurationField(),
        ))
    )['value']
    range_filter = Q()
    completed_range_filter = Q(
        rooms__proposal_departures__proposal__transfer_record__isnull=False,
    )
    if period_start:
        range_filter = Q(rooms__outgoing_exchange_requests__created_at__gte=period_start)
        completed_range_filter &= Q(
            rooms__proposal_departures__proposal__transfer_record__completed_at__gte=period_start,
        )
    preference_filter = Q()
    if period_start:
        preference_filter = Q(request_preferences__exchange_request__created_at__gte=period_start)
    return render(request, 'swaps/analytics.html', {
        'request_count': request_count,
        'matched_request_count': matched_request_count,
        'match_rate': round(matched_request_count * 100 / request_count, 1) if request_count else 0,
        'open_count': requests.filter(status=ExchangeRequest.Status.OPEN).count(),
        'proposal_count': proposals.count(),
        'completed_count': completed.count(),
        'average_duration': average_duration,
        'period': period,
        'blocks': HostelBlock.objects.annotate(
            request_count=Count(
                'rooms__outgoing_exchange_requests',
                filter=range_filter,
                distinct=True,
            ),
            transfer_count=Count(
                'rooms__proposal_departures__proposal__transfer_record',
                filter=completed_range_filter,
                distinct=True,
            ),
        ).order_by('name'),
        'most_requested_rooms': Room.objects.annotate(
            request_count=Count(
                'request_preferences',
                filter=preference_filter,
                distinct=True,
            ),
        ).filter(request_count__gt=0).select_related('block').order_by('-request_count')[:10],
        'warden_patterns': approval_records.values(
            'approver__username',
        ).annotate(
            approved_count=Count('pk', filter=Q(decision=Approval.Decision.APPROVED)),
            rejected_count=Count('pk', filter=Q(decision=Approval.Decision.REJECTED)),
        ).order_by('approver__username'),
    })
