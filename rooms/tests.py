from io import StringIO

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from accounts.models import StudentProfile, WardenAssignment
from rooms.models import HostelBlock, Room
from swaps.models import (
    Approval,
    AuditEntry,
    ExchangeProposal,
    ExchangeRequest,
    ProposalParticipant,
    TransferRecord,
)
from swaps.services import approve_proposal


class RoomDirectoryTests(TestCase):
    def setUp(self):
        self.block = HostelBlock.objects.create(
            name='North House',
            gender_restriction=HostelBlock.GenderRestriction.COED,
        )
        self.available_room = Room.objects.create(
            block=self.block,
            room_number='101',
            capacity=2,
            is_verified=True,
        )
        self.full_room = Room.objects.create(
            block=self.block,
            room_number='102',
            capacity=1,
            is_verified=True,
        )
        resident = User.objects.create_user(
            username='resident',
            email='resident@example.com',
            first_name='Private',
            last_name='Resident',
        )
        StudentProfile.objects.create(user=resident, current_room=self.full_room)

    def test_available_filter_uses_room_occupancy(self):
        response = self.client.get(reverse('rooms:room_list'), {'available': '1'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context['rooms']), [self.available_room])
        self.assertEqual(response.context['available_rooms_count'], 1)

    def test_capacity_filter_only_returns_matching_rooms(self):
        response = self.client.get(reverse('rooms:room_list'), {'capacity': '2'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context['rooms']), [self.available_room])
        self.assertEqual(response.context['selected_capacity_int'], 2)

    def test_public_room_detail_does_not_expose_resident_personal_details(self):
        response = self.client.get(
            reverse('rooms:room_detail', kwargs={'room_id': self.full_room.id})
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, '1 / 1 residents')
        self.assertNotContains(response, 'resident@example.com')
        self.assertNotContains(response, 'Private Resident')

    def test_room_directory_and_health_check_render(self):
        self.assertEqual(self.client.get('/').status_code, 302)
        self.assertEqual(self.client.get('/healthz/').json(), {'status': 'ok'})
        response = self.client.get(reverse('rooms:room_list'))
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Project guide')

    def test_unverified_room_is_hidden_from_students(self):
        self.available_room.is_verified = False
        self.available_room.save()

        response = self.client.get(reverse('rooms:room_list'))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'Room 101')
        self.assertEqual(list(response.context['rooms']), [self.full_room])

    def test_inventory_operations_are_staff_only(self):
        response = self.client.get(reverse('rooms:operations'))
        self.assertEqual(response.status_code, 302)

        student = User.objects.create_user(username='ordinary_student')
        self.client.force_login(student)
        self.assertEqual(self.client.get(reverse('rooms:operations')).status_code, 403)

        administrator = User.objects.create_superuser(
            username='hostel_admin',
            email='admin@example.edu',
            password='SafeTestPassword123!',
        )
        self.client.force_login(administrator)
        response = self.client.get(reverse('rooms:operations'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Verify rooms')


class P04SampleDataCommandTests(TestCase):
    def test_seeded_proposals_use_matched_requests_and_are_idempotent(self):
        call_command('seed_p04_data', stdout=StringIO())

        proposals = list(ExchangeProposal.objects.order_by('cycle_length'))
        self.assertEqual([proposal.cycle_length for proposal in proposals], [2, 3])
        self.assertEqual(
            {proposal.status for proposal in proposals},
            {
                ExchangeProposal.Status.AWAITING_ACCEPTANCE,
                ExchangeProposal.Status.AWAITING_APPROVAL,
            },
        )
        self.assertEqual(ExchangeRequest.objects.count(), 5)
        self.assertEqual(
            ExchangeRequest.objects.filter(status=ExchangeRequest.Status.MATCHED).count(),
            5,
        )
        self.assertEqual(WardenAssignment.objects.count(), 5)

        for proposal in proposals:
            participants = list(
                ProposalParticipant.objects.filter(proposal=proposal).select_related(
                    'exchange_request',
                )
            )
            self.assertEqual(len(participants), proposal.cycle_length)
            for participant in participants:
                self.assertEqual(
                    participant.exchange_request.status,
                    ExchangeRequest.Status.MATCHED,
                )
                self.assertTrue(
                    participant.exchange_request.preferences.filter(
                        room_id=participant.to_room_id,
                    ).exists(),
                )
            if proposal.status == ExchangeProposal.Status.AWAITING_APPROVAL:
                self.assertTrue(all(
                    participant.response == ProposalParticipant.Response.ACCEPTED
                    for participant in participants
                ))

        call_command('seed_p04_data', stdout=StringIO())
        self.assertEqual(ExchangeProposal.objects.count(), 2)
        self.assertEqual(ExchangeRequest.objects.count(), 5)

    def test_reset_sample_workflow_reseeds_students_without_warden_approval(self):
        call_command('seed_p04_data', stdout=StringIO())
        approved_proposal = ExchangeProposal.objects.get(
            status=ExchangeProposal.Status.AWAITING_APPROVAL,
        )
        approver = User.objects.create_superuser(
            username='sample_warden',
            email='sample.warden@example.edu',
            password='SafeTestPassword123!',
        )
        approve_proposal(
            approved_proposal.pk,
            approver,
            Approval.Decision.APPROVED,
            'Sample approval before reset.',
        )
        old_request_ids = set(ExchangeRequest.objects.values_list('pk', flat=True))

        call_command(
            'seed_p04_data',
            reset_sample_workflow=True,
            stdout=StringIO(),
        )

        self.assertEqual(ExchangeRequest.objects.count(), 5)
        self.assertTrue(old_request_ids.isdisjoint(
            ExchangeRequest.objects.values_list('pk', flat=True),
        ))
        self.assertEqual(Approval.objects.count(), 0)
        self.assertEqual(TransferRecord.objects.count(), 0)
        self.assertEqual(
            ExchangeRequest.objects.filter(status=ExchangeRequest.Status.MATCHED).count(),
            5,
        )
        self.assertEqual(
            set(ExchangeProposal.objects.values_list('status', flat=True)),
            {
                ExchangeProposal.Status.AWAITING_ACCEPTANCE,
                ExchangeProposal.Status.AWAITING_APPROVAL,
            },
        )
        self.assertEqual(AuditEntry.objects.filter(action='demo.sample_workflow_reset').count(), 1)

        expected_rooms = {
            'aarav': ('North House', '101'),
            'isha': ('South House', '101'),
            'kabir': ('East House', '101'),
            'meera': ('West House', '101'),
            'rohan': ('Central House', '101'),
        }
        for username, (block_name, room_number) in expected_rooms.items():
            profile = StudentProfile.objects.select_related(
                'user',
                'current_room__block',
            ).get(user__username=username)
            self.assertEqual(profile.current_room.block.name, block_name)
            self.assertEqual(profile.current_room.room_number, room_number)
