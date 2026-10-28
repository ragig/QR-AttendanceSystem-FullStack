import calendar
import csv
import secrets
from datetime import timedelta

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.db.models.functions import TruncDate
from django.http import HttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import AccessToken, RefreshToken

from .authentication import qr_fingerprint
from .models import Attendance, AttendanceSession, Notice, User
from .permissions import IsAdminOrManager, IsAdminRole, effective_role
from .serializers import (
    AttendanceSerializer,
    CheckoutSerializer,
    LoginSerializer,
    MarkAttendanceSerializer,
    NoticeCreateSerializer,
    NoticeSerializer,
    QrLoginSerializer,
    RegisterSerializer,
    SelfUserSerializer,
    SessionCreateSerializer,
    SessionSerializer,
    UserSerializer,
)

MAX_HISTORY_ROWS = 2000
MONTHLY_WINDOW_DAYS = 730  # monthly reports cover the last two years


def error(detail, http_status=status.HTTP_400_BAD_REQUEST, **extra):
    return Response({'detail': detail, **extra}, status=http_status)


def close_stale_records(user):
    """Close check-ins that were left open on an earlier day (forgotten check-out).

    They are closed at 23:59:59 of the day they started, so one forgotten check-out
    never blocks tomorrow's check-in or shows a 20-hour shift.
    """
    today = timezone.localdate()
    for record in Attendance.objects.filter(user=user, check_out__isnull=True):
        started = timezone.localtime(record.check_in)
        if started.date() < today:
            record.check_out = started.replace(hour=23, minute=59, second=59, microsecond=0)
            record.save(update_fields=['check_out'])


def open_record_for(user):
    """The user's current open check-in (after closing any stale ones), or None."""
    close_stale_records(user)
    return Attendance.objects.filter(user=user, check_out__isnull=True).first()


def count_days_present(user):
    return (
        Attendance.objects.filter(user=user)
        .annotate(day=TruncDate('check_in'))
        .order_by()  # drop the default ordering, otherwise DISTINCT is not per day
        .values('day')
        .distinct()
        .count()
    )


# ------------------------------------------------------------------ health

class HealthView(APIView):
    """GET /api/health/ - quick way to check the frontend can reach Django."""

    authentication_classes = []
    permission_classes = [AllowAny]

    def get(self, request):
        return Response({'status': 'ok', 'time': timezone.now()})


# -------------------------------------------------------------------- auth

class LoginView(APIView):
    """POST /api/auth/login/  {employee_id, password} -> {access, refresh, user}"""

    authentication_classes = []  # an old/expired token in the header must not break login
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'login'

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        employee_id = serializer.validated_data['employee_id'].strip()
        password = serializer.validated_data['password']

        user = User.objects.filter(employee_id__iexact=employee_id).first()
        if user is None or not user.is_active or not user.check_password(password):
            return error('Invalid employee ID or password.', status.HTTP_401_UNAUTHORIZED)

        refresh = RefreshToken.for_user(user)
        return Response({
            'access': str(refresh.access_token),
            'refresh': str(refresh),
            'user': SelfUserSerializer(user).data,
        })


class QrLoginView(APIView):
    """POST /api/auth/qr-login/  {qr_token} -> {access, user, active_attendance}

    Scanning a personal QR code signs the owner in with a short-lived token
    that can only mark attendance and read the owner's own data.
    """

    authentication_classes = []
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'qr_login'

    def post(self, request):
        serializer = QrLoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        qr_token = serializer.validated_data['qr_token'].strip()

        user = User.objects.filter(qr_token=qr_token, is_active=True).first()
        if user is None:
            return error('Invalid QR token. Ask your admin for a fresh QR code.')
        if user.role == 'admin':
            return error('Admins must log in with their employee ID and password.', status.HTTP_403_FORBIDDEN)

        access = AccessToken.for_user(user)
        access['scope'] = 'qr'
        access['qrh'] = qr_fingerprint(user.qr_token)
        access.set_exp(lifetime=timedelta(minutes=settings.QR_LOGIN_MINUTES))

        open_record = open_record_for(user)
        return Response({
            'access': str(access),
            'user': SelfUserSerializer(user).data,
            'active_attendance': open_record.id if open_record else None,
        })


class RegisterView(APIView):
    """POST /api/auth/register/  (admin only) -> {user}"""

    permission_classes = [IsAdminRole]

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            with transaction.atomic():
                user = serializer.save()
        except IntegrityError:
            return error('That username, email or ID is already in use. Try again.')
        return Response({'user': UserSerializer(user).data}, status=status.HTTP_201_CREATED)


# --------------------------------------------------------------- dashboard

