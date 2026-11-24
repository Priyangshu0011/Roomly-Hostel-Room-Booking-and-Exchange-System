from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from accounts.models import StudentProfile
from rooms.models import HostelBlock, Room


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
