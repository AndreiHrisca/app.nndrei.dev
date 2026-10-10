"""Client demo: a public HTML mockup per business, managed from the card by an admin."""
import uuid
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from core.demos import has_demo, save_demo, with_noindex
from core.models import ActivityEvent, Business

pytestmark = pytest.mark.django_db

DEMO = b'<!doctype html><html><head><style>h1{color:red}</style></head><body><h1>Bar La Luna</h1><script>document.title="ok"</script></body></html>'


def demo_url(business):
    """The public address: /demos/<slug>/ (fixed per business once it has had a demo)."""
    business.refresh_from_db(fields=['demo_slug'])
    return f'/demos/{business.demo_slug}/'


def old_url(business):
    return f'/clientes/{business.public_id}/demo-cliente'


def upload(client, business, content=DEMO, name='demo.html'):
    return client.post(f'/clientes/{business.public_id}/demo/', {'file': SimpleUploadedFile(name, content, content_type='text/html')})


def test_demo_is_public_and_served_as_is(client, businesses):
    own = businesses[0]
    save_demo(own, DEMO)
    for url in (demo_url(own), demo_url(own).rstrip('/')):
        response = client.get(url)
        assert response.status_code == 200
        assert response.content == with_noindex(DEMO)
        assert response['Content-Type'] == 'text/html; charset=utf-8'
        assert response['Content-Security-Policy'] == 'sandbox allow-scripts allow-popups allow-forms'
        assert response['X-Robots-Tag'] == 'noindex, nofollow'
        assert 'no-store' in response['Cache-Control']
        assert response['Referrer-Policy'] == 'no-referrer'
        assert 'sessionid' not in response.cookies


def test_missing_business_and_missing_demo_look_the_same(client, businesses):
    own = businesses[0]
    save_demo(own, DEMO)
    from core.demos import delete_demo
    delete_demo(own)
    without_demo = client.get(demo_url(own))
    unknown = client.get('/demos/no-existe-abc123/')
    assert without_demo.status_code == unknown.status_code == 404
    assert without_demo.content == unknown.content
    assert client.get(old_url(own)).status_code == client.get(f'/clientes/{uuid.uuid4()}/demo-cliente').status_code == 404


def test_old_address_redirects_to_the_fixed_one(client, businesses, settings):
    own = businesses[0]
    save_demo(own, DEMO)
    response = client.get(old_url(own))
    assert response.status_code == 301 and response['Location'] == settings.SITE_URL + demo_url(own)


def test_slug_is_fixed_unguessable_and_versions_pile_up(sign_in, businesses):
    own = businesses[0]
    first = save_demo(own, DEMO)
    slug = own.demo_slug
    assert slug.startswith('mi-negocio-') and len(slug) == len('mi-negocio-') + 6
    second = save_demo(own, b'<html>v2</html>', 'nueva.html')
    own.refresh_from_db()
    assert own.demo_slug == slug and (first.number, second.number) == (1, 2)
    assert sign_in('admin').get(demo_url(own)).content == with_noindex(b'<html>v2</html>')


def test_single_file_from_before_versions_becomes_version_one(businesses):
    from django.core.files.base import ContentFile
    from django.core.files.storage import default_storage
    from core.demos import legacy_path, read_demo
    own = businesses[0]
    default_storage.save(legacy_path(own), ContentFile(DEMO))
    assert has_demo(own) and read_demo(own) == DEMO
    save_demo(own, b'<html>v2</html>')
    versions = list(own.demoversion_set.order_by('number').values_list('number', 'size'))
    assert versions == [(1, len(DEMO)), (2, 15)] and not default_storage.exists(legacy_path(own))


def test_qr_png_of_the_public_address(sign_in, businesses):
    own = businesses[0]
    c = sign_in('admin')
    assert c.get(f'/clientes/{own.public_id}/demo/qr.png').status_code == 404
    save_demo(own, DEMO)
    response = c.get(f'/clientes/{own.public_id}/demo/qr.png?descargar=1')
    assert response['Content-Type'] == 'image/png' and response.content.startswith(b'\x89PNG')
    assert f'qr-{own.demo_slug}.png' in response['Content-Disposition']
    assert sign_in('client').get(f'/clientes/{own.public_id}/demo/qr.png').status_code == 404


@pytest.mark.parametrize('bad', ['1', 'not-a-uuid', '66413817-e885-4ebc-888a-34f261e1506', '../../etc/passwd'])
def test_malformed_ids_never_reach_the_database(client, businesses, bad, django_assert_num_queries):
    with django_assert_num_queries(0):
        assert client.get(f'/clientes/{bad}/demo-cliente').status_code == 404


