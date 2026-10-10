"""Recover saved Places coordinates before spending a call; never used by the map."""
import math
from urllib.parse import quote
import requests
from django.conf import settings
from django.utils import timezone
from .models import Business, PlaceSnapshot
from . import geo_sources
from .geo_sources import GeoError
from .places import reserve_call


class MonthlyLimit(GeoError):
    pass


def geocode_business(business, force=False):
    if not force and business.latitude is not None and business.longitude is not None:
        return 'Ya tenía ubicación'
    address, place_id = business.address, business.google_place_id
    point = None
    city_id, neighbourhood_id = business.city_id, None
    snapshot = PlaceSnapshot.objects.filter(place_id=place_id).first() if place_id else None
    if not force and snapshot and snapshot.latitude is not None and snapshot.longitude is not None:
        point = snapshot.latitude, snapshot.longitude
        city_id = snapshot.city_id or city_id
        neighbourhood_id = snapshot.neighbourhood_id
        source = 'Ubicación guardada en Buscar clientes'
    elif place_id:
        if not settings.GOOGLE_PLACES_API_KEY:
            raise GeoError('Falta la clave de Google Places')
        if not reserve_call():
            raise MonthlyLimit('Límite mensual de Google Places alcanzado; se ha detenido el proceso')
        try:
            response = requests.get('https://places.googleapis.com/v1/places/' + quote(place_id, safe=''),
                                    headers={'X-Goog-Api-Key': settings.GOOGLE_PLACES_API_KEY,
                                             'X-Goog-FieldMask': 'location'}, timeout=10)
            response.raise_for_status()
            location = response.json().get('location', {})
            if 'latitude' in location and 'longitude' in location:
                point = location['latitude'], location['longitude']
        except (requests.RequestException, ValueError) as exc:
            raise GeoError('Google Places no ha respondido correctamente') from exc
        source = 'Google Places'
    elif address.strip():
        found = geo_sources.geocode_address(address)
        if found:
            point = found[:2]
            city_id = found[2].pk if found[2] else city_id
        source = 'Dirección'
    else:
        return 'Sin dirección ni ficha de Google'
    if point is None:
        return 'No se ha encontrado la ubicación'
    try:
        lat, lng = map(float, point)
        valid = math.isfinite(lat) and math.isfinite(lng) and -90 <= lat <= 90 and -180 <= lng <= 180
    except (ValueError, TypeError):
        valid = False
    if not valid:
        raise GeoError('El servicio ha devuelto unas coordenadas incorrectas')
    # A queued old lookup must not overwrite a newly edited address.
    fields = {'latitude': lat, 'longitude': lng, 'geocoded_at': timezone.now(),
              'city_id': city_id, 'neighbourhood_id': neighbourhood_id}
    updated = Business.objects.filter(pk=business.pk, address=address, google_place_id=place_id).update(**fields)
    if not updated:
        return 'La dirección cambió durante la búsqueda; se volverá a buscar'
    business.refresh_from_db()
    # Assign known neighbourhood shapes without asking providers for new geography.
    if business.city_id and not business.neighbourhood_id:
        from .geo import find_neighbourhood
        neighbourhood = find_neighbourhood(list(business.city.neighbourhoods.all()), lat, lng)
        if neighbourhood:
            Business.objects.filter(pk=business.pk, address=address, google_place_id=place_id).update(neighbourhood=neighbourhood)
    return source
