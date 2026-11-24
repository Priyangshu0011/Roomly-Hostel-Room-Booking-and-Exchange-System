from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone
from datetime import timedelta
import uuid

from accounts.models import StudentProfile, WardenAssignment
from rooms.models import HostelBlock, Room
from swaps.models import (
    Approval,
    AuditEntry,
    ExchangeProposal,
    ExchangeRequest,
    PolicyRule,
    ProposalParticipant,
    RequestPreference,
)
from swaps.permissions import (
    CHIEF_WARDEN_GROUP,
    DEAN_GROUP,
    HOSTEL_ADMIN_GROUP,
    STUDENT_GROUP,
    WARDEN_GROUP,
)


class Command(BaseCommand):
    help = 'Populate local P04 database with an idempotent set of sample hostel data.'

    @transaction.atomic
    def handle(self, *args, **options):
        if not PolicyRule.objects.filter(is_active=True).exists():
            PolicyRule.objects.create(
                name='P04 Standard Hostel Policy',
                is_active=True,
                cooling_off_days=30,
                request_validity_days=30,
                proposal_validity_hours=48,
                max_chain_length=5,
                max_preferences=5,
            )

        block_specs = (
            ('North House', HostelBlock.GenderRestriction.MALE),
            ('South House', HostelBlock.GenderRestriction.FEMALE),
            ('East House', HostelBlock.GenderRestriction.COED),
            ('West House', HostelBlock.GenderRestriction.MALE),
            ('Central House', HostelBlock.GenderRestriction.COED),
        )
        blocks = {}
        for block_name, gender in block_specs:
            block, _ = HostelBlock.objects.get_or_create(
                name=block_name,
                defaults={'gender_restriction': gender},
            )
            block.gender_restriction = gender
            block.save(update_fields=('gender_restriction',))
            blocks[block_name] = block

            for index in range(1, 6):
                room, _ = Room.objects.get_or_create(
                    block=block,
                    room_number=f'{100 + index}',
                    defaults={'capacity': 1 if index % 3 else 2},
                )
                room.capacity = 1 if index % 3 else 2
                room.is_active = True
                room.is_verified = True
                if room.verified_at is None:
                    room.verified_at = timezone.now()
                room.save()

        student_specs = (
            ('aarav', 'Aarav', 'Sharma', 'North House', '101', StudentProfile.Gender.MALE),
            ('isha', 'Isha', 'Patel', 'South House', '101', StudentProfile.Gender.FEMALE),
            ('kabir', 'Kabir', 'Singh', 'East House', '101', StudentProfile.Gender.MALE),
            ('meera', 'Meera', 'Das', 'West House', '101', StudentProfile.Gender.FEMALE),
            ('rohan', 'Rohan', 'Khan', 'Central House', '101', StudentProfile.Gender.MALE),
        )
        students = []
        student_group = Group.objects.get_or_create(name=STUDENT_GROUP)[0]
        for username, first_name, last_name, block_name, room_number, gender in student_specs:
            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    'first_name': first_name,
                    'last_name': last_name,
                    'email': f'{username}@roomly.local',
                    'is_active': True,
                },
            )
            user.first_name = first_name
            user.last_name = last_name
            user.email = f'{username}@roomly.local'
            user.is_active = True
            if created:
                user.set_password('Roomly@2026')
            user.save()
            user.groups.add(student_group)

            room = Room.objects.get(block=blocks[block_name], room_number=room_number)
            profile, _ = StudentProfile.objects.get_or_create(
                user=user,
                defaults={'gender': gender, 'fees_clear': True},
            )
            profile.gender = gender
            profile.fees_clear = True
            profile.disciplinary_hold = False
            profile.current_room = room
            profile.save()
            students.append(profile)

        warden_specs = (
            ('warden.north', 'North House'),
            ('warden.south', 'South House'),
            ('warden.east', 'East House'),
            ('warden.west', 'West House'),
            ('warden.central', 'Central House'),
        )
        warden_group = Group.objects.get_or_create(name=WARDEN_GROUP)[0]
        for username, block_name in warden_specs:
            user, created = User.objects.get_or_create(
                username=username,
                defaults={
                    'first_name': 'Warden',
                    'last_name': block_name,
                    'email': f'{username}@roomly.local',
                    'is_active': True,
                },
            )
            user.email = f'{username}@roomly.local'
            user.is_active = True
            if created:
                user.set_password('Roomly@2026')
            user.save()
            user.groups.add(warden_group)
            WardenAssignment.objects.get_or_create(
                user=user,
                block=blocks[block_name],
            )

        chief, chief_created = User.objects.get_or_create(
            username='chief.warden',
            defaults={
                'first_name': 'Chief',
                'last_name': 'Warden',
                'email': 'chief.warden@roomly.local',
                'is_active': True,
            },
        )
        if chief_created:
            chief.set_password('Roomly@2026')
        chief.is_active = True
        chief.save()
        chief.groups.add(Group.objects.get_or_create(name=CHIEF_WARDEN_GROUP)[0])

        dean, dean_created = User.objects.get_or_create(
            username='dean.student',
            defaults={
                'first_name': 'Dean',
                'last_name': 'Student Welfare',
                'email': 'dean.student@roomly.local',
                'is_active': True,
            },
        )
        if dean_created:
            dean.set_password('Roomly@2026')
        dean.is_active = True
        dean.save()
        dean.groups.add(Group.objects.get_or_create(name=DEAN_GROUP)[0])

        admin_group = Group.objects.get_or_create(name=HOSTEL_ADMIN_GROUP)[0]
        administrator = User.objects.filter(is_superuser=True).first()
        if administrator:
            administrator.groups.add(admin_group)

        now = timezone.now()
        policy = PolicyRule.objects.filter(is_active=True).order_by('-updated_at', '-pk').first()

        request_specs = (
            (students[0], blocks['East House'], 'Closer to engineering laboratories'),
            (students[2], blocks['North House'], 'Returning to the North House block'),
            (students[1], blocks['Central House'], 'Preferred a quieter central location'),
            (students[3], blocks['East House'], 'Request for an available co-ed block'),
            (students[4], blocks['West House'], 'Move closer to assigned faculty'),
        )
        exchange_requests = []
        for index, (student, block, reason) in enumerate(request_specs):
            destination = Room.objects.filter(block=block, is_verified=True, is_active=True).exclude(
                pk=student.current_room_id
            ).order_by('room_number').first()
            if destination is None:
                continue

            exchange_request = ExchangeRequest.objects.filter(
                requester=student,
                status__in=(ExchangeRequest.Status.OPEN, ExchangeRequest.Status.MATCHED),
            ).first()
            if exchange_request is None:
                exchange_request = ExchangeRequest.objects.create(
                    requester=student,
                    from_room=student.current_room,
                    status=ExchangeRequest.Status.OPEN,
                    reason=f'Sample request {index + 1}: {reason}',
                    expires_at=now + timedelta(days=policy.request_validity_days),
                )
            RequestPreference.objects.update_or_create(
                exchange_request=exchange_request,
                priority=1,
                defaults={'room': destination},
            )
            exchange_requests.append(exchange_request)

        proposal_specs = (
            (ExchangeProposal.Status.AWAITING_ACCEPTANCE, '2-resident sample awaiting student acceptance'),
            (ExchangeProposal.Status.AWAITING_APPROVAL, '3-resident sample awaiting warden approval'),
            (ExchangeProposal.Status.DECLINED, '2-resident sample declined by a participant'),
            (ExchangeProposal.Status.REJECTED, 'Cross-block sample rejected by an approver'),
            (ExchangeProposal.Status.EXPIRED, '4-resident sample that passed its expiry time'),
        )
        for index, (status, description) in enumerate(proposal_specs, start=1):
            cycle_key = f'p04-sample-proposal-{index}'
            proposal, created = ExchangeProposal.objects.get_or_create(
                cycle_key=cycle_key,
                defaults={
                    'cycle_length': 2 if index in (1, 3) else 3 if index == 2 else 4,
                    'status': status,
                    'expires_at': now + timedelta(hours=48) if status not in (
                        ExchangeProposal.Status.EXPIRED,
                    ) else now - timedelta(hours=1),
                    'created_by': chief,
                },
            )
            proposal.status = status
            proposal.save(update_fields=('status',))

            participant_count = proposal.cycle_length
            for participant_index in range(participant_count):
                student = students[(index + participant_index - 1) % len(students)]
                exchange_request = ExchangeRequest.objects.filter(
                    requester=student,
                ).order_by('-created_at').first()
                if exchange_request is None:
                    continue
                to_student = students[(index + participant_index) % len(students)]
                participant, participant_created = ProposalParticipant.objects.get_or_create(
                    proposal=proposal,
                    student=student,
                    exchange_request=exchange_request,
                    defaults={
                        'from_room': student.current_room,
                        'to_room': to_student.current_room,
                        'response': (
                            ProposalParticipant.Response.ACCEPTED
                            if status in (
                                ExchangeProposal.Status.AWAITING_APPROVAL,
                                ExchangeProposal.Status.REJECTED,
                            )
                            else ProposalParticipant.Response.PENDING
                        ),
                    },
                )
                if not participant_created:
                    participant.from_room = student.current_room
                    participant.to_room = to_student.current_room
                    if status == ExchangeProposal.Status.DECLINED and participant_index == participant_count - 1:
                        participant.response = ProposalParticipant.Response.DECLINED
                    participant.save(update_fields=('from_room', 'to_room', 'response'))

            if created:
                AuditEntry.objects.create(
                    actor=chief,
                    action='sample.proposal_created',
                    object_type='ExchangeProposal',
                    object_id=str(proposal.pk),
                    details={'description': description, 'status': status},
                )

        self.stdout.write(self.style.SUCCESS('P04 sample data created successfully.'))
        self.stdout.write(f'Hostel blocks: {HostelBlock.objects.count()}')
        self.stdout.write(f'Rooms: {Room.objects.count()}')
        self.stdout.write(f'Students: {len(students)}')
        self.stdout.write(f'Wardens: {WardenAssignment.objects.count()}')
        self.stdout.write(f'Exchange requests: {ExchangeRequest.objects.count()}')
        self.stdout.write(f'Proposals: {ExchangeProposal.objects.count()}')
