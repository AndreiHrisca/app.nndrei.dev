from django.conf import settings
from django.core.management.base import BaseCommand
from django_q.models import Schedule
class Command(BaseCommand):
    help='Install or update the periodic jobs idempotently.'
    def handle(self,*args,**options):
        jobs=[('uptime','core.tasks.check_websites',Schedule.MINUTES,5),('daily','core.tasks.daily_maintenance',Schedule.DAILY,None),('monthly','core.tasks.generate_recurring',Schedule.MONTHLY,None),('email-retry','core.tasks.retry_emails',Schedule.MINUTES,5)]
        if settings.PLACES_WEEKLY_REFRESH:
            jobs.append(('places-weekly','core.tasks.refresh_places',Schedule.WEEKLY,None))
        else:
            Schedule.objects.filter(name='nndrei-places-weekly').delete()
        for name,func,kind,minutes in jobs:
            Schedule.objects.update_or_create(name='nndrei-'+name,defaults={'func':func,'schedule_type':kind,'minutes':minutes,'repeats':-1})
        self.stdout.write(self.style.SUCCESS('Tareas programadas configuradas.'))
