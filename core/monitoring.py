import ipaddress
import socket
import time
from datetime import timedelta
from decimal import Decimal
from urllib.parse import urlparse,urljoin
import urllib3
from django.conf import settings
from django.db import transaction
from django.db.models import Avg,Count,Q
from django.db.models.functions import TruncDate
from django.utils import timezone
from .models import Website,UptimeCheck,UptimeDaily
from .notifications import notify

def fetch_public_url(url):
    deadline=time.monotonic()+10
    for _ in range(6):
        parsed=urlparse(url)
        if parsed.scheme not in ['http','https'] or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError('URL de monitorización no permitida.')
        port=parsed.port or (443 if parsed.scheme=='https' else 80)
        if port not in [80,443]: raise ValueError('Solo se permiten puertos web públicos.')
        addresses={record[4][0] for record in socket.getaddrinfo(parsed.hostname,port,type=socket.SOCK_STREAM)}
        if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses):
            raise ValueError('No se permiten direcciones privadas o reservadas.')
        remaining=deadline-time.monotonic()
        if remaining<=0: raise TimeoutError('Tiempo de espera agotado.')
        # Connect to the validated IP, preserving TLS hostname verification and Host.
        ip=sorted(addresses)[0]
        kwargs={'host':ip,'port':port,'timeout':urllib3.Timeout(total=remaining),'retries':False}
        if parsed.scheme=='https':
            pool=urllib3.HTTPSConnectionPool(**kwargs,server_hostname=parsed.hostname,assert_hostname=parsed.hostname,cert_reqs='CERT_REQUIRED')
        else:
            pool=urllib3.HTTPConnectionPool(**kwargs)
        try:
            response=pool.urlopen('GET',(parsed.path or '/')+('?' + parsed.query if parsed.query else ''),headers={'Host':parsed.netloc,'User-Agent':'nndrei-uptime/1.0'},redirect=False,preload_content=False)
            status=response.status
            location=response.headers.get('Location')
            response.close()
        finally:
            pool.close()
        if status in [301,302,303,307,308] and location:
            url=urljoin(url,location)
            continue
        return status
    raise ValueError('Demasiadas redirecciones.')

def check_websites():
    for pk in Website.objects.filter(monitoring_enabled=True).values_list('pk',flat=True):
        with transaction.atomic():
            website=Website.objects.select_for_update(skip_locked=True).filter(pk=pk,monitoring_enabled=True).first()
            if not website: continue
            previous=UptimeCheck.objects.filter(website=website).order_by('-checked_at').first()
            if previous and previous.checked_at>timezone.now()-timedelta(minutes=4): continue
            started=time.monotonic(); status=None; error=''
            try:
                status=fetch_public_url(website.url)
                ok=status<400
            except (ValueError,OSError,TimeoutError,urllib3.exceptions.HTTPError) as exc:
                ok=False; error=str(exc)[:500]
            UptimeCheck.objects.create(website=website,http_status=status,latency_ms=round((time.monotonic()-started)*1000),ok=ok,error=error)
            if not ok and previous and not previous.ok and not website.incident_open:
                website.incident_open=True; website.save(update_fields=['incident_open'])
                notify('Web caída: '+website.business.name,[settings.ADMIN_EMAIL],'Dos comprobaciones consecutivas han fallado.',settings.SITE_URL+website.business.get_absolute_url())
            elif ok and website.incident_open:
                website.incident_open=False; website.save(update_fields=['incident_open'])
                notify('Web recuperada: '+website.business.name,[settings.ADMIN_EMAIL],'La web vuelve a responder.',settings.SITE_URL+website.business.get_absolute_url())

def aggregate_uptime():
    cutoff=timezone.localdate()-timedelta(days=35)
    rows=UptimeCheck.objects.annotate(day=TruncDate('checked_at')).values('website_id','day').annotate(checks=Count('pk'),failures=Count('pk',filter=Q(ok=False)),latency=Avg('latency_ms'))
    for row in rows:
        UptimeDaily.objects.update_or_create(website_id=row['website_id'],date=row['day'],defaults={'checks':row['checks'],'failures':row['failures'],'availability':Decimal(100)*(row['checks']-row['failures'])/row['checks'],'average_latency':row['latency']})
    UptimeCheck.objects.filter(checked_at__date__lt=cutoff).delete()

def uptime_context(user,website):
    if not website: return {}
    today=timezone.localdate(); start=today-timedelta(days=29)
    rows={r.date:r for r in UptimeDaily.objects.visible_to(user).filter(website=website,date__gte=start,date__lte=today)}
    days=[]; checks=failures=0
    for offset in range(30):
        day=start+timedelta(days=offset); row=rows.get(day)
        count=row.checks if row else 0; failed=row.failures if row else 0
        if day==today:
            live=UptimeCheck.objects.visible_to(user).filter(website=website,checked_at__date=today).aggregate(c=Count('pk'),f=Count('pk',filter=Q(ok=False)))
            count,failed=live['c'],live['f']
        checks+=count; failures+=failed
        days.append({'date':day.strftime('%d/%m/%Y'),'css':'ok' if count and not failed else 'down' if count and failed==count else 'partial' if count else '', 'label':f'{100*(count-failed)/count:.2f}%' if count else 'Sin datos'})
    return {'uptime_days':days,'uptime_percent':round(100*(checks-failures)/checks,2) if checks else None}
