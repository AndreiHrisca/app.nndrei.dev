import math
from urllib.parse import urlparse
import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from datetime import timedelta
from .models import Business,SearchZone,SearchCategory,PlaceSnapshot,PlacesUsage,SearchRun,SearchSlice

FIELD_MASK='places.id,places.displayName,places.formattedAddress,places.location,places.primaryType,places.rating,places.userRatingCount,places.websiteUri,places.nationalPhoneNumber,places.photos,places.googleMapsUri,places.businessStatus,places.regularOpeningHours,places.utcOffsetMinutes,places.priceLevel,nextPageToken'
# One business, from its card. Only what the card and the estimate use.
DETAILS_FIELD_MASK='id,location,regularOpeningHours,utcOffsetMinutes,rating,userRatingCount,priceLevel,googleMapsUri'
SOCIAL_DOMAINS={'facebook.com','instagram.com','tiktok.com','linktr.ee','tripadvisor.com','tripadvisor.es','thefork.es','google.com','wa.me'}

def opportunity_score(website,reviews,rating,photos):
    host=(urlparse(website).hostname or '').lower()
    social=any(host==d or host.endswith('.'+d) for d in SOCIAL_DOMAINS)
    presence=50 if not website else 35 if social else 0
    presence=min(50,presence+(10 if photos<5 else 0))
    traction=min(35,35*math.log10(1+max(0,reviews))/math.log10(301))
    quality=15 if rating>=4.5 else 10 if rating>=4 else 5 if rating>=3.5 else 0
    return min(100,round(presence+traction+quality))

def distance_m(lat1,lon1,lat2,lon2):
    p1,p2=math.radians(lat1),math.radians(lat2)
    a=math.sin((p2-p1)/2)**2+math.cos(p1)*math.cos(p2)*math.sin(math.radians(lon2-lon1)/2)**2
    return 6371000*2*math.atan2(math.sqrt(a),math.sqrt(max(0,1-a)))

def search_body(zone,category,token=None):
    dy=zone.radius/111320
    dx=dy/max(.01,math.cos(math.radians(zone.latitude)))
    body={'textQuery':category.query or category.name,'languageCode':'es','regionCode':'ES','pageSize':20,'locationRestriction':{'rectangle':{'low':{'latitude':zone.latitude-dy,'longitude':zone.longitude-dx},'high':{'latitude':zone.latitude+dy,'longitude':zone.longitude+dx}}}}
    if category.included_type:
        body['includedType']=category.included_type
        body['strictTypeFiltering']=True
    if token:
        body['pageToken']=token
    return body

@transaction.atomic
def reserve_call():
    month=timezone.localdate().replace(day=1)
    usage,_=PlacesUsage.objects.get_or_create(month=month)
    usage=PlacesUsage.objects.select_for_update().get(pk=usage.pk)
    if usage.requests>=settings.PLACES_MONTHLY_LIMIT:
        return False
    usage.requests+=1; usage.save()
    return True

def store_place(place,zone,category):
    if place.get('businessStatus')!='OPERATIONAL':
        PlaceSnapshot.objects.filter(place_id=place.get('id')).update(business_status=place.get('businessStatus','UNKNOWN'))
        return
    loc=place.get('location',{})
    if 'latitude' not in loc or 'longitude' not in loc:
        return
    distance=distance_m(zone.latitude,zone.longitude,loc['latitude'],loc['longitude'])
    if distance>zone.radius:
        return
    hours=place.get('regularOpeningHours',{}).get('periods') or None
    website=place.get('websiteUri',''); reviews=place.get('userRatingCount',0); rating=place.get('rating',0); photos=len(place.get('photos',[]))
    PlaceSnapshot.objects.update_or_create(place_id=place['id'],defaults={'name':place.get('displayName',{}).get('text','Sin nombre'),'address':place.get('formattedAddress',''),'primary_type':place.get('primaryType',''),'rating':rating,'reviews':reviews,'has_website':bool(website),'website_url':website,'photos':photos,'phone':place.get('nationalPhoneNumber',''),'maps_url':place.get('googleMapsUri',''),'business_status':'OPERATIONAL','zone':zone,'category':category,'fetched_at':timezone.now(),'opportunity_score':opportunity_score(website,reviews,rating,photos),'distance_m':round(distance),'latitude':loc['latitude'],'longitude':loc['longitude'],'opening_hours':hours,'price_level':place.get('priceLevel','')})
    # A business already in the pipeline gets its hours and location refreshed
    # by the same call: no extra request is spent on it.
    for business in Business.objects.filter(google_place_id=place['id']):
        apply_google(business,place)

