"""Fase 2: going to a business, "He ido", next steps and discarded businesses."""
from datetime import timedelta
from urllib.parse import parse_qs, urlparse
import pytest
from django.utils import timezone
from core.models import ActivityEvent, Business, FollowUp, StageChange, Visit
from core.visits import directions

pytestmark = pytest.mark.django_db


def query(url):
    return {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}


def test_directions_with_coordinates_and_place_id():
    b = Business(name='Bar', latitude=40.4168, longitude=-3.7038, google_place_id='ChIJ123', phone='600 11 22 33', address='Calle Mayor 1')
    go = directions(b)
    assert go['google'].startswith('https://www.google.com/maps/dir/?')
    assert query(go['google']) == {'api': '1', 'destination': '40.4168,-3.7038', 'destination_place_id': 'ChIJ123'}
    assert query(go['waze']) == {'ll': '40.4168,-3.7038', 'navigate': 'yes'} and go['waze'].startswith('https://waze.com/ul?')
    assert go['apple'] == 'https://maps.apple.com/?daddr=40.4168%2C-3.7038'
    assert go['phone'] == 'tel:600112233'


def test_directions_fall_back_to_the_encoded_address():
    b = Business(name='Bar', address='Calle Mayor 1, Madrid')
    go = directions(b)
    assert query(go['google']) == {'api': '1', 'destination': 'Calle Mayor 1, Madrid'}
    assert 'Calle+Mayor+1%2C+Madrid' in go['google']
    assert query(go['waze']) == {'q': 'Calle Mayor 1, Madrid', 'navigate': 'yes'}
    assert query(go['apple']) == {'daddr': 'Calle Mayor 1, Madrid'} and go['phone'] == ''
    assert query(directions(Business(name='Bar Pepe', zone='Villaverde'))['apple']) == {'daddr': 'Bar Pepe Villaverde'}


def he_ido(client, b, **extra):
    data = {'date': timezone.localtime().strftime('%Y-%m-%dT%H:%M'), 'result': 'Interesado', 'talked_to': 'Santiago', 'notes': 'Quiere carta online'}
    data.update(extra)
    return client.post(f'/clientes/{b.public_id}/visita/', data, HTTP_HX_REQUEST='true')


@pytest.mark.parametrize('stage', ['found', 'profile_created'])
def test_visit_moves_an_earlier_business_to_visited(sign_in, accounts, businesses, stage):
    b = businesses[0]; b.stage = stage; b.save()
    response = he_ido(sign_in('admin'), b)
    assert response.status_code == 204 and response['HX-Redirect'] == b.get_absolute_url()
    b.refresh_from_db()
    assert b.stage == 'visited'
    assert StageChange.objects.filter(business=b, from_stage=stage, to_stage='visited', actor=accounts['admin']).exists()
    visit = Visit.objects.get(business=b)
    assert visit.visitor == accounts['admin'] and visit.talked_to == 'Santiago'
    event = ActivityEvent.objects.get(business=b, kind='visit')
    assert 'He ido: Interesado · Hablé con Santiago' in event.text and not event.client_visible


@pytest.mark.parametrize('stage', ['visited', 'proposal', 'approved', 'production', 'discarded'])
def test_visit_never_moves_back(sign_in, businesses, stage):
    b = businesses[0]; b.stage = stage; b.save()
    he_ido(sign_in('admin'), b)
    b.refresh_from_db()
    assert b.stage == stage and not StageChange.objects.filter(business=b).exists()


def test_next_step_with_date_feeds_next_actions(sign_in, businesses):
    b = businesses[0]
    day = timezone.localdate() + timedelta(days=3)
    he_ido(sign_in('admin'), b, next_step='Enseñar la demo', followup_date=day.isoformat(), followup_kind='call')
    followup = FollowUp.objects.get(business=b)
    assert followup.description == 'Enseñar la demo' and followup.date == day and followup.kind == 'call' and not followup.done
    assert Visit.objects.get(business=b).followup == followup
    b.refresh_from_db()
    assert b.next_action == 'Enseñar la demo' and b.next_action_date == day
    page = sign_in('admin').get('/').content.decode()
    assert 'Enseñar la demo' in page


