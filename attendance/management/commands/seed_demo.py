from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from attendance.models import User

DEMO_PASSWORD = 'Demo@12345'

DEMO_USERS = [
    # employee_id, username, display name, role, department, phone, email
    ('ADM001', 'admin', 'Admin User', 'admin', '', '', 'admin@gmail.com'),
    ('MGR001', 'manager1', 'Maya Manager', 'manager', 'Operations', '9000000001', 'maya.manager@gmail.com'),
    ('EMP001', 'employee1', 'Ravi Employee', 'employee', 'Operations', '9000000002', 'ravi.employee@gmail.com'),
    ('EMP002', 'employee2', 'Sara Employee', 'employee', 'Sales', '9000000003', 'sara.employee@gmail.com'),
]


class Command(BaseCommand):
    help = 'Create demo users for local development (refuses to run when DEBUG is off).'

    def handle(self, *args, **options):
        if not settings.DEBUG:
            raise CommandError('seed_demo only runs when DJANGO_DEBUG=True.')

        for employee_id, username, name, role, department, phone, email in DEMO_USERS:
            if User.objects.filter(employee_id=employee_id).exists():
                self.stdout.write(f'{employee_id} already exists, skipped')
                continue
            extra = dict(display_name=name, employee_id=employee_id, department=department, phone=phone)
            if role == 'admin':
                User.objects.create_superuser(username=username, email=email, password=DEMO_PASSWORD, **extra)
            else:
                User.objects.create_user(username=username, email=email, password=DEMO_PASSWORD, role=role, **extra)
            self.stdout.write(self.style.SUCCESS(f'created {role} {employee_id}'))

        self.stdout.write('')
        self.stdout.write(f'Log in with employee ID ADM001 / MGR001 / EMP001 / EMP002 and password {DEMO_PASSWORD}')
