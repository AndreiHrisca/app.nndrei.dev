"""Where cities and neighbourhoods come from, and how they reach the database.

* Municipalities: INE, "Relación de municipios y sus códigos" (free reuse, citing INE).
* Madrid neighbourhoods and districts: Ayuntamiento de Madrid, geoportal (open data,
  free reuse, citing the source).
* Everything else (a city's outline, other cities' neighbourhoods, turning an address
  into coordinates): OpenStreetMap through Nominatim and Overpass. © OpenStreetMap
  contributors, ODbL: attribution is shown wherever these data are used.

OpenStreetMap services are shared and free: one request per second at most across
every process (`wait_turn`), an identifying User-Agent, and every answer is cached
in the database so the same question is never asked twice.
"""
import time
from datetime import timedelta
import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from . import geo
from .models import Business, City, ExternalCall, LegacySearchZone, Neighbourhood, PlaceSnapshot, SearchArea

INE_URL = 'https://www.ine.es/daco/daco42/codmun/diccionario26.xlsx'
MADRID_URL = 'https://geoportal.madrid.es/fsdescargas/IDEAM_WBGEOPORTAL/LIMITES_ADMINISTRATIVOS/Barrios/Barrios.zip'
MADRID_INE = '28079'
NOMINATIM_URL = 'https://nominatim.openstreetmap.org/search'
OVERPASS_URL = 'https://overpass-api.de/api/interpreter'
OSM_ATTRIBUTION = '© OpenStreetMap contributors (ODbL)'


class GeoError(Exception):
    pass


def headers():
    return {'User-Agent': settings.GEO_USER_AGENT, 'Accept-Language': 'es'}


def wait_turn(service, seconds=1.0):
    """At most one call per `seconds` to `service`, shared by all processes through a row lock."""
    with transaction.atomic():
        ExternalCall.objects.get_or_create(service=service, defaults={'last_at': timezone.now() - timedelta(hours=1)})
        row = ExternalCall.objects.select_for_update().get(service=service)
        wait = seconds - (timezone.now() - row.last_at).total_seconds()
        if wait > 0:
            time.sleep(wait)
        row.last_at = timezone.now()
        row.save(update_fields=['last_at'])


def download(url):
    response = requests.get(url, headers=headers(), timeout=60)
    response.raise_for_status()
    return response.content


def nominatim(**params):
    wait_turn('nominatim', 1.1)
    params.update(format='jsonv2', countrycodes='es')
    try:
        response = requests.get(NOMINATIM_URL, params=params, headers=headers(), timeout=20)
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError) as exc:
        raise GeoError('OpenStreetMap (Nominatim) no ha respondido. Vuelve a intentarlo en un momento.') from exc


def overpass(query):
    wait_turn('overpass', 1.0)
    try:
        response = requests.post(OVERPASS_URL, data={'data': query}, headers=headers(), timeout=120)
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError) as exc:
        raise GeoError('OpenStreetMap (Overpass) no ha respondido. Vuelve a intentarlo en un momento.') from exc


# --- Loading --------------------------------------------------------------------------

def load_municipalities(content=None):
    """Create or update every INE municipality. Safe to run again: keyed by INE code,
    and what OpenStreetMap already told us about a city (location, outline) is kept."""
    rows = geo.read_ine_register(content if content is not None else download(INE_URL))
    existing = {c.ine_code: c for c in City.objects.all()}
    new, changed = [], []
    for row in rows:
        city = existing.get(row['ine_code'])
        if city is None:
            new.append(City(**row))
        elif any(getattr(city, k) != v for k, v in row.items()):
            for k, v in row.items():
                setattr(city, k, v)
            changed.append(city)
    City.objects.bulk_create(new, batch_size=1000)
    City.objects.bulk_update(changed, ['name', 'search_name', 'province', 'community'], batch_size=1000)
    return len(new), len(changed)


def load_madrid_neighbourhoods(content=None):
    """The 131 official neighbourhoods (and their districts) of Madrid. Safe to run again."""
    madrid = City.objects.get(ine_code=MADRID_INE)
    count = 0
    for attrs, shape in geo.read_shapefile(content if content is not None else download(MADRID_URL)):
        if shape is None:
            continue
        geometry = geo.utm_geometry_to_wgs84(shape)
        bbox = geo.bbox_of(geometry)
        lat, lng = geo.center_of(bbox)
        name = attrs['NOMBRE']
        Neighbourhood.objects.update_or_create(
            city=madrid, source='madrid', external_id=attrs['COD_BAR'],
            defaults={'name': name, 'search_name': geo.normalize(name), 'district': attrs.get('NOMDIS', ''),
                      'bbox': bbox, 'geometry': geometry, 'latitude': lat, 'longitude': lng})
        count += 1
    City.objects.filter(pk=madrid.pk).update(neighbourhoods_fetched_at=timezone.now())
    return count


# --- A city's outline and neighbourhoods (OpenStreetMap, on first use) ------------------

