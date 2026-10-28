import re

from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.validators import UnicodeUsernameValidator
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from .models import Attendance, AttendanceSession, Notice, User


class UserSerializer(serializers.ModelSerializer):
    """Public profile (what admins/managers see in lists)."""

    user_id = serializers.IntegerField(source='id', read_only=True)

    class Meta:
        model = User
        fields = [
            'id', 'user_id', 'username', 'email', 'display_name',
            'employee_id', 'department', 'phone', 'role',
        ]


class SelfUserSerializer(UserSerializer):
    """Profile of the logged-in user: also contains their personal QR token."""

    class Meta(UserSerializer.Meta):
        fields = UserSerializer.Meta.fields + ['qr_token']


class LoginSerializer(serializers.Serializer):
    employee_id = serializers.CharField(max_length=30)
    password = serializers.CharField(max_length=128, trim_whitespace=False)


class QrLoginSerializer(serializers.Serializer):
    qr_token = serializers.CharField(max_length=128)


class RegisterSerializer(serializers.Serializer):
    display_name = serializers.CharField(max_length=150)
    email = serializers.EmailField()
    password = serializers.CharField(max_length=128, write_only=True, trim_whitespace=False)
    role = serializers.ChoiceField(choices=['employee', 'manager'], default='employee')
    username = serializers.CharField(
        max_length=150, required=False, allow_blank=True, validators=[UnicodeUsernameValidator()],
    )
    employee_id = serializers.CharField(max_length=30, required=False, allow_blank=True)
    department = serializers.CharField(max_length=100, required=False, allow_blank=True)
    phone = serializers.CharField(max_length=30, required=False, allow_blank=True)

    def validate_display_name(self, value):
        return value.strip()

    def validate_email(self, value):
        value = value.strip().lower()
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError('A user with this email already exists.')
        return value

    def validate_username(self, value):
        value = value.strip()
        if value and User.objects.filter(username__iexact=value).exists():
            raise serializers.ValidationError('This username is already taken.')
        return value

    def validate_employee_id(self, value):
        value = value.strip()
        if value and User.objects.filter(employee_id__iexact=value).exists():
            raise serializers.ValidationError('This employee/manager ID is already in use.')
        return value

    def validate(self, attrs):
        probe = User(
            username=attrs.get('username') or attrs['email'].split('@')[0],
            email=attrs['email'],
        )
        try:
            validate_password(attrs['password'], user=probe)
        except DjangoValidationError as error:
            raise serializers.ValidationError({'password': list(error.messages)})
        return attrs

    @staticmethod
    def _unique_username(email):
        base = re.sub(r'[^\w.@+-]', '', email.split('@')[0])[:120] or 'user'
        candidate = base
        counter = 1
        while User.objects.filter(username__iexact=candidate).exists():
            candidate = f'{base}{counter}'
            counter += 1
        return candidate

    def create(self, validated_data):
        password = validated_data.pop('password')
        username = validated_data.pop('username', '') or self._unique_username(validated_data['email'])
        user = User(username=username, **validated_data)
        user.set_password(password)
        user.save()
        return user


class MarkAttendanceSerializer(serializers.Serializer):
    token = serializers.CharField(max_length=128, required=False, allow_blank=True)


class CheckoutSerializer(serializers.Serializer):
    record = serializers.IntegerField(required=False, allow_null=True)


class AttendanceSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source='user.display_name', read_only=True)
    employee_role = serializers.CharField(source='user.role', read_only=True)
    employee_id = serializers.CharField(source='user.employee_id', read_only=True)
    session_title = serializers.SerializerMethodField()
    status = serializers.CharField(read_only=True)
    hours = serializers.FloatField(read_only=True)

    class Meta:
        model = Attendance
        fields = [
            'id', 'employee_name', 'employee_role', 'employee_id', 'session_title',
            'status', 'check_in', 'check_out', 'hours',
        ]

    def get_session_title(self, obj):
        return obj.session.title if obj.session_id else ''


class NoticeSerializer(serializers.ModelSerializer):
    sender_name = serializers.CharField(source='sender.display_name', read_only=True)
    recipient_name = serializers.CharField(source='recipient.display_name', read_only=True)

    class Meta:
        model = Notice
        fields = ['id', 'sender_name', 'recipient_name', 'message', 'created_at']


class NoticeCreateSerializer(serializers.Serializer):
    recipient = serializers.PrimaryKeyRelatedField(queryset=User.objects.filter(is_active=True))
    message = serializers.CharField(max_length=1000)

    def validate_recipient(self, value):
        if value.pk == self.context['request'].user.pk:
            raise serializers.ValidationError('Choose someone other than yourself.')
        return value

    def validate_message(self, value):
        return value.strip()


class SessionSerializer(serializers.ModelSerializer):
    qr_url = serializers.SerializerMethodField()

    class Meta:
        model = AttendanceSession
        fields = ['id', 'title', 'token', 'is_active', 'expires_at', 'created_at', 'qr_url']

    def get_qr_url(self, obj):
        return f"{settings.FRONTEND_URL.rstrip('/')}/?attendance={obj.token}"


class SessionCreateSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=150)
    expires_in_minutes = serializers.IntegerField(required=False, min_value=1, max_value=10080)
