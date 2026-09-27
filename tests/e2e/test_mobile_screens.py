"""Browser pass over every main screen: no sideways page scroll on phones.

Runs only where Playwright is installed (the `e2e` service in compose.test.yaml):

    docker compose -p nndrei-validation -f compose.test.yaml run --rm e2e

Screenshots land in docs/screenshots/mobile/ (or $SCREENSHOT_DIR) for review.
"""
import os
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

sync_api = pytest.importorskip('playwright.sync_api')

from django.core.files.uploadedfile import SimpleUploadedFile  # noqa: E402
from django.utils import timezone  # noqa: E402
from django_otp.plugins.otp_totp.models import TOTPDevice  # noqa: E402

from core.models import (  # noqa: E402
    ActivityEvent, Assignment, Business, ClientCharge, ClientMembership, Commission, Document, FollowUp,
    PlaceSnapshot, PlacesUsage, Proposal, ProposalLine, ProposalVersion, RecurringExpense, Request,
    SearchCategory, SearchZone, Study, Transaction, User, Website,
)

# Playwright's sync API runs its own event loop in this thread; the ORM calls
# made while seeding and signing in are still plain synchronous code.
os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')

pytestmark = pytest.mark.django_db(transaction=True)

OUT = Path(os.environ.get('SCREENSHOT_DIR', Path(__file__).resolve().parents[2] / 'docs/screenshots/mobile'))
PHONE = {'width': 393, 'height': 852}
WIDTHS = [375, 393, 430]
DESKTOP = {'width': 1440, 'height': 900}


