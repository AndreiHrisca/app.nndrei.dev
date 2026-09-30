"""Geography without a GIS stack: pure functions, tested without the network.

Outlines are stored as GeoJSON (lon/lat) in PostgreSQL JSON columns. A few
hundred neighbourhoods do not justify PostGIS: point-in-polygon, bounding boxes,
the search grid and the UTM conversion for Madrid's official files are simple
enough to live here. Boxes are always [south, west, north, east].
"""
import math
import re
import struct
import unicodedata
import xml.etree.ElementTree as ET
import zipfile
from io import BytesIO

# --- Names --------------------------------------------------------------------------

# INE writes the article after the name: "Palmas de Gran Canaria, Las".
ARTICLE = re.compile(r"^(?P<name>.+), (?P<article>El|La|Los|Las|L'|Lo|Es|Sa|Ses|S'|Els|A|O|As|Os|Les)$")

PROVINCES = {
    '01': 'Araba/Álava', '02': 'Albacete', '03': 'Alicante/Alacant', '04': 'Almería', '05': 'Ávila', '06': 'Badajoz',
    '07': 'Illes Balears', '08': 'Barcelona', '09': 'Burgos', '10': 'Cáceres', '11': 'Cádiz', '12': 'Castellón/Castelló',
    '13': 'Ciudad Real', '14': 'Córdoba', '15': 'A Coruña', '16': 'Cuenca', '17': 'Girona', '18': 'Granada',
    '19': 'Guadalajara', '20': 'Gipuzkoa', '21': 'Huelva', '22': 'Huesca', '23': 'Jaén', '24': 'León', '25': 'Lleida',
    '26': 'La Rioja', '27': 'Lugo', '28': 'Madrid', '29': 'Málaga', '30': 'Murcia', '31': 'Navarra', '32': 'Ourense',
    '33': 'Asturias', '34': 'Palencia', '35': 'Las Palmas', '36': 'Pontevedra', '37': 'Salamanca',
    '38': 'Santa Cruz de Tenerife', '39': 'Cantabria', '40': 'Segovia', '41': 'Sevilla', '42': 'Soria',
    '43': 'Tarragona', '44': 'Teruel', '45': 'Toledo', '46': 'Valencia/València', '47': 'Valladolid', '48': 'Bizkaia',
    '49': 'Zamora', '50': 'Zaragoza', '51': 'Ceuta', '52': 'Melilla',
}
COMMUNITIES = {
    '01': 'Andalucía', '02': 'Aragón', '03': 'Principado de Asturias', '04': 'Illes Balears', '05': 'Canarias',
    '06': 'Cantabria', '07': 'Castilla y León', '08': 'Castilla-La Mancha', '09': 'Cataluña', '10': 'Comunitat Valenciana',
    '11': 'Extremadura', '12': 'Galicia', '13': 'Comunidad de Madrid', '14': 'Región de Murcia',
    '15': 'Comunidad Foral de Navarra', '16': 'País Vasco', '17': 'La Rioja', '18': 'Ceuta', '19': 'Melilla',
}


def normalize(text):
    """'Móstoles ' -> 'mostoles': lower case, no accents, single spaces. For search, not for display."""
    text = unicodedata.normalize('NFKD', text or '')
    text = ''.join(c for c in text if not unicodedata.combining(c))
    return re.sub(r'\s+', ' ', text).strip().lower()


def display_name(ine_name):
    """'Palmas de Gran Canaria, Las' -> 'Las Palmas de Gran Canaria'; "Hospitalet de Llobregat, L'" -> "L'Hospitalet de Llobregat"."""
    parts = []
    for part in ine_name.split('/'):
        match = ARTICLE.match(part.strip())
        if match:
            article = match['article']
            part = article + match['name'] if article.endswith("'") else f"{article} {match['name']}"
        parts.append(part.strip())
    return '/'.join(parts)


def query_name(name):
    """The first of bilingual names ('Alicante/Alacant' -> 'Alicante'), for geocoders."""
    return name.split('/')[0].strip()


# --- Geometry -----------------------------------------------------------------------

def polygons(geometry):
    """GeoJSON Polygon or MultiPolygon as a list of polygons (lists of rings)."""
    if not geometry:
        return []
    if geometry['type'] == 'Polygon':
        return [geometry['coordinates']]
    if geometry['type'] == 'MultiPolygon':
        return geometry['coordinates']
    return []


def in_ring(lon, lat, ring):
    inside = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i][0], ring[i][1]
        xj, yj = ring[j][0], ring[j][1]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            inside = not inside
        j = i
    return inside


def contains(geometry, lat, lng):
    """Point in a GeoJSON (Multi)Polygon, holes excluded."""
    for rings in polygons(geometry):
        if rings and in_ring(lng, lat, rings[0]) and not any(in_ring(lng, lat, hole) for hole in rings[1:]):
            return True
    return False


