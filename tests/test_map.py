from io import StringIO
from unittest.mock import Mock
import pytest
from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from core.models import Business, PlaceSnapshot, PlacesUsage, SearchCategory
from core.geocoding import geocode_business
from core import geo_sources

pytestmark = pytest.mark.django_db
URL = '/seguimiento/mapa/datos/'


def test_map_login(client):
    assert client.get(URL).status_code == 302


@pytest.mark.parametrize('role', ['client', 'developer', 'accountant'])
def test_map_roles(sign_in, role):
    assert sign_in(role).get(URL).status_code == 404


def test_map_filter_and_private_fields(sign_in, area):
    cat = SearchCategory.objects.create(name='Bares')
    b = Business.objects.create(name='Bar', stage='proposal', category=cat, neighbourhood=area.neighbourhood,
                                address='Calle Uno', latitude=40.4, longitude=-3.7,
                                opening_hours=[{'open': {'day': 0, 'hour': 0}}], internal_notes='secreto', phone='privado')
    missing = Business.objects.create(name='Sin ubicación')
    Business.objects.create(name='Descartado', stage='discarded', latitude=40, longitude=-3)
    client = sign_in('admin')
    data = client.get(URL).json()['negocios']
    assert {r['id'] for r in data} == {str(b.public_id), str(missing.public_id)}
    filtered = client.get(URL, {'fase': 'proposal'}).json()['negocios']
    assert len(filtered) == 1
    assert filtered[0]['barrio'] == area.neighbourhood.name
    assert filtered[0]['abierto'] is True
    assert filtered[0]['color'] == '#FAD9D5'
    assert 'telefono' not in filtered[0] and 'internal_notes' not in filtered[0]
    assert client.get(URL, {'fase': 'discarded'}).json()['negocios'] == []
    assert len(client.get(URL, {'fase': 'invalida'}).json()['negocios']) == 2
    assert 'no-store' in client.get(URL)['Cache-Control']


def test_map_queries_do_not_grow(sign_in, area):
    cat = SearchCategory.objects.create(name='Bares')
    client = sign_in('admin')
    def create(n):
        Business.objects.bulk_create([Business(name=f'Bar {i}', category=cat, neighbourhood=area.neighbourhood,
                                              latitude=40.4, longitude=-3.7) for i in range(n)])
    create(1)
    with CaptureQueriesContext(connection) as small:
        assert client.get(URL).status_code == 200
    create(40)
    with CaptureQueriesContext(connection) as large:
        assert len(client.get(URL).json()['negocios']) == 41
    assert len(large) == len(small)
    business_queries = [q for q in large.captured_queries if 'FROM "core_business"' in q['sql']]
    assert len(business_queries) == 1


def test_map_page_keeps_view_and_phase(sign_in):
    Business.objects.create(name='Bar', stage='proposal')
    body = sign_in('admin').get('/', {'vista': 'mapa', 'fase': 'proposal'}).content.decode()
    assert 'data-map-url=' in body and 'vista=mapa&fase=found' in body
    assert 'Ver lista' in body and '1 en Propuesta' in body
    assert 'pipeline-table' not in body


def test_command_uses_snapshot_without_api(monkeypatch, area):
    category = SearchCategory.objects.create(name='Bares')
    Business.objects.create(name='Bar', google_place_id='saved')
    PlaceSnapshot.objects.create(name='Bar', place_id='saved', category=category, city=area.city,
                                 neighbourhood=area.neighbourhood, latitude=40.4, longitude=-3.7)
    monkeypatch.setattr('core.geocoding.requests.get', lambda *a, **k: pytest.fail('API unnecessary'))
    out = StringIO(); call_command('geocodificar_clientes', stdout=out)
    b = Business.objects.get(name='Bar')
    assert b.latitude == 40.4 and b.neighbourhood == area.neighbourhood and b.geocoded_at
    assert '1 clientes ubicados; 0 sin ubicación' in out.getvalue()


def test_command_minimal_mask_and_quota(monkeypatch, settings):
    settings.GOOGLE_PLACES_API_KEY = 'test'; settings.PLACES_MONTHLY_LIMIT = 1
    Business.objects.create(name='Primero', google_place_id='one')
    Business.objects.create(name='Segundo', google_place_id='two')
    calls = []
    def get(url, **kwargs):
        calls.append(kwargs)
        return Mock(json=lambda: {'location': {'latitude': 40.4, 'longitude': -3.7}})
    monkeypatch.setattr('core.geocoding.requests.get', get)
    out = StringIO(); call_command('geocodificar_clientes', stdout=out)
    assert len(calls) == 1 and calls[0]['headers']['X-Goog-FieldMask'] == 'location'
    assert PlacesUsage.objects.get(month=timezone.localdate().replace(day=1)).requests == 1
    assert Business.objects.get(name='Segundo').latitude is None
    assert 'Límite mensual' in out.getvalue()


