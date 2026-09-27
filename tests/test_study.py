"""Estudio previo: internal groundwork, and the gate into "Ficha creada"."""
import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from core.models import ActivityEvent, Study

pytestmark = pytest.mark.django_db

FULL = {'who': 'Bar de barrio, unos 15 años', 'sells': 'Menú del día y tapas',
        'audience': 'Vecinos y oficinas cercanas', 'has_google': 'on', 'has_instagram': 'on',
        'links': 'https://instagram.com/barejemplo', 'competitors': 'La Parada, sin web',
        'needs': 'No aparecen en Google Maps con la carta actualizada',
        'opportunity': 'Web sencilla con carta y QR', 'best_time': 'Martes por la mañana',
        'contact_to_find': 'Santiago', 'notes': 'Local reformado hace poco'}


def test_study_is_created_and_logged(sign_in, businesses):
    own = businesses[0]
    client = sign_in('admin')
    body = client.get(f'/clientes/{own.public_id}/').content.decode()
    assert 'Estudio previo' in body and 'Sin empezar' in body

    response = client.post(f'/clientes/{own.public_id}/estudio/', FULL, HTTP_HX_REQUEST='true')
    assert response.status_code == 200
    study = Study.objects.get(business=own)
    assert study.sells == FULL['sells'] and study.has_google and not study.has_tiktok
    assert study.created_at is not None
    assert ActivityEvent.objects.filter(business=own, kind='study_created').count() == 1

    # Editing the same study logs an update, not a second creation.
    client.post(f'/clientes/{own.public_id}/estudio/', {**FULL, 'notes': 'Cambio'}, HTTP_HX_REQUEST='true')
    assert Study.objects.filter(business=own).count() == 1
    assert ActivityEvent.objects.filter(business=own, kind='study_updated').count() == 1


def test_mark_profile_created_is_blocked_until_the_minimum_is_there(sign_in, businesses):
    own = businesses[0]
    client = sign_in('admin')
    # A study missing both required answers names them and disables the button.
    client.post(f'/clientes/{own.public_id}/estudio/', {'who': 'Bar de barrio'}, HTTP_HX_REQUEST='true')
    body = client.get(f'/clientes/{own.public_id}/').content.decode()
    assert 'disabled' in body and 'Falta rellenar' in body
    assert 'Qué venden / servicios principales' in body and 'Necesidades observadas' in body

    # Only one of the two is still not enough.
    client.post(f'/clientes/{own.public_id}/estudio/', {'who': 'Bar', 'sells': 'Menús'}, HTTP_HX_REQUEST='true')
    body = client.get(f'/clientes/{own.public_id}/').content.decode()
    assert 'Falta rellenar: Necesidades observadas' in body

    client.post(f'/clientes/{own.public_id}/estudio/', FULL, HTTP_HX_REQUEST='true')
    body = client.get(f'/clientes/{own.public_id}/').content.decode()
    assert 'Falta rellenar' not in body
    assert f'action="/clientes/{own.public_id}/fase/"' in body

    response = client.post(f'/clientes/{own.public_id}/fase/', {'stage': 'profile_created'})
    assert response.status_code == 302
    own.refresh_from_db()
    assert own.stage == 'profile_created'
    assert ActivityEvent.objects.filter(business=own, kind='stage_changed').count() == 1


def test_indicator_in_the_clients_table(sign_in, businesses):
    own = businesses[0]
    client = sign_in('admin')
    assert 'Sin empezar' in client.get('/clientes/').content.decode()
    client.post(f'/clientes/{own.public_id}/estudio/', {'who': 'Bar'}, HTTP_HX_REQUEST='true')
    assert 'Pendiente' in client.get('/clientes/').content.decode()
    client.post(f'/clientes/{own.public_id}/estudio/', FULL, HTTP_HX_REQUEST='true')
    body = client.get('/clientes/').content.decode()
    assert '<th>Estudio</th>' in body and 'Completo' in body


def test_the_study_is_internal(sign_in, accounts, businesses):
    own = businesses[0]
    sign_in('admin').post(f'/clientes/{own.public_id}/estudio/', FULL, HTTP_HX_REQUEST='true')

    # A client sees neither the card in their portal nor the endpoint.
    c = sign_in('client')
    portal = c.get('/portal/').content.decode()
    assert 'Estudio previo' not in portal and FULL['needs'] not in portal
    assert c.get(f'/clientes/{own.public_id}/estudio/').status_code == 404
    assert c.post(f'/clientes/{own.public_id}/estudio/', FULL, HTTP_HX_REQUEST='true').status_code == 404
    assert Study.objects.visible_to(accounts['client']).count() == 0

    # Neither does the accountant, on their fiscal card or in the listing.
    a = sign_in('accountant')
    assert a.get(f'/clientes/{own.public_id}/estudio/').status_code == 404
    assert FULL['needs'] not in a.get(f'/clientes/{own.public_id}/').content.decode()
    assert '<th>Estudio</th>' not in a.get('/clientes/').content.decode()
    assert Study.objects.visible_to(accounts['accountant']).count() == 0

    # A developer works the business, so they read and edit it, but cannot move the phase.
    d = sign_in('developer')
    assert d.get(f'/clientes/{own.public_id}/estudio/').status_code == 200
    body = d.get(f'/clientes/{own.public_id}/').content.decode()
    assert FULL['needs'] in body and 'Marcar ficha creada' not in body
    assert d.post(f'/clientes/{own.public_id}/fase/', {'stage': 'profile_created'}).status_code == 404


def test_a_developer_cannot_reach_a_study_of_another_business(sign_in, businesses):
    other = businesses[1]
    sign_in('admin').post(f'/clientes/{other.public_id}/estudio/', FULL, HTTP_HX_REQUEST='true')
    assert sign_in('developer').get(f'/clientes/{other.public_id}/estudio/').status_code == 404


BEFORE = ('core', '0014_remove_business_legacy_category')
AFTER = ('core', '0015_study_and_stage_order')


@pytest.mark.django_db(transaction=True)
def test_reordering_keeps_every_business_in_its_phase():
    executor = MigrationExecutor(connection)
    executor.migrate([BEFORE])
    old_apps = executor.loader.project_state([BEFORE]).apps
    Business = old_apps.get_model('core', 'Business')
    try:
        expected = {}
        for stage in ['found', 'visited', 'profile_created', 'proposal', 'review', 'approved', 'development', 'testing', 'production', 'discarded']:
            expected[Business.objects.create(name=f'Negocio {stage}', stage=stage).pk] = stage

        executor.loader.build_graph()
        executor.migrate([AFTER])

        Updated = executor.loader.project_state([AFTER]).apps.get_model('core', 'Business')
        # Each row keeps its key, so the phase survives the reorder by name.
        assert {b.pk: b.stage for b in Updated.objects.all()} == expected
    finally:
        executor.loader.build_graph()
        executor.migrate([AFTER])
