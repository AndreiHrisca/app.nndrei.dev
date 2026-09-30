"""Fase 4: the MCP connector. Tools directly, then the whole Claude.ai flow over HTTP."""
import base64
import hashlib
import json
import secrets
from urllib.parse import parse_qs, urlparse
import pytest
from core import mcp_tools as tools
from core.mcp_tools import ToolError
from core.models import ActivityEvent, Business, DemoVersion, Estimation, McpToken, PlaceSnapshot, SearchCategory, Study

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin(accounts):
    return accounts['admin']


def bid(business):
    return str(business.public_id)


# --- Tools ------------------------------------------------------------------------

@pytest.mark.parametrize('role', ['developer', 'accountant', 'client'])
def test_only_admins(accounts, businesses, role):
    with pytest.raises(ToolError, match='Solo un administrador'):
        tools.buscar_clientes(accounts[role])
    with pytest.raises(ToolError):
        tools.get_cliente(accounts[role], bid(businesses[0]))


def test_search_by_text_and_stage(admin, businesses):
    own, other = businesses
    other.stage = 'visited'; other.save()
    assert [c['nombre'] for c in tools.buscar_clientes(admin, 'otro')['clientes']] == ['Otro negocio']
    assert [c['nombre'] for c in tools.buscar_clientes(admin, estado='Visitado')['clientes']] == ['Otro negocio']
    other.stage = 'discarded'; other.save()
    assert [c['nombre'] for c in tools.buscar_clientes(admin)['clientes']] == ['Mi negocio']
    assert tools.buscar_clientes(admin, estado='descartado')['clientes'][0]['nombre'] == 'Otro negocio'
    with pytest.raises(ToolError, match='Estado desconocido'):
        tools.buscar_clientes(admin, estado='lead')


def test_get_cliente_and_bad_ids(admin, businesses):
    data = tools.get_cliente(admin, bid(businesses[0]))
    assert data['nombre'] == 'Mi negocio' and data['estudio'] is None and data['horario']['estado'] == 'Horario desconocido'
    for bad in ['1', 'xx', '00000000-0000-0000-0000-000000000000']:
        with pytest.raises(ToolError):
            tools.get_cliente(admin, bad)


def test_create_is_idempotent_by_place_id_and_links_the_search_result(admin):
    from core.models import City
    category = SearchCategory.objects.create(name='Bares')
    snapshot = PlaceSnapshot.objects.create(place_id='ChIJ_abc', name='Bar', city=City.objects.get(ine_code='28079'), category=category, phone='600', latitude=40.41, longitude=-3.71)
    first = tools.crear_cliente(admin, 'Bar Pepe', 'Calle Mayor 1', 'ChIJ_abc')
    again = tools.crear_cliente(admin, 'Otro nombre', '', 'ChIJ_abc')
    assert not first['ya_existia'] and again['ya_existia'] and again['id'] == first['id']
    b = Business.objects.get(public_id=first['id'])
    assert b.stage == 'found' and b.category == category and b.latitude == 40.41
    snapshot.refresh_from_db()
    assert snapshot.status == 'pipeline' and snapshot.business == b
    assert ActivityEvent.objects.filter(business=b, kind='mcp_created', client_visible=False, actor=admin).exists()
    with pytest.raises(ToolError):
        tools.crear_cliente(admin, '', 'x')
    with pytest.raises(ToolError, match='place_id'):
        tools.crear_cliente(admin, 'X', 'y', 'bad id!')


def test_study_is_an_ai_draft_until_validated(admin, businesses, sign_in):
    b = businesses[0]
    result = tools.guardar_estudio(admin, bid(b), {'que_venden': 'Tapas', 'necesidades': 'Carta online', 'instagram': True},
                                   fuentes=['https://example.com/bar'])
    study = Study.objects.get(business=b)
    assert study.sells == 'Tapas' and study.has_instagram and study.ai_draft and study.ai_updated_at
    assert study.ai_sources == ['https://example.com/bar'] and result['estudio']['borrador_ia']
    assert ActivityEvent.objects.filter(business=b, kind='mcp_study').exists()
    page = sign_in('admin').get(b.get_absolute_url()).content.decode()
    assert 'Borrador IA' in page and 'Validar estudio' in page and 'https://example.com/bar' in page
    sign_in('developer').post(f'/clientes/{b.public_id}/estudio/validar/', HTTP_HX_REQUEST='true')
    study.refresh_from_db()
    assert not study.ai_draft and study.validated_at and study.validated_by.role == 'developer'
    # Writing again makes it a draft again.
    tools.guardar_estudio(admin, bid(b), {'notas': 'Abren a las 8'})
    study.refresh_from_db()
    assert study.ai_draft and study.validated_at is None and study.sells == 'Tapas'