def test_command_nominatim_idempotent_and_force(monkeypatch, area):
    b = Business.objects.create(name='Manual', address='Calle Uno, Madrid')
    calls = []
    def geocode(text):
        calls.append(text); return 40.4, -3.7, area.city
    monkeypatch.setattr(geo_sources, 'geocode_address', geocode)
    call_command('geocodificar_clientes', stdout=StringIO())
    call_command('geocodificar_clientes', stdout=StringIO())
    assert len(calls) == 1
    call_command('geocodificar_clientes', forzar=True, stdout=StringIO())
    assert len(calls) == 2
    b.refresh_from_db(); assert b.neighbourhood == area.neighbourhood


def test_command_reports_failures_and_retries(monkeypatch):
    Business.objects.create(name='Sin datos')
    b = Business.objects.create(name='Dirección', address='Calle inventada')
    monkeypatch.setattr(geo_sources, 'geocode_address', lambda text: None)
    out = StringIO(); call_command('geocodificar_clientes', stdout=out)
    assert 'Sin dirección ni ficha de Google' in out.getvalue() and 'No se ha encontrado' in out.getvalue()
    monkeypatch.setattr(geo_sources, 'geocode_address', lambda text: (40.4, -3.7, None))
    call_command('geocodificar_clientes', stdout=StringIO())
    b.refresh_from_db(); assert b.latitude == 40.4


def test_nominatim_user_agent_and_rate_limit(monkeypatch, settings):
    calls = []
    # Load an isolated helper so the autouse network block stays intact.
    import importlib.util
    spec = importlib.util.spec_from_file_location('core._test_geo_sources', geo_sources.__file__)
    helper = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(helper)
    monkeypatch.setattr(helper, 'wait_turn', lambda service, seconds: calls.append((service, seconds)))
    get = Mock(return_value=Mock(json=lambda: []))
    monkeypatch.setattr(helper.requests, 'get', get)
    helper.nominatim(q='Madrid')
    assert calls == [('nominatim', 1.1)]
    assert get.call_args.kwargs['headers']['User-Agent'] == settings.GEO_USER_AGENT


def test_address_change_clears_old_location(sign_in, monkeypatch):
    b = Business.objects.create(name='Bar', address='Vieja', latitude=40.4, longitude=-3.7, google_place_id='old')
    queued = []
    monkeypatch.setattr(geo_sources, 'queue_placing', lambda item: queued.append(item.pk))
    response = sign_in('admin').post(f'/clientes/{b.public_id}/editar/', {'name': 'Bar', 'address': 'Nueva', 'stage': 'found'})
    assert response.status_code == 302
    b.refresh_from_db()
    assert b.latitude is None and b.longitude is None and b.google_place_id is None and queued == [b.pk]


def test_location_details_does_not_renew_old_google_hours(monkeypatch, settings):
    from datetime import timedelta
    from core.tasks import expire_google_data
    settings.GOOGLE_PLACES_API_KEY = 'test'
    old = timezone.now() - timedelta(days=31)
    b = Business.objects.create(name='Bar', google_place_id='one', google_fetched_at=old,
                                hours_source='google', opening_hours=[{'open': {'day': 0}}])
    monkeypatch.setattr('core.geocoding.requests.get', Mock(return_value=Mock(json=lambda: {'location': {'latitude': 40.4, 'longitude': -3.7}})))
    geocode_business(b)
    assert b.google_fetched_at == old
    expire_google_data(); b.refresh_from_db()
    assert b.latitude == 40.4 and b.opening_hours is None


def test_command_google_timeout_continues_without_coordinates(monkeypatch, settings):
    import requests
    settings.GOOGLE_PLACES_API_KEY = 'test'
    b = Business.objects.create(name='Bar', google_place_id='timeout')
    monkeypatch.setattr('core.geocoding.requests.get', Mock(side_effect=requests.Timeout()))
    out = StringIO(); call_command('geocodificar_clientes', stdout=out)
    b.refresh_from_db()
    assert b.latitude is None and 'Google Places no ha respondido' in out.getvalue()


def test_force_reports_failure_even_when_old_coordinates_remain(monkeypatch, settings):
    import requests
    settings.GOOGLE_PLACES_API_KEY = 'test'
    b = Business.objects.create(name='Bar', google_place_id='timeout', latitude=40.4, longitude=-3.7)
    monkeypatch.setattr('core.geocoding.requests.get', Mock(side_effect=requests.Timeout()))
    out = StringIO(); call_command('geocodificar_clientes', forzar=True, stdout=out)
    b.refresh_from_db()
    assert b.latitude == 40.4 and 'Google Places no ha respondido' in out.getvalue()
    assert '0 clientes ubicados' in out.getvalue()
