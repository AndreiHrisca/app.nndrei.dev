"""Fase 3: second step per role, remembered devices and email login links."""
from datetime import timedelta
from unittest import mock
import pytest
from django.utils import timezone
from django_otp.oath import totp
from django_otp.plugins.otp_totp.models import TOTPDevice
from core.models import DEFAULT_TWO_FACTOR, EmailOutbox, LoginLinkRequest, LoginToken, TrustedDevice, TwoFactorPolicy
from core.security import TRUSTED_COOKIE, digest

pytestmark = pytest.mark.django_db
PASSWORD = 'Valid-test-password-829!'
LANDING = {'admin': '/', 'developer': '/clientes/', 'accountant': '/clientes/', 'client': '/portal/'}


def code(device):
    return str(totp(device.bin_key, step=device.step, t0=device.t0, digits=device.digits, drift=device.drift)).zfill(6)


def set_policy(role, value):
    TwoFactorPolicy.objects.update_or_create(role=role, defaults={'policy': value})


def password_login(client, user):
    response = client.post('/login/', {'username': user.email, 'password': PASSWORD})
    assert response.status_code == 302, response.content
    return client.get(LANDING[user.role])


def test_defaults_are_installed_by_the_migration():
    assert dict(TwoFactorPolicy.objects.values_list('role', 'policy')) == DEFAULT_TWO_FACTOR == {
        'admin': 'required', 'developer': 'optional', 'accountant': 'optional', 'client': 'disabled'}


@pytest.mark.parametrize('role', ['admin', 'developer', 'accountant', 'client'])
@pytest.mark.parametrize('value,has_device,asks', [
    ('required', False, True),   # Must enrol.
    ('required', True, True),
    ('optional', False, False),  # Not turned on: straight in.
    ('optional', True, True),    # Turned on: it is used.
    ('disabled', False, False),
    ('disabled', True, True),    # Already on before the policy changed: kept until turned off.
])
def test_login_flow_per_role_and_policy(client, accounts, role, value, has_device, asks):
    set_policy(role, value)
    user = accounts[role]
    if has_device:
        TOTPDevice.objects.create(user=user, confirmed=True)
    response = password_login(client, user)
    if asks:
        assert response.status_code == 302 and response.url == '/2fa/'
    else:
        assert response.status_code == 200


def test_optional_user_turns_it_on_and_off(client, accounts):
    user = accounts['developer']
    password_login(client, user)
    assert 'Activar' in client.get('/seguridad/').content.decode()
    client.get('/2fa/')  # Creates the pending device and shows the QR.
    device = TOTPDevice.objects.get(user=user)
    response = client.post('/2fa/', {'token': code(device)})
    assert response.url == '/seguridad/' and TOTPDevice.objects.get(user=user).confirmed
    # Turning it off needs a fresh code.
    assert client.post('/seguridad/desactivar/', {'token': '000000'}).status_code == 302 and TOTPDevice.objects.filter(user=user).exists()
    device = TOTPDevice.objects.get(user=user)
    # A wrong code throttles the next attempt; reset that as if the wait had passed.
    device.last_t = -1; device.throttling_failure_count = 0; device.throttling_failure_timestamp = None; device.save()
    client.post('/seguridad/desactivar/', {'token': code(device)})
    assert not TOTPDevice.objects.filter(user=user).exists()
    client.logout()
    assert password_login(client, user).status_code == 200


def test_required_cannot_be_turned_off(sign_in, accounts):
    c = sign_in('admin')
    device = TOTPDevice.objects.get(user=accounts['admin'])
    assert 'no se puede desactivar' in c.get('/seguridad/').content.decode()
    c.post('/seguridad/desactivar/', {'token': code(device)})
    assert TOTPDevice.objects.filter(user=accounts['admin']).exists()


def test_disabled_role_is_not_offered_enrolment(client, accounts):
    password_login(client, accounts['client'])
    assert client.get('/2fa/').url == '/seguridad/'
    assert not TOTPDevice.objects.filter(user=accounts['client']).exists()


def logout_keeping_device(client):
    """The test client's logout() drops every cookie; a real browser keeps the device cookie."""
    kept = client.cookies.get(TRUSTED_COOKIE)
    client.logout()
    if kept is not None:
        client.cookies[TRUSTED_COOKIE] = kept.value


