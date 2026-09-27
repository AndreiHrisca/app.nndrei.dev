import pytest
from django_otp.plugins.otp_totp.models import TOTPDevice
from core.models import User, Business, Assignment, ClientMembership

@pytest.fixture
def accounts(db):
    return {role: User.objects.create_user(role + '@example.com', 'Valid-test-password-829!', name=role, role=role) for role in ['admin', 'developer', 'accountant', 'client']}

@pytest.fixture
def businesses(accounts):
    own = Business.objects.create(name='Mi negocio')
    other = Business.objects.create(name='Otro negocio')
    Assignment.objects.create(developer=accounts['developer'], business=own)
    ClientMembership.objects.create(user=accounts['client'], business=own)
    return own, other

@pytest.fixture
def sign_in(client, accounts):
    def sign(role):
        user = accounts[role]
        client.force_login(user)
        device = TOTPDevice.objects.create(user=user, confirmed=True)
        session = client.session
        session['otp_device_id'] = device.persistent_id
        session.save()
        return client
    return sign

@pytest.fixture(autouse=True)
def test_settings(settings):
    settings.PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
    settings.STORAGES = {'default': {'BACKEND': 'django.core.files.storage.InMemoryStorage'}, 'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}}
