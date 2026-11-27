from datetime import timedelta

from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.db.models import Q
from django.utils import timezone

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
    TransferRecord,
    TransferRecord,
)
from swaps.permissions import (
    CHIEF_WARDEN_GROUP,
    DEAN_GROUP,
    HOSTEL_ADMIN_GROUP,
    STUDENT_GROUP,
    WARDEN_GROUP,
)
from swaps.services import (
    create_exchange_request,
    create_matching_proposals,
    respond_to_proposal,
)


class Command(BaseCommand):
    help = 'Populate local P04 database with an idempotent set of sample hostel data.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--reset-sample-workflow',
            action='store_true',
            help='Reset the local sample students’ requests, proposals, and transfer before reseeding.',
        )

    @transaction.atomic
    def handle(self, *args, reset_sample_workflow=False, **options):
        if reset_sample_workflow and connection.vendor != 'sqlite':
            raise CommandError('Sample workflow reset is allowed only for the local SQLite database.')

        legacy_proposals = ExchangeProposal.objects.filter(
            cycle_key__startswith='p04-sample-proposal-',
            approval__isnull=True,
            transfer_record__isnull=True,
        )
        legacy_proposal_ids = list(legacy_proposals.values_list('pk', flat=True))
        if legacy_proposal_ids:
            AuditEntry.objects.filter(
                action='sample.proposal_created',
                object_type='ExchangeProposal',
                object_id__in=[str(proposal_id) for proposal_id in legacy_proposal_ids],
            ).delete()
            ProposalParticipant.objects.filter(proposal_id__in=legacy_proposal_ids).delete()
            legacy_proposals.delete()

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
        policy = PolicyRule.current()
        sample_assignment_date = timezone.now() - timedelta(
            days=policy.cooling_off_days + 1,
        )

        block_specs = (
            ('North House', HostelBlock.GenderRestriction.MALE),
            ('South House', HostelBlock.GenderRestriction.FEMALE),
            ('East House', HostelBlock.GenderRestriction.COED),
            ('West House', HostelBlock.GenderRestriction.COED),
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
            profile, profile_created = StudentProfile.objects.get_or_create(
                user=user,
                defaults={'gender': gender, 'fees_clear': True},
            )
            if profile_created or not profile.current_room_id:
                profile.current_room = room
                profile.save()
                StudentProfile.objects.filter(pk=profile.pk).update(
                    current_room_assigned_at=sample_assignment_date,
                )
                profile.refresh_from_db()
            elif (
                legacy_proposal_ids
                and profile.current_room_id == room.pk
                and not TransferRecord.objects.filter(
                    proposal__participants__student=profile,
                ).exists()
            ):
                StudentProfile.objects.filter(pk=profile.pk).update(
                    current_room_assigned_at=sample_assignment_date,
                )
                profile.refresh_from_db()
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

        if reset_sample_workflow:
            self._reset_sample_workflow(students, student_specs, blocks, policy)

        request_specs = (
            (students[0], blocks['East House'], 'Closer to engineering laboratories'),
            (students[2], blocks['Central House'], 'Returning to the Central House block'),
            (students[4], blocks['North House'], 'Move closer to assigned faculty'),
            (students[1], blocks['West House'], 'Preferred a quieter co-ed block'),
            (students[3], blocks['South House'], 'Request for a female hostel block'),
        )
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
            if exchange_request is None and not ExchangeRequest.objects.filter(
                requester=student,
            ).exists():
                exchange_request = create_exchange_request(
                    student.pk,
                    [destination],
                    reason=f'Sample request {index + 1}: {reason}',
                    actor=student.user,
                )
            elif exchange_request and exchange_request.status == ExchangeRequest.Status.OPEN:
                RequestPreference.objects.update_or_create(
                    exchange_request=exchange_request,
                    priority=1,
                    defaults={'room': destination},
                )

        proposals = create_matching_proposals(requested_by=chief)
        proposal_for_approval = next(
            (proposal for proposal in proposals if proposal.cycle_length == 3),
            None,
        )
        if proposal_for_approval:
            participants = proposal_for_approval.participants.select_related(
                'student__user',
            )
            for participant in participants:
                respond_to_proposal(
                    proposal_for_approval.pk,
                    participant.student_id,
                    True,
                    participant.student.user,
                )

        self.stdout.write(self.style.SUCCESS('P04 sample data created successfully.'))
        self.stdout.write(f'Hostel blocks: {HostelBlock.objects.count()}')
        self.stdout.write(f'Rooms: {Room.objects.count()}')
        self.stdout.write(f'Students: {len(students)}')
        self.stdout.write(f'Wardens: {WardenAssignment.objects.count()}')
        self.stdout.write(f'Exchange requests: {ExchangeRequest.objects.count()}')
        self.stdout.write(f'Proposals: {ExchangeProposal.objects.count()}')

    def _reset_sample_workflow(self, students, student_specs, blocks, policy):
        profile_ids = {student.pk for student in students}
        request_ids = list(
            ExchangeRequest.objects.filter(requester_id__in=profile_ids)
            .values_list('pk', flat=True)
        )
        proposal_ids = list(
            ProposalParticipant.objects.filter(
                Q(student_id__in=profile_ids) | Q(exchange_request_id__in=request_ids),
            ).values_list('proposal_id', flat=True).distinct()
        )
        for proposal_id in proposal_ids:
            participants = ProposalParticipant.objects.filter(proposal_id=proposal_id)
            participant_student_ids = set(participants.values_list('student_id', flat=True))
            participant_requester_ids = set(
                participants.values_list('exchange_request__requester_id', flat=True)
            )
            if (
                not participant_student_ids.issubset(profile_ids)
                or not participant_requester_ids.issubset(profile_ids)
            ):
                raise CommandError(
                    f'Cannot reset sample proposal #{proposal_id} because it includes non-sample students.'
                )

        proposal_id_strings = [str(proposal_id) for proposal_id in proposal_ids]
        request_id_strings = [str(request_id) for request_id in request_ids]
        transfer_ids = list(
            TransferRecord.objects.filter(
                proposal_id__in=proposal_ids,
            ).values_list('transfer_id', flat=True)
        )
        AuditEntry.objects.filter(
            Q(
                object_type='ExchangeProposal',
                object_id__in=proposal_id_strings,
            ) | Q(
                object_type='ExchangeRequest',
                object_id__in=request_id_strings,
            )
        ).delete()

        TransferRecord.objects.filter(proposal_id__in=proposal_ids).delete()
        Approval.objects.filter(proposal_id__in=proposal_ids).delete()
        ProposalParticipant.objects.filter(proposal_id__in=proposal_ids).delete()
        ExchangeProposal.objects.filter(pk__in=proposal_ids).delete()
        ExchangeRequest.objects.filter(requester_id__in=profile_ids).delete()

        assigned_at = timezone.now() - timedelta(days=policy.cooling_off_days + 1)
        for profile, student_spec in zip(students, student_specs):
            block_name, room_number = student_spec[3], student_spec[4]
            profile.current_room = Room.objects.get(
                block=blocks[block_name],
                room_number=room_number,
            )
            profile.save(update_fields=('current_room',))
            StudentProfile.objects.filter(pk=profile.pk).update(
                current_room_assigned_at=assigned_at,
            )
            profile.refresh_from_db()

        AuditEntry.objects.create(
            action='demo.sample_workflow_reset',
            object_type='SampleWorkflow',
            object_id=timezone.now().strftime('%Y%m%d%H%M%S%f'),
            details={
                'students': [student.user.username for student in students],
                'removed_request_ids': request_ids,
                'removed_proposal_ids': proposal_ids,
                'removed_transfer_ids': [str(transfer_id) for transfer_id in transfer_ids],
            },
        )
