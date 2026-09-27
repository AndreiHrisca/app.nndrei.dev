import calendar
from datetime import date,timedelta
from decimal import Decimal
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from .models import ClientCharge,Transaction,RecurringExpense,NotificationReceipt
from .notifications import notify

def advance_date(value,periodicity):
    month=value.month+(12 if periodicity=='annual' else 1)
    year=value.year+(month-1)//12
    month=(month-1)%12+1
    # Preserve end-of-month schedules through February and short months.
    day=calendar.monthrange(year,month)[1] if value.day==calendar.monthrange(value.year,value.month)[1] else min(value.day,calendar.monthrange(year,month)[1])
    return date(year,month,day)

def generate_recurring(today=None):
    today=today or timezone.localdate()
    for model,prefix in [(ClientCharge,'charge'),(RecurringExpense,'expense')]:
        for pk in model.objects.filter(next_date__lte=today).values_list('pk',flat=True):
            with transaction.atomic():
                item=model.objects.select_for_update().get(pk=pk)
                if isinstance(item,ClientCharge) and (item.amount is None or item.included): continue
                while item.next_date and item.next_date<=today:
                    key=f'{prefix}:{item.pk}:{item.next_date}'
                    amount=item.amount if prefix=='charge' else -item.amount
                    Transaction.objects.get_or_create(source_key=key,defaults={'date':item.next_date,'concept':item.concept,'business_id':getattr(item,'business_id',None),'category':getattr(item,'kind','other'),'amount':amount,'notes':'Movimiento recurrente generado; no acredita cobro bancario.'})
                    if isinstance(item,ClientCharge): item.status='pending'
                    item.next_date=advance_date(item.next_date,item.periodicity) if item.periodicity!='once' else None
                item.save()

def upcoming_reminders(today=None):
    today=today or timezone.localdate()
    until=today+timedelta(days=7)
    for model,prefix in [(ClientCharge,'charge'),(RecurringExpense,'expense')]:
        for item in model.objects.filter(next_date__range=(today,until)):
            key=f'reminder:{prefix}:{item.pk}:{item.next_date}'
            with transaction.atomic():
                _,created=NotificationReceipt.objects.get_or_create(key=key)
                if created:
                    notify('Cobro o renovación próximos',[settings.ADMIN_EMAIL],f'{item.concept}: {item.next_date:%d/%m/%Y}',settings.SITE_URL+'/finanzas/')
