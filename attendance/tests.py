"""Run with:  python manage.py test"""
import calendar
from datetime import datetime, timedelta

from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import AccessToken

from .models import Attendance, AttendanceSession, Notice, User


class ApiTestCase(APITestCase):
    def setUp(self):
        cache.clear()  # login throttling uses the cache
        self.admin = User.objects.create_superuser(
            username='admin', email='admin@example.com', password='Adm1n-Pass-9', display_name='Admin User')
        self.manager = User.objects.create_user(
            username='mgr', email='mgr@example.com', password='Mgr-Pass-9x', role='manager',
            display_name='Manager One', department='Ops', phone='111')
        self.emp = User.objects.create_user(
            username='emp', email='emp@example.com', password='Emp-Pass-9x', role='employee',
            display_name='Employee One', department='Ops', phone='222')
        self.emp2 = User.objects.create_user(
            username='emp2', email='emp2@example.com', password='Emp2-Pass-9x', role='employee',
            display_name='Employee Two', department='Sales', phone='333')

    def login_as(self, user):
        self.client.credentials(HTTP_AUTHORIZATION=f'Bearer {AccessToken.for_user(user)}')

    def logout(self):
        self.client.credentials()


class AuthTests(ApiTestCase):
    def test_ids_are_generated(self):
        self.assertEqual(self.admin.role, 'admin')
        self.assertEqual(self.admin.employee_id, 'ADM001')
        self.assertEqual(self.manager.employee_id, 'MGR001')
        self.assertEqual(self.emp.employee_id, 'EMP001')
        self.assertEqual(self.emp2.employee_id, 'EMP002')

    def test_login_with_employee_id(self):
        response = self.client.post(
            '/api/auth/login/', {'employee_id': 'emp001', 'password': 'Emp-Pass-9x'}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertIn('access', response.data)
        self.assertEqual(response.data['user']['role'], 'employee')
        self.assertEqual(response.data['user']['qr_token'], self.emp.qr_token)

    def test_login_wrong_password(self):
        response = self.client.post(
            '/api/auth/login/', {'employee_id': 'EMP001', 'password': 'nope'}, format='json')
        self.assertEqual(response.status_code, 401)
        self.assertIn('detail', response.data)

    def test_login_ignores_stale_token_header(self):
        self.client.credentials(HTTP_AUTHORIZATION='Bearer not-a-real-token')
        response = self.client.post(
            '/api/auth/login/', {'employee_id': 'EMP001', 'password': 'Emp-Pass-9x'}, format='json')
        self.assertEqual(response.status_code, 200)

    def test_protected_endpoint_needs_token(self):
        self.assertEqual(self.client.get('/api/dashboard/').status_code, 401)

    def test_qr_login_and_scope(self):
        response = self.client.post('/api/auth/qr-login/', {'qr_token': self.manager.qr_token}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.data['active_attendance'])
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")

        # allowed: own data and marking attendance
        self.assertEqual(self.client.get('/api/dashboard/').status_code, 200)
        self.assertEqual(self.client.post('/api/attendance/mark/', {}, format='json').status_code, 201)
        # not allowed: manager powers
        self.assertEqual(self.client.get('/api/reports/export/').status_code, 403)
        self.assertEqual(self.client.post('/api/sessions/', {'title': 'x'}, format='json').status_code, 403)

    def test_qr_scoped_manager_only_sees_own_attendance(self):
        Attendance.objects.create(user=self.emp, check_in=timezone.now() - timedelta(hours=3),
                                  check_out=timezone.now() - timedelta(hours=2))
        response = self.client.post('/api/auth/qr-login/', {'qr_token': self.manager.qr_token}, format='json')
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")
        history = self.client.get('/api/attendance/history/')
        self.assertEqual(history.status_code, 200)
        self.assertEqual(len(history.data), 0)
        self.assertEqual(self.client.get('/api/dashboard/').data['employees'], [])

    def test_regenerated_qr_invalidates_scoped_token(self):
        response = self.client.post('/api/auth/qr-login/', {'qr_token': self.emp.qr_token}, format='json')
        self.client.credentials(HTTP_AUTHORIZATION=f"Bearer {response.data['access']}")
        self.assertEqual(self.client.get('/api/dashboard/').status_code, 200)
        self.emp.regenerate_qr_token()
        self.assertEqual(self.client.get('/api/dashboard/').status_code, 401)

    def test_employee_ids_are_upper_cased(self):
        user = User.objects.create_user(username='lower', password='Lower-Pass-91', employee_id=' emp-77 ')
        self.assertEqual(user.employee_id, 'EMP-77')

    def test_qr_login_reports_open_record(self):
        record = Attendance.objects.create(user=self.emp)
        response = self.client.post('/api/auth/qr-login/', {'qr_token': self.emp.qr_token}, format='json')
        self.assertEqual(response.data['active_attendance'], record.id)

    def test_qr_login_invalid_and_admin(self):
        bad = self.client.post('/api/auth/qr-login/', {'qr_token': 'nope'}, format='json')
        self.assertEqual(bad.status_code, 400)
        admin = self.client.post('/api/auth/qr-login/', {'qr_token': self.admin.qr_token}, format='json')
        self.assertEqual(admin.status_code, 403)

    def test_admin_registers_user(self):
        self.login_as(self.admin)
        payload = {
            'display_name': 'New Person', 'email': 'New.Person@gmail.com', 'password': 'Str0ng-Pass-77',
            'role': 'employee', 'username': '', 'employee_id': '', 'department': 'Sales', 'phone': '999',
        }
        response = self.client.post('/api/auth/register/', payload, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['user']['role'], 'employee')
        self.assertTrue(response.data['user']['employee_id'].startswith('EMP'))
        self.assertNotIn('qr_token', response.data['user'])
        created = User.objects.get(email='new.person@gmail.com')
        self.assertTrue(created.check_password('Str0ng-Pass-77'))

        duplicate = self.client.post('/api/auth/register/', payload, format='json')
        self.assertEqual(duplicate.status_code, 400)

    def test_register_rules(self):
        payload = {'display_name': 'X Person', 'email': 'x.person@gmail.com', 'password': 'Str0ng-Pass-77'}
        self.login_as(self.emp)
        self.assertEqual(self.client.post('/api/auth/register/', payload, format='json').status_code, 403)
        self.login_as(self.admin)
        admin_payload = dict(payload, role='admin')
        self.assertEqual(self.client.post('/api/auth/register/', admin_payload, format='json').status_code, 400)
        weak = dict(payload, password='123')
        self.assertEqual(self.client.post('/api/auth/register/', weak, format='json').status_code, 400)


class AttendanceTests(ApiTestCase):
    def test_check_in_and_out_cycle(self):
        self.login_as(self.emp)
        first = self.client.post('/api/attendance/mark/', {}, format='json')
        self.assertEqual(first.status_code, 201)
        record_id = first.data['record']

        again = self.client.post('/api/attendance/mark/', {}, format='json')
        self.assertEqual(again.status_code, 400)
        self.assertIn('already checked in', again.data['detail'].lower())
        self.assertEqual(again.data['record'], record_id)

        out = self.client.post('/api/attendance/checkout/', {'record': record_id}, format='json')
        self.assertEqual(out.status_code, 200)
        self.assertIsNotNone(Attendance.objects.get(pk=record_id).check_out)

        out_again = self.client.post('/api/attendance/checkout/', {}, format='json')
        self.assertEqual(out_again.status_code, 400)

    def test_mark_with_own_personal_token(self):
        self.login_as(self.emp)
        response = self.client.post('/api/attendance/mark/', {'token': self.emp.qr_token}, format='json')
        self.assertEqual(response.status_code, 201)

    def test_forgotten_check_out_is_closed_automatically(self):
        yesterday = timezone.now() - timedelta(days=1)
        stale = Attendance.objects.create(user=self.emp, check_in=yesterday)
        self.login_as(self.emp)
        response = self.client.post('/api/attendance/mark/', {}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        stale.refresh_from_db()
        self.assertIsNotNone(stale.check_out)
        self.assertEqual(timezone.localtime(stale.check_out).date(), timezone.localtime(yesterday).date())
        self.assertEqual(Attendance.objects.filter(user=self.emp, check_out__isnull=True).count(), 1)

    def test_cannot_use_someone_elses_personal_token(self):
        self.login_as(self.emp)
        response = self.client.post('/api/attendance/mark/', {'token': self.emp2.qr_token}, format='json')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(Attendance.objects.count(), 0)

    def test_admin_does_not_mark_attendance(self):
        self.login_as(self.admin)
        self.assertEqual(self.client.post('/api/attendance/mark/', {}, format='json').status_code, 403)

    def test_cannot_check_out_someone_elses_record(self):
        record = Attendance.objects.create(user=self.emp2)
        self.login_as(self.emp)
        response = self.client.post('/api/attendance/checkout/', {'record': record.id}, format='json')
        self.assertEqual(response.status_code, 400)
        record.refresh_from_db()
        self.assertIsNone(record.check_out)

    def test_session_qr_flow(self):
        self.login_as(self.admin)
        created = self.client.post('/api/sessions/', {'title': 'Morning shift', 'expires_in_minutes': 30}, format='json')
        self.assertEqual(created.status_code, 201)
        token = created.data['token']
        self.assertIn(f'/?attendance={token}', created.data['qr_url'])

        self.login_as(self.emp)
        marked = self.client.post('/api/attendance/mark/', {'token': token}, format='json')
        self.assertEqual(marked.status_code, 201)
        self.assertEqual(Attendance.objects.get().session.title, 'Morning shift')
        self.client.post('/api/attendance/checkout/', {}, format='json')

        repeat = self.client.post('/api/attendance/mark/', {'token': token}, format='json')
        self.assertEqual(repeat.status_code, 400)
        self.assertIn('already recorded', repeat.data['detail'].lower())

    def test_invalid_and_expired_session_tokens(self):
        expired = AttendanceSession.objects.create(title='Old', expires_at=timezone.now() - timedelta(minutes=1))
        self.login_as(self.emp)
        for token in ('does-not-exist', expired.token):
            response = self.client.post('/api/attendance/mark/', {'token': token}, format='json')
            self.assertEqual(response.status_code, 400)
            self.assertIn('invalid or expired', response.data['detail'].lower())

    def test_employee_cannot_create_session(self):
        self.login_as(self.emp)
        self.assertEqual(self.client.post('/api/sessions/', {'title': 'x'}, format='json').status_code, 403)

    def test_only_one_open_record_per_user(self):
        Attendance.objects.create(user=self.emp)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Attendance.objects.create(user=self.emp)


class HistoryAndReportTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        # Use last month so the tests never depend on today's date or the monthly window.
        last_day_of_previous_month = timezone.localdate().replace(day=1) - timedelta(days=1)
        self.year, self.month = last_day_of_previous_month.year, last_day_of_previous_month.month

    def make_record(self, user, day, start_hour=9, hours=8):
        start = timezone.make_aware(datetime(self.year, self.month, day, start_hour, 0))
        return Attendance.objects.create(user=user, check_in=start, check_out=start + timedelta(hours=hours))

    def test_history_is_scoped_by_role(self):
        self.make_record(self.emp, 5)
        self.make_record(self.emp2, 5)
        self.make_record(self.manager, 6)

        self.login_as(self.emp)
        self.assertEqual(len(self.client.get('/api/attendance/history/').data), 1)

        self.login_as(self.manager)  # employees + own
        self.assertEqual(len(self.client.get('/api/attendance/history/').data), 3)

        self.login_as(self.admin)
        self.assertEqual(len(self.client.get('/api/attendance/history/').data), 3)
        only_managers = self.client.get('/api/attendance/history/', {'role': 'manager'})
        self.assertEqual(len(only_managers.data), 1)
        one_person = self.client.get('/api/attendance/history/', {'employee': self.emp2.id})
        self.assertEqual(len(one_person.data), 1)
        self.assertEqual(one_person.data[0]['employee_name'], 'Employee Two')

    def test_history_rejects_bad_params(self):
        self.login_as(self.admin)
        self.assertEqual(self.client.get('/api/attendance/history/', {'period': 'yearly'}).status_code, 400)
        self.assertEqual(self.client.get('/api/attendance/history/', {'employee': 'abc'}).status_code, 400)

    def test_monthly_summary(self):
        self.make_record(self.emp, 5)
        self.make_record(self.emp, 6)
        self.make_record(self.emp, 6, start_hour=18, hours=1)  # same day: one day, extra hours
        self.login_as(self.emp)
        response = self.client.get('/api/attendance/history/', {'period': 'monthly'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        row = response.data[0]
        self.assertEqual(row['days_present'], 2)
        self.assertEqual(row['hours'], 17.0)
        self.assertEqual(row['session_title'], f'{calendar.month_name[self.month]} {self.year}')

    def test_dashboard_numbers(self):
        self.make_record(self.emp, 5)
        self.make_record(self.emp, 6)
        Notice.objects.create(sender=self.admin, recipient=self.emp, message='Hello')

        self.login_as(self.emp)
        summary = self.client.get('/api/dashboard/').data['summary']
        self.assertEqual(summary['records'], 2)
        self.assertEqual(summary['notices'], 1)

        self.login_as(self.admin)
        data = self.client.get('/api/dashboard/').data
        self.assertEqual(data['summary']['employees'], 2)
        self.assertEqual(data['summary']['managers'], 1)
        self.assertEqual(len(data['employees']), 2)
        self.assertEqual(data['employees'][0]['user_id'], data['employees'][0]['id'])

    def test_export_csv(self):
        self.make_record(self.emp, 5)
        self.login_as(self.manager)
        response = self.client.get('/api/reports/export/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv')
        text = response.content.decode()
        self.assertIn('Employee ID', text)
        self.assertIn('EMP001', text)

        self.login_as(self.emp)
        self.assertEqual(self.client.get('/api/reports/export/').status_code, 403)


class NoticeTests(ApiTestCase):
    def test_notice_flow(self):
        self.login_as(self.admin)
        sent = self.client.post('/api/notices/', {'recipient': self.emp.id, 'message': ' Please submit report '}, format='json')
        self.assertEqual(sent.status_code, 201)
        self.assertEqual(sent.data['message'], 'Please submit report')
        self.assertEqual(sent.data['recipient_name'], 'Employee One')
        self.assertEqual(len(self.client.get('/api/notices/').data), 1)  # admin: sent

        self.login_as(self.emp)
        received = self.client.get('/api/notices/')
        self.assertEqual(len(received.data), 1)
        self.assertEqual(received.data[0]['sender_name'], 'Admin User')
        self.assertEqual(self.client.post(
            '/api/notices/', {'recipient': self.emp2.id, 'message': 'hi'}, format='json').status_code, 403)

        self.login_as(self.emp2)
        self.assertEqual(len(self.client.get('/api/notices/').data), 0)

    def test_cannot_notify_yourself(self):
        self.login_as(self.admin)
        response = self.client.post('/api/notices/', {'recipient': self.admin.id, 'message': 'hi'}, format='json')
        self.assertEqual(response.status_code, 400)


class HealthTests(ApiTestCase):
    def test_health(self):
        self.assertEqual(self.client.get('/api/health/').status_code, 200)
