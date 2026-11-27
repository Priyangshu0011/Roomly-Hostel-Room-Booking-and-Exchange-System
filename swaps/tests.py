from datetime import timedelta
from io import StringIO
import json
import threading

from django.contrib.auth.models import Group, Permission, User
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import close_old_connections
from django.core.management import call_command
from django.test import Client, TestCase, TransactionTestCase, skipUnlessDBFeature
from django.urls import reverse
from django.utils.formats import date_format
from django.utils import timezone

from accounts.models import StudentProfile, WardenAssignment
from rooms.models import HostelBlock, Room
from swaps.models import (
    AuditEntry,
    ExchangeProposal,
    ExchangeRequest,
    PolicyRule,
    TransferRecord,
)
from swaps.services import (
    approve_proposal,
    create_exchange_request,
    create_matching_proposals,
    respond_to_proposal,
)


class ExchangeWorkflowTests(TestCase):
    def setUp(self):
        self.block = HostelBlock.objects.create(
            name='North House',
            gender_restriction=HostelBlock.GenderRestriction.COED,
        )
        self.rooms = [
            Room.objects.create(
                block=self.block,
                room_number=str(100 + index),
                capacity=1,
                is_verified=True,
            )
            for index in range(1, 4)
        ]
        self.students = []
        for index, room in enumerate(self.rooms, start=1):
            user = User.objects.create_user(
                username=f'student{index}',
                password='SafeTestPassword123!',
            )
            profile = StudentProfile.objects.create(
                user=user,
                current_room=room,
                gender=StudentProfile.Gender.MALE,
            )
            StudentProfile.objects.filter(pk=profile.pk).update(
                current_room_assigned_at=timezone.now() - timedelta(days=60),
            )
            profile.refresh_from_db()
            self.students.append(profile)
        PolicyRule.objects.create(
            cooling_off_days=30,
            request_validity_days=30,
            proposal_validity_hours=48,
            max_chain_length=5,
        )
        self.requests = [
            create_exchange_request(
                profile.pk,
                [self.rooms[(index + 1) % len(self.rooms)]],
                reason='Room exchange test',
                actor=profile.user,
            )
            for index, profile in enumerate(self.students)
        ]
        proposals = create_matching_proposals()
        self.assertEqual(len(proposals), 1)
        self.proposal = proposals[0]

    def accept_all(self):
        for profile in self.students:
            respond_to_proposal(
                self.proposal.pk,
                profile.pk,
                True,
                profile.user,
            )
        self.proposal.refresh_from_db()
        self.assertEqual(self.proposal.status, ExchangeProposal.Status.AWAITING_APPROVAL)

    def test_partial_acceptance_never_changes_room_occupancy(self):
        respond_to_proposal(
            self.proposal.pk,
            self.students[0].pk,
            True,
            self.students[0].user,
        )
        self.proposal.refresh_from_db()
        self.assertEqual(self.proposal.status, ExchangeProposal.Status.AWAITING_ACCEPTANCE)

        with self.assertRaises(ValidationError):
            approve_proposal(
                self.proposal.pk,
                User.objects.create_superuser('supervisor', 'warden@example.edu', 'SafeTestPassword123!'),
                'APPROVED',
                'Premature approval attempt',
            )

        for profile, room in zip(self.students, self.rooms):
            profile.refresh_from_db()
            self.assertEqual(profile.current_room_id, room.pk)
        self.assertFalse(TransferRecord.objects.exists())

    def test_inactive_request_error_identifies_student_and_individual_expiry(self):
        self.accept_all()
        inactive_request = self.requests[1]
        inactive_request.status = ExchangeRequest.Status.EXPIRED
        inactive_request.expires_at = timezone.now() - timedelta(hours=1)
        inactive_request.save(update_fields=('status', 'expires_at'))
        approver = User.objects.create_superuser(
            'expired_request_supervisor',
            'expired@example.edu',
            'SafeTestPassword123!',
        )

        with self.assertRaises(ValidationError) as error:
            approve_proposal(
                self.proposal.pk,
                approver,
                'APPROVED',
                'Check the request expiry.',
            )

        message = str(error.exception)
        self.assertIn(f'Request #{inactive_request.pk}', message)
        self.assertIn(self.students[1].user.username, message)
        self.assertIn('has status "Expired" instead of "In a proposal"', message)
        self.assertIn(
            timezone.localtime(inactive_request.expires_at).strftime('%Y-%m-%d %H:%M %Z'),
            message,
        )

    def test_three_party_cycle_requires_all_acceptances_and_creates_transfer_record(self):
        self.assertEqual(self.proposal.cycle_length, 3)
        self.assertEqual(
            set(self.proposal.participants.values_list('to_room_id', flat=True)),
            {room.pk for room in self.rooms},
        )
        self.accept_all()

        approver = User.objects.create_superuser(
            'supervisor',
            'warden@example.edu',
            'SafeTestPassword123!',
        )
        proposal = approve_proposal(
            self.proposal.pk,
            approver,
            'APPROVED',
            'All residents accepted; room availability checked.',
        )

        self.assertEqual(proposal.status, ExchangeProposal.Status.EXECUTED)
        self.assertEqual(TransferRecord.objects.count(), 1)
        self.assertEqual(AuditEntry.objects.filter(action='transfer.completed').count(), 1)
        for index, profile in enumerate(self.students):
            profile.refresh_from_db()
            self.assertEqual(profile.current_room_id, self.rooms[(index + 1) % 3].pk)
        for room in self.rooms:
            self.assertEqual(room.occupants.count(), 1)

    def test_four_party_rotation_is_proposed_accepted_and_executed(self):
        rooms = [
            Room.objects.create(
                block=self.block,
                room_number=str(400 + index),
                capacity=1,
                is_verified=True,
            )
            for index in range(1, 5)
        ]
        profiles = []
        for index, room in enumerate(rooms, start=1):
            user = User.objects.create_user(username=f'four_party_student{index}')
            profile = StudentProfile.objects.create(
                user=user,
                current_room=room,
                gender=StudentProfile.Gender.MALE,
            )
            StudentProfile.objects.filter(pk=profile.pk).update(
                current_room_assigned_at=timezone.now() - timedelta(days=60),
            )
            profile.refresh_from_db()
            profiles.append(profile)
        for index, profile in enumerate(profiles):
            create_exchange_request(
                profile.pk,
                [rooms[(index + 1) % 4]],
                actor=profile.user,
            )

        proposals = create_matching_proposals()
        four_party_proposal = next(proposal for proposal in proposals if proposal.cycle_length == 4)
        for profile in profiles:
            respond_to_proposal(
                four_party_proposal.pk,
                profile.pk,
                True,
                profile.user,
            )
        approver = User.objects.create_superuser(
            'four_party_supervisor',
            'fourparty@example.edu',
            'SafeTestPassword123!',
        )
        approve_proposal(
            four_party_proposal.pk,
            approver,
            'APPROVED',
            'Four residents accepted the rotation.',
        )

        for index, profile in enumerate(profiles):
            profile.refresh_from_db()
            self.assertEqual(profile.current_room_id, rooms[(index + 1) % 4].pk)
        self.assertEqual(TransferRecord.objects.filter(proposal=four_party_proposal).count(), 1)

    def test_declined_proposal_releases_other_requests_without_transfer(self):
        respond_to_proposal(
            self.proposal.pk,
            self.students[1].pk,
            False,
            self.students[1].user,
        )
        self.proposal.refresh_from_db()

        self.assertEqual(self.proposal.status, ExchangeProposal.Status.DECLINED)
        self.assertFalse(TransferRecord.objects.exists())
        for index, profile in enumerate(self.students):
            profile.refresh_from_db()
            self.assertEqual(profile.current_room_id, self.rooms[index].pk)
        self.assertEqual(
            ExchangeRequest.objects.filter(status=ExchangeRequest.Status.OPEN).count(),
            2,
        )

    def test_expired_proposal_is_persisted_when_a_resident_responds(self):
        ExchangeProposal.objects.filter(pk=self.proposal.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1),
        )

        with self.assertRaisesMessage(ValidationError, 'This proposal has expired.'):
            respond_to_proposal(
                self.proposal.pk,
                self.students[0].pk,
                True,
                self.students[0].user,
            )

        self.proposal.refresh_from_db()
        self.assertEqual(self.proposal.status, ExchangeProposal.Status.EXPIRED)
        self.assertFalse(TransferRecord.objects.exists())

    def test_student_request_and_proposal_pages_render(self):
        client = Client()
        client.force_login(self.students[0].user)

        list_response = client.get(reverse('swaps:list'))
        self.assertEqual(list_response.status_code, 200)
        self.assertContains(list_response, 'My exchange requests')
        detail_response = client.get(
            reverse('swaps:request_detail', kwargs={'request_id': self.requests[0].pk})
        )
        self.assertEqual(detail_response.status_code, 200)
        proposals_response = client.get(reverse('swaps:proposals'))
        self.assertEqual(proposals_response.status_code, 200)
        self.assertContains(proposals_response, f'Proposal #{self.proposal.pk}')
        self.assertEqual(client.get(reverse('swaps:transfers')).status_code, 200)

    def test_request_form_persists_preferences_and_withdraws(self):
        room = Room.objects.create(
            block=self.block,
            room_number='104',
            capacity=1,
            is_verified=True,
        )
        user = User.objects.create_user(username='new_requester')
        profile = StudentProfile.objects.create(
            user=user,
            current_room=room,
            gender=StudentProfile.Gender.MALE,
        )
        StudentProfile.objects.filter(pk=profile.pk).update(
            current_room_assigned_at=timezone.now() - timedelta(days=60),
        )
        client = Client()
        client.force_login(user)

        response = client.post(reverse('swaps:create'), {
            'preferred_rooms': [self.rooms[0].pk, self.rooms[1].pk],
            'reason': 'Closer to classes',
        })

        exchange_request = ExchangeRequest.objects.get(requester=profile)
        self.assertRedirects(
            response,
            reverse('swaps:request_detail', kwargs={'request_id': exchange_request.pk}),
        )
        self.assertEqual(exchange_request.preferences.count(), 2)
        response = client.post(reverse('swaps:withdraw', kwargs={'request_id': exchange_request.pk}))
        self.assertRedirects(response, reverse('swaps:list'))
        exchange_request.refresh_from_db()
        self.assertEqual(exchange_request.status, ExchangeRequest.Status.WITHDRAWN)

    def test_student_cannot_access_warden_approval_queue(self):
        client = Client()
        response = client.get(reverse('swaps:approval_queue'))
        self.assertEqual(response.status_code, 302)

        client.force_login(self.students[0].user)
        response = client.get(reverse('swaps:approval_queue'))
        self.assertEqual(response.status_code, 403)

    def test_json_api_authenticates_students_and_creates_requests(self):
        client = Client()
        self.assertEqual(
            client.get(reverse('swaps_api:exchange_requests')).status_code,
            401,
        )
        room = Room.objects.create(
            block=self.block,
            room_number='105',
            capacity=1,
            is_verified=True,
        )
        user = User.objects.create_user(username='api_student')
        profile = StudentProfile.objects.create(
            user=user,
            current_room=room,
            gender=StudentProfile.Gender.MALE,
        )
        StudentProfile.objects.filter(pk=profile.pk).update(
            current_room_assigned_at=timezone.now() - timedelta(days=60),
        )
        client.force_login(user)

        response = client.post(
            reverse('swaps_api:exchange_requests'),
            data=json.dumps({
                'preferred_room_ids': [self.rooms[0].pk],
                'reason': 'Request through the API',
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()['request']['status'], ExchangeRequest.Status.OPEN)
        list_response = client.get(reverse('swaps_api:exchange_requests'))
        self.assertEqual(len(list_response.json()['results']), 1)
        client.force_login(self.students[0].user)
        proposal_list = client.get(reverse('swaps_api:proposals'))
        self.assertEqual(len(proposal_list.json()['results']), 1)
        response = client.post(
            reverse('swaps_api:respond', kwargs={'proposal_id': self.proposal.pk}),
            data=json.dumps({'decision': 'accept'}),
            content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)

    def test_staff_approval_and_analytics_pages_render(self):
        self.accept_all()
        approver = User.objects.create_superuser(
            'reporting_supervisor',
            'reporting@example.edu',
            'SafeTestPassword123!',
        )
        client = Client()
        client.force_login(approver)

        queue_response = client.get(reverse('swaps:approval_queue'))
        self.assertEqual(queue_response.status_code, 200)
        self.assertContains(queue_response, f'Proposal #{self.proposal.pk}')
        for exchange_request in self.requests:
            self.assertContains(queue_response, f'Request #{exchange_request.pk}')
            self.assertContains(
                queue_response,
                date_format(
                    timezone.localtime(exchange_request.expires_at),
                    'M j, Y, g:i a',
                ),
            )
        approval_response = client.post(
            reverse('swaps:approve', kwargs={'proposal_id': self.proposal.pk}),
            {
                'decision': 'APPROVED',
                'remarks': 'All residents accepted the exchange.',
            },
        )
        self.assertRedirects(approval_response, reverse('swaps:approval_queue'))
        report_response = client.get(reverse('swaps:analytics'))
        self.assertEqual(report_response.status_code, 200)
        self.assertContains(report_response, 'Requests by hostel block')
        transfer = TransferRecord.objects.get(proposal=self.proposal)
        client.force_login(self.students[0].user)
        detail_response = client.get(
            reverse('swaps:transfer_detail', kwargs={'transfer_id': transfer.transfer_id})
        )
        self.assertEqual(detail_response.status_code, 200)
        self.assertContains(detail_response, str(transfer.transfer_id))
        api_transfers = client.get(reverse('swaps_api:transfers'))
        self.assertEqual(len(api_transfers.json()['results']), 1)
        api_transfer = client.get(
            reverse('swaps_api:transfer_detail', kwargs={'transfer_id': transfer.transfer_id})
        )
        self.assertEqual(api_transfer.status_code, 200)

    def test_api_warden_approval_updates_every_room(self):
        self.accept_all()
        approver = User.objects.create_superuser(
            'api_supervisor',
            'api-supervisor@example.edu',
            'SafeTestPassword123!',
        )
        client = Client()
        client.force_login(approver)

        response = client.post(
            reverse('swaps_api:approve', kwargs={'proposal_id': self.proposal.pk}),
            data=json.dumps({
                'decision': 'APPROVED',
                'remarks': 'Approved through the JSON API.',
            }),
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn('transfer_id', response.json())
        for index, profile in enumerate(self.students):
            profile.refresh_from_db()
            self.assertEqual(profile.current_room_id, self.rooms[(index + 1) % 3].pk)

    def test_warden_approval_requires_an_assignment_to_every_participant_block(self):
        self.accept_all()
        wardens = Group.objects.create(name='Wardens')
        permission = Permission.objects.get(
            content_type__app_label='swaps',
            codename='review_exchangeproposal',
        )
        wardens.permissions.add(permission)
        warden = User.objects.create_user(username='unassigned_warden')
        warden.groups.add(wardens)

        with self.assertRaises(PermissionDenied):
            approve_proposal(
                self.proposal.pk,
                warden,
                'APPROVED',
                'Should not be permitted.',
            )

        WardenAssignment.objects.create(user=warden, block=self.block)
        proposal = approve_proposal(
            self.proposal.pk,
            warden,
            'APPROVED',
            'Assigned to this hostel block.',
        )
        self.assertEqual(proposal.status, ExchangeProposal.Status.EXECUTED)


class RolePermissionTests(TestCase):
    def test_standard_roles_are_scoped_to_their_p04_responsibilities(self):
        call_command('setup_roles', stdout=StringIO())

        warden_permissions = set(
            Group.objects.get(name='Wardens').permissions.values_list('codename', flat=True)
        )
        admin_permissions = set(
            Group.objects.get(name='Hostel Administrators').permissions.values_list(
                'codename',
                flat=True,
            )
        )
        dean_permissions = set(
            Group.objects.get(name='Student Welfare Deans').permissions.values_list(
                'codename',
                flat=True,
            )
        )

        self.assertIn('review_exchangeproposal', warden_permissions)
        self.assertNotIn('view_exchangerequest', warden_permissions)
        self.assertIn('manage_room_inventory', admin_permissions)
        self.assertIn('manage_policyrule', admin_permissions)
        self.assertEqual(dean_permissions, {'view_exchangeanalytics'})


class MatchingEngineTests(TestCase):
    def test_creates_distinct_cycles_that_share_a_room(self):
        block = HostelBlock.objects.create(name='Shared Room House')
        room_a = Room.objects.create(block=block, room_number='301', capacity=1, is_verified=True)
        room_b = Room.objects.create(block=block, room_number='302', capacity=2, is_verified=True)
        room_c = Room.objects.create(block=block, room_number='303', capacity=1, is_verified=True)
        allocations = (
            (room_a, room_b),
            (room_b, room_a),
            (room_b, room_c),
            (room_c, room_b),
        )
        PolicyRule.objects.create(cooling_off_days=30, proposal_validity_hours=48)

        for index, (room, target_room) in enumerate(allocations):
            user = User.objects.create_user(username=f'shared_room_student{index}')
            profile = StudentProfile.objects.create(
                user=user,
                current_room=room,
                gender=StudentProfile.Gender.MALE,
            )
            StudentProfile.objects.filter(pk=profile.pk).update(
                current_room_assigned_at=timezone.now() - timedelta(days=60),
            )
            create_exchange_request(profile.pk, [target_room], actor=user)

        proposals = create_matching_proposals()

        self.assertEqual(len(proposals), 2)
        first_rooms = {
            room_id
            for participant in proposals[0].participants.all()
            for room_id in (participant.from_room_id, participant.to_room_id)
        }
        second_rooms = {
            room_id
            for participant in proposals[1].participants.all()
            for room_id in (participant.from_room_id, participant.to_room_id)
        }
        self.assertEqual(first_rooms.intersection(second_rooms), {room_b.pk})


class CrossBlockApprovalTests(TestCase):
    def test_only_chief_warden_can_approve_a_cross_block_rotation(self):
        block_a = HostelBlock.objects.create(name='West House')
        block_b = HostelBlock.objects.create(name='East House')
        room_a = Room.objects.create(block=block_a, room_number='401', capacity=1, is_verified=True)
        room_b = Room.objects.create(block=block_b, room_number='402', capacity=1, is_verified=True)
        profiles = []
        for index, room in enumerate((room_a, room_b)):
            user = User.objects.create_user(username=f'cross_block_student{index}')
            profile = StudentProfile.objects.create(
                user=user,
                current_room=room,
                gender=StudentProfile.Gender.MALE,
            )
            StudentProfile.objects.filter(pk=profile.pk).update(
                current_room_assigned_at=timezone.now() - timedelta(days=60),
            )
            profile.refresh_from_db()
            profiles.append(profile)
        PolicyRule.objects.create(cooling_off_days=30, proposal_validity_hours=48)
        create_exchange_request(profiles[0].pk, [room_b], actor=profiles[0].user)
        create_exchange_request(profiles[1].pk, [room_a], actor=profiles[1].user)
        proposal = create_matching_proposals()[0]
        for profile in profiles:
            respond_to_proposal(proposal.pk, profile.pk, True, profile.user)

        wardens = Group.objects.create(name='Wardens')
        review_permission = Permission.objects.get(
            content_type__app_label='swaps',
            codename='review_exchangeproposal',
        )
        wardens.permissions.add(review_permission)
        warden = User.objects.create_user(username='cross_block_warden')
        warden.groups.add(wardens)
        WardenAssignment.objects.create(user=warden, block=block_a)
        WardenAssignment.objects.create(user=warden, block=block_b)

        with self.assertRaises(PermissionDenied):
            approve_proposal(
                proposal.pk,
                warden,
                'APPROVED',
                'A warden cannot approve a cross-block exchange.',
            )

        chief_wardens = Group.objects.create(name='Chief Wardens')
        chief = User.objects.create_user(username='chief_warden')
        chief.groups.add(chief_wardens)
        approved = approve_proposal(
            proposal.pk,
            chief,
            'APPROVED',
            'Cross-block exchange reviewed by the Chief Warden.',
        )
        self.assertEqual(approved.status, ExchangeProposal.Status.EXECUTED)


@skipUnlessDBFeature('has_select_for_update')
class ConcurrentApprovalTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.block = HostelBlock.objects.create(name='Concurrency House')
        room_a = Room.objects.create(block=self.block, room_number='201', capacity=1, is_verified=True)
        room_b = Room.objects.create(block=self.block, room_number='202', capacity=2, is_verified=True)
        room_c = Room.objects.create(block=self.block, room_number='203', capacity=1, is_verified=True)
        self.rooms = (room_a, room_b, room_c)
        allocations = (
            ('overlap_a', room_a, room_b),
            ('overlap_b', room_b, room_a),
            ('overlap_c', room_b, room_c),
            ('overlap_d', room_c, room_b),
        )
        self.students = []
        for username, room, target_room in allocations:
            user = User.objects.create_user(username=username)
            profile = StudentProfile.objects.create(
                user=user,
                current_room=room,
                gender=StudentProfile.Gender.MALE,
            )
            StudentProfile.objects.filter(pk=profile.pk).update(
                current_room_assigned_at=timezone.now() - timedelta(days=60),
            )
            profile.refresh_from_db()
            self.students.append(profile)
        PolicyRule.objects.create(cooling_off_days=30, proposal_validity_hours=48)
        for profile, (_, _, target_room) in zip(self.students, allocations):
            create_exchange_request(
                profile.pk,
                [target_room],
                actor=profile.user,
            )
        proposals = create_matching_proposals()
        self.assertEqual(len(proposals), 2)
        self.proposal_ids = [proposal.pk for proposal in proposals]
        for proposal in proposals:
            for participant in proposal.participants.select_related('student__user'):
                respond_to_proposal(
                    proposal.pk,
                    participant.student_id,
                    True,
                    participant.student.user,
                )
        participant_rooms = [
            set(
                room_id
                for participant in proposal.participants.all()
                for room_id in (participant.from_room_id, participant.to_room_id)
            )
            for proposal in proposals
        ]
        self.assertEqual(participant_rooms[0].intersection(participant_rooms[1]), {room_b.pk})

        wardens = Group.objects.create(name='Wardens')
        permission = Permission.objects.get(
            content_type__app_label='swaps',
            codename='review_exchangeproposal',
        )
        wardens.permissions.add(permission)
        self.approvers = []
        for index in range(2):
            user = User.objects.create_user(username=f'warden{index}')
            user.groups.add(wardens)
            WardenAssignment.objects.create(user=user, block=self.block)
            self.approvers.append(user.pk)

    def test_concurrent_overlapping_chains_keep_room_occupancy_consistent(self):
        barrier = threading.Barrier(2)
        outcomes = []
        outcomes_lock = threading.Lock()

        def approve(approver_id, proposal_id):
            close_old_connections()
            try:
                approver = User.objects.get(pk=approver_id)
                barrier.wait(timeout=10)
                approve_proposal(
                    proposal_id,
                    approver,
                    'APPROVED',
                    'Concurrent approval test.',
                )
                outcome = 'approved'
            except ValidationError:
                outcome = 'already resolved'
            except Exception as error:
                outcome = f'error: {type(error).__name__}: {error}'
            finally:
                close_old_connections()
            with outcomes_lock:
                outcomes.append(outcome)

        threads = [
            threading.Thread(target=approve, args=(approver_id, proposal_id))
            for approver_id, proposal_id in zip(self.approvers, self.proposal_ids)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertCountEqual(outcomes, ['approved', 'approved'])
        self.assertEqual(TransferRecord.objects.count(), 2)
        expected_rooms = (self.rooms[1], self.rooms[0], self.rooms[2], self.rooms[1])
        for profile, expected_room in zip(self.students, expected_rooms):
            profile.refresh_from_db()
            self.assertEqual(profile.current_room_id, expected_room.pk)
        self.assertEqual(self.rooms[0].occupants.count(), 1)
        self.assertEqual(self.rooms[1].occupants.count(), 2)
        self.assertEqual(self.rooms[2].occupants.count(), 1)