def ensure_city_located(city):
    """Centre, box and outline of the municipality, asked once and kept."""
    if city.geocoded_at:
        return city
    query = f'{geo.query_name(city.name)}, {geo.query_name(city.province)}, España'
    results = nominatim(q=query, polygon_geojson=1, polygon_threshold=0.0005, limit=5)
    best = next((r for r in results if r.get('osm_type') == 'relation' and r.get('category') == 'boundary'), results[0] if results else None)
    if best:
        south, north, west, east = (float(v) for v in best['boundingbox'])
        city.latitude, city.longitude = float(best['lat']), float(best['lon'])
        city.bbox = [south, west, north, east]
        shape = best.get('geojson') or {}
        city.geometry = geo.rounded(shape) if shape.get('type') in ('Polygon', 'MultiPolygon') else None
        city.osm_relation = int(best['osm_id']) if best.get('osm_type') == 'relation' else None
    city.geocoded_at = timezone.now()
    city.save(update_fields=['latitude', 'longitude', 'bbox', 'geometry', 'osm_relation', 'geocoded_at'])
    return city


def stitch(ways):
    """Join way segments end to end into closed rings (OSM multipolygon members)."""
    ways = [list(w) for w in ways if len(w) >= 2]
    rings = []
    while ways:
        ring = ways.pop(0)
        progress = True
        while ring[0] != ring[-1] and progress:
            progress = False
            for i, way in enumerate(ways):
                if way[0] == ring[-1]:
                    ring += way[1:]
                elif way[-1] == ring[-1]:
                    ring += way[::-1][1:]
                elif way[-1] == ring[0]:
                    ring = way[:-1] + ring
                elif way[0] == ring[0]:
                    ring = way[::-1][:-1] + ring
                else:
                    continue
                ways.pop(i)
                progress = True
                break
        if len(ring) >= 4 and ring[0] == ring[-1]:
            rings.append(ring)
    return rings


def osm_geometry(element):
    """GeoJSON outline of an Overpass `out geom` way or relation, or None."""
    if element['type'] == 'way':
        ring = [[p['lon'], p['lat']] for p in element.get('geometry', [])]
        return {'type': 'Polygon', 'coordinates': [ring]} if len(ring) >= 4 and ring[0] == ring[-1] else None
    members = element.get('members', [])
    outer = stitch([[[p['lon'], p['lat']] for p in m.get('geometry', [])] for m in members if m.get('type') == 'way' and m.get('role') in ('outer', '')])
    inner = stitch([[[p['lon'], p['lat']] for p in m.get('geometry', [])] for m in members if m.get('type') == 'way' and m.get('role') == 'inner'])
    if not outer:
        return None
    polys = [[ring] for ring in outer]
    for hole in inner:
        for poly in polys:
            if geo.in_ring(hole[0][0], hole[0][1], poly[0]):
                poly.append(hole)
                break
    return {'type': 'MultiPolygon', 'coordinates': polys}


def neighbourhoods_from_overpass(data, city):
    """Pick the finest complete level: admin_level 10, else 9, else place=suburb/neighbourhood areas.

    Points without an area are left out: a dot cannot tell which side of a street
    belongs to it. Only areas whose centre is in the municipality are kept.
    """
    candidates = {'10': [], '9': [], 'place': []}
    for element in data.get('elements', []):
        tags = element.get('tags', {})
        name = tags.get('name')
        geometry = osm_geometry(element) if name else None
        if geometry is None:
            continue
        level = tags.get('admin_level') if tags.get('boundary') == 'administrative' else 'place'
        if level in candidates:
            candidates[level].append((f"{element['type']}/{element['id']}", name, geo.rounded(geometry)))
    chosen = candidates['10'] or candidates['9'] or candidates['place']
    kept = []
    for external_id, name, geometry in chosen:
        bbox = geo.bbox_of(geometry)
        lat, lng = geo.center_of(bbox)
        if geo.area_contains(city.geometry, city.bbox, lat, lng) or geo.contains(geometry, city.latitude or 0, city.longitude or 0):
            kept.append((external_id, name, geometry, bbox, lat, lng))
    return kept


def ensure_neighbourhoods(city):
    """The city's neighbourhoods: Madrid's official ones, or OpenStreetMap's the first time
    someone opens the city. A city without any is searched as a whole."""
    if city.neighbourhoods_fetched_at:
        return city.neighbourhoods.all()
    if city.ine_code == MADRID_INE:
        load_madrid_neighbourhoods()
        return city.neighbourhoods.all()
    ensure_city_located(city)
    if city.osm_relation:
        query = f"""[out:json][timeout:90];
area(id:{3600000000 + city.osm_relation})->.a;
(relation["boundary"="administrative"]["admin_level"~"^(9|10)$"](area.a);
 relation["place"~"^(suburb|neighbourhood|quarter)$"](area.a);
 way["place"~"^(suburb|neighbourhood|quarter)$"](area.a););
out geom;"""
        for external_id, name, geometry, bbox, lat, lng in neighbourhoods_from_overpass(overpass(query), city):
            Neighbourhood.objects.update_or_create(
                city=city, source='osm', external_id=external_id,
                defaults={'name': name, 'search_name': geo.normalize(name), 'bbox': bbox, 'geometry': geometry, 'latitude': lat, 'longitude': lng})
    City.objects.filter(pk=city.pk).update(neighbourhoods_fetched_at=timezone.now())
    city.neighbourhoods_fetched_at = timezone.now()
    return city.neighbourhoods.all()


