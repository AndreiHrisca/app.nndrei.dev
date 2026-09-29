from datetime import timedelta
from django.utils import timezone
from .models import Business,PlaceSnapshot
from .monitoring import check_websites,aggregate_uptime
from .finance import generate_recurring,upcoming_reminders
from .notifications import retry_emails
from .places import refresh_places

def daily_maintenance():
    aggregate_uptime()
    generate_recurring()
    upcoming_reminders()
    # Keep place IDs and workflow metadata; remove expired provider content.
    PlaceSnapshot.objects.filter(fetched_at__lt=timezone.now()-timedelta(days=30)).update(name='',address='',primary_type='',rating=0,reviews=0,has_website=False,website_url='',photos=0,phone='',maps_url='',opportunity_score=0,latitude=None,longitude=None,opening_hours=None,price_level='')
    expire_google_data()

def expire_google_data():
    """Google content on a business also lives 30 days; hand-typed hours stay."""
    old=Business.objects.filter(google_fetched_at__lt=timezone.now()-timedelta(days=30))
    old.filter(hours_source='google').update(opening_hours=None,hours_source='',hours_updated_at=None)
    old.update(latitude=None,longitude=None,google_hours=None,google_rating=None,google_reviews=None,google_price_level='',google_maps_url='',google_fetched_at=None)