def verify_and_remember(client, user):
    device = TOTPDevice.objects.create(user=user, confirmed=True)
    password_login(client, user)
    response = client.post('/2fa/', {'token': code(device), 'remember': '1'})
    assert response.status_code == 302
    return response.cookies[TRUSTED_COOKIE]


def test_remembered_device_skips_the_code_for_thirty_days(client, accounts):
    user = accounts['admin']
    cookie = verify_and_remember(client, user)
    assert cookie['max-age'] == 30 * 86400 and cookie['httponly'] and cookie['samesite'] == 'Lax'
    trusted = TrustedDevice.objects.get(user=user)
    assert trusted.token_hash not in cookie.value  # Only the hash is stored.
    logout_keeping_device(client)
    assert password_login(client, user).status_code == 200
    trusted.refresh_from_db()
    assert trusted.last_used_at is not None
    # Expired: asks again.
    TrustedDevice.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    logout_keeping_device(client)
    assert password_login(client, user).url == '/2fa/'


def test_revoked_device_asks_again(client, accounts):
    user = accounts['admin']
    verify_and_remember(client, user)
    trusted = TrustedDevice.objects.get(user=user)
    assert trusted.label in client.get('/seguridad/').content.decode()
    client.post(f'/seguridad/dispositivos/{trusted.public_id}/olvidar/')
    assert TrustedDevice.objects.get(pk=trusted.pk).revoked_at is not None
    logout_keeping_device(client)
    assert password_login(client, user).url == '/2fa/'


def test_cookie_of_another_user_or_tampered_does_not_count(client, accounts):
    admin = accounts['admin']
    cookie = verify_and_remember(client, admin)
    client.logout()
    other = accounts['developer']
    set_policy('developer', 'required')
    TOTPDevice.objects.create(user=other, confirmed=True)
    client.cookies[TRUSTED_COOKIE] = cookie.value
    assert password_login(client, other).url == '/2fa/'
    client.logout()
    client.cookies[TRUSTED_COOKIE] = cookie.value[:-3] + 'abc'
    assert password_login(client, admin).url == '/2fa/'


def test_revoke_all(client, accounts):
    user = accounts['admin']
    verify_and_remember(client, user)
    client.post('/seguridad/dispositivos/olvidar-todos/')
    assert not TrustedDevice.objects.filter(user=user, revoked_at__isnull=True).exists()


# --- Email login link -------------------------------------------------------------

@pytest.fixture
def queue():
    with mock.patch('django_q.tasks.async_task') as task:
        yield task


def ask_link(client, email, capture):
    with capture(execute=True):
        return client.post('/entrar/enlace/', {'email': email})


def link_from_outbox():
    message = EmailOutbox.objects.latest('pk')
    return message, message.url[message.url.index('/entrar/'):]


def test_client_gets_a_link_through_the_queue(client, accounts, queue, django_capture_on_commit_callbacks, mailoutbox):
    response = ask_link(client, 'CLIENT@example.com', django_capture_on_commit_callbacks)
    assert 'Si ese email corresponde a un cliente' in response.content.decode()
    message, path = link_from_outbox()
    assert message.recipients == ['client@example.com'] and message.sensitive_until is not None
    queue.assert_called_once_with('core.notifications.send_outbox', message.pk)
    assert not mailoutbox  # Sent by the worker, not during the request.
    token = path.split('/')[2]
    assert LoginToken.objects.get().token_hash == digest(token)
    # The worker sends it and wipes the link from the outbox.
    from core.notifications import send_outbox
    send_outbox(message.pk)
    assert path in mailoutbox[0].body
    message.refresh_from_db()
    assert message.sent_at and message.url == ''


@pytest.mark.parametrize('email', ['nadie@example.com', 'admin@example.com', 'developer@example.com'])
def test_same_answer_and_no_email_for_unknown_or_staff(client, accounts, queue, django_capture_on_commit_callbacks, email):
    known = ask_link(client, 'client@example.com', django_capture_on_commit_callbacks).content.decode()
    other = ask_link(client, email, django_capture_on_commit_callbacks).content.decode()
    import re
    strip = lambda html: re.sub(r'(CSRFToken": "|csrfmiddlewaretoken" value=")[^"]+', '', html)
    assert strip(known) == strip(other)
    assert EmailOutbox.objects.count() == 1 and LoginToken.objects.count() == 1


