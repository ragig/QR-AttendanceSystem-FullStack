from django.urls import path
from rest_framework_simplejwt.views import TokenRefreshView

from . import views

urlpatterns = [
    path('health/', views.HealthView.as_view()),

    path('auth/login/', views.LoginView.as_view()),
    path('auth/qr-login/', views.QrLoginView.as_view()),
    path('auth/register/', views.RegisterView.as_view()),
    path('auth/token/refresh/', TokenRefreshView.as_view()),

    path('dashboard/', views.DashboardView.as_view()),

    path('attendance/mark/', views.MarkAttendanceView.as_view()),
    path('attendance/checkout/', views.CheckoutView.as_view()),
    path('attendance/history/', views.AttendanceHistoryView.as_view()),

    path('notices/', views.NoticeListCreateView.as_view()),
    path('sessions/', views.SessionListCreateView.as_view()),
    path('reports/export/', views.ExportReportView.as_view()),
]
