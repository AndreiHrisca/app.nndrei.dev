import re
from django.shortcuts import redirect
from django_otp.plugins.otp_totp.models import TOTPDevice

# The client demo is public and ignores the session, so a half-signed-in user
# opening a demo link sees the demo rather than the 2FA screen.
DEMO_PATH = re.compile(r'^/clientes/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/demo-cliente/?$')

class TwoFactorMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        allowed = request.path in {'/2fa/', '/logout/', '/sw.js', '/manifest.webmanifest', '/offline/'} or request.path.startswith('/static/') or DEMO_PATH.match(request.path)
        if user.is_authenticated and not allowed:
            required = user.role != 'client' or TOTPDevice.objects.filter(user=user, confirmed=True).exists()
            if required and not user.is_verified():
                return redirect('two_factor')
        return self.get_response(request)
