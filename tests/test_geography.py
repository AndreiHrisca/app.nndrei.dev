"""Cities and neighbourhoods: loading, placing businesses, searching by area, zones and MCP."""
import io
import zipfile
from unittest import mock
import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone
from core import geo, geo_sources, mcp_tools as tools
from core.models import Business, City, LegacySearchZone, Neighbourhood, PlaceSnapshot, SearchArea, SearchCategory, SearchRun, SearchSlice

pytestmark = pytest.mark.django_db


def square(lat, lng, half):
    ring = [[lng - half, lat - half], [lng + half, lat - half], [lng + half, lat + half], [lng - half, lat + half], [lng - half, lat - half]]
    return {'type': 'Polygon', 'coordinates': [ring]}


def neighbourhood(city, name, geometry=None, bbox=None, center=None):
    bbox = bbox or geo.bbox_of(geometry)
    lat, lng = center or geo.center_of(bbox)
    return Neighbourhood.objects.create(city=city, name=name, search_name=geo.normalize(name), source='osm', external_id=name,
                                        bbox=bbox, geometry=geometry, latitude=lat, longitude=lng)


# --- Names, geometry and conversions (pure) ------------------------------------------

def test_names_from_the_ine_register():
    assert geo.display_name('Palmas de Gran Canaria, Las') == 'Las Palmas de Gran Canaria'
    assert geo.display_name("Hospitalet de Llobregat, L'") == "L'Hospitalet de Llobregat"
    assert geo.display_name('Coruña, A') == 'A Coruña'
    assert geo.display_name('Alacant/Alicante') == 'Alacant/Alicante'
    assert geo.normalize(' Móstoles  de   Abajo ') == 'mostoles de abajo'
    assert geo.query_name('Alicante/Alacant') == 'Alicante'


def test_point_in_polygon_with_a_hole():
    outer = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]
    hole = [[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]]
    shape = {'type': 'Polygon', 'coordinates': [outer, hole]}
    assert geo.contains(shape, 2, 2) and not geo.contains(shape, 5, 5) and not geo.contains(shape, 11, 5)
    assert geo.contains({'type': 'MultiPolygon', 'coordinates': [[outer], [[[20, 20], [30, 20], [30, 30], [20, 20]]]]}, 22, 25)


def test_utm_conversion_puts_puerta_del_sol_in_madrid():
    # Puerta del Sol, ETRS89 / UTM 30N (IGN): x 440 295, y 4 474 255.
    lat, lng = geo.utm_to_latlng(440295, 4474255)
    assert abs(lat - 40.41675) < 0.0005 and abs(lng - (-3.70380)) < 0.0005


def test_grid_keeps_only_cells_touching_the_outline():
    bbox = [40.0, -4.0, 40.1, -3.9]  # ~11 × 8,5 km
    assert len(geo.grid(bbox, 1.5)) == 8 * 6
    # An L-shaped outline leaves the empty corner out.
    l_shape = {'type': 'Polygon', 'coordinates': [[[-4.0, 40.0], [-3.9, 40.0], [-3.9, 40.02], [-3.98, 40.02], [-3.98, 40.1], [-4.0, 40.1], [-4.0, 40.0]]]}
    # Bottom strip 2 rows × 6 columns + left column 8 rows × 2 columns − 4 shared = 24 of 48.
    assert len(geo.grid(bbox, 1.5, l_shape)) == 24
    assert len(geo.grid([40.0, -4.0, 40.001, -3.999], 1.5)) == 1


# --- Loading municipalities (idempotent) -----------------------------------------------

