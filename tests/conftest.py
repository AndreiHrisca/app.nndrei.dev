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


@pytest.fixture(autouse=True)
def no_geo_network(monkeypatch):
    """No test reaches INE, Madrid or OpenStreetMap unless it mocks them itself."""
    from core import geo_sources

    def offline(*args, **kwargs):
        raise geo_sources.GeoError('Sin red en las pruebas.')
    for name in ['download', 'nominatim', 'overpass']:
        monkeypatch.setattr(geo_sources, name, offline)


SQUARE = [[-3.705, 40.395], [-3.695, 40.395], [-3.695, 40.405], [-3.705, 40.405], [-3.705, 40.395]]


@pytest.fixture
def area(db):
    """A small test neighbourhood in Madrid (one search cell) as an active search area."""
    from django.utils import timezone
    from core.models import City, Neighbourhood, SearchArea
    madrid = City.objects.get(ine_code='28079')
    madrid.neighbourhoods_fetched_at = timezone.now()
    madrid.save()
    barrio = Neighbourhood.objects.create(city=madrid, name='Barrio de Prueba', search_name='barrio de prueba', source='madrid', external_id='T1',
                                          bbox=[40.395, -3.705, 40.405, -3.695], geometry={'type': 'Polygon', 'coordinates': [SQUARE]},
                                          latitude=40.4, longitude=-3.7)
    return SearchArea.objects.create(city=madrid, neighbourhood=barrio, active=True)
