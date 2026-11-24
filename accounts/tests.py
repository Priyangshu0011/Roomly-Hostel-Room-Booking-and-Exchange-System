from django.test import TestCase, Client
from django.urls import reverse
from django.contrib.auth.models import User
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