@pytest.mark.parametrize('campos,error', [
    ({}, 'al menos un campo'),
    ({'vende': 'x'}, 'Campos desconocidos: vende'),
    ({'instagram': 'sí'}, 'true o false'),
    ({'notas': 'x' * 5001}, '5000'),
    ({'mejor_momento': 'x' * 201}, '200'),
    ({'que_venden': 7}, 'texto'),
])
def test_study_validation(admin, businesses, campos, error):
    with pytest.raises(ToolError, match=error):
        tools.guardar_estudio(admin, bid(businesses[0]), campos)
    assert not Study.objects.exists()


def test_sources(admin, businesses):
    b = businesses[0]
    tools.guardar_fuentes(admin, bid(b), ['https://a.es', 'https://a.es', 'http://b.es/x'])
    assert Study.objects.get(business=b).ai_sources == ['https://a.es', 'http://b.es/x']
    for bad in [['javascript:alert(1)'], ['ftp://a.es'], 'https://a.es', ['https://a.es'] * 51, ['https://' + 'a' * 2000 + '.es']]:
        with pytest.raises(ToolError):
            tools.guardar_fuentes(admin, bid(b), bad)
    assert ActivityEvent.objects.filter(business=b, kind='mcp_sources').count() == 1


def test_demo_upload_is_versioned_and_public(admin, businesses, client):
    b = businesses[0]
    first = tools.subir_demo(admin, bid(b), 'bar-pepe.html', '<!doctype html><h1>Hola</h1>')
    second = tools.subir_demo(admin, bid(b), '../../etc/passwd.html', '<!doctype html><h1>v2</h1>')
    assert (first['version'], second['version']) == (1, 2) and first['url'] == second['url']
    assert '/demos/mi-negocio-' in first['url']
    path = urlparse(second['url']).path
    response = client.get(path)
    assert response.content == '<!doctype html><h1>v2</h1>'.encode() and 'sandbox' in response['Content-Security-Policy']
    assert DemoVersion.objects.get(business=b, number=2).filename == 'passwd.html'
    assert ActivityEvent.objects.filter(business=b, kind='mcp_demo').count() == 2
    for name, html, error in [('a.pdf', '<p>', '.html'), ('a.html', 'x' * (5 * 1024 * 1024 + 1), '5 MB'), ('a.html', '  ', 'vacía'), ('a.html', 42, 'texto')]:
        with pytest.raises(ToolError, match=error):
            tools.subir_demo(admin, bid(b), name, html)
    assert DemoVersion.objects.filter(business=b).count() == 2


def test_estimate_through_mcp(admin, businesses):
    b = businesses[0]
    data = tools.guardar_estimacion(admin, bid(b), {'resenas': 480, 'anos': 4, 'ticket': 20, 'servicios': ['comida', 'cena'],
                                                    'concurrencia': 'media', 'usa_reparto': True})
    from decimal import Decimal
    assert Decimal(data['mensual_medio']) == 10000 and data['confianza'] == 'Baja' and 'delivery_text' in data['retorno']
    item = Estimation.objects.get()
    assert item.source == 'mcp' and item.services == ['lunch', 'dinner'] and item.uses_delivery
    assert ActivityEvent.objects.filter(business=b, kind='estimation', text__contains='(Claude)').exists()
    for datos, error in [({'mesas': 'muchas'}, 'número'), ({'ocupacion': 150}, 'ocupacion'), ({'servicios': ['merienda']}, 'desayuno'),
                         ({'precio': 3}, 'desconocidos'), ({'concurrencia': 'mucha'}, 'baja, media o alta'), ({'usa_reparto': 'sí'}, 'true o false')]:
        with pytest.raises(ToolError, match=error):
            tools.guardar_estimacion(admin, bid(b), datos)
    assert Estimation.objects.count() == 1


