from django.conf import settings
from django.contrib import admin, messages
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import Attendance, AttendanceSession, Notice, User


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = ('employee_id', 'display_name', 'username', 'email', 'role', 'department', 'is_active')
    list_filter = ('role', 'is_active', 'department')
    search_fields = ('employee_id', 'display_name', 'username', 'email')
    ordering = ('employee_id',)
    readonly_fields = ('qr_token',)

    fieldsets = DjangoUserAdmin.fieldsets + (
        ('QR Attendance', {'fields': ('role', 'employee_id', 'display_name', 'department', 'phone', 'qr_token')}),
    )
    add_fieldsets = DjangoUserAdmin.add_fieldsets + (
        ('QR Attendance', {'fields': ('email', 'role', 'employee_id', 'display_name', 'department', 'phone')}),
    )
    actions = ['regenerate_qr']

    @admin.action(description='Regenerate QR token (invalidates the old QR code)')
    def regenerate_qr(self, request, queryset):
        for user in queryset:
            user.regenerate_qr_token()
        self.message_user(request, f'New QR token created for {queryset.count()} user(s).', messages.SUCCESS)


@admin.register(Attendance)
class AttendanceAdmin(admin.ModelAdmin):
    list_display = ('user', 'session', 'check_in', 'check_out', 'hours')
    list_filter = ('user__role', 'session')
    search_fields = ('user__display_name', 'user__employee_id')
    date_hierarchy = 'check_in'
    autocomplete_fields = ('user',)


@admin.register(AttendanceSession)
class AttendanceSessionAdmin(admin.ModelAdmin):
    list_display = ('title', 'is_active', 'expires_at', 'created_by', 'created_at')
    list_filter = ('is_active',)
    readonly_fields = ('token', 'qr_link', 'created_at')
    fields = ('title', 'is_active', 'expires_at', 'created_by', 'token', 'qr_link', 'created_at')

    @admin.display(description='QR link (turn this into a QR code)')
    def qr_link(self, obj):
        if not obj.pk:
            return 'Save first, then the link appears here.'
        return f"{settings.FRONTEND_URL.rstrip('/')}/?attendance={obj.token}"

    def save_model(self, request, obj, form, change):
        if not obj.created_by_id:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(Notice)
class NoticeAdmin(admin.ModelAdmin):
    list_display = ('sender', 'recipient', 'created_at')
    search_fields = ('message', 'recipient__display_name')