@pytest.fixture
def demo(db):
    today = timezone.localdate()
    users = {role: User.objects.create_user(f'{role}@example.com', 'Valid-test-password-829!', name=name, role=role)
             for role, name in [('admin', 'Andrei'), ('developer', 'Lucía Developer'), ('accountant', 'Marta Contable'), ('client', 'Santiago')]}
    zones = [SearchZone.objects.create(name=n, latitude=40.35, longitude=-3.69) for n in ['Villaverde Bajo', 'Miguel Hernández', 'San Cristóbal de los Ángeles']]
    cats = {n: SearchCategory.objects.create(name=n, color=c) for n, c in [
        ('Bares', 'yellow'), ('Peluquerías', 'pink'), ('Talleres', 'blue'), ('Clínicas dentales', 'green'), ('Cafeterías', 'purple'), ('Gimnasios', 'teal')]}
    rows = [
        ('El Rincón del Quijote', 'Bares', 'production', '600 123 456', 'Calle de Alcocer 12, Madrid'),
        ('Peluquería y Estética Unisex Hermanas Rodríguez-Castellanos', 'Peluquerías', 'proposal', '611 222 333', 'Av. de Andalucía 45, Madrid'),
        ('Talleres Mecánicos Hermanos García', 'Talleres', 'visited', '622 333 444', 'Calle Eduardo Barreiros 101'),
        ('Clínica Dental Sonrisas', 'Clínicas dentales', 'found', '', 'Calle Arroyo Bueno 3'),
        ('Café Central', 'Cafeterías', 'profile_created', '633 444 555', ''),
        ('Gimnasio Fuerza Sur', 'Gimnasios', 'development', '644 555 666', 'Paseo de Alberto Palacios 20'),
        ('Bar La Parada', 'Bares', 'review', '655 666 777', 'Calle Villaverde 9'),
        ('Barbería Vintage 1985', 'Peluquerías', 'approved', '666 777 888', ''),
        ('Cafetería Los Porches', 'Cafeterías', 'testing', '677 888 999', 'Plaza Mayor de Villaverde 1'),
        ('Taller Rápido Express', 'Talleres', 'discarded', '', ''),
    ]
    bs = []
    for i, (name, cat, stage, phone, address) in enumerate(rows):
        b = Business.objects.create(name=name, category=cats[cat], stage=stage, phone=phone, address=address,
                                    zone=zones[i % 3].name, contact='Santiago' if i == 0 else '', email='hola@example.com' if i < 3 else '',
                                    next_action='Llamar para concretar visita' if i % 3 == 1 else '',
                                    next_action_date=today + timedelta(days=i) if i % 2 else None,
                                    stage_changed_at=timezone.now() - timedelta(days=i))
        bs.append(b)
        ActivityEvent.objects.create(business=b, kind='created', text='Ficha de negocio creada.', actor=users['admin'], occurred_at=timezone.now() - timedelta(days=i + 1))
    ClientMembership.objects.create(user=users['client'], business=bs[0])
    for b in bs[:4]:
        Assignment.objects.create(developer=users['developer'], business=b)
    for i, b in enumerate(bs[1:6]):
        FollowUp.objects.create(business=b, description=['Llamar a la dueña', 'Enviar propuesta revisada', 'Visita comercial', 'Confirmar dominio', 'Revisar pruebas'][i],
                                date=today + timedelta(days=4 - i), owner=users['admin' if i % 2 else 'developer'], kind='call')
    Study.objects.create(business=bs[1], who='Peluquería familiar con 20 años en el barrio.', sells='Corte, color y estética.', audience='Mujeres de 30 a 60 años del barrio.',
                         has_google=True, has_instagram=True, needs='Reservas online y una web sencilla.', opportunity='Web con reservas y carta de servicios.')
    Website.objects.create(business=bs[0], url='https://barrinconquijote.es', domain='barrinconquijote.es', monitoring_enabled=True)
    for i, title in enumerate(['Carta digital con QR', 'Módulo de almacén', 'Reservas para grupos']):
        Request.objects.create(business=bs[0], title=title, description='Que los clientes puedan verlo desde el móvil.', created_by=users['client'], status=['production', 'requested', 'in_progress'][i])
    Document.objects.create(business=bs[0], kind='contract', uploaded_by=users['admin'], file=SimpleUploadedFile('contrato.pdf', b'%PDF-1.4'))
    for i, (b, status, valid) in enumerate([(bs[0], 'accepted', None), (bs[1], 'sent', today + timedelta(days=15)), (bs[6], 'changes', today + timedelta(days=3)), (bs[4], 'draft', None)]):
        p = Proposal.objects.create(business=b, number=f'P-2026-00{i + 1}', status=status, valid_until=valid)
        v = ProposalVersion.objects.create(proposal=p, number=1, development_total=Decimal([500, 1200, 850, 0][i]), monthly_fee=20)
        ProposalLine.objects.create(version=v, concept='Página web', amount=v.development_total)
        if status != 'draft':
            v.sent_at = timezone.now()
            v.save()
    for i in range(24):
        status = 'pipeline' if i in (2, 5) else 'discarded' if i in (7, 11) else 'new'
        PlaceSnapshot.objects.create(
            place_id=f'place-{i}', name=['Bar Casa Paco', 'Peluquería Marisol de Villaverde Alto', 'Taller El Pistón', 'Cafetería Nuevo Estilo'][i % 4] + (f' {i}' if i > 3 else ''),
            zone=zones[i % 3], category=list(cats.values())[i % 6], rating=3.8 + (i % 10) / 10, reviews=40 + i * 37, photos=i % 9, has_website=i % 3 == 0,
            website_url='https://example.com' if i % 3 == 0 else '', opportunity_score=100 - i * 3, status=status, business=bs[i % 3] if status == 'pipeline' else None, distance_m=300 + i * 50)
    PlacesUsage.objects.create(month=today.replace(day=1), requests=51)
    for months_back, amount in [(0, 500), (1, 1200), (2, 0), (3, 430), (5, 250)]:
        day = (today.replace(day=1) - timedelta(days=28 * months_back)).replace(day=5)
        if amount:
            Transaction.objects.create(date=day, concept='Cobro desarrollo web', business=bs[months_back % 3], category='development', amount=amount)
        Transaction.objects.create(date=day + timedelta(days=1), concept='Hosting VPS', category='hosting', amount=-12)
    Transaction.objects.create(date=today.replace(day=1), concept='Mantenimiento mensual El Rincón del Quijote', business=bs[0], category='maintenance', amount=20)
    Transaction.objects.create(date=today.replace(day=1), concept='Licencia de herramientas de diseño', category='tools', amount=Decimal('-29.99'))
    RecurringExpense.objects.create(concept='Dominio nndrei.dev', amount=7, periodicity='annual', next_date=today + timedelta(days=90))
    RecurringExpense.objects.create(concept='Servidor VPS Hetzner', amount=12, periodicity='monthly', next_date=today + timedelta(days=6))
    for concept, kind, amount, period, status in [('Desarrollo', 'development', 500, 'once', 'paid'), ('Mantenimiento', 'maintenance', 20, 'monthly', 'pending'), ('Dominio', 'domain', None, 'annual', 'pending')]:
        ClientCharge.objects.create(business=bs[0], concept=concept, kind=kind, amount=amount, periodicity=period, status=status, next_date=today + timedelta(days=20) if period != 'once' else None)
    Commission.objects.create(business=bs[1], beneficiary='Carlos (referido)', percentage=10)
    return {'users': users, 'businesses': bs, 'proposal': Proposal.objects.get(number='P-2026-002')}


