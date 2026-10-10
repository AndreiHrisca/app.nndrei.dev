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
        allowed = request.path in {'/2fa/', '/logout/', '/sw.js', '/manifest.webmanifest', '/offline/', '/robots.txt'} or request.path.startswith(('/static/', '/entrar/', '/demos/', '/c/')) or DEMO_PATH.match(request.path)
        if user.is_authenticated and not allowed and not user.is_verified():
            # The role's policy (TwoFactorPolicy) decides; a remembered device counts as the second step.
            from .security import second_step_required, verify_with_trusted_device
            if second_step_required(user) and not verify_with_trusted_device(request):
                return redirect('two_factor')
        return self.get_response(request)


class NoIndexMiddleware:
    """The whole app is private: nothing here belongs in a search engine, demos included.

    robots.txt leaves crawling open on purpose; a crawler has to fetch a page to
    see this header, and a blocked URL can still be listed bare if it leaks.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        response.setdefault('X-Robots-Tag', 'noindex, nofollow')
        return response