def in_bbox(bbox, lat, lng):
    south, west, north, east = bbox
    return south <= lat <= north and west <= lng <= east


def bbox_of(geometry):
    points = [p for rings in polygons(geometry) for ring in rings for p in ring]
    lons, lats = [p[0] for p in points], [p[1] for p in points]
    return [min(lats), min(lons), max(lats), max(lons)]


def center_of(bbox):
    return (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2


def area_contains(geometry, bbox, lat, lng):
    """Inside the real outline when there is one; inside the box when that is all we have."""
    if lat is None or lng is None:
        return False
    return contains(geometry, lat, lng) if geometry else bool(bbox) and in_bbox(bbox, lat, lng)


def rect_touches(rect, geometry):
    """Does a grid cell overlap the outline? Corners, centre or any outline vertex inside."""
    south, west, north, east = rect
    probes = [(south, west), (south, east), (north, west), (north, east), ((south + north) / 2, (west + east) / 2)]
    if any(contains(geometry, lat, lng) for lat, lng in probes):
        return True
    return any(in_bbox(rect, p[1], p[0]) for rings in polygons(geometry) for p in rings[0])


def grid(bbox, cell_km, geometry=None):
    """Split a box into cells about `cell_km` wide, keeping only those that touch the outline."""
    south, west, north, east = bbox
    lat_km = 111.32
    lng_km = 111.32 * max(0.01, math.cos(math.radians((south + north) / 2)))
    rows = max(1, math.ceil((north - south) * lat_km / cell_km))
    cols = max(1, math.ceil((east - west) * lng_km / cell_km))
    dlat, dlng = (north - south) / rows, (east - west) / cols
    cells = []
    for i in range(rows):
        for j in range(cols):
            rect = [south + i * dlat, west + j * dlng, south + (i + 1) * dlat, west + (j + 1) * dlng]
            if geometry is None or rect_touches(rect, geometry):
                cells.append(rect)
    return cells


def distance_m(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 6371000 * 2 * math.atan2(math.sqrt(a), math.sqrt(max(0, 1 - a)))


def find_neighbourhood(neighbourhoods, lat, lng):
    """Point inside a polygon wins; with only boxes, the nearest one whose box contains it."""
    if lat is None or lng is None:
        return None
    boxed = []
    for item in neighbourhoods:
        if not in_bbox(item.bbox, lat, lng):
            continue
        if item.geometry:
            if contains(item.geometry, lat, lng):
                return item
        else:
            boxed.append(item)
    return min(boxed, key=lambda n: distance_m(lat, lng, n.latitude, n.longitude)) if boxed else None


def rounded(geometry, digits=6):
    def walk(value):
        return [walk(v) for v in value] if isinstance(value, list) and value and isinstance(value[0], list) else [round(v, digits) for v in value]
    return {'type': geometry['type'], 'coordinates': walk(geometry['coordinates'])}


# --- Madrid's official files: ETRS89 / UTM zone 30N ------------------------------------

def utm_to_latlng(x, y, zone=30):
    """UTM (northern hemisphere, GRS80) to latitude/longitude. ETRS89 and WGS84 differ by
    well under a metre in Spain, which is plenty for telling neighbourhoods apart."""
    a, f, k0 = 6378137.0, 1 / 298.257222101, 0.9996
    e2 = f * (2 - f)
    ep2 = e2 / (1 - e2)
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    x -= 500000.0
    mu = y / k0 / (a * (1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256))
    phi = (mu + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu) + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu)
           + (151 * e1 ** 3 / 96) * math.sin(6 * mu) + (1097 * e1 ** 4 / 512) * math.sin(8 * mu))
    sin, cos, tan = math.sin(phi), math.cos(phi), math.tan(phi)
    n = a / math.sqrt(1 - e2 * sin ** 2)
    t, c = tan ** 2, ep2 * cos ** 2
    r = a * (1 - e2) / (1 - e2 * sin ** 2) ** 1.5
    d = x / (n * k0)
    lat = phi - (n * tan / r) * (d ** 2 / 2 - (5 + 3 * t + 10 * c - 4 * c ** 2 - 9 * ep2) * d ** 4 / 24
                                 + (61 + 90 * t + 298 * c + 45 * t ** 2 - 252 * ep2 - 3 * c ** 2) * d ** 6 / 720)
    lng = (d - (1 + 2 * t + c) * d ** 3 / 6 + (5 - 2 * c + 28 * t - 3 * c ** 2 + 8 * ep2 + 24 * t ** 2) * d ** 5 / 120) / cos
    return math.degrees(lat), (zone - 1) * 6 - 180 + 3 + math.degrees(lng)


