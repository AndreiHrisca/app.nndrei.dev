import re
from django.shortcuts import redirect

# The client demo is public and ignores the session, so a half-signed-in user
# opening a demo link sees the demo rather than the 2FA screen.
DEMO_PATH = re.compile(r'^/clientes/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/demo-cliente/?$')

class TwoFactorMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        allowed = request.path in {'/2fa/', '/logout/', '/sw.js', '/manifest.webmanifest', '/offline/'} or request.path.startswith(('/static/', '/entrar/')) or DEMO_PATH.match(request.path)
        if user.is_authenticated and not allowed and not user.is_verified():
            # The role's policy (TwoFactorPolicy) decides; a remembered device counts as the second step.
            from .security import second_step_required, verify_with_trusted_device
            if second_step_required(user) and not verify_with_trusted_device(request):
                return redirect('two_factor')
        return self.get_response(request)