def ine_workbook(rows):
    """A minimal xlsx shaped like INE's 'diccionario' (title row, header row, data)."""
    def cell(ref, value):
        if isinstance(value, str):
            return f'<c r="{ref}" t="inlineStr"><is><t>{value}</t></is></c>'
        return f'<c r="{ref}"><v>{value}</v></c>'
    body = [f'<row r="1">{cell("A1", "Relación de municipios")}</row>',
            '<row r="2">' + ''.join(cell(f'{c}2', h) for c, h in zip('ABCDE', ['CODAUTO', 'CPRO', 'CMUN', 'DC', 'NOMBRE'])) + '</row>']
    for i, row in enumerate(rows, start=3):
        body.append(f'<row r="{i}">' + ''.join(cell(f'{c}{i}', v) for c, v in zip('ABCDE', row)) + '</row>')
    sheet = ('<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             '<sheetData>' + ''.join(body) + '</sheetData></worksheet>')
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as z:
        z.writestr('xl/worksheets/sheet1.xml', sheet)
    return buffer.getvalue()


def test_municipalities_load_twice_without_duplicates_and_keep_what_osm_told_us():
    rows = [[13, 28, 79, 6, 'Madrid'], [13, 28, 92, 3, 'Móstoles'], [5, 35, 16, 0, 'Palmas de Gran Canaria, Las']]
    before = City.objects.count()
    assert geo_sources.load_municipalities(ine_workbook(rows)) == (2, 0)  # Madrid already exists (migration).
    City.objects.filter(ine_code='28092').update(latitude=40.32, geocoded_at=timezone.now())
    assert geo_sources.load_municipalities(ine_workbook(rows)) == (0, 0)
    rows[1][4] = 'Móstoles (nuevo nombre)'
    assert geo_sources.load_municipalities(ine_workbook(rows)) == (0, 1)
    assert City.objects.count() == before + 2
    mostoles = City.objects.get(ine_code='28092')
    assert mostoles.name == 'Móstoles (nuevo nombre)' and mostoles.province == 'Madrid' and mostoles.community == 'Comunidad de Madrid'
    assert mostoles.latitude == 40.32 and mostoles.geocoded_at is not None
    assert City.objects.get(ine_code='35016').name == 'Las Palmas de Gran Canaria'


# --- Placing a business by its coordinates ----------------------------------------------

def test_neighbourhood_by_polygon_then_by_nearest_box():
    city = City.objects.get(ine_code='28079')
    inside = neighbourhood(city, 'Poligono', square(40.40, -3.70, 0.01))
    # Its box covers the point, but its outline does not.
    neighbourhood(city, 'Cerca pero fuera', {'type': 'Polygon', 'coordinates': [[[-3.72, 40.38], [-3.71, 40.38], [-3.71, 40.42], [-3.72, 40.38]]]})
    near_box = neighbourhood(city, 'Caja cercana', bbox=[40.50, -3.80, 40.60, -3.70], center=(40.52, -3.78))
    neighbourhood(city, 'Caja lejana', bbox=[40.50, -3.80, 40.60, -3.70], center=(40.59, -3.71))
    items = list(city.neighbourhoods.all())
    assert geo.find_neighbourhood(items, 40.40, -3.70) == inside
    assert geo.find_neighbourhood(items, 40.521, -3.779) == near_box
    assert geo.find_neighbourhood(items, 41.5, -3.7) is None and geo.find_neighbourhood(items, None, None) is None


def test_business_without_coordinates_is_geocoded_once(monkeypatch):
    city = City.objects.get(ine_code='28079')
    city.neighbourhoods_fetched_at = timezone.now(); city.save()
    sol = neighbourhood(city, 'Sol', square(40.4168, -3.7038, 0.004))
    calls = []

    def fake_nominatim(**params):
        calls.append(params)
        return [{'lat': '40.4168', 'lon': '-3.7038', 'address': {'city': 'Madrid', 'state': 'Comunidad de Madrid'}}]
    monkeypatch.setattr(geo_sources, 'nominatim', fake_nominatim)
    b = Business.objects.create(name='Sin coordenadas', address='Puerta del Sol 1, Madrid')
    geo_sources.place_business(b)
    b.refresh_from_db()
    assert (b.city, b.neighbourhood, b.latitude) == (city, sol, 40.4168) and b.geocoded_at
    assert calls[0]['q'] == 'Puerta del Sol 1, Madrid, España'
    b.latitude = b.longitude = None; b.save()
    geo_sources.place_business(b)
    assert len(calls) == 1  # Never asked twice.


# --- Migrating the current businesses ----------------------------------------------------

BEFORE = ('core', '0022_merge_proposal_add_demo_ready')
AFTER = ('core', '0024_retire_fixed_zones')


@pytest.mark.django_db(transaction=True)
def test_current_businesses_get_madrid_and_keep_everything_else():
    executor = MigrationExecutor(connection)
    executor.migrate([BEFORE])
    old = executor.loader.project_state([BEFORE]).apps
    try:
        Zone, OldBusiness = old.get_model('core', 'SearchZone'), old.get_model('core', 'Business')
        OldSnapshot, OldCategory = old.get_model('core', 'PlaceSnapshot'), old.get_model('core', 'SearchCategory')
        zone = Zone.objects.create(name='Miguel Hernández', latitude=40.3726, longitude=-3.6555, radius=1000)
        category = OldCategory.objects.create(name='Bares')
        OldSnapshot.objects.create(place_id='p1', name='Bar', zone=zone, category=category, latitude=40.3726, longitude=-3.6555)
        tracked = OldBusiness.objects.create(name='En seguimiento', zone='Miguel Hernández', stage='proposal', phone='600', next_action='Llamar')
        plain = OldBusiness.objects.create(name='Sin zona')

        executor.loader.build_graph()
        executor.migrate([AFTER])
        new = executor.loader.project_state([AFTER]).apps
        Business_ = new.get_model('core', 'Business')
        madrid = new.get_model('core', 'City').objects.get(ine_code='28079')
        moved = Business_.objects.get(pk=tracked.pk)
        assert moved.city_id == madrid.pk and moved.legacy_zone == 'Miguel Hernández'
        assert (moved.stage, moved.phone, moved.next_action, moved.neighbourhood_id) == ('proposal', '600', 'Llamar', None)
        assert Business_.objects.get(pk=plain.pk).city_id == madrid.pk
        assert new.get_model('core', 'PlaceSnapshot').objects.get(place_id='p1').city_id == madrid.pk
        assert new.get_model('core', 'LegacySearchZone').objects.get().name == 'Miguel Hernández'
    finally:
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())


