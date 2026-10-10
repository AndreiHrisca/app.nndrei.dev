"""Second step per role, remembered devices and email login links for clients.

Tokens (device cookies and login links) are random, sent once, and only their
SHA-256 is stored: a copy of the database does not let anyone sign in.
"""
import hashlib
import secrets
from datetime import timedelta
from django.conf import settings
from django.core import signing
from django.db import transaction
from django.utils import timezone
from django_otp import login as otp_login
from django_otp.plugins.otp_totp.models import TOTPDevice
from .models import LoginLinkRequest, LoginToken, TrustedDevice, TwoFactorPolicy, User

TRUSTED_COOKIE = 'nndrei_trusted_device'
TRUSTED_SALT = 'trusted-device'
LINK_MINUTES = 15
# Requests allowed per email in LINK_MINUTES and per IP address in an hour.
LINKS_PER_EMAIL = 3
LINKS_PER_IP = 10


def digest(token):
    return hashlib.sha256(token.encode()).hexdigest()


def confirmed_device(user):
    return TOTPDevice.objects.filter(user=user, confirmed=True).first()


def policy(user):
    return TwoFactorPolicy.for_role(user.role)


def second_step_required(user):
    """Required by the role, or chosen by the person: whoever has it set up uses it.

    With "Desactivada" nobody new is offered it, but people who already turned
    it on keep it until they turn it off themselves.
    """
    return policy(user) == 'required' or confirmed_device(user) is not None


def can_enroll(user):
    return policy(user) != 'disabled'


def can_turn_off(user):
    return policy(user) != 'required'


# --- Remembered devices ------------------------------------------------------------

def device_label(request):
    agent = request.META.get('HTTP_USER_AGENT', '')
    for name in ['iPhone', 'iPad', 'Android', 'Macintosh', 'Windows', 'Linux']:
        if name in agent:
            system = {'Macintosh': 'Mac'}.get(name, name)
            break
    else:
        system = 'Otro dispositivo'
    for name in ['Edg', 'Firefox', 'Chrome', 'Safari']:
        if name in agent:
            return f"{system} · {({'Edg': 'Edge'}).get(name, name)}"
    return system


def remember_device(request, response):
    token = secrets.token_urlsafe(32)
    days = settings.TRUSTED_DEVICE_DAYS
    device = TrustedDevice.objects.create(user=request.user, token_hash=digest(token), label=device_label(request),
                                          expires_at=timezone.now() + timedelta(days=days))
    response.set_signed_cookie(TRUSTED_COOKIE, f'{request.user.public_id}:{token}', salt=TRUSTED_SALT, max_age=days * 86400,
                               secure=settings.SESSION_COOKIE_SECURE, httponly=True, samesite='Lax')
    return device


def trusted_device(request):
    """The live remembered device behind this browser's cookie for the signed-in user, if any."""
    try:
        value = request.get_signed_cookie(TRUSTED_COOKIE, salt=TRUSTED_SALT, max_age=settings.TRUSTED_DEVICE_DAYS * 86400)
    except (KeyError, signing.BadSignature):
        return None
    owner, _, token = value.partition(':')
    if owner != str(request.user.public_id) or not token:
        return None
    return TrustedDevice.objects.filter(user=request.user, token_hash=digest(token), revoked_at__isnull=True,
                                        expires_at__gt=timezone.now()).first()


def verify_with_trusted_device(request):
    """Count a remembered device as the second step for this session."""
    device = confirmed_device(request.user)
    trusted = trusted_device(request) if device else None
    if trusted is None:
        return False
    TrustedDevice.objects.filter(pk=trusted.pk).update(last_used_at=timezone.now())
    otp_login(request, device)
    return True


def turn_off(user):
    TOTPDevice.objects.filter(user=user).delete()
    TrustedDevice.objects.filter(user=user, revoked_at__isnull=True).update(revoked_at=timezone.now())


# --- Email login links (clients) ---------------------------------------------------

def client_ip(request):
    # The proxy connection address, as proposals do: X-Forwarded-For is not trusted.
    return request.META.get('REMOTE_ADDR') or None


def request_login_link(request, email):
    """Send a link if `email` belongs to an active client and the limits allow it.

    The caller shows the same message whatever happens here.
    """
    email = email.strip().lower()
    ip = client_ip(request)
    now = timezone.now()
    with transaction.atomic():
        recent_email = LoginLinkRequest.objects.filter(email=email, created_at__gt=now - timedelta(minutes=LINK_MINUTES)).count()
        recent_ip = LoginLinkRequest.objects.filter(ip=ip, created_at__gt=now - timedelta(hours=1)).count() if ip else 0
        LoginLinkRequest.objects.create(email=email, ip=ip)
        if recent_email >= LINKS_PER_EMAIL or recent_ip >= LINKS_PER_IP:
            return False
        user = User.objects.filter(email__iexact=email, role='client', is_active=True).first()
        if user is None:
            return False
        token = secrets.token_urlsafe(32)
        expires = now + timedelta(minutes=LINK_MINUTES)
        LoginToken.objects.create(user=user, token_hash=digest(token), expires_at=expires)
        from .notifications import notify
        notify('Tu enlace para entrar en nndrei', [user.email],
               f'Pulsa el enlace para entrar. Caduca en {LINK_MINUTES} minutos y solo sirve una vez. Si no lo has pedido tú, ignora este correo.',
               settings.SITE_URL + '/entrar/' + token + '/', sensitive_until=expires)
    return True


def live_token(token, lock=False):
    queryset = LoginToken.objects.select_related('user')
    if lock:
        queryset = queryset.select_for_update()
    return queryset.filter(token_hash=digest(token), used_at__isnull=True, expires_at__gt=timezone.now(),
                           user__is_active=True, user__role='client').first()


@transaction.atomic
def use_login_token(token):
    """Spend the link and every other pending link of the same person."""
    item = live_token(token, lock=True)
    if item is None:
        return None
    LoginToken.objects.filter(user=item.user, used_at__isnull=True).update(used_at=timezone.now())
    return item.user
