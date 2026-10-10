"""Map captures and real HTMX interactions in Chromium and iPhone WebKit."""
import os
from pathlib import Path
import pytest
from django.utils import timezone
from core.models import Business, City, Neighbourhood, SearchCategory, User
from tests.e2e.test_mobile_screens import session_cookie

sync_api = pytest.importorskip('playwright.sync_api')
os.environ.setdefault('DJANGO_ALLOW_ASYNC_UNSAFE', 'true')
pytestmark = pytest.mark.django_db(transaction=True)
OUT = Path(__file__).resolve().parents[2] / 'docs/screenshots/mapa'


@pytest.fixture
def map_data(settings):
    settings.SESSION_COOKIE_SECURE = settings.CSRF_COOKIE_SECURE = False
    admin = User.objects.create_user('mapa@example.com', 'test', name='Andrei', role='admin')
    city = City.objects.get_or_create(ine_code='28079', defaults={'name': 'Madrid', 'search_name': 'madrid', 'province': 'Madrid'})[0]
    cat = SearchCategory.objects.create(name='Cafeterías')
    names = ['Los Rosales', 'Butarque']
    zones = [Neighbourhood.objects.create(city=city, name=name, search_name=name.lower(), source='madrid', external_id=f'map-{i}', latitude=40.35, longitude=-3.69, bbox=[40.34, -3.71, 40.37, -3.67], geometry={'type': 'Polygon', 'coordinates': [[[-3.71, 40.34], [-3.67, 40.34], [-3.67, 40.37], [-3.71, 40.37], [-3.71, 40.34]]]}) for i, name in enumerate(names)]
    for i, name in enumerate(['Café Los Rosales', 'Bar La Esquina', 'Panadería Ana', 'El Rincón', 'Café Central', 'Café Butarque']):
        Business.objects.create(name=name, category=cat, city=city, neighbourhood=zones[i // 4],
                                address=f'Calle de la Alianza {i + 1}, Madrid', stage='proposal' if i < 4 else 'visited',
                                latitude=40.354 + i * .001, longitude=-3.688 - i * .001,
                                opening_hours=[{'open': {'day': 0, 'hour': 0}}])
    Business.objects.create(name='Negocio pendiente de ubicar', stage='proposal')
    Business.objects.create(name='Nunca mostrar', stage='discarded', latitude=40.35, longitude=-3.69)
    return admin


@pytest.mark.parametrize('width', [1280, 390])
def test_map_screens(live_server, client, map_data, width):
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_api.sync_playwright() as pw:
        browser = pw.chromium.launch()
        context = browser.new_context(viewport={'width': width, 'height': 900 if width == 1280 else 844},
                                      geolocation={'latitude': 40.355, 'longitude': -3.689}, permissions=['geolocation'])
        context.add_cookies([session_cookie(client, map_data, live_server)])
        page = context.new_page()
        errors = []; page.on('pageerror', lambda error: errors.append(str(error) + '\n' + error.stack))
        page.goto(live_server.url + '/')
        page.locator('#pipeline-actions' if width == 1280 else '#pipeline-mobile-actions').get_by_text('Ver mapa').click()
        page.wait_for_selector('.visit-marker,.visit-cluster')
        page.wait_for_function("document.querySelector('[data-map-message]').textContent.includes('6 negocios')")
        assert 'vista=mapa' in page.url
        assert page.locator('.zone-business').count() == 6
        assert page.locator('.map-missing summary').inner_text() == '1 negocio sin ubicación'
        page.wait_for_function("document.querySelectorAll('.leaflet-tile').length > 0 && [...document.querySelectorAll('.leaflet-tile')].every(img => img.complete && img.naturalWidth > 0)", timeout=30000)
        page.screenshot(path=str(OUT / f'{width}-todas.png'), full_page=True)
        page.locator('.stage-tile.proposal').click()
        page.wait_for_function("document.querySelector('[data-map-message]').textContent.includes('4 negocios')")
        assert 'fase=proposal' in page.url and 'vista=mapa' in page.url
        assert page.locator('.zone-business').count() == 4
        page.wait_for_function("document.querySelectorAll('.leaflet-tile').length > 0 && [...document.querySelectorAll('.leaflet-tile')].every(img => img.complete && img.naturalWidth > 0)", timeout=30000)
        page.screenshot(path=str(OUT / f'{width}-propuesta.png'), full_page=True)
        page.reload()
        page.wait_for_function("document.querySelector('[data-map-message]').textContent.includes('4 negocios')")
        if width == 390:
            page.locator('.zone-handle').click()
        page.locator('.zone-business input').first.check()
        page.locator('.zone-business input').nth(1).check()
        with page.expect_popup() as popup:
            page.locator('[data-route]').click()
        assert 'travelmode=walking' in popup.value.url and 'waypoints=' in popup.value.url
        popup.value.close()
        page.locator('.zone-business-name').first.click()
        page.wait_for_selector('.leaflet-popup-content')
        assert 'Abierto ahora' in page.locator('.leaflet-popup-content').inner_text()
        assert page.locator('.leaflet-popup-content a', has_text='Waze').get_attribute('href').startswith('https://waze.com/ul?')
        page.locator('.leaflet-popup-close-button').click()
        page.locator('[data-locate]').click()
        page.wait_for_function("document.querySelector('[data-map-message]').textContent.includes('cercanía')")
        assert 'a ' in page.locator('.zone-business small').first.text_content()
        page.locator('.stage-tile.proposal').click()
        page.wait_for_function("document.querySelector('[data-map-message]').textContent.includes('6 negocios')")
        assert 'fase=proposal' not in page.url
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        page.locator('#pipeline-actions' if width == 1280 else '#pipeline-mobile-actions').get_by_text('Ver lista').click()
        page.wait_for_selector('.visit-map', state='detached')
        page.go_back()
        page.wait_for_selector('.visit-marker,.visit-cluster')
        assert not errors
        browser.close()


def test_iphone_pwa_geolocation(live_server, client, map_data):
    with sync_api.sync_playwright() as pw:
        browser = pw.webkit.launch()
        context = browser.new_context(**pw.devices['iPhone 13'], geolocation={'latitude': 40.355, 'longitude': -3.689}, permissions=['geolocation'])
        context.add_init_script("Object.defineProperty(navigator, 'standalone', {value: true});")
        context.add_cookies([session_cookie(client, map_data, live_server)])
        page = context.new_page(); page.goto(live_server.url + '/?vista=mapa')
        page.wait_for_function("document.querySelector('[data-map-message]').textContent.includes('6 negocios')")
        page.locator('[data-locate]').click()
        page.wait_for_function("document.querySelector('[data-map-message]').textContent.includes('cercanía')")
        page.locator('.zone-handle').click()
        assert page.locator('.zone-panel').get_attribute('class').endswith('expanded')
        assert 'a ' in page.locator('.zone-business small').first.text_content()
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
        browser.close()


def test_mobile_routes_keep_all_stops_and_limit(live_server, client, map_data):
    from urllib.parse import parse_qs, urlparse
    for i in range(4):
        Business.objects.create(name=f'Otro {i}', latitude=40.36 + i * .001, longitude=-3.69)
    with sync_api.sync_playwright() as pw:
        browser = pw.chromium.launch()
        context = browser.new_context(viewport={'width': 390, 'height': 844})
        context.add_cookies([session_cookie(client, map_data, live_server)])
        page = context.new_page(); page.goto(live_server.url + '/?vista=mapa')
        page.wait_for_function("document.querySelector('[data-map-message]').textContent.includes('10 negocios')")
        page.locator('.zone-handle').click()
        checkboxes = page.locator('.zone-business input')
        for i in range(9):
            checkboxes.nth(i).check()
        checkboxes.nth(9).click()
        assert not checkboxes.nth(9).is_checked()
        assert 'hasta 9' in page.locator('[data-route-message]').inner_text()
        page.locator('[data-route]').click()
        links = page.locator('[data-route-links] a')
        assert links.count() == 3
        stops = []
        previous_end = None
        for i in range(3):
            query = parse_qs(urlparse(links.nth(i).get_attribute('href')).query)
            assert query['travelmode'] == ['walking']
            if previous_end:
                assert query['origin'] == [previous_end]
            part = query.get('waypoints', [''])[0].split('|') if query.get('waypoints') else []
            assert len(part) <= 3
            stops.extend(part + query['destination'])
            previous_end = query['destination'][0]
        assert len(stops) == len(set(stops)) == 9
        browser.close()
