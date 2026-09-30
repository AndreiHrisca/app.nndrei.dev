import math
from urllib.parse import urlparse
import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from datetime import timedelta
from . import geo
from .models import Business,SearchArea,SearchCategory,PlaceSnapshot,PlacesUsage,SearchRun,SearchSlice

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

distance_m=geo.distance_m

def search_body(rect,category,token=None):
    """Text Search (New) limited to one grid cell: locationRestriction only takes rectangles."""
    south,west,north,east=rect
    body={'textQuery':category.query or category.name,'languageCode':'es','regionCode':'ES','pageSize':20,'locationRestriction':{'rectangle':{'low':{'latitude':south,'longitude':west},'high':{'latitude':north,'longitude':east}}}}
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

def store_place(place,area,category,shape,neighbourhoods):
    """Keep a result only if it is inside the area's real outline, not just the searched cell."""
    if place.get('businessStatus')!='OPERATIONAL':
        PlaceSnapshot.objects.filter(place_id=place.get('id')).update(business_status=place.get('businessStatus','UNKNOWN'))
        return False
    loc=place.get('location',{})
    if 'latitude' not in loc or 'longitude' not in loc:
        return False
    geometry,bbox=shape
    if not geo.area_contains(geometry,bbox,loc['latitude'],loc['longitude']):
        return False
    neighbourhood=geo.find_neighbourhood(neighbourhoods,loc['latitude'],loc['longitude'])
    hours=place.get('regularOpeningHours',{}).get('periods') or None
    website=place.get('websiteUri',''); reviews=place.get('userRatingCount',0); rating=place.get('rating',0); photos=len(place.get('photos',[]))
    PlaceSnapshot.objects.update_or_create(place_id=place['id'],defaults={'name':place.get('displayName',{}).get('text','Sin nombre'),'address':place.get('formattedAddress',''),'primary_type':place.get('primaryType',''),'rating':rating,'reviews':reviews,'has_website':bool(website),'website_url':website,'photos':photos,'phone':place.get('nationalPhoneNumber',''),'maps_url':place.get('googleMapsUri',''),'business_status':'OPERATIONAL','category':category,'fetched_at':timezone.now(),'opportunity_score':opportunity_score(website,reviews,rating,photos),'city':area.city,'neighbourhood':neighbourhood,'latitude':loc['latitude'],'longitude':loc['longitude'],'opening_hours':hours,'price_level':place.get('priceLevel','')})
    # A business already in the pipeline gets its hours and location refreshed
    # by the same call: no extra request is spent on it.
    for business in Business.objects.filter(google_place_id=place['id']):
        apply_google(business,place)
    return True

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
    # New coordinates may mean another neighbourhood.
    from .geo_sources import place_business
    place_business(business)

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
    business.city_id,business.neighbourhood_id=snapshot.city_id,snapshot.neighbourhood_id

def fresh_since():
    return timezone.now()-timedelta(days=settings.PLACES_REFRESH_MIN_DAYS)

def area_shape(area,locate=True):
    """(outline, box) searched for an area: the neighbourhood's, or the whole municipality's.
    With locate=False nothing is asked to OpenStreetMap (page renders)."""
    if area.neighbourhood_id:
        return area.neighbourhood.geometry,area.neighbourhood.bbox
    city=area.city
    if locate:
        from .geo_sources import ensure_city_located
        city=ensure_city_located(city)
    return city.geometry,city.bbox

def area_cells(area,locate=True):
    geometry,bbox=area_shape(area,locate)
    return geo.grid(bbox,settings.PLACES_CELL_KM,geometry) if bbox else []

def estimate(area,categories,locate=True):
    """How many calls searching `area` for `categories` may cost: one to three pages per
    cell and category, never more than the per-search budget."""
    cells=len(area_cells(area,locate)) or (0 if locate else 1)
    wanted=cells*len(categories)
    budget=settings.PLACES_SEARCH_BUDGET
    return {'cells':cells,'low':min(wanted,budget),'high':min(wanted*3,budget),'budget':budget,
            'capped':wanted*3>budget,'confirm':min(wanted*3,budget)>settings.PLACES_CONFIRM_ABOVE}