def test_old_zones_become_the_official_neighbourhood_that_contains_them():
    madrid = City.objects.get(ine_code='28079')
    madrid.neighbourhoods_fetched_at = timezone.now(); madrid.save()
    entrevias = neighbourhood(madrid, 'Entrevías', square(40.3726, -3.6555, 0.01))
    LegacySearchZone.objects.create(name='Miguel Hernández', latitude=40.3726, longitude=-3.6555, active=True)
    LegacySearchZone.objects.create(name='Zona apagada', latitude=40.373, longitude=-3.656, active=False)
    geo_sources.resolve_legacy_zones()
    area = SearchArea.objects.get(city=madrid, neighbourhood=entrevias)
    assert area.active and LegacySearchZone.objects.filter(resolved_area=area).count() == 2
    assert not Neighbourhood.objects.filter(name='Miguel Hernández').exists()  # Not forced into a neighbourhood.
    geo_sources.resolve_legacy_zones()
    assert SearchArea.objects.count() == 1


def test_assign_areas_places_businesses_and_results(monkeypatch):
    madrid = City.objects.get(ine_code='28079')
    madrid.neighbourhoods_fetched_at = timezone.now(); madrid.save()
    sol = neighbourhood(madrid, 'Sol', square(40.4168, -3.7038, 0.004))
    b = Business.objects.create(name='Con coordenadas', city=madrid, latitude=40.4169, longitude=-3.7037, legacy_zone='Villaverde Bajo', stage='visited')
    snap = PlaceSnapshot.objects.create(place_id='s', name='X', city=madrid, latitude=40.4167, longitude=-3.704, category=SearchCategory.objects.create(name='C'))
    geo_sources.assign_areas(geocode=False)
    b.refresh_from_db(); snap.refresh_from_db()
    assert b.neighbourhood == sol and b.stage == 'visited' and b.legacy_zone == 'Villaverde Bajo'
    assert snap.neighbourhood == sol


# --- OpenStreetMap parsing ----------------------------------------------------------

def test_overpass_relations_are_stitched_and_the_finest_level_wins():
    city = City(latitude=40.0, longitude=-4.0, bbox=[39.9, -4.1, 40.1, -3.9], geometry=square(40.0, -4.0, 0.1))
    way_a = [{'lat': 40.0, 'lon': -4.0}, {'lat': 40.0, 'lon': -3.98}, {'lat': 40.02, 'lon': -3.98}]
    way_b = [{'lat': 40.02, 'lon': -3.98}, {'lat': 40.02, 'lon': -4.0}, {'lat': 40.0, 'lon': -4.0}]
    data = {'elements': [
        {'type': 'relation', 'id': 1, 'tags': {'boundary': 'administrative', 'admin_level': '10', 'name': 'Barrio Norte'},
         'members': [{'type': 'way', 'role': 'outer', 'geometry': way_a}, {'type': 'way', 'role': 'outer', 'geometry': way_b}]},
        {'type': 'relation', 'id': 2, 'tags': {'boundary': 'administrative', 'admin_level': '9', 'name': 'Distrito'},
         'members': [{'type': 'way', 'role': 'outer', 'geometry': way_a + way_b[1:]}]},
        {'type': 'relation', 'id': 3, 'tags': {'boundary': 'administrative', 'admin_level': '10', 'name': 'Fuera'},
         'members': [{'type': 'way', 'role': 'outer', 'geometry': [{'lat': 45, 'lon': 2}, {'lat': 45, 'lon': 2.1}, {'lat': 45.1, 'lon': 2.1}, {'lat': 45, 'lon': 2}]}]},
    ]}
    kept = geo_sources.neighbourhoods_from_overpass(data, city)
    assert [k[1] for k in kept] == ['Barrio Norte']
    assert geo.contains(kept[0][2], 40.01, -3.99)