# --- The Claude.ai flow over HTTP ---------------------------------------------------

REDIRECT = 'https://claude.ai/api/mcp/auth_callback'


def pkce():
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()
    return verifier, challenge


def rpc(http, token, method, params=None, session=None, id=1):
    headers = {'Authorization': f'Bearer {token}', 'Accept': 'application/json, text/event-stream', 'Content-Type': 'application/json'}
    if session:
        headers['mcp-session-id'] = session
    body = {'jsonrpc': '2.0', 'method': method, 'params': params or {}}
    if id is not None:
        body['id'] = id
    response = http.post('/mcp', headers=headers, content=json.dumps(body))
    if id is None:
        return response, None
    text = response.text
    payload = json.loads(text[text.index('data:') + 5:].split('\n', 1)[0]) if text.lstrip().startswith(('event:', 'data:')) else response.json()
    return response, payload


@pytest.fixture
def mcp_app():
    from starlette.testclient import TestClient
    from core.mcp_server import build_app
    # The SDK's session manager runs once per app: a fresh one per test.
    with TestClient(build_app(), base_url='http://localhost:8000') as http:
        yield http


def connect(mcp_app, django_client):
    """Register, authorize, approve in the web app, trade the code: what Claude.ai does."""
    client_id = mcp_app.post('/register', json={'redirect_uris': [REDIRECT], 'client_name': 'Claude', 'token_endpoint_auth_method': 'none',
                                                'grant_types': ['authorization_code', 'refresh_token'], 'response_types': ['code']}).json()['client_id']
    verifier, challenge = pkce()
    response = mcp_app.get('/authorize', params={'response_type': 'code', 'client_id': client_id, 'redirect_uri': REDIRECT, 'scope': 'nndrei',
                                                  'code_challenge': challenge, 'code_challenge_method': 'S256', 'state': 'st4te'}, follow_redirects=False)
    assert response.status_code == 302 and '/conectar-claude/?txn=' in response.headers['location']
    consent_path = response.headers['location'].split('localhost:8000', 1)[-1].split('app.nndrei.dev', 1)[-1]
    page = django_client.get(consent_path)
    assert page.status_code == 200 and 'Conectar con Claude' in page.content.decode()
    txn = parse_qs(urlparse(consent_path).query)['txn'][0]
    back = django_client.post('/conectar-claude/', {'txn': txn, 'decision': 'allow'})
    query = parse_qs(urlparse(back['Location']).query)
    assert back['Location'].startswith(REDIRECT) and query['state'] == ['st4te']
    tokens = mcp_app.post('/token', data={'grant_type': 'authorization_code', 'code': query['code'][0], 'redirect_uri': REDIRECT,
                                          'client_id': client_id, 'code_verifier': verifier}).json()
    return client_id, tokens