def signed_area(ring):
    return sum(ring[i][0] * ring[i + 1][1] - ring[i + 1][0] * ring[i][1] for i in range(len(ring) - 1)) / 2


def read_shapefile(archive):
    """Polygons and attributes from a zipped shapefile: [(attributes, GeoJSON geometry in UTM)].

    Shapefile outer rings go clockwise and holes counter-clockwise, which is how
    rings are grouped into polygons here.
    """
    z = zipfile.ZipFile(BytesIO(archive))
    names = {n.lower().rsplit('.', 1)[-1]: n for n in z.namelist() if '.' in n}
    shp, dbf = z.read(names['shp']), z.read(names['dbf'])
    encoding = z.read(names['cpg']).decode().strip() if 'cpg' in names else 'latin-1'
    count, header, length = struct.unpack('<xxxxIHH', dbf[:12])
    fields, pos = [], 32
    while dbf[pos] != 0x0D:
        fields.append((dbf[pos:pos + 11].split(b'\0')[0].decode(), dbf[pos + 16]))
        pos += 32
    records = []
    for k in range(count):
        row = dbf[header + k * length + 1: header + (k + 1) * length]
        values, offset = {}, 0
        for name, size in fields:
            values[name] = row[offset:offset + size].decode(encoding, 'replace').strip()
            offset += size
        records.append(values)
    shapes, pos = [], 100
    while pos < len(shp):
        _, words = struct.unpack('>II', shp[pos:pos + 8])
        content = shp[pos + 8: pos + 8 + words * 2]
        pos += 8 + words * 2
        if struct.unpack('<i', content[:4])[0] != 5:
            shapes.append(None)
            continue
        parts_count, points_count = struct.unpack('<ii', content[36:44])
        parts = list(struct.unpack(f'<{parts_count}i', content[44:44 + 4 * parts_count])) + [points_count]
        start = 44 + 4 * parts_count
        points = [struct.unpack('<dd', content[start + 16 * i: start + 16 * (i + 1)]) for i in range(points_count)]
        polys = []
        for i in range(parts_count):
            ring = [list(p) for p in points[parts[i]:parts[i + 1]]]
            if signed_area(ring) <= 0 or not polys:  # Clockwise: a new outer ring.
                polys.append([ring])
            else:
                polys[-1].append(ring)
        shapes.append({'type': 'MultiPolygon', 'coordinates': polys})
    return list(zip(records, shapes))


def utm_geometry_to_wgs84(geometry, zone=30):
    polys = [[[list(reversed(utm_to_latlng(x, y, zone))) for x, y in ring] for ring in rings] for rings in geometry['coordinates']]
    # GeoJSON wants counter-clockwise outer rings; reversing keeps point-in-polygon unaffected but tidy.
    polys = [[ring[::-1] for ring in rings] for rings in polys]
    return rounded({'type': 'MultiPolygon', 'coordinates': polys})


# --- INE municipality register (xlsx) ------------------------------------------------

def read_ine_register(archive):
    """Rows of the INE 'Relación de municipios' workbook: dicts with ine_code, name, province, community."""
    z = zipfile.ZipFile(BytesIO(archive))
    ns = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    shared = []
    if 'xl/sharedStrings.xml' in z.namelist():
        for item in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('m:si', ns):
            shared.append(''.join(t.text or '' for t in item.iter(f"{{{ns['m']}}}t")))
    sheet = ET.fromstring(z.read('xl/worksheets/sheet1.xml'))
    rows = []
    for row in sheet.iter(f"{{{ns['m']}}}row"):
        cells = {}
        for cell in row.findall('m:c', ns):
            column = re.match(r'[A-Z]+', cell.get('r')).group()
            kind = cell.get('t')
            if kind == 'inlineStr':
                value = ''.join(t.text or '' for t in cell.iter(f"{{{ns['m']}}}t"))
            else:
                v = cell.find('m:v', ns)
                value = None if v is None else (shared[int(v.text)] if kind == 's' else v.text)
            cells[column] = value
        rows.append(cells)
    header = next(i for i, r in enumerate(rows) if (r.get('A') or '').strip().upper() == 'CODAUTO')
    result = []
    for r in rows[header + 1:]:
        if not r.get('B') or not r.get('C') or not r.get('E'):
            continue
        cpro = str(int(float(r['B']))).zfill(2)
        cmun = str(int(float(r['C']))).zfill(3)
        auto = str(int(float(r['A']))).zfill(2)
        name = display_name(r['E'].strip())
        result.append({'ine_code': cpro + cmun, 'name': name, 'search_name': normalize(name),
                       'province': PROVINCES.get(cpro, cpro), 'community': COMMUNITIES.get(auto, auto)})
    return result
