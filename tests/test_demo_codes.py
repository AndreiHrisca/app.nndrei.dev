"""Card codes: nndrei.dev/qr → /c/<code> → the business's demo, and what gets counted on the way."""
import re
from datetime import timedelta
import pytest
from django.utils import timezone
from core import demo_codes
from core.demo_codes import CODE_ALPHABET, CODE_LENGTH, ensure_code, new_code, normalize
from core.demos import save_demo
from core.models import ActivityEvent, Business, CodeAttempt, DemoOpen

pytestmark = pytest.mark.django_db

DEMO = b'<!doctype html><html><head><title>Bar</title></head><body><h1>Bar La Luna</h1></body></html>'


def with_demo(business):
    save_demo(business, DEMO)
    business.refresh_from_db()
    return business


def test_alphabet_has_no_lookalikes():
    assert not set('0O1IL') & set(CODE_ALPHABET)
    assert len(set(CODE_ALPHABET)) == len(CODE_ALPHABET) == 31


def test_generated_codes_use_only_the_alphabet():
    for _ in range(2000):
        code = new_code()
        assert len(code) == CODE_LENGTH and set(code) <= set(CODE_ALPHABET)


def test_a_demo_brings_a_code_and_the_code_is_stable(businesses):
    own = with_demo(businesses[0])
    code = own.demo_code
    assert re.fullmatch(f'[{CODE_ALPHABET}]{{5}}', code)
    save_demo(own, b'<html>v2</html>')
    own.refresh_from_db()
    assert own.demo_code == code
    assert Business.objects.get(pk=businesses[1].pk).demo_code is None


def test_collisions_draw_again(businesses, monkeypatch):
    own, other = businesses
    Business.objects.filter(pk=other.pk).update(demo_code='AAAAA')
    draws = iter(['AAAAA', 'AAAAA', 'BBBBB'])
    monkeypatch.setattr(demo_codes, 'new_code', lambda: next(draws))
    assert ensure_code(own) == 'BBBBB'
    assert Business.objects.get(pk=own.pk).demo_code == 'BBBBB'


def test_many_codes_are_all_different(db):
    codes = {ensure_code(Business.objects.create(name=f'Negocio {i}')) for i in range(300)}
    assert len(codes) == 300


@pytest.mark.parametrize('typed,stored', [('ab3cd', 'AB3CD'), (' ab 3c d ', 'AB3CD'), ('AB3CD', 'AB3CD'), ('a\tb3\ncd', 'AB3CD'), ('', '')])
def test_normalize(typed, stored):
    assert normalize(typed) == stored


def test_valid_code_redirects_to_the_demo_and_counts_once(client, businesses, settings):
    own = with_demo(businesses[0])
    typed = own.demo_code.lower()
    response = client.get(f'/c/{typed[:2]}%20{typed[2:]}')
    assert response.status_code == 302
    assert response['Location'] == f'{settings.SITE_URL}/demos/{own.demo_slug}/'
    assert response['Cache-Control'] == 'no-store'
    demo = client.get(f'/demos/{own.demo_slug}/')
    assert demo.status_code == 200
    # The landing after the code is not counted again as a direct open.
    assert list(DemoOpen.objects.filter(business=own).values_list('via', flat=True)) == ['code']
    attempt = CodeAttempt.objects.get()
    assert attempt.valid and attempt.code == own.demo_code and attempt.business == own
    assert len(attempt.ip_hash) == 64 and '127.0.0.1' not in attempt.ip_hash


def test_direct_url_opens_are_counted(client, businesses):
    own = with_demo(businesses[0])
    client.get(f'/demos/{own.demo_slug}/')
    client.get(f'/demos/{own.demo_slug}')
    assert DemoOpen.objects.filter(business=own, via='direct').count() == 2


def test_our_own_visits_are_not_counted(sign_in, businesses):
    own = with_demo(businesses[0])
    sign_in('admin').get(f'/demos/{own.demo_slug}/')
    assert not DemoOpen.objects.exists()


def neutral(client, code):
    response = client.get(f'/c/{code}')
    return response.status_code, response.content


def test_invalid_unknown_discarded_and_demo_less_look_identical(client, businesses):
    from core.demos import delete_demo
    own, other = businesses
    own = with_demo(own)
    other = with_demo(other)
    Business.objects.filter(pk=own.pk).update(stage='discarded')
    delete_demo(other)
    unused = next(c for c in iter(new_code, None) if c not in {own.demo_code, other.demo_code})
    answers = {neutral(client, code) for code in [unused, 'XX', 'TOO-LONG-CODE-123', own.demo_code, other.demo_code, 'ab%20']}
    assert len(answers) == 1
    status, content = answers.pop()
    assert status == 404
    page = content.decode()
    assert 'Código no válido, revisa la tarjeta' in page and 'https://nndrei.dev/qr' in page
    assert own.name not in page and other.name not in page
    assert CodeAttempt.objects.filter(valid=False).count() == 6
    assert not DemoOpen.objects.exists()


def test_rate_limit_per_ip(client, businesses, settings):
    own = with_demo(businesses[0])
    for _ in range(10):
        assert client.get('/c/ZZZZZ').status_code == 404
    # The eleventh try, even with the right code, only gets told to wait.
    blocked = client.get(f'/c/{own.demo_code}')
    assert blocked.status_code == 429 and 'Espera un rato' in blocked.content.decode()
    assert CodeAttempt.objects.filter(rate_limited=True).count() == 1
    assert not DemoOpen.objects.exists()
    # Someone else is not affected, and the hour passes.
    assert client.get(f'/c/{own.demo_code}', REMOTE_ADDR='10.0.0.9').status_code == 302
    CodeAttempt.objects.update(created_at=timezone.now() - timedelta(hours=1, minutes=1))
    assert client.get(f'/c/{own.demo_code}').status_code == 302