def test_city_outline_is_asked_once(monkeypatch):
    city = City.objects.create(ine_code='47186', name='Valladolid', search_name='valladolid', province='Valladolid', community='Castilla y León')
    answers = [[{'osm_type': 'relation', 'osm_id': 345, 'category': 'boundary', 'lat': '41.65', 'lon': '-4.72',
                 'boundingbox': ['41.55', '41.81', '-4.93', '-4.65'], 'geojson': square(41.65, -4.72, 0.1)}]]
    monkeypatch.setattr(geo_sources, 'nominatim', lambda **params: answers.pop())
    geo_sources.ensure_city_located(city)
    geo_sources.ensure_city_located(City.objects.get(pk=city.pk))  # Second time: cached, no call.
    city.refresh_from_db()
    assert city.bbox == [41.55, -4.93, 41.81, -4.65] and city.osm_relation == 345 and city.geometry['type'] == 'Polygon'


def test_city_without_neighbourhoods_is_searched_whole(sign_in, monkeypatch):
    town = City.objects.create(ine_code='19999', name='Pueblo', search_name='pueblo', province='Guadalajara', community='Castilla-La Mancha',
                               osm_relation=9, geocoded_at=timezone.now(), bbox=[40.6, -2.6, 40.62, -2.58])
    monkeypatch.setattr(geo_sources, 'overpass', lambda query: {'elements': []})
    body = sign_in('admin').get(f'/buscar/barrios/?city={town.pk}').content.decode()
    assert body.count('<option') == 1 and 'Toda la ciudad' in body and 'Sin barrios disponibles' in body
    town.refresh_from_db()
    assert town.neighbourhoods_fetched_at is not None


# --- Searching an area ----------------------------------------------------------------

def place(place_id, lat, lng):
    return {'id': place_id, 'displayName': {'text': place_id}, 'businessStatus': 'OPERATIONAL', 'location': {'latitude': lat, 'longitude': lng}}


def fake_response(places):
    response = mock.Mock()
    response.json.return_value = {'places': places}
    return response


def test_results_outside_the_real_outline_are_discarded(settings, area):
    from core.places import refresh_places
    settings.GOOGLE_PLACES_API_KEY = 'test'
    SearchCategory.objects.create(name='Bares')
    # An L-shaped neighbourhood: its box includes a corner that is not in the neighbourhood.
    barrio = area.neighbourhood
    barrio.geometry = {'type': 'Polygon', 'coordinates': [[[-3.705, 40.395], [-3.695, 40.395], [-3.695, 40.40], [-3.70, 40.40], [-3.70, 40.405], [-3.705, 40.405], [-3.705, 40.395]]]}
    barrio.save()
    results = [place('dentro', 40.397, -3.702), place('en-la-caja-no-en-el-barrio', 40.403, -3.697), place('lejos', 40.5, -3.7)]
    with mock.patch('core.places.requests.post', return_value=fake_response(results)) as post:
        refresh_places()
    body = post.call_args.kwargs['json']
    assert 'rectangle' in body['locationRestriction'] and 'circle' not in body['locationRestriction']
    assert list(PlaceSnapshot.objects.values_list('place_id', flat=True)) == ['dentro']
    snapshot = PlaceSnapshot.objects.get()
    assert snapshot.city == area.city and snapshot.neighbourhood == barrio