def pending_categories(area,categories):
    fresh=set(SearchSlice.objects.filter(area=area,fetched_at__gte=fresh_since()).values_list('category_id',flat=True))
    return [c for c in categories if c.pk not in fresh]

def pending_slices():
    """Active areas × active categories not searched in the last PLACES_REFRESH_MIN_DAYS days."""
    categories=list(SearchCategory.objects.filter(active=True))
    areas=SearchArea.objects.filter(active=True).select_related('city','neighbourhood')
    return [(area,category) for area in areas for category in pending_categories(area,categories)]

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

def search_area(area,categories,run):
    """Search one area cell by cell for each category, within the per-search budget."""
    shape=area_shape(area)
    cells=area_cells(area)
    neighbourhoods=list(area.city.neighbourhoods.all())
    budget=settings.PLACES_SEARCH_BUDGET
    spent=kept=0
    for category in categories:
        complete=True
        progress,_=SearchSlice.objects.get_or_create(area=area,category=category)
        start=progress.next_cell if progress.next_cell<len(cells) else 0
        for index in range(start,len(cells)):
            rect=cells[index]
            token=None
            for page in range(3):
                if spent>=budget:
                    complete=False
                    break
                if not reserve_call():
                    raise ValueError('Límite mensual de Google Places alcanzado.')
                spent+=1
                response=requests.post('https://places.googleapis.com/v1/places:searchText',json=search_body(rect,category,token),headers={'X-Goog-Api-Key':settings.GOOGLE_PLACES_API_KEY,'X-Goog-FieldMask':FIELD_MASK},timeout=20)
                response.raise_for_status()
                data=response.json()
                kept+=sum(1 for place in data.get('places',[]) if store_place(place,area,category,shape,neighbourhoods))
                token=data.get('nextPageToken')
                if not token: break
            if not complete:
                # Carry on from this cell next time (it may have been left half done).
                progress.next_cell=index; progress.save(update_fields=['next_cell'])
                break
        if complete:
            progress.fetched_at,progress.next_cell=timezone.now(),0
            progress.save(update_fields=['fetched_at','next_cell'])
        run.completed+=1; run.save()
        if not complete:
            return spent,kept,False
    return spent,kept,True

def refresh_places(run_id=None,area_id=None,category_id=None):
    """"Actualizar" (active areas) or a search of one area from the filters."""
    run=SearchRun.objects.get(pk=run_id) if run_id else SearchRun.objects.create()
    run.status='running'; run.save()
    try:
        if not settings.GOOGLE_PLACES_API_KEY:
            raise ValueError('Configura GOOGLE_PLACES_API_KEY antes de actualizar.')
        if area_id:
            area=SearchArea.objects.select_related('city','neighbourhood').get(pk=area_id)
            categories=SearchCategory.objects.filter(active=True)
            if category_id: categories=categories.filter(pk=category_id)
            plan={area:pending_categories(area,list(categories))}
        else:
            plan={}
            for area,category in pending_slices(): plan.setdefault(area,[]).append(category)
        plan={area:cats for area,cats in plan.items() if cats}
        run.total=sum(len(cats) for cats in plan.values()); run.save()
        spent=kept=0; partial=[]
        for area,categories in plan.items():
            used,found,complete=search_area(area,categories,run)
            spent+=used; kept+=found
            if not complete: partial.append(str(area))
        run.status='done'
        if not plan:
            run.message='Todo estaba al día: no se ha gastado ninguna llamada.'
        else:
            run.message=f'Búsqueda completada: {spent} llamadas, {kept} negocios dentro de la zona.'
            if partial: run.message+=' Tope de llamadas alcanzado en '+', '.join(partial)+': lo que falta se buscará en la próxima actualización.'
    except (ValueError,requests.RequestException) as exc:
        run.status='failed'; run.message=str(exc)[:300] if isinstance(exc,ValueError) else 'Google Places no ha respondido correctamente. Revisa la configuración y vuelve a intentar.'
    except Exception as exc:
        from .geo_sources import GeoError
        if not isinstance(exc,GeoError): raise
        run.status='failed'; run.message=str(exc)[:300]
    finally:
        run.finished_at=timezone.now(); run.save()
    return run.pk
