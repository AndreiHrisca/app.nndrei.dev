"""URLs expose an opaque UUID; the integer primary key never leaves the server."""
import re
import pytest
from django.urls import reverse
from core.models import Business, Document, Proposal, ProposalVersion, Request, Transaction
from django.core.files.uploadedfile import SimpleUploadedFile

pytestmark = pytest.mark.django_db


def test_numeric_business_url_is_gone(sign_in, businesses):
    client = sign_in('admin')
    own = businesses[0]
    assert client.get(f'/clientes/{own.pk}/').status_code == 404
    assert client.get(f'/clientes/{own.public_id}/').status_code == 200


@pytest.mark.parametrize('path', ['/clientes/{pk}/editar/', '/clientes/{pk}/portal/', '/peticiones/{pk}/planificar/', '/documentos/{pk}/', '/propuestas/{pk}/', '/usuarios/{pk}/'])
def test_every_numeric_detail_url_is_gone(sign_in, businesses, path):
    assert sign_in('admin').get(path.format(pk=1)).status_code == 404


def test_reverse_builds_uuid_urls(businesses):
    own = businesses[0]
    assert reverse('business_detail', args=[own.public_id]) == f'/clientes/{own.public_id}/'
    assert own.get_absolute_url() == f'/clientes/{own.public_id}/'


def test_client_cannot_reach_another_business(sign_in, businesses):
    """Obfuscation is not authorisation: the scope check still decides, and it 404s."""
    own, other = businesses
    client = sign_in('client')
    assert client.get(f'/portal/negocio/{own.public_id}/').status_code == 200
    assert client.get(f'/portal/negocio/{other.public_id}/').status_code == 404
    # The business card itself is internal-only, even for one's own business.
    assert client.get(f'/clientes/{own.public_id}/').status_code == 404
    assert sign_in('admin').get(f'/clientes/{other.public_id}/').status_code == 200


def test_developer_only_reaches_assigned_businesses(sign_in, businesses):
    own, other = businesses
    client = sign_in('developer')
    assert client.get(f'/clientes/{own.public_id}/').status_code == 200
    assert client.get(f'/clientes/{other.public_id}/').status_code == 404


def test_pages_do_not_leak_row_ids(sign_in, accounts, businesses):
    """No page renders a bare integer id for an object addressable by URL."""
    own = businesses[0]
    own.stage = 'profile_created'
    own.save()
    Request.objects.create(business=own, title='Carta', description='Texto', created_by=accounts['client'])
    Document.objects.create(business=own, kind='contract', uploaded_by=accounts['admin'], file=SimpleUploadedFile('c.txt', b'x'))
    proposal = Proposal.objects.create(business=own, number='P-2026-LEAK')
    ProposalVersion.objects.create(proposal=proposal, number=1)
    client = sign_in('admin')
    for url in ['/clientes/', f'/clientes/{own.public_id}/', '/propuestas/', f'/propuestas/{proposal.public_id}/', '/usuarios/', '/buscar/']:
        body = client.get(url).content.decode()
        for needle in [f'/clientes/{own.pk}/', f'/propuestas/{proposal.pk}/', f'/usuarios/{accounts["admin"].pk}/']:
            assert needle not in body, (url, needle)


def test_choice_fields_offer_public_ids(sign_in, businesses, accounts):
    body = sign_in('admin').get('/usuarios/').content.decode()
    # The invitation form lists businesses by public id, never by row id.
    assert f'value="{businesses[0].public_id}"' in body
    assert not re.search(r'name="business"[^>]*>\s*<option value="%d"' % businesses[0].pk, body)


def test_finance_filter_accepts_public_id(sign_in, businesses):
    from datetime import date
    Transaction.objects.create(business=businesses[0], date=date.today(), concept='VISIBLE_TX', category='hosting', amount=-5)
    Transaction.objects.create(business=businesses[1], date=date.today(), concept='OTHER_TX', category='hosting', amount=-5)
    body = sign_in('admin').get(f'/finanzas/?business={businesses[0].public_id}').content.decode()
    assert 'VISIBLE_TX' in body and 'OTHER_TX' not in body
    assert 'VISIBLE_TX' not in sign_in('admin').get('/finanzas/?business=not-a-uuid').content.decode()