def test_next_step_without_date_only_sets_the_next_action(sign_in, businesses):
    b = businesses[0]
    he_ido(sign_in('admin'), b, next_step='Volver con el dueño')
    assert not FollowUp.objects.filter(business=b).exists()
    b.refresh_from_db()
    assert b.next_action == 'Volver con el dueño' and b.next_action_date is None


def test_date_without_next_step_is_rejected(sign_in, businesses):
    b = businesses[0]
    response = he_ido(sign_in('admin'), b, followup_date=timezone.localdate().isoformat())
    assert response.status_code == 200 and 'Indica qué toca hacer en esa fecha.' in response.content.decode()
    assert not Visit.objects.exists()


def test_overdue_actions_stand_out_in_order(sign_in, accounts, businesses):
    b = businesses[0]; b.stage = 'visited'; b.save()
    today = timezone.localdate()
    FollowUp.objects.create(business=b, description='Futura', date=today + timedelta(days=2), owner=accounts['admin'], kind='call')
    FollowUp.objects.create(business=b, description='Atrasada', date=today - timedelta(days=2), owner=accounts['admin'], kind='call')
    FollowUp.objects.create(business=b, description='Para hoy', date=today, owner=accounts['admin'], kind='call')
    page = sign_in('admin').get('/').content.decode()
    panel = page[page.index('Próximas acciones'):]
    assert panel.index('Atrasada') < panel.index('Para hoy') < panel.index('Futura')
    assert 'followup is-overdue' in panel and 'Vencida · </span>Atrasada' in panel
    # The pipeline column shows the nearest step, flagged as overdue.
    table = page[:page.index('Próximas acciones')]
    assert 'Atrasada' in table and 'is-overdue' in table


def test_discarded_are_hidden_by_default_and_listed_on_request(sign_in, businesses):
    b = businesses[1]; b.stage = 'discarded'; b.discarded_reason = 'Ya tiene web'; b.save()
    c = sign_in('admin')
    assert 'Otro negocio' not in c.get('/clientes/', HTTP_HX_REQUEST='true').content.decode()
    body = c.get('/clientes/?stage=discarded', HTTP_HX_REQUEST='true').content.decode()
    assert 'Otro negocio' in body and 'Mi negocio' not in body


def test_discard_needs_a_reason_and_reactivate_returns_to_the_previous_stage(sign_in, businesses):
    b = businesses[0]; b.stage = 'visited'; b.save()
    c = sign_in('admin')
    assert c.post(f'/clientes/{b.public_id}/fase/', {'stage': 'discarded'}).status_code == 400
    c.post(f'/clientes/{b.public_id}/fase/', {'stage': 'discarded', 'reason': 'No le interesa ahora'})
    b.refresh_from_db()
    assert b.stage == 'discarded' and b.discarded_reason == 'No le interesa ahora'
    page = c.get(f'/clientes/{b.public_id}/').content.decode()
    assert 'No le interesa ahora' in page and 'Reactivar' in page
    c.post(f'/clientes/{b.public_id}/reactivar/')
    b.refresh_from_db()
    assert b.stage == 'visited' and b.discarded_reason == ''
    assert c.post(f'/clientes/{b.public_id}/reactivar/').status_code == 400


def test_business_page_offers_the_go_block(sign_in, businesses):
    b = businesses[0]; b.latitude, b.longitude, b.phone = 40.1, -3.2, '911 22 33 44'; b.save()
    page = sign_in('admin').get(f'/clientes/{b.public_id}/').content.decode()
    for text in ['Ir al negocio', 'Google Maps', 'Waze', 'Apple Maps', 'He ido', 'tel:911223344', 'destination=40.1%2C-3.2']:
        assert text in page
    developer = sign_in('developer').get(f'/clientes/{b.public_id}/').content.decode()
    assert 'Google Maps' in developer and 'm-visit-sheet' not in developer
