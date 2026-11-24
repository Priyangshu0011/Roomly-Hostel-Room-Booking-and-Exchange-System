from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth.models import Group
from django.contrib.auth import login, logout
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.decorators import login_required
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST
from swaps.models import ExchangeProposal, ProposalParticipant, TransferRecord
from swaps.permissions import STUDENT_GROUP
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