class DashboardView(APIView):
    """GET /api/dashboard/ -> {summary, employees, managers}"""

    def get(self, request):
        user = request.user
        role = effective_role(request)
        days_present = count_days_present(user)

        summary = {
            'records': days_present,
            'attendanceRecords': days_present,
            'notices': Notice.objects.filter(recipient=user).count(),
        }
        employees = []
        managers = []

        if role in ('admin', 'manager'):
            employee_qs = User.objects.filter(role='employee', is_active=True).order_by('display_name', 'id')
            summary['employees'] = employee_qs.count()
            employees = UserSerializer(employee_qs, many=True).data

        if role == 'admin':
            manager_qs = User.objects.filter(role='manager', is_active=True).order_by('display_name', 'id')
            summary['managers'] = manager_qs.count()
            managers = UserSerializer(manager_qs, many=True).data

        return Response({'summary': summary, 'employees': employees, 'managers': managers})


# -------------------------------------------------------------- attendance

class MarkAttendanceView(APIView):
    """POST /api/attendance/mark/  {token?} -> 201 {detail, record, action}

    - no token / personal QR token: checks the logged-in user in
    - session QR token: checks the user in to that session
    """

    def post(self, request):
        serializer = MarkAttendanceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        token = (serializer.validated_data.get('token') or '').strip()
        user = request.user

        if user.role == 'admin':
            return error('Admin accounts do not mark attendance.', status.HTTP_403_FORBIDDEN)

        session = None
        if token and not secrets.compare_digest(token.encode(), user.qr_token.encode()):
            if User.objects.filter(qr_token=token).exists():
                return error('This QR code belongs to another person.', status.HTTP_403_FORBIDDEN)
            session = AttendanceSession.objects.filter(token=token).first()
            if session is None or not session.is_open():
                return error('Invalid or expired attendance QR code.')
            if Attendance.objects.filter(user=user, session=session).exists():
                return error('Attendance already recorded for this session.')

        existing = open_record_for(user)
        if existing:
            return error('Already checked in. Please check out first.', record=existing.id)

        try:
            with transaction.atomic():
                record = Attendance.objects.create(user=user, session=session, check_in=timezone.now())
        except IntegrityError:
            # Two requests raced: the other one won.
            existing = open_record_for(user)
            if existing:
                return error('Already checked in. Please check out first.', record=existing.id)
            return error('Attendance already recorded for this session.')

        detail = f'Checked in to "{session.title}" successfully.' if session else 'Checked in successfully.'
        return Response(
            {'detail': detail, 'action': 'check-in', 'record': record.id, 'check_in': record.check_in},
            status=status.HTTP_201_CREATED,
        )


class CheckoutView(APIView):
    """POST /api/attendance/checkout/  {record?} -> {detail, record, hours}"""

    def post(self, request):
        serializer = CheckoutSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        record_id = serializer.validated_data.get('record')

        close_stale_records(request.user)
        records = Attendance.objects.filter(user=request.user, check_out__isnull=True)
        if record_id:
            records = records.filter(pk=record_id)
        record = records.first()
        if record is None:
            return error('No active check-in found.')

        record.check_out = timezone.now()
        record.save(update_fields=['check_out'])
        return Response({
            'detail': 'Checked out successfully.',
            'action': 'check-out',
            'record': record.id,
            'check_out': record.check_out,
            'hours': record.hours,
        })


def visible_attendance(request):
    """Attendance rows the requester is allowed to see."""
    user = request.user
    role = effective_role(request)
    queryset = Attendance.objects.select_related('user', 'session')

    if role == 'admin':
        role_filter = request.query_params.get('role')
        if role_filter in ('employee', 'manager'):
            queryset = queryset.filter(user__role=role_filter)
    elif role == 'manager':
        queryset = queryset.filter(Q(user__role='employee') | Q(user=user))
    else:
        return queryset.filter(user=user)

    employee = request.query_params.get('employee')
    if employee:
        try:
            queryset = queryset.filter(user_id=int(employee))
        except ValueError:
            raise ValidationError({'employee': 'Must be a number.'})
    return queryset


