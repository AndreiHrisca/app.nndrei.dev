"""The Clientes table: extra columns, filters, sorting and query count."""
from datetime import date, timedelta
import pytest
from django.utils import timezone
from core.models import ActivityEvent, Business, PlaceSnapshot, Proposal, ProposalVersion, SearchCategory, SearchZone

pytestmark = pytest.mark.django_db


@pytest.fixture
def catalogue(accounts, businesses):
    own, other = businesses
    zone = SearchZone.objects.create(name='Villaverde', latitude=40.4, longitude=-3.7)
    bares = SearchCategory.objects.create(name='Bares', color='yellow')
    peluquerias = SearchCategory.objects.create(name='Peluquerías', color='pink')
    own.category, own.phone, own.zone = bares, '600 123 456', 'Villaverde'
    own.next_action, own.next_action_date = 'Llamar a Santiago', date(2026, 10, 1)
    own.stage = 'proposal'
    own.save()
    other.category = peluquerias
    other.save()
    PlaceSnapshot.objects.create(place_id='snap', name=own.name, zone=zone, category=bares, rating=4.7, reviews=1960, photos=9, has_website=True, business=own, status='pipeline')
    ActivityEvent.objects.create(business=own, kind='test', text='Algo pasó', occurred_at=timezone.now() - timedelta(days=2))
    proposal = Proposal.objects.create(business=own, number='P-2026-LIST')
    ProposalVersion.objects.create(proposal=proposal, number=1, development_total=1500)
    return own, other


def test_table_shows_the_working_columns(sign_in, catalogue):
    own, _ = catalogue
    body = sign_in('admin').get('/clientes/').content.decode()
    for needle in ['Bares', 'tel:600123456', '4,7 · 1.960', 'Tiene web', 'hace 2', 'Llamar a Santiago', '1.500 €', 'Villaverde']:
        assert needle in body, needle
    assert own.get_absolute_url() in body


def test_search_filters_and_sorts(sign_in, catalogue):
    own, other = catalogue
    client = sign_in('admin')
    body = client.get('/clientes/', {'q': 'Otro'}, HTTP_HX_REQUEST='true').content.decode()
    assert other.name in body and own.name not in body
    # The HTMX response is only the table, not the whole page.
    assert '<aside' not in body and 'id="clients-table"' in body

    body = client.get('/clientes/', {'stage': 'proposal'}).content.decode()
    assert own.name in body and other.name not in body

    body = client.get('/clientes/', {'category': other.category_id}).content.decode()
    assert other.name in body and own.name not in body

    ascending = client.get('/clientes/', {'sort': 'negocio'}).content.decode()
    descending = client.get('/clientes/', {'sort': '-negocio'}).content.decode()
    assert ascending.index(own.name) < ascending.index(other.name)
    assert descending.index(other.name) < descending.index(own.name)


def test_sort_parameter_cannot_reach_the_orm(sign_in, catalogue):
    assert sign_in('admin').get('/clientes/', {'sort': 'internal_notes'}).status_code == 200


def test_listing_does_not_scale_with_the_number_of_businesses(sign_in, catalogue, django_assert_max_num_queries):
    bares = SearchCategory.objects.get(name='Bares')
    for i in range(12):
        Business.objects.create(name=f'Negocio {i}', category=bares)
    client = sign_in('admin')
    with django_assert_max_num_queries(8):
        assert client.get('/clientes/').status_code == 200


def test_developer_only_sees_assigned_businesses(sign_in, catalogue):
    own, other = catalogue
    body = sign_in('developer').get('/clientes/').content.decode()
    assert own.name in body and other.name not in body
