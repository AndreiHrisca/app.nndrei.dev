"""Captar / Descartar in "Buscar clientes"."""
import pytest
from core import labels
from core.models import ActivityEvent, Business, PlaceSnapshot, SearchCategory, SearchZone

pytestmark = pytest.mark.django_db


@pytest.fixture
def opportunities(db):
    zone = SearchZone.objects.create(name='Villaverde', latitude=40.4, longitude=-3.7)
    category = SearchCategory.objects.create(name='Bares', color='yellow')
    return [PlaceSnapshot.objects.create(place_id=f'place-{i}', name=f'Bar {i}', zone=zone, category=category, rating=4.7, reviews=1960, photos=8, has_website=False, phone='600 123 456') for i in range(3)]


def counter(body, label):
    """Read one of the counters above the table out of the rendered stats block."""
    marker = f'>{label}</span><div class="stat">'
    start = body.index(marker) + len(marker)
    return int(body[start:body.index('<', start)])


def test_capture_creates_the_business_and_updates_counters(sign_in, opportunities):
    client = sign_in('admin')
    body = client.get('/buscar/').content.decode()
    assert counter(body, 'En pipeline') == 0 and counter(body, 'Sin web') == 3
    assert labels.CAPTURE in body and 'Al pipeline' not in body

    target = opportunities[0]
    response = client.post(f'/buscar/{target.public_id}/captar/', HTTP_HX_REQUEST='true')
    assert response.status_code == 200
    fragment = response.content.decode()

    target.refresh_from_db()
    assert target.status == 'pipeline'
    business = Business.objects.get(name='Bar 0')
    assert business.category.name == 'Bares' and business.phone == '600 123 456'
    assert ActivityEvent.objects.filter(business=business, text=labels.CAPTURE_ACTIVITY).exists()

    # The swapped row shows the new state and a way back to the card...
    assert 'En pipeline' in fragment and business.get_absolute_url() in fragment and 'Ver ficha' in fragment
    # ...and the counters come back out of band.
    assert 'hx-swap-oob="true"' in fragment
    assert counter(fragment, 'En pipeline') == 1


def test_discard_updates_state_and_counters(sign_in, opportunities):
    client = sign_in('admin')
    target = opportunities[1]
    response = client.post(f'/buscar/{target.public_id}/descartar/', {'notes': 'Ya tiene proveedor'}, HTTP_HX_REQUEST='true')
    assert response.status_code == 200
    fragment = response.content.decode()
    target.refresh_from_db()
    assert target.status == 'discarded' and target.notes == 'Ya tiene proveedor'
    assert counter(fragment, 'Descartados') == 1
    assert counter(fragment, 'En pipeline') == 0
    assert not Business.objects.filter(name='Bar 1').exists()


def test_discard_asks_for_confirmation_and_capture_does_not(sign_in, opportunities):
    body = sign_in('admin').get('/buscar/').content.decode()
    row = body[body.index(f'oportunidad-{opportunities[0].public_id}'):]
    row = row[:row.index('</tr>')]
    assert 'hx-confirm' in row
    assert row.index('hx-confirm') > row.index(f'/buscar/{opportunities[0].public_id}/captar/')


def test_capture_is_idempotent_under_a_double_click(sign_in, opportunities):
    client = sign_in('admin')
    target = opportunities[2]
    assert client.post(f'/buscar/{target.public_id}/captar/', HTTP_HX_REQUEST='true').status_code == 200
    assert client.post(f'/buscar/{target.public_id}/captar/', HTTP_HX_REQUEST='true').status_code == 200
    assert Business.objects.filter(name='Bar 2').count() == 1


def test_rows_in_pipeline_offer_a_link_instead_of_an_empty_cell(sign_in, opportunities):
    client = sign_in('admin')
    client.post(f'/buscar/{opportunities[0].public_id}/captar/', HTTP_HX_REQUEST='true')
    body = client.get('/buscar/').content.decode()
    assert 'Ver ficha' in body


def test_numbers_are_formatted_and_right_aligned(sign_in, opportunities):
    body = sign_in('admin').get('/buscar/').content.decode()
    assert '<td class="number">1.960</td>' in body
    assert '<td class="number">4,7</td>' in body
    assert '<th class="number">Reseñas</th>' in body and '<th class="number">Nota</th>' in body


@pytest.mark.parametrize('role', ['developer', 'accountant', 'client'])
def test_only_admin_can_act_on_opportunities(sign_in, opportunities, role):
    client = sign_in(role)
    assert client.post(f'/buscar/{opportunities[0].public_id}/captar/', HTTP_HX_REQUEST='true').status_code == 404
    assert client.post(f'/buscar/{opportunities[0].public_id}/descartar/', HTTP_HX_REQUEST='true').status_code == 404
