import pytest
from django.urls import reverse
from django.utils import timezone
from core.models import Business, Invitation, User

pytestmark = pytest.mark.django_db

@pytest.mark.parametrize('role,count', [('admin',2),('accountant',2),('developer',1),('client',1)])
def test_scope(accounts,businesses,role,count):
    assert Business.objects.visible_to(accounts[role]).count() == count

@pytest.mark.parametrize('role', ['admin','developer','accountant'])
def test_mandatory_otp(client,accounts,role):
    client.force_login(accounts[role])
    assert client.get('/clientes/').url == '/2fa/'
    assert client.get('/admin/').url == '/2fa/'

@pytest.mark.parametrize('role,expected',[('admin',200),('developer',404),('accountant',404),('client',404)])
def test_users_permission(sign_in,role,expected):
    client=sign_in(role)
    assert client.get('/usuarios/').status_code == expected
    if role != 'admin':
        assert client.post('/usuarios/',{},HTTP_HX_REQUEST='true').status_code == expected

def test_invitation_single_use(client,accounts):
    inv=Invitation.objects.create(email='new@example.com',role='developer',invited_by=accounts['admin'])
    response=client.post(reverse('accept_invitation',args=[inv.token]), {'name':'Nueva persona','new_password1':'Unique-Strong-password-837!','new_password2':'Unique-Strong-password-837!'})
    assert response.status_code==302
    inv.refresh_from_db()
    assert inv.accepted_at
    assert User.objects.get(email='new@example.com').check_password('Unique-Strong-password-837!')
    client.logout()
    assert client.get(reverse('accept_invitation',args=[inv.token])).status_code==404

def test_expired_invitation(client,accounts):
    inv=Invitation.objects.create(email='old@example.com',role='client',invited_by=accounts['admin'],expires_at=timezone.now())
    assert client.get(reverse('accept_invitation',args=[inv.token])).status_code==404

def test_admin_can_manage_assignments(sign_in,accounts,businesses):
    from core.models import Assignment
    response=sign_in('admin').post(f'/usuarios/{accounts["developer"].public_id}/',{'name':'Dev','role':'developer','is_active':'on','businesses':[businesses[1].public_id]})
    assert response.status_code==302
    assert list(Assignment.objects.filter(developer=accounts['developer']).values_list('business_id',flat=True))==[businesses[1].pk]

def test_admin_cannot_disable_self(sign_in,accounts):
    response=sign_in('admin').post(f'/usuarios/{accounts["admin"].public_id}/',{'name':'Admin','role':'client'})
    assert response.status_code==200
    accounts['admin'].refresh_from_db()
    assert accounts['admin'].role=='admin' and accounts['admin'].is_active

def test_csrf_protects_htmx(accounts):
    from django.test import Client
    from django_otp.plugins.otp_totp.models import TOTPDevice
    c=Client(enforce_csrf_checks=True); c.force_login(accounts['admin'])
    d=TOTPDevice.objects.create(user=accounts['admin'],confirmed=True)
    session=c.session; session['otp_device_id']=d.persistent_id; session.save()
    assert c.post('/buscar/actualizar/',HTTP_HX_REQUEST='true').status_code==403

def test_otp_challenge_replay(client,accounts):
    from django_otp.plugins.otp_totp.models import TOTPDevice
    from django_otp.oath import totp
    u=accounts['admin']; client.force_login(u)
    d=TOTPDevice.objects.create(user=u,confirmed=False)
    code=totp(d.bin_key,step=d.step,t0=d.t0,digits=d.digits,drift=d.drift)
    assert client.post('/2fa/',{'token':str(code).zfill(6)}).status_code==302
    assert client.get('/usuarios/').status_code==200
    client.logout(); client.force_login(u)
    assert client.get('/usuarios/').url=='/2fa/'
    assert client.post('/2fa/',{'token':str(code).zfill(6)}).status_code==200
    assert client.get('/usuarios/').url=='/2fa/'

def test_login_rate_limit(client,accounts):
    for _ in range(6):
        response=client.post('/login/',{'username':accounts['admin'].email,'password':'wrong'})
    assert response.status_code==429

def test_invitation_email_and_optional_client_otp(sign_in,accounts,businesses,django_capture_on_commit_callbacks,mailoutbox):
    c=sign_in('admin')
    with django_capture_on_commit_callbacks(execute=True):
        response=c.post('/usuarios/',{'email':'invited@example.com','role':'client','business':businesses[0].public_id})
    assert response.status_code==302 and len(mailoutbox)==1
    inv=Invitation.objects.get(email='invited@example.com')
    assert inv.token in mailoutbox[0].body
    c.logout()
    response=c.post(reverse('accept_invitation',args=[inv.token]),{'name':'Invitado','new_password1':'Unique-invitation-password-381!','new_password2':'Unique-invitation-password-381!'})
    assert response.status_code==302
    assert c.get('/portal/').status_code==200