@pytest.fixture(autouse=True)
def plain_http(settings):
    """The live server speaks plain HTTP: WebKit, unlike Chromium, drops Secure cookies on
    http://localhost, which would leave HTMX posts without their CSRF cookie."""
    settings.SESSION_COOKIE_SECURE = settings.CSRF_COOKIE_SECURE = False


def session_cookie(client, user, live_server):
    client.force_login(user)
    device = TOTPDevice.objects.create(user=user, confirmed=True)
    session = client.session
    session['otp_device_id'] = device.persistent_id
    session.save()
    return {'name': 'sessionid', 'value': client.cookies['sessionid'].value, 'url': live_server.url}


def screens(demo):
    b, p = demo['businesses'][1], demo['proposal']
    return {
        'admin': [('pipeline', '/'), ('buscar', '/buscar/'), ('clientes', '/clientes/'), ('ficha', f'/clientes/{b.public_id}/'),
                  ('propuestas', '/propuestas/'), ('propuesta', f'/propuestas/{p.public_id}/'), ('finanzas', '/finanzas/'),
                  ('usuarios', '/usuarios/'), ('mas', '/mas/'), ('nuevo-lead', '/clientes/nuevo/')],
        'developer': [('clientes', '/clientes/'), ('ficha', f'/clientes/{demo["businesses"][0].public_id}/')],
        'accountant': [('finanzas', '/finanzas/'), ('ficha', f'/clientes/{b.public_id}/')],
        'client': [('portal', '/portal/'), ('peticiones', '/portal/peticiones/'), ('gastos', '/portal/gastos/'), ('documentos', '/portal/documentos/')],
    }


def overflow(page):
    """Page-level sideways scroll plus the elements that stick out, for a useful failure message."""
    return page.evaluate('''() => {
        const width = window.innerWidth;
        const culprits = [...document.querySelectorAll('body *')].filter(el => {
            const r = el.getBoundingClientRect();
            return r.width && r.right > width + 1 && getComputedStyle(el).position !== 'fixed' && !el.closest('.m-scroll, .table-scroll, dialog');
        }).slice(0, 5).map(el => el.tagName.toLowerCase() + (el.className ? '.' + String(el.className).split(' ').join('.') : ''));
        return {scroll: document.documentElement.scrollWidth, width, culprits};
    }''')


@pytest.fixture
def browser():
    with sync_api.sync_playwright() as p:
        engine = getattr(p, os.environ.get('E2E_BROWSER', 'webkit'))
        instance = engine.launch()
        yield instance
        instance.close()


def open_page(browser, client, live_server, user, viewport, mobile=True):
    context = browser.new_context(viewport=viewport, device_scale_factor=2, is_mobile=mobile, has_touch=mobile, locale='es-ES',
                                  timezone_id='Europe/Madrid', service_workers='block')
    context.add_cookies([session_cookie(client, user, live_server)])
    return context, context.new_page()


def visit(page, url):
    page.goto(url)
    page.wait_for_load_state('networkidle')
    page.evaluate('document.fonts.ready')


def test_every_screen_fits_a_phone(browser, client, live_server, demo):
    OUT.mkdir(parents=True, exist_ok=True)
    failures = []
    for role, pages in screens(demo).items():
        user = demo['users'][role]
        for width in WIDTHS:
            context, page = open_page(browser, client, live_server, user, {'width': width, 'height': PHONE['height']})
            for name, path in pages:
                visit(page, live_server.url + path)
                result = overflow(page)
                if result['scroll'] > result['width']:
                    failures.append(f'{role}/{name} @ {width}px: scrollWidth {result["scroll"]} > {result["width"]} {result["culprits"]}')
                if width == PHONE['width']:
                    page.screenshot(path=str(OUT / f'{PHONE["width"]}-{role}-{name}.png'))
                    page.add_style_tag(content='.m-appbar{position:static!important}.m-tabbar,.fab{display:none!important}')
                    page.screenshot(path=str(OUT / f'{PHONE["width"]}-{role}-{name}-full.png'), full_page=True)
            context.close()
    assert not failures, '\n'.join(failures)