@pytest.mark.django_db(transaction=True)
def test_claude_connects_lists_tools_and_writes(mcp_app, sign_in, accounts, businesses):
    django_client = sign_in('admin')
    client_id, tokens = connect(mcp_app, django_client)
    assert tokens['token_type'].lower() == 'bearer' and tokens['refresh_token']
    stored = McpToken.objects.get(kind='access')
    assert stored.user == accounts['admin'] and stored.token_hash != tokens['access_token']

    # No token: 401 with the discovery header Claude.ai follows.
    unauthorized = mcp_app.post('/mcp', json={'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}})
    assert unauthorized.status_code == 401 and 'resource_metadata' in unauthorized.headers.get('www-authenticate', '')
    assert mcp_app.get('/.well-known/oauth-authorization-server').json()['registration_endpoint'].endswith('/register')

    from mcp_types import LATEST_PROTOCOL_VERSION
    response, init = rpc(mcp_app, tokens['access_token'], 'initialize',
                         {'protocolVersion': LATEST_PROTOCOL_VERSION, 'capabilities': {}, 'clientInfo': {'name': 'test', 'version': '1'}})
    assert response.status_code == 200 and init['result']['serverInfo']['name'] == 'nndrei'
    session = response.headers.get('mcp-session-id')
    rpc(mcp_app, tokens['access_token'], 'notifications/initialized', session=session, id=None)
    _, listed = rpc(mcp_app, tokens['access_token'], 'tools/list', session=session, id=2)
    names = {tool['name'] for tool in listed['result']['tools']}
    assert names == {'buscar_clientes', 'get_cliente', 'crear_cliente', 'guardar_estudio', 'guardar_fuentes', 'subir_demo', 'guardar_estimacion'}
    assert all(tool['description'] for tool in listed['result']['tools'])

    b = businesses[0]
    _, called = rpc(mcp_app, tokens['access_token'], 'tools/call', {'name': 'guardar_estudio', 'arguments': {'cliente_id': bid(b), 'campos': {'que_venden': 'Pizzas'}}},
                    session=session, id=3)
    assert not called['result'].get('isError') and Study.objects.get(business=b).ai_draft
    _, failed = rpc(mcp_app, tokens['access_token'], 'tools/call', {'name': 'guardar_estudio', 'arguments': {'cliente_id': bid(b), 'campos': {'vende': 'x'}}},
                    session=session, id=4)
    assert failed['result']['isError'] and 'Campos desconocidos' in json.dumps(failed['result'], ensure_ascii=False)

    # Refresh rotates the pair; the old refresh token stops working.
    renewed = mcp_app.post('/token', data={'grant_type': 'refresh_token', 'refresh_token': tokens['refresh_token'], 'client_id': client_id}).json()
    assert renewed['access_token'] != tokens['access_token']
    assert mcp_app.post('/token', data={'grant_type': 'refresh_token', 'refresh_token': tokens['refresh_token'], 'client_id': client_id}).status_code == 400
    assert rpc(mcp_app, tokens['access_token'], 'tools/list', session=session, id=5)[0].status_code == 401

    # The admin cuts the connection from Seguridad.
    grant = McpToken.objects.get(kind='access').grant_id
    assert 'Claude' in django_client.get('/seguridad/').content.decode()
    assert sign_in('developer').post(f'/seguridad/claude/{grant}/desconectar/').status_code == 404
    django_client = sign_in('admin')
    django_client.post(f'/seguridad/claude/{grant}/desconectar/')
    assert not McpToken.objects.exists()
    assert rpc(mcp_app, renewed['access_token'], 'tools/list', session=session, id=6)[0].status_code == 401


@pytest.mark.django_db(transaction=True)
def test_consent_needs_an_admin_and_can_be_refused(mcp_app, sign_in, accounts):
    client_id = mcp_app.post('/register', json={'redirect_uris': [REDIRECT], 'token_endpoint_auth_method': 'none'}).json()['client_id']
    _, challenge = pkce()
    location = mcp_app.get('/authorize', params={'response_type': 'code', 'client_id': client_id, 'redirect_uri': REDIRECT,
                                                  'code_challenge': challenge, 'code_challenge_method': 'S256', 'state': 's'}, follow_redirects=False).headers['location']
    path = location[location.index('/conectar-claude/'):]
    assert sign_in('developer').get(path).status_code == 404
    django_client = sign_in('admin')
    txn = parse_qs(urlparse(path).query)['txn'][0]
    denied = django_client.post('/conectar-claude/', {'txn': txn, 'decision': 'deny'})
    assert 'error=access_denied' in denied['Location']
    assert django_client.post('/conectar-claude/', {'txn': txn, 'decision': 'allow'}).status_code == 400  # Used up.


@pytest.mark.django_db(transaction=True)
def test_demoted_admin_loses_access(mcp_app, sign_in, accounts):
    _, tokens = connect(mcp_app, sign_in('admin'))
    admin = accounts['admin']; admin.role = 'developer'; admin.save()
    response, _ = rpc(mcp_app, tokens['access_token'], 'initialize', {'protocolVersion': '2025-06-18', 'capabilities': {}, 'clientInfo': {'name': 't', 'version': '1'}}, id=1)
    assert response.status_code == 401