def apply_google(business,place,now=None):
    """Copy a Places response onto a business. Hand-typed hours are never overwritten."""
    now=now or timezone.now()
    loc=place.get('location',{})
    if 'latitude' in loc and 'longitude' in loc:
        business.latitude,business.longitude=loc['latitude'],loc['longitude']
    if 'rating' in place: business.google_rating=place['rating']
    if 'userRatingCount' in place: business.google_reviews=place['userRatingCount']
    business.google_price_level=place.get('priceLevel','')
    if place.get('googleMapsUri'): business.google_maps_url=place['googleMapsUri']
    business.google_hours=place.get('regularOpeningHours',{}).get('periods') or None
    if business.hours_source!='manual':
        business.opening_hours=business.google_hours
        business.hours_source='google' if business.google_hours else ''
        business.hours_updated_at=now
    business.google_fetched_at=now
    business.save()

def apply_snapshot(business,snapshot):
    """Carry what the search already fetched onto a business being captured."""
    business.latitude,business.longitude=snapshot.latitude,snapshot.longitude
    business.google_rating,business.google_reviews=snapshot.rating,snapshot.reviews
    business.google_price_level=snapshot.price_level
    business.google_maps_url=snapshot.maps_url
    business.google_hours=snapshot.opening_hours
    if business.hours_source!='manual':
        business.opening_hours=snapshot.opening_hours
        business.hours_source='google' if snapshot.opening_hours else ''
        business.hours_updated_at=snapshot.fetched_at
    business.google_fetched_at=snapshot.fetched_at

def fresh_since():
    return timezone.now()-timedelta(days=settings.PLACES_REFRESH_MIN_DAYS)

def pending_slices():
    """Zone × category pairs "Actualizar" would ask Google for (each one is 1 to 3 calls)."""
    zones=SearchZone.objects.filter(active=True,latitude__isnull=False,longitude__isnull=False)
    categories=SearchCategory.objects.filter(active=True)
    fresh=set(SearchSlice.objects.filter(fetched_at__gte=fresh_since()).values_list('zone_id','category_id'))
    return [(zone,category) for zone in zones for category in categories if (zone.pk,category.pk) not in fresh]

class PlacesError(Exception):
    pass

def refresh_business(business):
    """Place Details (New) for one business: exactly one call from the quota."""
    if not settings.GOOGLE_PLACES_API_KEY:
        raise PlacesError('Configura GOOGLE_PLACES_API_KEY antes de actualizar.')
    if not business.google_place_id:
        raise PlacesError('Este negocio no está enlazado a una ficha de Google.')
    if not reserve_call():
        raise PlacesError('Límite mensual de Google Places alcanzado.')
    try:
        response=requests.get('https://places.googleapis.com/v1/places/'+business.google_place_id,headers={'X-Goog-Api-Key':settings.GOOGLE_PLACES_API_KEY,'X-Goog-FieldMask':DETAILS_FIELD_MASK},params={'languageCode':'es'},timeout=10)
        response.raise_for_status()
    except requests.RequestException:
        raise PlacesError('Google Places no ha respondido correctamente. Vuelve a intentarlo más tarde.')
    apply_google(business,response.json())
    return business

def refresh_places(run_id=None):
    run=SearchRun.objects.get(pk=run_id) if run_id else SearchRun.objects.create()
    run.status='running'; run.save()
    try:
        if not settings.GOOGLE_PLACES_API_KEY:
            raise ValueError('Configura GOOGLE_PLACES_API_KEY antes de actualizar.')
        zones=list(SearchZone.objects.filter(active=True,latitude__isnull=False,longitude__isnull=False))
        pending=pending_slices()
        run.total=len(pending); run.save()
        for zone,category in pending:
            token=None
            for page in range(3):
                if not reserve_call():
                    raise ValueError('Límite mensual de Google Places alcanzado.')
                response=requests.post('https://places.googleapis.com/v1/places:searchText',json=search_body(zone,category,token),headers={'X-Goog-Api-Key':settings.GOOGLE_PLACES_API_KEY,'X-Goog-FieldMask':FIELD_MASK},timeout=20)
                response.raise_for_status()
                data=response.json()
                for place in data.get('places',[]): store_place(place,zone,category)
                token=data.get('nextPageToken')
                if not token: break
            SearchSlice.objects.update_or_create(zone=zone,category=category,defaults={'fetched_at':timezone.now()})
            run.completed+=1; run.save()
        run.status='done'
        run.message=('Actualización completada.' if pending else 'Todo estaba al día: no se ha gastado ninguna llamada.') if zones else 'Rellena las coordenadas de las zonas en el admin.'
    except (ValueError,requests.RequestException) as exc:
        run.status='failed'; run.message=str(exc)[:300] if isinstance(exc,ValueError) else 'Google Places no ha respondido correctamente. Revisa la configuración y vuelve a intentar.'
    finally:
        run.finished_at=timezone.now(); run.save()
    return run.pk