@pytest.mark.parametrize('path', ['/clientes/', '/clientes/{id}/', '/clientes/{id}/editar/', '/clientes/{id}/portal/', '/clientes/{id}/estudio/', '/clientes/{id}/demo/'])
def test_the_rest_of_clientes_still_requires_login(client, businesses, path):
    own = businesses[0]
    save_demo(own, DEMO)
    response = client.get(path.format(id=own.public_id))
    assert response.status_code == 302 and response['Location'].startswith('/login/')


def test_half_signed_in_user_sees_the_demo_not_2fa(client, accounts, businesses):
    own = businesses[0]
    save_demo(own, DEMO)
    client.force_login(accounts['admin'])  # no OTP device verified
    assert client.get(demo_url(own)).status_code == 200
    assert client.get(f'/clientes/{own.public_id}/').status_code == 302


def test_admin_uploads_replaces_and_deletes(sign_in, businesses, settings):
    own = businesses[0]
    stage = own.stage
    client = sign_in('admin')
    page = client.get(own.get_absolute_url()).content.decode()
    assert 'Subir web de muestra' in page and 'Web de muestra disponible' not in page
    assert 'Aún no hay una web publicada.' in page

    assert upload(client, own).status_code == 302
    assert client.get(demo_url(own)).content == with_noindex(DEMO)
    page = client.get(own.get_absolute_url()).content.decode()
    assert 'Web de muestra disponible' in page and 'Copiar enlace' in page and 'Eliminar web de muestra' in page
    assert settings.SITE_URL + demo_url(own) in page and 'Descargar QR' in page
    assert 'Aún no hay una web publicada.' in page

    assert upload(client, own, b'<html><body>v2</body></html>').status_code == 302
    assert client.get(demo_url(own)).content == with_noindex(b'<html><body>v2</body></html>')

    assert client.post(f'/clientes/{own.public_id}/demo/eliminar/').status_code == 302
    assert not has_demo(own)
    assert client.get(demo_url(own)).status_code == 404

    own.refresh_from_db()
    assert own.stage == stage
    kinds = set(ActivityEvent.objects.filter(business=own).values_list('kind', flat=True))
    assert {'demo_uploaded', 'demo_deleted'} <= kinds
    assert not ActivityEvent.objects.filter(business=own, kind__startswith='demo', client_visible=True).exists()


@pytest.mark.parametrize('name,content,error', [
    ('demo.pdf', DEMO, 'Sube un archivo .html.'),
    ('demo.html', b'x' * (5 * 1024 * 1024 + 1), 'Máximo 5 MB.'),
    ('demo.html', '<p>café</p>'.encode('latin-1'), 'UTF-8'),
])
def test_upload_is_validated(sign_in, businesses, name, content, error):
    own = businesses[0]
    response = upload(sign_in('admin'), own, content, name)
    assert response.status_code == 400
    assert error in response.content.decode()
    assert not has_demo(own)


@pytest.mark.parametrize('role', ['developer', 'accountant', 'client'])
def test_only_admin_manages_the_demo(sign_in, businesses, role):
    own = businesses[0]
    client = sign_in(role)
    assert upload(client, own).status_code == 404
    save_demo(own, DEMO)
    assert client.post(f'/clientes/{own.public_id}/demo/eliminar/').status_code == 404
    assert has_demo(own)


def test_developer_sees_the_link_but_no_controls(sign_in, businesses):
    own = businesses[0]
    save_demo(own, DEMO)
    page = sign_in('developer').get(own.get_absolute_url()).content.decode()
    assert 'Abrir web de muestra' in page and 'Copiar enlace' in page
    assert 'Eliminar web de muestra' not in page and 'Reemplazar' not in page


def test_demo_link_in_client_view(sign_in, businesses, client):
    own = businesses[0]
    save_demo(own, DEMO)
    preview = sign_in('admin').get(f'/clientes/{own.public_id}/portal/').content.decode()
    assert 'Abrir web de muestra' in preview and 'Subir web de muestra' not in preview and 'Eliminar web de muestra' not in preview
    client.logout()
    portal = sign_in('client').get(f'/portal/negocio/{own.public_id}/').content.decode()
    assert 'Abrir web de muestra' in portal and 'Eliminar web de muestra' not in portal


def test_session_cookies_are_out_of_scripts_reach(client, accounts):
    client.get('/login/')
    response = client.post('/login/', {'username': 'admin@example.com', 'password': 'Valid-test-password-829!'})
    session = response.cookies['sessionid']
    assert session['httponly'] and session['samesite'] == 'Lax'
    csrf = response.cookies.get('csrftoken') or client.cookies['csrftoken']
    assert csrf['httponly'] and csrf['samesite'] == 'Lax'
