from datetime import timedelta
from django.utils import timezone
from django.db.models import Q
from .models import Business,PlaceSnapshot
from .monitoring import check_websites,aggregate_uptime
from .finance import generate_recurring,upcoming_reminders
from .notifications import retry_emails
from .places import refresh_places
from .inquiries import purge_old_inquiries

def daily_maintenance():
    aggregate_uptime()
    generate_recurring()
    upcoming_reminders()
    # Keep place IDs and workflow metadata; remove expired provider content.
    PlaceSnapshot.objects.filter(fetched_at__lt=timezone.now()-timedelta(days=30)).update(name='',address='',primary_type='',rating=0,reviews=0,has_website=False,website_url='',photos=0,phone='',maps_url='',opportunity_score=0,latitude=None,longitude=None,opening_hours=None,price_level='')
    expire_google_data()
    purge_old_inquiries()

def expire_google_data():
    """Google content on a business also lives 30 days; hand-typed hours stay."""
    cutoff=timezone.now()-timedelta(days=30)
    old=Business.objects.filter(google_fetched_at__lt=cutoff)
    # A location-only lookup has its own timestamp; it must not renew old hours or ratings.
    Business.objects.filter(Q(google_place_id__isnull=False, geocoded_at__lt=cutoff) |
                            Q(google_fetched_at__lt=cutoff, geocoded_at__isnull=True)).update(latitude=None,longitude=None)
    old.filter(hours_source='google').update(opening_hours=None,hours_source='',hours_updated_at=None)
    old.update(google_hours=None,google_rating=None,google_reviews=None,google_price_level='',google_maps_url='',google_fetched_at=None)