def test_forwarded_address_counts_only_when_trusted(client, businesses, settings):
    for _ in range(10):
        client.get('/c/ZZZZZ', HTTP_X_FORWARDED_FOR='203.0.113.7')
    # Untrusted: every request comes from REMOTE_ADDR, already at the limit.
    assert client.get('/c/ZZZZZ', HTTP_X_FORWARDED_FOR='203.0.113.8').status_code == 429
    settings.TRUST_X_FORWARDED_FOR = True
    assert client.get('/c/ZZZZZ', HTTP_X_FORWARDED_FOR='203.0.113.8').status_code == 404


def test_discarded_demo_is_404_by_its_address(client, businesses):
    own = with_demo(businesses[0])
    Business.objects.filter(pk=own.pk).update(stage='discarded')
    unknown = client.get('/demos/no-existe-abc123/')
    response = client.get(f'/demos/{own.demo_slug}/')
    assert response.status_code == 404 and response.content == unknown.content
    assert client.get(f'/clientes/{own.public_id}/demo-cliente').status_code == 404


def test_demos_carry_noindex(client, businesses):
    own = with_demo(businesses[0])
    response = client.get(f'/demos/{own.demo_slug}/')
    assert response['X-Robots-Tag'] == 'noindex, nofollow'
    assert response.content.startswith(b'<!doctype html><html><head><meta name="robots" content="noindex, nofollow"><title>')
    save_demo(own, b'<p>sin cabecera</p>')
    assert client.get(f'/demos/{own.demo_slug}/').content == b'<meta name="robots" content="noindex, nofollow"><p>sin cabecera</p>'


def test_demos_listing_stays_404_and_robots_announces_nothing(client, businesses):
    with_demo(businesses[0])
    for path in ('/demos/', '/demos', '/c/', '/sitemap.xml'):
        assert client.get(path).status_code == 404
    robots = client.get('/robots.txt')
    assert robots.status_code == 200 and robots['Content-Type'].startswith('text/plain')
    text = robots.content.decode()
    assert 'demos' not in text and 'Sitemap' not in text and 'Disallow: /\n' not in text


def test_open_updates_last_activity_and_history_without_flooding(client, accounts, businesses, rf):
    from core.views import business_list_context
    own = with_demo(businesses[0])
    for _ in range(3):
        client.get(f'/c/{own.demo_code}')
    assert DemoOpen.objects.filter(business=own).count() == 3
    assert ActivityEvent.objects.filter(business=own, kind='demo_opened', client_visible=False).count() == 1
    # The history is immutable, so put the open after every history line instead.
    later = timezone.now() + timedelta(hours=2)
    DemoOpen.objects.create(business=own, via='direct', opened_at=later)
    request = rf.get('/clientes/')
    request.user = accounts['admin']
    rows = {b.pk: b for b in business_list_context(request)['businesses']}
    assert rows[own.pk].last_activity == later
    assert rows[businesses[1].pk].last_activity is None


def test_card_shows_code_and_opens(client, sign_in, businesses):
    own = with_demo(businesses[0])
    client.get(f'/c/{own.demo_code}')
    client.get(f'/demos/{own.demo_slug}/')  # counted by the code, not again
    client.get(f'/demos/{own.demo_slug}/')
    client.cookies.clear()
    page = sign_in('admin').get(own.get_absolute_url()).content.decode()
    assert own.demo_code in page and 'Generar otro' in page
    assert 'Demo abierta 2 veces' in page and '1 con código · 1 por URL directa' in page
    portal = sign_in('admin').get(f'/clientes/{own.public_id}/portal/').content.decode()
    assert own.demo_code not in portal and 'Demo abierta' not in portal


def test_regenerate_replaces_the_code(sign_in, client, businesses):
    own = with_demo(businesses[0])
    old = own.demo_code
    assert sign_in('admin').post(f'/clientes/{own.public_id}/demo/codigo/').status_code == 302
    own.refresh_from_db()
    assert own.demo_code != old and len(own.demo_code) == 5
    client.logout()
    assert client.get(f'/c/{old}').status_code == 404
    assert client.get(f'/c/{own.demo_code}').status_code == 302


@pytest.mark.parametrize('role', ['developer', 'accountant', 'client'])
def test_only_admin_regenerates_and_sees_attempts(sign_in, businesses, role):
    own = with_demo(businesses[0])
    c = sign_in(role)
    assert c.post(f'/clientes/{own.public_id}/demo/codigo/').status_code == 404
    assert c.get('/codigos/').status_code == 404


def test_failed_attempts_view(client, sign_in, businesses):
    own = with_demo(businesses[0])
    client.get('/c/QQQQ2', HTTP_USER_AGENT='Movil de prueba')
    client.get(f'/c/{own.demo_code}')
    page = sign_in('admin').get('/codigos/').content.decode()
    assert 'QQQQ2' in page and 'Movil de prueba' in page and own.demo_code not in page


def test_mcp_card_includes_the_code(accounts, businesses):
    from core.mcp_tools import get_cliente
    own = with_demo(businesses[0])
    assert get_cliente(accounts['admin'], str(own.public_id))['demo']['codigo_tarjeta'] == own.demo_code
