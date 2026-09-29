"""Seguridad (second step on/off, remembered devices) and email login links."""
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.db import transaction
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from . import labels
from .access import scoped
from .auth_views import roles
from .models import TrustedDevice
from .security import (LINK_MINUTES, TRUSTED_COOKIE, can_enroll, can_turn_off, confirmed_device, live_token,
                       policy, request_login_link, trusted_device, turn_off, use_login_token)

ALL_ROLES = ('admin', 'developer', 'accountant', 'client')
LINK_SENT = f'Si ese email corresponde a un cliente, te hemos enviado un enlace para entrar. Caduca en {LINK_MINUTES} minutos.'


@roles(*ALL_ROLES)
def security(request):
    current = trusted_device(request)
    devices = TrustedDevice.objects.visible_to(request.user).filter(revoked_at__isnull=True, expires_at__gt=timezone.now()).order_by('-created_at')
    from .models import TWO_FACTOR_POLICIES
    return render(request, 'core/security.html', {
        'title': labels.TWO_FACTOR, 'policy': dict(TWO_FACTOR_POLICIES)[policy(request.user)],
        'enabled': confirmed_device(request.user) is not None, 'can_enroll': can_enroll(request.user),
        'can_turn_off': can_turn_off(request.user), 'devices': devices, 'current_device': current.pk if current else None,
        'days': settings.TRUSTED_DEVICE_DAYS})


@roles(*ALL_ROLES)
@require_POST
def security_turn_off(request):
    device = confirmed_device(request.user)
    if device is None or not can_turn_off(request.user):
        messages.error(request, 'Tu rol exige la verificación en dos pasos: no se puede desactivar.')
        return redirect('security')
    with transaction.atomic():
        if not device.verify_token(request.POST.get('token', '')):
            messages.error(request, 'Código incorrecto. Escribe el código actual de tu aplicación.')
            return redirect('security')
        turn_off(request.user)
    request.session.pop('otp_device_id', None)
    messages.success(request, 'Verificación en dos pasos desactivada.')
    response = redirect('security')
    response.delete_cookie(TRUSTED_COOKIE)
    return response


@roles(*ALL_ROLES)
@require_POST
def device_revoke(request, public_id):
    device = scoped(TrustedDevice, request.user, public_id)
    current = trusted_device(request)
    TrustedDevice.objects.filter(pk=device.pk, revoked_at__isnull=True).update(revoked_at=timezone.now())
    messages.success(request, 'Dispositivo olvidado: la próxima vez pedirá el código.')
    response = redirect('security')
    if current and current.pk == device.pk:
        response.delete_cookie(TRUSTED_COOKIE)
    return response


@roles(*ALL_ROLES)
@require_POST
def device_revoke_all(request):
    TrustedDevice.objects.visible_to(request.user).filter(revoked_at__isnull=True).update(revoked_at=timezone.now())
    messages.success(request, 'Todos los dispositivos olvidados.')
    response = redirect('security')
    response.delete_cookie(TRUSTED_COOKIE)
    return response


def login_link(request):
    """"Entrar con un enlace": the answer is the same whether or not the email exists."""
    sent = False
    if request.method == 'POST':
        email = request.POST.get('email', '')
        if '@' in email and len(email) <= 254:
            request_login_link(request, email)
        sent = True
    response = render(request, 'registration/login_link.html', {'title': 'Entrar con un enlace', 'sent': sent, 'message': LINK_SENT})
    response['Cache-Control'] = 'no-store'
    return response


def login_link_use(request, token):
    """GET only shows a button: mail scanners that open links must not spend a one-use link."""
    if request.method == 'POST':
        user = use_login_token(token)
        if user is not None:
            login(request, user, backend='django.contrib.auth.backends.ModelBackend')
            return redirect('home')
        valid = False
    else:
        valid = live_token(token) is not None
    response = render(request, 'registration/login_link_use.html', {'title': 'Entrar', 'valid': valid}, status=200 if valid else 410)
    response['Cache-Control'] = 'no-store'
    response['Referrer-Policy'] = 'no-referrer'
    return response
