import math
from urllib.parse import urlparse
import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from .models import SearchZone,SearchCategory,PlaceSnapshot,PlacesUsage,SearchRun

FIELD_MASK='places.id,places.displayName,places.formattedAddress,places.location,places.primaryType,places.rating,places.userRatingCount,places.websiteUri,places.nationalPhoneNumber,places.photos,places.googleMapsUri,places.businessStatus,nextPageToken'
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
    website=place.get('websiteUri',''); reviews=place.get('userRatingCount',0); rating=place.get('rating',0); photos=len(place.get('photos',[]))
    PlaceSnapshot.objects.update_or_create(place_id=place['id'],defaults={'name':place.get('displayName',{}).get('text','Sin nombre'),'address':place.get('formattedAddress',''),'primary_type':place.get('primaryType',''),'rating':rating,'reviews':reviews,'has_website':bool(website),'website_url':website,'photos':photos,'phone':place.get('nationalPhoneNumber',''),'maps_url':place.get('googleMapsUri',''),'business_status':'OPERATIONAL','zone':zone,'category':category,'fetched_at':timezone.now(),'opportunity_score':opportunity_score(website,reviews,rating,photos),'distance_m':round(distance)})

def refresh_places(run_id=None):
    run=SearchRun.objects.get(pk=run_id) if run_id else SearchRun.objects.create()
    run.status='running'; run.save()
    try:
        if not settings.GOOGLE_PLACES_API_KEY:
            raise ValueError('Configura GOOGLE_PLACES_API_KEY antes de actualizar.')
        zones=list(SearchZone.objects.filter(active=True,latitude__isnull=False,longitude__isnull=False))
        categories=list(SearchCategory.objects.filter(active=True))
        run.total=len(zones)*len(categories); run.save()
        for zone in zones:
            for category in categories:
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
                run.completed+=1; run.save()
        run.status='done'; run.message='Actualización completada.' if zones else 'Rellena las coordenadas de las zonas en el admin.'
    except (ValueError,requests.RequestException) as exc:
        run.status='failed'; run.message=str(exc)[:300] if isinstance(exc,ValueError) else 'Google Places no ha respondido correctamente. Revisa la configuración y vuelve a intentar.'
    finally:
        run.finished_at=timezone.now(); run.save()
    return run.pk
