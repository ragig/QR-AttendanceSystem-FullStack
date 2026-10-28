import hashlib

from rest_framework.exceptions import AuthenticationFailed
from rest_framework_simplejwt.authentication import JWTAuthentication


def qr_fingerprint(qr_token):
    """Short, non-reversible tag of a QR token (stored inside QR-scoped JWTs)."""
    return hashlib.sha256(qr_token.encode()).hexdigest()[:16]


class ScopedJWTAuthentication(JWTAuthentication):
    """Normal JWT auth, plus: a token created by a personal-QR scan stops working
    as soon as that person's QR token is regenerated (lost / leaked QR)."""

    def authenticate(self, request):
        result = super().authenticate(request)
        if result is None:
            return None
        user, token = result
        if token.get('scope') == 'qr' and token.get('qrh') != qr_fingerprint(user.qr_token):
            raise AuthenticationFailed('This QR code was replaced. Please scan the new one.')
        return user, token
