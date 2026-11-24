from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import Group, Permission, User
from accounts.models import StudentProfile
from accounts.forms import StudentRegistrationForm
from swaps.permissions import STUDENT_GROUP

# Neutral mock fixture value satisfying Django's AUTH_PASSWORD_VALIDATORS
dummy_test_pass = 'MockFixturePass2026!'  # ggignore


class AccountsModelAndFormTests(TestCase):
    def test_student_profile_auto_creation_via_form(self):
        """Test StudentProfile is automatically created when StudentRegistrationForm saves."""
        form_data = {
            'username': 'john_doe',
            'first_name': 'John',
            'last_name': 'Doe',
            'email': 'john@example.com',
            'gender': StudentProfile.Gender.MALE,
            'password1': dummy_test_pass,
            'password2': dummy_test_pass,
        }
        form = StudentRegistrationForm(data=form_data)
        self.assertTrue(form.is_valid(), form.errors)
        user = form.save()

        self.assertIsNotNone(user.pk)
        self.assertTrue(StudentProfile.objects.filter(user=user).exists())
        profile = StudentProfile.objects.get(user=user)
        self.assertEqual(profile.user.email, 'john@example.com')
        self.assertEqual(profile.gender, StudentProfile.Gender.MALE)
        self.assertIsNone(profile.current_room)
        self.assertIn('john_doe', str(profile))


class AccountsAuthenticationAndAccessTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.password = dummy_test_pass
        self.user = User.objects.create_user(
            username='student1',
            email='student1@hostel.edu',
            password=self.password,
            first_name='Alex',
            last_name='Smith',
        )
        self.profile = StudentProfile.objects.create(user=self.user)
        self.login_url = reverse('accounts:login')
        self.logout_url = reverse('accounts:logout')
        self.dashboard_url = reverse('accounts:dashboard')

    def test_login_required_redirects_unauthenticated_user(self):
        """Unauthenticated user accessing protected dashboard must be redirected to login."""
        response = self.client.get(self.dashboard_url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(self.login_url, response.url)

    def test_login_successful_and_redirects(self):
        """Valid credentials log in the user and redirect to dashboard."""
        response = self.client.post(self.login_url, {
            'username': 'student1',
            'password': self.password,
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['user'].is_authenticated)
        self.assertEqual(response.context['user'].username, 'student1')

    def test_registration_assigns_student_role(self):
        response = self.client.post(reverse('accounts:register'), {
            'username': 'new_student',
            'first_name': 'New',
            'last_name': 'Student',
            'email': 'new.student@example.edu',
            'gender': StudentProfile.Gender.FEMALE,
            'password1': self.password,
            'password2': self.password,
        })

        self.assertEqual(response.status_code, 302)
        registered_user = User.objects.get(username='new_student')
        self.assertTrue(registered_user.groups.filter(name=STUDENT_GROUP).exists())

    def test_logout_workflow(self):
        """Logging out terminates the session and redirects to login."""
        self.client.login(username='student1', password=self.password)
        self.assertEqual(self.client.get(self.logout_url).status_code, 405)
        response = self.client.post(self.logout_url, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context['user'].is_authenticated)

    def test_staff_navigation_only_shows_role_relevant_links(self):
        approval_permission = Permission.objects.get(
            content_type__app_label='swaps',
            codename='review_exchangeproposal',
        )
        analytics_permission = Permission.objects.get(
            content_type__app_label='swaps',
            codename='view_exchangeanalytics',
        )
        inventory_permission = Permission.objects.get(
            content_type__app_label='rooms',
            codename='manage_room_inventory',
        )

        for username, permission, visible_link in (
            ('warden_nav', approval_permission, '>Approvals</a>'),
            ('chief_warden_nav', approval_permission, '>Approvals</a>'),
            ('administrator_nav', inventory_permission, '>Operations</a>'),
            ('dean_nav', analytics_permission, '>Reports</a>'),
        ):
            with self.subTest(role=username):
                staff_user = User.objects.create_user(username=username, password=self.password)
                staff_user.user_permissions.add(permission)
                self.client.force_login(staff_user)

                response = self.client.get(reverse('rooms:room_list'))

                self.assertEqual(response.status_code, 200)
                self.assertContains(response, visible_link)
                self.assertContains(response, '>My dashboard</a>')
                for student_link in (
                    '>Find a room</a>',
                    '>My exchanges</a>',
                    '>Proposals</a>',
                ):
                    self.assertNotContains(response, student_link)

                self.client.logout()

    def test_student_navigation_keeps_room_and_exchange_links(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse('rooms:room_list'))

        self.assertContains(response, '>Find a room</a>')
        self.assertContains(response, '>My dashboard</a>')
        self.assertContains(response, '>My exchanges</a>')
        self.assertContains(response, '>Proposals</a>')


class StaffDashboardTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.password = dummy_test_pass

    def _create_staff_user(self, username, group_name, permission):
        user = User.objects.create_user(username=username, password=self.password)
        group, _ = Group.objects.get_or_create(name=group_name)
        user.groups.add(group)
        user.user_permissions.add(permission)
        self.client.force_login(user)
        return user

    def test_warden_dashboard_shows_scoped_approval_workflow(self):
        permission = Permission.objects.get(
            content_type__app_label='swaps',
            codename='review_exchangeproposal',
        )
        user = self._create_staff_user('warden_dashboard', 'Wardens', permission)

        response = self.client.get(reverse('accounts:dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Warden dashboard')
        self.assertContains(response, 'Proposals needing your review')
        self.assertContains(response, '>My dashboard</a>')
        self.assertNotContains(response, 'Your allocation')
        self.assertFalse(StudentProfile.objects.filter(user=user).exists())

    def test_administrator_dashboard_shows_inventory_workflow(self):
        permission = Permission.objects.get(
            content_type__app_label='rooms',
            codename='manage_room_inventory',
        )
        user = self._create_staff_user(
            'admin_dashboard',
            'Hostel Administrators',
            permission,
        )

        response = self.client.get(reverse('accounts:dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Hostel administrator dashboard')
        self.assertContains(response, 'Keep room data ready')
        self.assertContains(response, 'Warden assignments')
        self.assertFalse(StudentProfile.objects.filter(user=user).exists())

    def test_dean_dashboard_shows_read_only_analytics(self):
        permission = Permission.objects.get(
            content_type__app_label='swaps',
            codename='view_exchangeanalytics',
        )
        user = self._create_staff_user(
            'dean_dashboard',
            'Student Welfare Deans',
            permission,
        )

        response = self.client.get(reverse('accounts:dashboard'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Student Welfare Dean dashboard')
        self.assertContains(response, 'Institution-wide view')
        self.assertContains(response, 'Open exchange reports')
        self.assertFalse(StudentProfile.objects.filter(user=user).exists())


class AccountsDashboardTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.password = dummy_test_pass
        self.user = User.objects.create_user(
            username='session_student',
            email='session@hostel.edu',
            password=self.password,
        )
        self.profile = StudentProfile.objects.create(user=self.user)
        self.client.login(username='session_student', password=self.password)

    def test_dashboard_shows_exchange_workflow_without_teaching_diagnostics(self):
        url = reverse('accounts:dashboard')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Proposal inbox')
        self.assertNotContains(response, 'Session and cookie learning demo')
        self.assertNotContains(response, 'Dashboard visits:')

    def test_login_rejects_external_next_url(self):
        self.client.logout()
        dashboard_url = reverse('accounts:dashboard')
        response = self.client.post(reverse('accounts:login') + '?next=https://evil.example/', {
            'username': 'session_student',
            'password': self.password,
            'next': 'https://evil.example/',
        })
        self.assertRedirects(response, dashboard_url, fetch_redirect_response=False)
