from datetime import timedelta
from django.utils import timezone
from .models import PlaceSnapshot
from .monitoring import check_websites,aggregate_uptime
from .finance import generate_recurring,upcoming_reminders
from .notifications import retry_emails
from .places import refresh_places

def daily_maintenance():
    aggregate_uptime()
    generate_recurring()
    upcoming_reminders()
    # Keep place IDs and workflow metadata; remove expired provider content.
    PlaceSnapshot.objects.filter(fetched_at__lt=timezone.now()-timedelta(days=30)).update(name='',address='',primary_type='',rating=0,reviews=0,has_website=False,website_url='',photos=0,phone='',maps_url='',opportunity_score=0)