def test_desktop_screens(browser, client, live_server, demo):
    """Reference captures at 1440 px, to compare against the previous desktop layout."""
    out = OUT / 'desktop'
    out.mkdir(parents=True, exist_ok=True)
    for role, pages in screens(demo).items():
        context, page = open_page(browser, client, live_server, demo['users'][role], DESKTOP, mobile=False)
        for name, path in pages:
            visit(page, live_server.url + path)
            page.screenshot(path=str(out / f'1440-{role}-{name}.png'), full_page=True)
        context.close()


def test_phone_interactions(browser, client, live_server, demo):
    """The HTMX flows behind the phone controls, driven like a thumb would."""
    zone, category = SearchZone.objects.first(), SearchCategory.objects.first()
    for i in range(40):  # Enough rows for a second page of opportunities.
        PlaceSnapshot.objects.create(place_id=f'extra-{i}', name=f'Negocio extra {i}', zone=zone, category=category, opportunity_score=5, reviews=10)
    context, page = open_page(browser, client, live_server, demo['users']['admin'], PHONE)
    cards = '.mob-only .list-card'

    # Pipeline: a stage tile filters "Clientes en curso"; tapping it again clears the filter.
    visit(page, live_server.url + '/')
    assert page.locator(f'#pipeline-list {cards}').count() == 9
    page.locator('.stage-tile.proposal').click()
    page.wait_for_selector('.stage-tile.proposal.is-active')
    assert page.locator(f'#pipeline-list {cards}').count() == 1 and 'stage=proposal' in page.url
    page.locator('.stage-tile.proposal').click()
    page.wait_for_selector('.stage-tile.proposal:not(.is-active)')
    assert page.locator(f'#pipeline-list {cards}').count() == 9

    # Buscar: the filter sheet applies without reloading and shows its count.
    visit(page, live_server.url + '/buscar/')
    assert page.locator('#op-list .list-card').count() == 50
    page.get_by_role('button', name='Cargar más').click()
    page.wait_for_function('document.querySelectorAll("#op-list .list-card").length === 64')
    page.locator('[data-sheet-open="m-search-sheet"]').click()
    page.select_option('#m-quality', 'no_web')
    page.get_by_role('button', name='Aplicar').click()
    page.wait_for_function('document.querySelectorAll("#op-list .list-card").length < 64')
    assert not page.locator('#m-search-sheet').is_visible()
    assert page.locator('[data-filter-count="m-search-sheet"]').inner_text().strip() == '· 1'
    assert all('Sin web' in text for text in page.locator('#op-list .list-card').all_inner_texts())
    first = page.locator('#op-list .list-card', has=page.get_by_role('button', name='Captar')).first
    name, card_id = first.locator('.lc-title').inner_text(), first.get_attribute('id')
    first.get_by_role('button', name='Captar').click()
    page.wait_for_selector(f'#{card_id} >> text=En pipeline')
    assert Business.objects.filter(name=name).exists()

    # Clientes: typing filters the list in place.
    visit(page, live_server.url + '/clientes/')
    page.fill('#m-q', 'Café')
    page.wait_for_function('document.querySelectorAll("#clients-table .mob-only .list-card").length === 1')

    # Finanzas: the month arrows change the period without a reload.
    visit(page, live_server.url + '/finanzas/')
    before = page.locator('.m-month b').inner_text()
    page.get_by_role('link', name='Mes anterior').click()
    page.wait_for_function(f'document.querySelector(".m-month b").textContent !== {before!r}')
    assert 'month=' in page.url

    # Ficha: a quick note lands in the history.
    visit(page, live_server.url + f'/clientes/{demo["businesses"][1].public_id}/')
    page.get_by_role('button', name='Nota rápida').click()
    page.fill('#m-note', 'Volver el jueves por la tarde')
    page.get_by_role('button', name='Guardar nota').click()
    page.wait_for_selector('text=Nota guardada en el historial.')
    assert ActivityEvent.objects.filter(business=demo['businesses'][1], text='Nota: Volver el jueves por la tarde').exists()
    context.close()