def test_get_does_not_spend_the_link_and_post_signs_in(client, accounts, queue, django_capture_on_commit_callbacks):
    ask_link(client, 'client@example.com', django_capture_on_commit_callbacks)
    _, path = link_from_outbox()
    for _ in range(3):  # Mail scanners opening the link.
        response = client.get(path)
        assert response.status_code == 200 and 'Entrar' in response.content.decode()
    assert LoginToken.objects.get().used_at is None
    response = client.post(path)
    assert response.status_code == 302
    assert client.get('/portal/').status_code == 200  # Clients have no second step by default.
    client.logout()
    assert client.get(path).status_code == 410 and client.post(path).status_code == 410


def test_link_expires_after_fifteen_minutes(client, accounts, queue, django_capture_on_commit_callbacks):
    ask_link(client, 'client@example.com', django_capture_on_commit_callbacks)
    message, path = link_from_outbox()
    LoginToken.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    assert client.post(path).status_code == 410
    # An expired link is never sent late.
    EmailOutbox.objects.update(sensitive_until=timezone.now() - timedelta(seconds=1))
    from core.notifications import send_outbox
    send_outbox(message.pk)
    message.refresh_from_db()
    assert message.sent_at is None and message.url == ''


def test_using_one_link_spends_the_others(client, accounts, queue, django_capture_on_commit_callbacks):
    ask_link(client, 'client@example.com', django_capture_on_commit_callbacks)
    _, first = link_from_outbox()
    ask_link(client, 'client@example.com', django_capture_on_commit_callbacks)
    _, second = link_from_outbox()
    assert client.post(second).status_code == 302
    client.logout()
    assert client.post(first).status_code == 410


def test_rate_limits_per_email_and_per_ip(client, accounts, queue, django_capture_on_commit_callbacks):
    for _ in range(5):
        response = ask_link(client, 'client@example.com', django_capture_on_commit_callbacks)
        assert 'Si ese email corresponde a un cliente' in response.content.decode()
    assert LoginToken.objects.count() == 3
    LoginLinkRequest.objects.all().delete(); LoginToken.objects.all().delete()
    for i in range(10):
        ask_link(client, f'otro{i}@example.com', django_capture_on_commit_callbacks)
    ask_link(client, 'client@example.com', django_capture_on_commit_callbacks)
    assert LoginToken.objects.count() == 0


def test_link_then_second_step_if_the_client_turned_it_on(client, accounts, queue, django_capture_on_commit_callbacks):
    TOTPDevice.objects.create(user=accounts['client'], confirmed=True)
    ask_link(client, 'client@example.com', django_capture_on_commit_callbacks)
    _, path = link_from_outbox()
    client.post(path)
    assert client.get('/portal/').url == '/2fa/'


def test_password_login_still_works_for_clients(client, accounts):
    assert password_login(client, accounts['client']).status_code == 200


def test_policy_is_editable_in_admin(sign_in, accounts):
    accounts['admin'].is_staff = True; accounts['admin'].save()
    c = sign_in('admin')
    item = TwoFactorPolicy.objects.get(role='developer')
    response = c.post(f'/admin/core/twofactorpolicy/{item.pk}/change/', {'role': 'developer', 'policy': 'required'})
    assert response.status_code == 302
    assert TwoFactorPolicy.for_role('developer') == 'required'


def test_only_the_owner_can_forget_a_device(sign_in, accounts):
    device = TrustedDevice.objects.create(user=accounts['developer'], token_hash='x' * 64, expires_at=timezone.now() + timedelta(days=1))
    for role in ['admin', 'accountant', 'client']:
        assert sign_in(role).post(f'/seguridad/dispositivos/{device.public_id}/olvidar/').status_code == 404
    assert TrustedDevice.objects.get(pk=device.pk).revoked_at is None
    assert sign_in('developer').post(f'/seguridad/dispositivos/{device.public_id}/olvidar/').status_code == 302
    assert TrustedDevice.objects.get(pk=device.pk).revoked_at is not None
