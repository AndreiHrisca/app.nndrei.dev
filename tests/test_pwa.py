import struct
from pathlib import Path
import pytest
from django.conf import settings

pytestmark = pytest.mark.django_db

@pytest.mark.parametrize('path,mime', [('/sw.js','application/javascript'),('/manifest.webmanifest','application/manifest+json'),('/offline/','text/html')])
@pytest.mark.parametrize('role', [None, 'admin', 'developer', 'accountant', 'client'])
def test_public_pwa_resources(client, accounts, path, mime, role):
    if role:
        client.force_login(accounts[role])  # Must work before completing OTP too.
    response = client.get(path)
    assert response.status_code == 200
    assert response['Content-Type'].split(';')[0] == mime
    if path != '/offline/':
        assert response['Cache-Control'] == 'no-cache'
    else:
        assert b'Sin conexi' in response.content
        assert b'csrf' not in response.content
        anonymous = __import__('django.test', fromlist=['Client']).Client().get(path)
        assert response.content == anonymous.content

def test_manifest_icons(client):
    manifest = client.get('/manifest.webmanifest').json()
    assert manifest['start_url'] == '/?source=pwa'
    assert manifest['scope'] == '/' and manifest['display'] == 'standalone'
    for name, size in [('icon-192.png',192),('icon-512.png',512),('icon-maskable-512.png',512),('apple-touch-icon.png',180)]:
        content = (settings.BASE_DIR / 'static/icons' / name).read_bytes()
        assert content[:8] == b'\x89PNG\r\n\x1a\n'
        assert struct.unpack('>II',content[16:24]) == (size,size)
        assert content[25] == 2  # RGB; no alpha channel.

def test_persistent_secure_session(sign_in):
    response = sign_in('client').get('/portal/', secure=True)
    cookie = response.cookies['sessionid']
    assert cookie['max-age'] == 30 * 24 * 60 * 60
    assert cookie['secure'] and cookie['httponly']
    assert cookie['samesite'] == 'Lax'
    assert settings.SESSION_SAVE_EVERY_REQUEST
