from rest_framework.permissions import BasePermission


def is_qr_scoped(request):
    """True when the request is authenticated with a token created by a personal-QR scan.

    Those tokens are short-lived and may only do the things the QR owner could do
    anyway (mark attendance, read their own data). They never get admin/manager powers.
    """
    token = getattr(request, 'auth', None)
    if token is None:
        return False
    try:
        return token.get('scope') == 'qr'
    except AttributeError:
        return False


def effective_role(request):
    """Role used for deciding what data a request may see."""
    if is_qr_scoped(request):
        return 'employee'
    return request.user.role


class IsAdminRole(BasePermission):
    message = 'Only admins can do this.'

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and user.role == 'admin'
            and not is_qr_scoped(request)
        )


class IsAdminOrManager(BasePermission):
    message = 'Only admins and managers can do this.'

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and user.role in ('admin', 'manager')
            and not is_qr_scoped(request)
        )
