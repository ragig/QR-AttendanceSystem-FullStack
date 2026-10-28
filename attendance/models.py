import secrets

from django.conf import settings
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils import timezone


def generate_token():
    """Unguessable URL-safe token used for personal and session QR codes."""
    return secrets.token_urlsafe(24)


class User(AbstractUser):
    """Login account. Staff sign in with `employee_id` + password."""

    class Role(models.TextChoices):
        ADMIN = 'admin', 'Admin'
        MANAGER = 'manager', 'Manager'
        EMPLOYEE = 'employee', 'Employee'

    ID_PREFIXES = {'admin': 'ADM', 'manager': 'MGR', 'employee': 'EMP'}

    role = models.CharField(max_length=10, choices=Role.choices, default=Role.EMPLOYEE)
    employee_id = models.CharField(
        max_length=30,
        unique=True,
        blank=True,
        help_text='Used to log in. Leave blank to auto-generate (EMP001, MGR001, ADM001).',
    )
    display_name = models.CharField(max_length=150, blank=True)
    department = models.CharField(max_length=100, blank=True)
    phone = models.CharField(max_length=30, blank=True)
    qr_token = models.CharField(
        max_length=64,
        unique=True,
        editable=False,
        default=generate_token,
        help_text='Secret behind the personal QR code. Regenerate it if the QR is lost.',
    )

    @property
    def is_admin_role(self):
        return self.role == 'admin'

    @property
    def is_manager_role(self):
        return self.role == 'manager'

    def save(self, *args, **kwargs):
        if self.is_superuser:
            self.role = 'admin'
        if not self.display_name:
            self.display_name = self.get_full_name() or self.username
        self.employee_id = (self.employee_id or '').strip().upper()
        if not self.employee_id:
            self.employee_id = self._next_employee_id()
        super().save(*args, **kwargs)

    def _next_employee_id(self):
        prefix = self.ID_PREFIXES.get(self.role, 'EMP')
        number = User.objects.filter(role=self.role).count() + 1
        while True:
            candidate = f'{prefix}{number:03d}'
            if not User.objects.filter(employee_id__iexact=candidate).exists():
                return candidate
            number += 1

    def regenerate_qr_token(self):
        self.qr_token = generate_token()
        self.save(update_fields=['qr_token'])

    def __str__(self):
        return f'{self.display_name or self.username} ({self.employee_id})'


class AttendanceSession(models.Model):
    """A shared QR code (e.g. a meeting or a shift) that people scan to check in."""

    title = models.CharField(max_length=150)
    token = models.CharField(max_length=64, unique=True, editable=False, default=generate_token)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='created_sessions',
    )
    is_active = models.BooleanField(default=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def is_open(self):
        if not self.is_active:
            return False
        return self.expires_at is None or self.expires_at > timezone.now()

    def __str__(self):
        return self.title


class Attendance(models.Model):
    """One check-in / check-out pair."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='attendance_records',
    )
    session = models.ForeignKey(
        AttendanceSession,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='records',
    )
    check_in = models.DateTimeField(default=timezone.now)
    check_out = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-check_in']
        constraints = [
            # A person can only have one open (not checked out) record at a time,
            # even if two requests arrive at the same moment.
            models.UniqueConstraint(
                fields=['user'],
                condition=Q(check_out__isnull=True),
                name='one_open_attendance_per_user',
            ),
            # One record per person per session QR.
            models.UniqueConstraint(
                fields=['user', 'session'],
                condition=Q(session__isnull=False),
                name='one_attendance_per_user_per_session',
            ),
        ]
        indexes = [models.Index(fields=['user', '-check_in'])]

    @property
    def hours(self):
        if not self.check_out:
            return None
        return round((self.check_out - self.check_in).total_seconds() / 3600, 2)

    @property
    def status(self):
        return 'present' if self.check_out else 'checked-in'

    def clean(self):
        if self.check_out and self.check_out < self.check_in:
            raise ValidationError({'check_out': 'Check-out cannot be earlier than check-in.'})

    def __str__(self):
        return f'{self.user} @ {self.check_in:%Y-%m-%d %H:%M}'


class Notice(models.Model):
    """Message from an admin to one employee/manager."""

    sender = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='sent_notices',
    )
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='received_notices',
    )
    message = models.TextField(max_length=1000)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.sender} -> {self.recipient}'
