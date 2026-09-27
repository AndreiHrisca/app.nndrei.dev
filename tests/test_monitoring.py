from datetime import timedelta,date
from unittest.mock import patch
import pytest
from django.utils import timezone
from django.core.management import call_command
from core.models import Website,UptimeCheck,UptimeDaily,EmailOutbox,ClientCharge,NotificationReceipt,Proposal,User
from core.monitoring import check_websites,aggregate_uptime,fetch_public_url,uptime_context
from core.finance import upcoming_reminders
from core.notifications import notify,retry_emails
pytestmark=pytest.mark.django_db

def test_two_failures_recovery(businesses,django_capture_on_commit_callbacks,mailoutbox):
    w=Website.objects.create(business=businesses[0],url='https://example.com',monitoring_enabled=True)
    with patch('core.monitoring.fetch_public_url',return_value=500):
        with django_capture_on_commit_callbacks(execute=True): check_websites()
        assert len(mailoutbox)==0
        UptimeCheck.objects.update(checked_at=timezone.now()-timedelta(minutes=5))
        with django_capture_on_commit_callbacks(execute=True): check_websites()
    w.refresh_from_db(); assert w.incident_open
    assert len(mailoutbox)==1
    UptimeCheck.objects.update(checked_at=timezone.now()-timedelta(minutes=5))
    with patch('core.monitoring.fetch_public_url',return_value=200):
        with django_capture_on_commit_callbacks(execute=True): check_websites()
    w.refresh_from_db(); assert not w.incident_open
    assert len(mailoutbox)==2

def test_aggregation_and_retention(businesses,accounts):
    w=Website.objects.create(business=businesses[0])
    old=timezone.now()-timedelta(days=36)
    UptimeCheck.objects.create(website=w,checked_at=old,ok=False,latency_ms=100)
    UptimeCheck.objects.create(website=w,ok=True,latency_ms=100)
    aggregate_uptime()
    assert UptimeCheck.objects.count()==1 and UptimeDaily.objects.count()==2
    assert len(uptime_context(accounts['client'],w)['uptime_days'])==30
    assert uptime_context(accounts['client'],w)['uptime_percent']==100

@pytest.mark.parametrize('url',['http://127.0.0.1/','http://169.254.169.254/','file:///etc/passwd','http://user:pass@example.com/','http://example.com:5432/'])
def test_monitor_ssrf(url):
    with pytest.raises(ValueError): fetch_public_url(url)

def test_notification_retry(django_capture_on_commit_callbacks,mailoutbox):
    with patch('core.notifications.deliver',side_effect=OSError('SMTP down')):
        with django_capture_on_commit_callbacks(execute=True): notify('Test',['a@example.com'],'Message')
    assert EmailOutbox.objects.get().sent_at is None
    retry_emails(); retry_emails()
    assert len(mailoutbox)==1 and EmailOutbox.objects.get().sent_at

def test_reminder_once(businesses,django_capture_on_commit_callbacks):
    ClientCharge.objects.create(business=businesses[0],concept='Dominio',kind='domain',amount=20,next_date=date(2026,10,8))
    upcoming_reminders(date(2026,10,1)); upcoming_reminders(date(2026,10,2))
    assert NotificationReceipt.objects.count()==1 and EmailOutbox.objects.count()==1

def test_schedules_idempotent(settings):
    from django_q.models import Schedule
    call_command('setup_schedules'); call_command('setup_schedules')
    assert Schedule.objects.filter(name__startswith='nndrei-').count()==4

def test_seed_idempotent(monkeypatch):
    monkeypatch.setenv('SEED_ADMIN_EMAIL','owner@example.com')
    monkeypatch.setenv('SEED_ADMIN_PASSWORD','A-long-unique-password-29184!')
    call_command('seed_demo'); call_command('seed_demo')
    p=Proposal.objects.get(number='P-2026-001')
    assert p.versions.count()==2
    assert p.versions.first().accepted_at is None
    assert p.versions.first().historical_status=='accepted'
    assert ClientCharge.objects.get(kind='domain').amount is None

def test_daily_purges_provider_cache_but_preserves_workflow():
    from core.models import SearchZone,SearchCategory,PlaceSnapshot
    from core.tasks import daily_maintenance
    z=SearchZone.objects.create(name='Zone'); c=SearchCategory.objects.create(name='Category')
    p=PlaceSnapshot.objects.create(place_id='keep-id',name='Cached',phone='123',zone=z,category=c,status='discarded',notes='Keep own notes',fetched_at=timezone.now()-timedelta(days=31))
    daily_maintenance(); p.refresh_from_db()
    assert p.name=='' and p.phone=='' and p.place_id=='keep-id'
    assert p.status=='discarded' and p.notes=='Keep own notes'
