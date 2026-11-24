from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth.models import Group
from django.contrib.auth import login, logout
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.decorators import login_required
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from rooms.models import HostelBlock, Room
from swaps.models import (
    Approval,
    AuditEntry,
    ExchangeProposal,
    ExchangeRequest,
    PolicyRule,
    ProposalParticipant,
    TransferRecord,
)
from swaps.permissions import (
    CHIEF_WARDEN_GROUP,
    STUDENT_GROUP,
    WARDEN_GROUP,
    can_approve_proposal,
    can_view_analytics,
    has_role,
    is_hostel_administrator,
)
from .models import StudentProfile
from .forms import StudentRegistrationForm


def register_view(request):
    if request.user.is_authenticated:
        return redirect('accounts:dashboard')

    if request.method == 'POST':
        form = StudentRegistrationForm(request.POST)
        if form.is_valid():
            user = form.save()
            student_group, _ = Group.objects.get_or_create(name=STUDENT_GROUP)
            user.groups.add(student_group)
            login(request, user)
            messages.success(request, 'Registration successful. Welcome to Roomly!')
            return redirect('accounts:dashboard')
    else:
        form = StudentRegistrationForm()
    return render(request, 'accounts/register.html', {'form': form})


def user_login_view(request):
    if request.user.is_authenticated:
        return redirect('accounts:dashboard')

    if request.method == 'POST':
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            login(request, user)
            messages.info(request, f'Welcome back, {user.username}!')
            next_url = request.POST.get('next') or request.GET.get('next')
            if next_url and url_has_allowed_host_and_scheme(
                next_url,
                allowed_hosts={request.get_host()},
                require_https=request.is_secure(),
            ):
                return redirect(next_url)
            return redirect('accounts:dashboard')
    else:
        form = AuthenticationForm(request)
    return render(request, 'accounts/login.html', {
        'form': form,
        'next_url': request.GET.get('next', ''),
    })


@login_required(login_url='accounts:login')
@require_POST
def user_logout_view(request):
    logout(request)
    messages.info(request, 'You have been successfully logged out.')
    return redirect('accounts:login')


@login_required(login_url='accounts:login')
def student_dashboard_view(request):
    user = request.user
    if is_hostel_administrator(user):
        return _administrator_dashboard(request)
    if has_role(user, CHIEF_WARDEN_GROUP) or has_role(user, WARDEN_GROUP):
        return _warden_dashboard(request)
    if can_view_analytics(user):
        return _dean_dashboard(request)

    profile, _ = StudentProfile.objects.get_or_create(user=request.user)
    assigned_room = profile.current_room
    requests = profile.exchange_requests.all()[:5]
    proposals = ProposalParticipant.objects.filter(
        student=profile,
        proposal__status__in=(
            ExchangeProposal.Status.AWAITING_ACCEPTANCE,
            ExchangeProposal.Status.AWAITING_APPROVAL,
        ),
    ).select_related('proposal').order_by('-proposal__created_at')
    transfers = TransferRecord.objects.filter(
        proposal__participants__student=profile,
    ).distinct()[:5]

    context = {
        'profile': profile,
        'assigned_room': assigned_room,
        'exchange_requests': requests,
        'proposals': proposals,
        'transfers': transfers,
    }
    return render(request, 'accounts/dashboard.html', context)


def _administrator_dashboard(request):
    context = {
        'dashboard_role': 'Hostel administrator',
        'active_blocks': HostelBlock.objects.count(),
        'total_rooms': Room.objects.count(),
        'verified_rooms': Room.objects.filter(is_verified=True, is_active=True).count(),
        'unverified_rooms': Room.objects.filter(is_verified=False).count(),
        'active_requests': ExchangeRequest.objects.filter(
            status__in=(ExchangeRequest.Status.OPEN, ExchangeRequest.Status.MATCHED),
        ).count(),
        'approval_queue': ExchangeProposal.objects.filter(
            status=ExchangeProposal.Status.AWAITING_APPROVAL,
        ).count(),
        'policy': PolicyRule.current(),
        'recent_activity': AuditEntry.objects.select_related('actor')[:5],
    }
    return render(request, 'accounts/staff_dashboard.html', context)


def _warden_dashboard(request):
    proposals = ExchangeProposal.objects.filter(
        status=ExchangeProposal.Status.AWAITING_APPROVAL,
    ).prefetch_related(
        'participants__from_room__block',
        'participants__to_room__block',
    ).order_by('expires_at')
    pending_proposals = [
        proposal for proposal in proposals
        if can_approve_proposal(request.user, proposal)
    ]
    is_chief_warden = has_role(request.user, CHIEF_WARDEN_GROUP)
    assigned_blocks = (
        HostelBlock.objects.all()
        if is_chief_warden
        else HostelBlock.objects.filter(warden_assignments__user=request.user).distinct()
    )
    context = {
        'dashboard_role': 'Chief warden' if is_chief_warden else 'Warden',
        'assigned_blocks': assigned_blocks,
        'pending_proposals': pending_proposals[:5],
        'pending_count': len(pending_proposals),
        'recent_approvals': Approval.objects.filter(
            approver=request.user,
        ).select_related('proposal').order_by('-decided_at')[:5],
    }
    return render(request, 'accounts/staff_dashboard.html', context)


def _dean_dashboard(request):
    request_count = ExchangeRequest.objects.count()
    matched_request_count = ProposalParticipant.objects.values(
        'exchange_request_id',
    ).distinct().count()
    context = {
        'dashboard_role': 'Student Welfare Dean',
        'request_count': request_count,
        'matched_request_count': matched_request_count,
        'match_rate': round(matched_request_count * 100 / request_count, 1) if request_count else 0,
        'completed_count': ExchangeProposal.objects.filter(
            status=ExchangeProposal.Status.EXECUTED,
        ).count(),
        'pending_count': ExchangeProposal.objects.filter(
            status=ExchangeProposal.Status.AWAITING_APPROVAL,
        ).count(),
        'block_count': HostelBlock.objects.count(),
    }
    return render(request, 'accounts/staff_dashboard.html', context)