def test_big_area_is_searched_cell_by_cell_within_the_budget(settings, area):
    from core.places import estimate, refresh_places
    settings.GOOGLE_PLACES_API_KEY = 'test'
    settings.PLACES_SEARCH_BUDGET = 5
    settings.PLACES_CELL_KM = 0.3
    category = SearchCategory.objects.create(name='Bares')
    numbers = estimate(area, [category])
    assert numbers['cells'] > 5 and numbers['high'] == 5 and numbers['capped']
    with mock.patch('core.places.requests.post', return_value=fake_response([])) as post:
        pk = refresh_places()
    rects = [c.kwargs['json']['locationRestriction']['rectangle'] for c in post.call_args_list]
    assert post.call_count == 5 and len({str(r) for r in rects}) == 5  # Five different cells.
    progress = SearchSlice.objects.get()
    assert progress.fetched_at is None and progress.next_cell == 5  # Cut short: pending, and it knows where it stopped.
    assert 'Tope de llamadas' in SearchRun.objects.get(pk=pk).message
    # The next run carries on from cell 5 instead of starting again.
    with mock.patch('core.places.requests.post', return_value=fake_response([])) as post:
        refresh_places()
    later = [c.kwargs['json']['locationRestriction']['rectangle'] for c in post.call_args_list]
    assert not {str(r) for r in later} & {str(r) for r in rects}
    cells = numbers['cells']
    runs = 2
    while SearchSlice.objects.get().fetched_at is None:
        with mock.patch('core.places.requests.post', return_value=fake_response([])):
            refresh_places()
        runs += 1
    assert runs == -(-cells // 5)  # Every cell searched exactly once across the runs.
    assert SearchSlice.objects.get().next_cell == 0


def test_estimate_and_confirmation_before_searching(sign_in, settings, area):
    settings.PLACES_CONFIRM_ABOVE = 2
    SearchCategory.objects.create(name='Bares')
    client = sign_in('admin')
    data = {'city': area.city_id, 'barrio': area.neighbourhood_id}
    panel = client.get('/buscar/zona/', data).content.decode()
    assert 'entre <b>1</b> y <b>3</b> llamadas' in panel and 'hx-confirm=' in panel
    with mock.patch('django_q.tasks.async_task') as task:
        refused = client.post('/buscar/zona/buscar/', data)
        assert refused.status_code == 400 and 'Confírmala' in refused.content.decode() and not SearchRun.objects.exists()
        started = client.post('/buscar/zona/buscar/', dict(data, confirmed='1'))
    assert started.status_code == 200 and SearchRun.objects.count() == 1
    settings.PLACES_CONFIRM_ABOVE = 30
    assert 'hx-confirm=' not in client.get('/buscar/zona/', data).content.decode()


def test_actualizar_only_goes_through_active_zones(sign_in, area):
    SearchCategory.objects.create(name='Bares')
    client = sign_in('admin')
    assert 'Se consultarán 1 combinaciones de zona activa' in client.get('/buscar/').content.decode()
    client.post(f'/buscar/zonas/{area.pk}/alternar/')
    area.refresh_from_db()
    assert not area.active
    assert 'No se gastará ninguna llamada' in client.get('/buscar/').content.decode()


def test_new_zone_can_be_added_to_active_zones(sign_in, area):
    madrid = area.city
    client = sign_in('admin')
    client.post('/buscar/zona/activar/', {'city': madrid.pk, 'barrio': ''}, HTTP_HX_REQUEST='true')
    assert SearchArea.objects.get(city=madrid, neighbourhood=None).active


# --- The page's filters -----------------------------------------------------------------

def test_city_autocomplete_shows_the_province(sign_in):
    City.objects.create(ine_code='09001', name='Villanueva', search_name='villanueva', province='Burgos', community='Castilla y León')
    City.objects.create(ine_code='06001', name='Villanueva', search_name='villanueva', province='Badajoz', community='Extremadura')
    body = sign_in('admin').get('/buscar/ciudades/', {'q': 'VILLANUEVA'}).content.decode()
    assert 'Villanueva <small>(Badajoz)</small>' in body and 'Villanueva <small>(Burgos)</small>' in body
    assert sign_in('admin').get('/buscar/ciudades/', {'q': 'mostol'}).status_code == 200


def test_filters_remember_the_last_area_and_counters_follow_it(sign_in, area):
    madrid, barrio = area.city, area.neighbourhood
    other = neighbourhood(madrid, 'Otro', square(40.5, -3.6, 0.01))
    category = SearchCategory.objects.create(name='Bares')
    PlaceSnapshot.objects.create(place_id='a', name='Bar del barrio', city=madrid, neighbourhood=barrio, category=category, has_website=False)
    PlaceSnapshot.objects.create(place_id='b', name='Bar de otro', city=madrid, neighbourhood=other, category=category, has_website=False)
    client = sign_in('admin')
    body = client.get('/buscar/', {'city': madrid.pk, 'barrio': barrio.pk}).content.decode()
    assert 'Bar del barrio' in body and 'Bar de otro' not in body
    assert 'Radio (m)' not in body and '<option value="">Toda la ciudad</option>' in body
    assert body.index('Ciudad</label>') < body.index('Barrio</label>') < body.index('Categoría</label>') < body.index('Presencia digital') < body.index('Estado</label>')
    again = client.get('/buscar/').content.decode()  # No parameters: the session remembers.
    assert 'Bar del barrio' in again and 'Bar de otro' not in again
    marker = '>Sin web</span><div class="stat">'
    assert again[again.index(marker) + len(marker)] == '1'


# --- MCP -------------------------------------------------------------------------------

def test_mcp_search_by_city_and_neighbourhood_without_accents(accounts, area):
    madrid, barrio = area.city, area.neighbourhood
    getafe = City.objects.create(ine_code='28065', name='Getafe', search_name='getafe', province='Madrid', community='Comunidad de Madrid')
    Business.objects.create(name='Bar Centro', city=madrid, neighbourhood=barrio)
    Business.objects.create(name='Bar Sur', city=getafe, legacy_zone='Villaverde Bajo')
    admin = accounts['admin']
    names = lambda **kw: [c['nombre'] for c in tools.buscar_clientes(admin, **kw)['clientes']]
    assert names(ciudad='MADRID') == ['Bar Centro'] and names(ciudad='getafé') == ['Bar Sur']
    assert names(barrio='barrio de prúeba') == ['Bar Centro']
    assert names(texto='villaverde bajo') == ['Bar Sur']  # Old zone, through legacy_zone.
    found = tools.buscar_clientes(admin, ciudad='Madrid')['clientes'][0]
    assert (found['ciudad'], found['barrio'], found['provincia']) == ('Madrid', 'Barrio de Prueba', 'Madrid')
    detail = tools.get_cliente(admin, found['id'])
    assert detail['ciudad'] == 'Madrid' and detail['barrio'] == 'Barrio de Prueba'


def test_mcp_create_places_the_business_from_its_address(accounts, area, monkeypatch):
    monkeypatch.setattr(geo_sources, 'nominatim', lambda **params: [{'lat': '40.40', 'lon': '-3.70', 'address': {'city': 'Madrid', 'state': 'Comunidad de Madrid'}}])
    created = tools.crear_cliente(accounts['admin'], 'Bar Nuevo', 'Calle Falsa 1, Madrid')
    assert created['ciudad'] == 'Madrid' and created['barrio'] == 'Barrio de Prueba'


def test_mcp_create_places_the_business_from_a_search_result(accounts, area):
    PlaceSnapshot.objects.create(place_id='ChIJ_x', name='Bar', city=area.city, neighbourhood=area.neighbourhood, latitude=40.401, longitude=-3.701,
                                 category=SearchCategory.objects.create(name='Bares'))
    created = tools.crear_cliente(accounts['admin'], 'Bar', '', 'ChIJ_x')
    assert created['ciudad'] == 'Madrid' and created['barrio'] == 'Barrio de Prueba'


def test_mcp_descriptions_mention_city_and_neighbourhood():
    import asyncio
    from core.mcp_server import mcp
    tools_by_name = {t.name: t for t in asyncio.run(mcp.list_tools())}
    schema = tools_by_name['buscar_clientes'].input_schema['properties']
    assert {'texto', 'estado', 'ciudad', 'barrio'} <= set(schema)
    assert 'ciudad' in tools_by_name['crear_cliente'].description.lower() and 'barrio' in tools_by_name['get_cliente'].description.lower()


def test_google_coordinates_also_set_the_neighbourhood(sign_in, settings, area):
    settings.GOOGLE_PLACES_API_KEY = 'test'
    b = Business.objects.create(name='Sin sitio', city=area.city, google_place_id='g-77')
    response = mock.Mock()
    response.json.return_value = {'id': 'g-77', 'location': {'latitude': 40.401, 'longitude': -3.701}}
    with mock.patch('core.places.requests.get', return_value=response):
        sign_in('admin').post(f'/clientes/{b.public_id}/actualizar-google/')
    b.refresh_from_db()
    assert b.latitude == 40.401 and b.neighbourhood == area.neighbourhood