class AttendanceHistoryView(APIView):
    """GET /api/attendance/history/?period=all|daily|monthly&role=&employee="""

    def get(self, request):
        period = request.query_params.get('period', 'all')
        if period not in ('all', 'daily', 'monthly'):
            return error('period must be all, daily or monthly.')

        queryset = visible_attendance(request)

        if period == 'daily':
            queryset = queryset.filter(check_in__date=timezone.localdate())
        if period == 'monthly':
            since = timezone.now() - timedelta(days=MONTHLY_WINDOW_DAYS)
            return Response(self.monthly_rows(queryset.filter(check_in__gte=since)))

        queryset = queryset.order_by('-check_in')[:MAX_HISTORY_ROWS]
        return Response(AttendanceSerializer(queryset, many=True).data)

    @staticmethod
    def monthly_rows(queryset):
        """One row per person per month: days present and total hours."""
        buckets = {}
        for record in queryset.order_by('check_in'):
            local = timezone.localtime(record.check_in)
            key = (record.user_id, local.year, local.month)
            bucket = buckets.setdefault(key, {'user': record.user, 'days': set(), 'seconds': 0.0})
            bucket['days'].add(local.date())
            if record.check_out:
                bucket['seconds'] += (record.check_out - record.check_in).total_seconds()

        rows = []
        for (user_id, year, month), bucket in buckets.items():
            user = bucket['user']
            rows.append({
                'id': f'{user_id}-{year}-{month:02d}',
                'employee_name': user.display_name,
                'employee_role': user.role,
                'employee_id': user.employee_id,
                'session_title': f'{calendar.month_name[month]} {year}',
                'status': 'present',
                'days_present': len(bucket['days']),
                'hours': round(bucket['seconds'] / 3600, 2),
                'check_in': None,
                'check_out': None,
                '_sort': (year, month, user.display_name.lower()),
            })
        rows.sort(key=lambda row: (-row['_sort'][0], -row['_sort'][1], row['_sort'][2]))
        for row in rows:
            del row['_sort']
        return rows


# ----------------------------------------------------------------- notices

class NoticeListCreateView(APIView):
    """GET /api/notices/  - admin: notices they sent, others: notices they received
       POST /api/notices/ {recipient, message} - admin only
    """

    def get_permissions(self):
        if self.request.method == 'POST':
            return [IsAdminRole()]
        return [IsAuthenticated()]

    def get(self, request):
        if effective_role(request) == 'admin':
            queryset = Notice.objects.filter(sender=request.user)
        else:
            queryset = Notice.objects.filter(recipient=request.user)
        queryset = queryset.select_related('sender', 'recipient')[:200]
        return Response(NoticeSerializer(queryset, many=True).data)

    def post(self, request):
        serializer = NoticeCreateSerializer(data=request.data, context={'request': request})
        serializer.is_valid(raise_exception=True)
        notice = Notice.objects.create(
            sender=request.user,
            recipient=serializer.validated_data['recipient'],
            message=serializer.validated_data['message'],
        )
        return Response(NoticeSerializer(notice).data, status=status.HTTP_201_CREATED)


# ---------------------------------------------------------------- sessions

class SessionListCreateView(APIView):
    """Shared session QR codes.

    POST /api/sessions/ {title, expires_in_minutes?} -> session with `qr_url`
    GET  /api/sessions/ -> latest sessions (admin: all, manager: own)
    Turn `qr_url` into a QR code; scanning it opens the React app and marks attendance.
    """

    permission_classes = [IsAdminOrManager]

    def get(self, request):
        queryset = AttendanceSession.objects.all()
        if request.user.role != 'admin':
            queryset = queryset.filter(created_by=request.user)
        return Response(SessionSerializer(queryset[:50], many=True).data)

    def post(self, request):
        serializer = SessionCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        minutes = serializer.validated_data.get('expires_in_minutes')
        session = AttendanceSession.objects.create(
            title=serializer.validated_data['title'].strip(),
            created_by=request.user,
            expires_at=timezone.now() + timedelta(minutes=minutes) if minutes else None,
        )
        return Response(SessionSerializer(session).data, status=status.HTTP_201_CREATED)


# ------------------------------------------------------------------ report

def csv_safe(value):
    """Stop spreadsheet apps from running a cell that starts with = + - @ as a formula."""
    text = '' if value is None else str(value)
    return f"'{text}" if text[:1] in ('=', '+', '-', '@', '\t', '\r') else text


class ExportReportView(APIView):
    """GET /api/reports/export/ -> attendance_report.csv"""

    permission_classes = [IsAdminOrManager]

    def get(self, request):
        queryset = visible_attendance(request).order_by('-check_in')[:50000]

        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="attendance_report.csv"'
        writer = csv.writer(response)
        writer.writerow(['Employee ID', 'Name', 'Role', 'Department', 'Session', 'Check in', 'Check out', 'Hours', 'Status'])
        for record in queryset:
            check_in = timezone.localtime(record.check_in).strftime('%Y-%m-%d %H:%M')
            check_out = timezone.localtime(record.check_out).strftime('%Y-%m-%d %H:%M') if record.check_out else ''
            writer.writerow([
                csv_safe(record.user.employee_id),
                csv_safe(record.user.display_name),
                record.user.role,
                csv_safe(record.user.department),
                csv_safe(record.session.title if record.session_id else 'Personal check-in'),
                check_in,
                check_out,
                '' if record.hours is None else record.hours,
                record.status,
            ])
        return response