# --- Placing a business ---------------------------------------------------------------

def city_from_address(address):
    """The municipality in a Nominatim address, matched against the INE register."""
    name = address.get('city') or address.get('town') or address.get('village') or address.get('municipality')
    if not name:
        return None
    matches = list(City.objects.filter(search_name=geo.normalize(name)))
    if len(matches) > 1:
        province = geo.normalize(address.get('province') or address.get('state_district') or address.get('county') or '')
        matches = [c for c in matches if geo.normalize(geo.query_name(c.province)) in province or province in geo.normalize(c.province)] or matches
    if not matches:
        # Bilingual names: 'Alacant/Alicante' is found by either half.
        matches = [c for c in City.objects.filter(search_name__contains=geo.normalize(name)) if geo.normalize(name) in geo.normalize(c.name).split('/')]
    return matches[0] if matches else None


def geocode_address(text):
    """(lat, lng, City or None) for an address, or None. One Nominatim call."""
    results = nominatim(q=text if 'españa' in geo.normalize(text) else f'{text}, España', addressdetails=1, limit=1)
    if not results:
        return None
    best = results[0]
    return float(best['lat']), float(best['lon']), city_from_address(best.get('address', {}))


def place_business(business, save=True):
    """Give a business its city and neighbourhood from its coordinates, geocoding the
    address once if it has none. Returns the fields changed."""
    changed = []
    if (business.latitude is None or business.longitude is None) and business.address and not business.geocoded_at:
        business.geocoded_at = timezone.now()
        changed.append('geocoded_at')
        try:
            found = geocode_address(business.address)
        except GeoError:
            found = None
        if found:
            business.latitude, business.longitude = found[0], found[1]
            changed += ['latitude', 'longitude']
            if business.city_id is None and found[2] is not None:
                business.city = found[2]
                changed.append('city')
    if business.city_id and business.latitude is not None:
        try:
            neighbourhoods = list(ensure_neighbourhoods(business.city))
        except GeoError:
            neighbourhoods = list(business.city.neighbourhoods.all())
        found = geo.find_neighbourhood(neighbourhoods, business.latitude, business.longitude)
        if found is not None and found.pk != business.neighbourhood_id:
            business.neighbourhood = found
            changed.append('neighbourhood')
    if save and changed:
        business.save(update_fields=list(dict.fromkeys(changed)))
    return changed


def place_snapshot(snapshot, neighbourhoods):
    found = geo.find_neighbourhood(neighbourhoods, snapshot.latitude, snapshot.longitude)
    if found is not None and found.pk != snapshot.neighbourhood_id:
        snapshot.neighbourhood = found
        snapshot.save(update_fields=['neighbourhood'])


def resolve_legacy_zones():
    """Each old active zone becomes an active search area for the official neighbourhood
    containing its centre. The old names are not forced onto anything."""
    madrid = City.objects.get(ine_code=MADRID_INE)
    neighbourhoods = list(ensure_neighbourhoods(madrid))
    resolved = []
    for zone in LegacySearchZone.objects.filter(resolved_area__isnull=True, latitude__isnull=False, longitude__isnull=False):
        found = geo.find_neighbourhood(neighbourhoods, zone.latitude, zone.longitude)
        area, _ = SearchArea.objects.get_or_create(city=madrid, neighbourhood=found)
        if zone.active and not area.active:
            area.active = True
            area.save(update_fields=['active'])
        zone.resolved_area = area
        zone.save(update_fields=['resolved_area'])
        resolved.append((zone.name, str(area)))
    return resolved


def assign_areas(geocode=True):
    """City and neighbourhood for every business and search result that can have one."""
    placed = 0
    for business in Business.objects.select_related('city').all():
        if not geocode and business.latitude is None:
            continue
        if place_business(business):
            placed += 1
    for city in City.objects.filter(pk__in=PlaceSnapshot.objects.values('city')):
        neighbourhoods = list(city.neighbourhoods.all())
        for snapshot in PlaceSnapshot.objects.filter(city=city, latitude__isnull=False):
            place_snapshot(snapshot, neighbourhoods)
    return placed


def place_business_by_pk(pk):
    """Queue-friendly: runs in the worker so saving a card never waits on OpenStreetMap."""
    business = Business.objects.select_related('city').filter(pk=pk).first()
    if business is not None:
        place_business(business)


def queue_placing(business):
    from django_q.tasks import async_task
    transaction.on_commit(lambda: async_task('core.geo_sources.place_business_by_pk', business.pk))
