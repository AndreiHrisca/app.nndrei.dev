"""Consultas from nndrei.dev: mailbox sync, statuses, replies, conversion, retention and access."""
import logging
import sqlite3
from datetime import datetime, timedelta, timezone as dt_timezone
import pytest
import requests
from django.db import DatabaseError
from django.utils import timezone
from core import inquiry_sync
from core.inquiries import purge_old_inquiries, whatsapp_url
from core.inquiry_sync import notify_pending, sync_once
from core.models import ActivityEvent, Business, Inquiry, InquiryStatusChange
from core.tasks import daily_maintenance

pytestmark = pytest.mark.django_db

# The schema portfolio-api creates (server/app.py, init_db).
SCHEMA = '''CREATE TABLE inbox (id INTEGER PRIMARY KEY, created_at INTEGER NOT NULL, request_id TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
  email TEXT NOT NULL, business TEXT NOT NULL, phone TEXT NOT NULL, need TEXT NOT NULL, message TEXT NOT NULL, budget TEXT NOT NULL{extra})'''
NEW_COLUMNS = ", source TEXT NOT NULL DEFAULT '', origin_page TEXT NOT NULL DEFAULT ''"


def row(n=1, **extra):
    data = dict(created_at=1760000000 + n, request_id=f'12345678-1234-1234-1234-{n:012d}', name=f'Persona {n}', email=f'persona{n}@example.com',
                business=f'Bar {n}', phone='612 345 678', need='Web', message=f'Mensaje secreto {n}', budget='1.000 €', source='card', origin_page='/qr')
    data.update(extra)
    return data


@pytest.fixture
def mailbox(tmp_path):
    path = tmp_path / 'portfolio.sqlite3'

    def make(*rows, old=False):
        with sqlite3.connect(path) as db:
            db.execute(SCHEMA.format(extra='' if old else NEW_COLUMNS).replace('CREATE TABLE', 'CREATE TABLE IF NOT EXISTS', 1))
            for r in rows:
                if old:
                    r = {k: v for k, v in r.items() if k not in ('source', 'origin_page')}
                db.execute(f"INSERT INTO inbox({','.join(r)}) VALUES({','.join('?' * len(r))})", list(r.values()))
        db.close()
        return path
    make.path = path
    make.count = lambda: sqlite3.connect(path).execute('SELECT count(*) FROM inbox').fetchone()[0]
    return make


@pytest.fixture(autouse=True)
def fresh_warnings():
    inquiry_sync._last_warning = None


# --- Sync -----------------------------------------------------------------------------

def test_sync_moves_rows_and_empties_the_mailbox(mailbox):
    path = mailbox(row(1), row(2, source='direct', origin_page='/hablamos'))
    assert sync_once(path) == 2
    assert mailbox.count() == 0
    card, web = Inquiry.objects.order_by('received_at')
    assert (card.name, card.email, card.business_name, card.phone, card.need, card.budget) == ('Persona 1', 'persona1@example.com', 'Bar 1', '612 345 678', 'Web', '1.000 €')
    assert (card.status, card.origin_kind, web.origin_kind) == ('new', 'card', 'web')
    assert card.received_at == datetime.fromtimestamp(1760000001, dt_timezone.utc)


def test_sync_is_idempotent_when_retried(mailbox):
    path = mailbox(row(1), row(2))
    sync_once(path)
    # The mailbox delete "failed" last time: the same rows come back.
    mailbox(row(1), row(2))
    assert sync_once(path) == 0
    assert Inquiry.objects.count() == 2
    assert mailbox.count() == 0


def test_rows_without_request_id_are_keyed_by_hash(mailbox):
    # portfolio-api always sets one, but an empty value must not merge different messages.
    path = mailbox(row(1, request_id=''))
    sync_once(path)
    mailbox(row(1, request_id=''))
    sync_once(path)
    assert Inquiry.objects.count() == 1
    mailbox(row(2, request_id=''))
    sync_once(path)
    assert Inquiry.objects.count() == 2 and mailbox.count() == 0
    assert len(Inquiry.objects.get(name='Persona 1').sync_key) == 64


def test_failed_insert_keeps_the_row_in_the_mailbox(mailbox, monkeypatch):
    path = mailbox(row(1), row(2))
    real = inquiry_sync.import_row

    def flaky(r):
        if r['id'] == 2:
            raise DatabaseError('postgres down')
        return real(r)
    monkeypatch.setattr(inquiry_sync, 'import_row', flaky)
    sync_once(path)
    assert list(Inquiry.objects.values_list('name', flat=True)) == ['Persona 1']
    assert [r[0] for r in sqlite3.connect(path).execute('SELECT name FROM inbox')] == ['Persona 2']
    monkeypatch.setattr(inquiry_sync, 'import_row', real)
    sync_once(path)
    assert Inquiry.objects.count() == 2 and mailbox.count() == 0


def test_nothing_is_deleted_when_the_database_is_unavailable(mailbox, monkeypatch):
    path = mailbox(row(1))
    monkeypatch.setattr(inquiry_sync, 'import_row', lambda r: (_ for _ in ()).throw(DatabaseError('down')))
    assert sync_once(path) == 0
    assert mailbox.count() == 1


def test_old_mailbox_without_new_columns_still_syncs(mailbox):
    path = mailbox(row(1), old=True)
    sync_once(path)
    item = Inquiry.objects.get()
    assert (item.source, item.origin_page, item.origin_kind) == ('', '', 'web')


def test_missing_volume_or_table_only_warns(tmp_path, caplog):
    missing = tmp_path / 'nada' / 'portfolio.sqlite3'
    with caplog.at_level(logging.WARNING, logger='core.inquiry_sync'):
        assert sync_once(missing) == 0
        assert sync_once(missing) == 0
    assert not missing.exists()
    assert len([r for r in caplog.records if 'no encontrado' in r.message]) == 1
    empty = tmp_path / 'empty.sqlite3'
    sqlite3.connect(empty).close()
    with caplog.at_level(logging.WARNING, logger='core.inquiry_sync'):
        assert sync_once(empty) == 0
    assert 'tabla inbox' in caplog.text


def test_busy_database_is_retried(monkeypatch):
    monkeypatch.setattr(inquiry_sync.time, 'sleep', lambda s: None)
    calls = []

    def busy():
        calls.append(1)
        if len(calls) < 3:
            raise sqlite3.OperationalError('database is locked')
        return 'ok'
    assert inquiry_sync.retry_busy(busy) == 'ok' and len(calls) == 3
    with pytest.raises(sqlite3.OperationalError):
        inquiry_sync.retry_busy(lambda: (_ for _ in ()).throw(sqlite3.OperationalError('database is locked')))


def test_real_lock_waits_for_the_writer(mailbox):
    """portfolio-api holding a write lock: the sync waits instead of failing."""
    import threading
    path = mailbox(row(1))
    writer = sqlite3.connect(path, isolation_level=None, check_same_thread=False)
    writer.execute('BEGIN IMMEDIATE')
    threading.Timer(0.5, lambda: writer.execute('COMMIT')).start()
    assert sync_once(path) == 1
    assert mailbox.count() == 0
    writer.close()


def test_logs_carry_no_personal_data(mailbox, caplog, monkeypatch, settings):
    settings.TELEGRAM_BOT_TOKEN, settings.TELEGRAM_CHAT_ID = 'secret-token', '42'
    monkeypatch.setattr(requests, 'post', lambda *a, **k: (_ for _ in ()).throw(requests.ConnectionError('https://api.telegram.org/botsecret-token/')))
    with caplog.at_level(logging.DEBUG):
        sync_once(mailbox(row(1)))
        Inquiry.objects.update(received_at=timezone.now())
        notify_pending()
    for secret in ['Persona 1', 'persona1@example.com', 'Mensaje secreto', '612 345 678', 'Bar 1', 'secret-token']:
        assert secret not in caplog.text
    assert 'importadas' in caplog.text and 'Telegram' in caplog.text


# --- Telegram ---------------------------------------------------------------------------

def make_inquiry(**extra):
    data = dict(sync_key=extra.pop('key', 'k1'), received_at=timezone.now(), name='Lucía Pérez', email='lucia@example.com', phone='612345678',
                business_name='Panadería Sol', need='Web', message='Quiero una web', source='card', origin_page='/qr')
    data.update(extra)
    return Inquiry.objects.create(**data)


def test_telegram_is_skipped_without_variables(settings, monkeypatch):
    settings.TELEGRAM_BOT_TOKEN = settings.TELEGRAM_CHAT_ID = ''
    monkeypatch.setattr(requests, 'post', lambda *a, **k: pytest.fail('No debe llamar a Telegram'))
    make_inquiry()
    assert notify_pending() == 0


def test_telegram_notice_once_with_link(settings, monkeypatch):
    settings.TELEGRAM_BOT_TOKEN, settings.TELEGRAM_CHAT_ID, settings.SITE_URL = 'tok', '42', 'https://app.nndrei.dev'
    sent = []

    class Ok:
        def raise_for_status(self):
            pass
    monkeypatch.setattr(requests, 'post', lambda url, **k: sent.append((url, k['json'])) or Ok())
    item = make_inquiry()
    assert notify_pending() == 1 and notify_pending() == 0
    url, payload = sent[0]
    assert url == 'https://api.telegram.org/bottok/sendMessage' and payload['chat_id'] == '42'
    assert 'Lucía Pérez · Panadería Sol' in payload['text'] and 'Tarjeta / QR' in payload['text']
    assert f'https://app.nndrei.dev/consultas/{item.public_id}/' in payload['text']
    assert 'lucia@example.com' not in payload['text'] and '612345678' not in payload['text']


def test_telegram_failures_are_retried_a_few_times(settings, monkeypatch):
    settings.TELEGRAM_BOT_TOKEN, settings.TELEGRAM_CHAT_ID = 'tok', '42'
    monkeypatch.setattr(requests, 'post', lambda *a, **k: (_ for _ in ()).throw(requests.Timeout()))
    item = make_inquiry()
    for _ in range(8):
        notify_pending()
    item.refresh_from_db()
    assert item.notified_at is None and item.notify_attempts == inquiry_sync.TELEGRAM_ATTEMPTS


# --- Statuses, replies, conversion, deletion ----------------------------------------------

def test_opening_a_new_inquiry_marks_it_read(sign_in, accounts):
    item = make_inquiry()
    c = sign_in('admin')
    response = c.get(f'/consultas/{item.public_id}/')
    assert response.status_code == 200 and b'Quiero una web' in response.content
    item.refresh_from_db()
    assert item.status == 'read' and item.status_changed_at
    change = InquiryStatusChange.objects.get()
    assert (change.from_status, change.to_status, change.actor) == ('new', 'read', accounts['admin'])
    c.get(f'/consultas/{item.public_id}/')
    assert InquiryStatusChange.objects.count() == 1


def test_manual_status_changes_and_history(sign_in):
    item = make_inquiry()
    c = sign_in('admin')
    for status in ['replied', 'archived', 'spam', 'read']:
        assert c.post(f'/consultas/{item.public_id}/estado/', {'status': status}).status_code == 302
        item.refresh_from_db()
        assert item.status == status
    assert c.post(f'/consultas/{item.public_id}/estado/', {'status': 'converted'}).status_code == 400
    assert list(item.changes.values_list('to_status', flat=True)) == ['replied', 'archived', 'spam', 'read']


def test_list_filters_and_new_counter(sign_in):
    make_inquiry(key='a', name='Tarjeta Uno')
    make_inquiry(key='b', name='Web Dos', source='direct', origin_page='/hablamos', business_name='Ferretería')
    make_inquiry(key='c', name='Campaña Tres', source='restaurant', origin_page='/hablamos', status='read')
    make_inquiry(key='d', name='Spam Cuatro', status='spam')
    c = sign_in('admin')
    page = c.get('/consultas/').content.decode()
    assert 'Tarjeta Uno' in page and 'Web Dos' in page and 'Campaña Tres' in page and 'Spam Cuatro' not in page
    assert 'class="nav-count"' in page and '>2<' in page
    names = lambda q: c.get('/consultas/' + q, HTTP_HX_REQUEST='true').content.decode()
    assert 'Tarjeta Uno' in names('?origen=card') and 'Web Dos' not in names('?origen=card')
    assert 'Campaña Tres' in names('?origen=campaign') and 'Tarjeta Uno' not in names('?origen=campaign')
    assert 'Web Dos' in names('?origen=web') and 'Campaña Tres' not in names('?origen=web')
    assert 'Spam Cuatro' in names('?estado=spam') and 'Spam Cuatro' in names('?estado=todas')
    found = names('?q=ferre')
    assert 'Web Dos' in found and 'Tarjeta Uno' not in found
    assert names('?orden=antiguas').index('Tarjeta Uno') < names('?orden=antiguas').index('Web Dos')


def test_reply_by_email_and_whatsapp_marks_replied(sign_in):
    item = make_inquiry()
    c = sign_in('admin')
    response = c.post(f'/consultas/{item.public_id}/responder/email/')
    assert response.status_code == 302 and response['Location'].startswith('mailto:lucia@example.com?subject=')
    assert 'Hola%20Luc%C3%ADa' in response['Location']
    item.refresh_from_db()
    assert item.status == 'replied'
    response = c.post(f'/consultas/{item.public_id}/responder/whatsapp/')
    assert response['Location'].startswith('https://wa.me/34612345678?text=')
    assert c.post(f'/consultas/{item.public_id}/responder/fax/').status_code == 400


def test_no_whatsapp_without_phone(sign_in):
    item = make_inquiry(phone='')
    assert whatsapp_url(item) == ''
    assert sign_in('admin').post(f'/consultas/{item.public_id}/responder/whatsapp/').status_code == 400
    assert whatsapp_url(make_inquiry(key='z', phone='+44 7700 900123')) .startswith('https://wa.me/447700900123?')


def test_convert_creates_a_prefilled_client(sign_in):
    item = make_inquiry()
    c = sign_in('admin')
    response = c.post(f'/consultas/{item.public_id}/convertir/')
    item.refresh_from_db()
    b = item.business
    assert response.status_code == 302 and response['Location'] == b.get_absolute_url()
    assert (b.name, b.phone, b.email, b.contact) == ('Panadería Sol', '612345678', 'lucia@example.com', 'Lucía Pérez')
    assert 'Quiero una web' in b.internal_notes and 'Tarjeta / QR' in b.internal_notes
    assert item.status == 'converted' and item.changes.last().to_status == 'converted'
    # Both ways: the client card lists the inquiry.
    assert str(item.public_id) in c.get(b.get_absolute_url()).content.decode()
    # Converting again does not create a second card.
    c.post(f'/consultas/{item.public_id}/convertir/')
    assert Business.objects.count() == 1


def test_convert_links_an_existing_client_suggested_by_phone(sign_in, businesses):
    own = businesses[0]
    own.phone = '+34 612 34 56 78'
    own.save()
    item = make_inquiry(business_name='Otro nombre', email='otra@example.com')
    c = sign_in('admin')
    page = c.get(f'/consultas/{item.public_id}/').content.decode()
    assert '¿Ya es cliente?' in page and str(own.public_id) in page
    assert 'Mi negocio' in c.get(f'/consultas/{item.public_id}/?buscar=negocio').content.decode()
    c.post(f'/consultas/{item.public_id}/convertir/', {'business': own.public_id})
    item.refresh_from_db()
    assert item.business == own and item.status == 'converted'
    event = ActivityEvent.objects.filter(business=own, kind='inquiry').get()
    assert 'Lucía' not in event.text and not event.client_visible
    assert c.post(f'/consultas/{make_inquiry(key="x").public_id}/convertir/', {'business': 'no-uuid'}).status_code == 400


def test_notes_and_definitive_delete(sign_in):
    item = make_inquiry()
    c = sign_in('admin')
    c.get(f'/consultas/{item.public_id}/')
    c.post(f'/consultas/{item.public_id}/notas/', {'internal_notes': 'Llamar el lunes'})
    item.refresh_from_db()
    assert item.internal_notes == 'Llamar el lunes'
    assert 'data-confirm' in c.get(f'/consultas/{item.public_id}/').content.decode()
    assert c.post(f'/consultas/{item.public_id}/eliminar/').status_code == 302
    assert not Inquiry.objects.exists() and not InquiryStatusChange.objects.exists()
    assert c.get(f'/consultas/{item.public_id}/').status_code == 404


# --- Retention ------------------------------------------------------------------------------

def test_retention_180_days_except_converted(businesses):
    now = timezone.now()
    old = make_inquiry(key='old', received_at=now - timedelta(days=181))
    recent = make_inquiry(key='recent', received_at=now - timedelta(days=179))
    converted = make_inquiry(key='conv', received_at=now - timedelta(days=400), status='converted', business=businesses[0])
    InquiryStatusChange.objects.create(inquiry=old, to_status='read')
    daily_maintenance()
    assert set(Inquiry.objects.values_list('pk', flat=True)) == {recent.pk, converted.pk}
    assert not InquiryStatusChange.objects.filter(inquiry_id=old.pk).exists()
    assert purge_old_inquiries(now + timedelta(days=2)) == 1


# --- Access ---------------------------------------------------------------------------------

@pytest.mark.parametrize('role', ['developer', 'accountant', 'client'])
def test_only_administrators_see_inquiries(sign_in, role):
    item = make_inquiry()
    c = sign_in(role)
    for url in ['/consultas/', f'/consultas/{item.public_id}/']:
        assert c.get(url).status_code == 404
    assert c.post(f'/consultas/{item.public_id}/eliminar/').status_code == 404
    assert Inquiry.objects.filter(pk=item.pk).exists()
    page = c.get('/mas/').content.decode()
    assert '/consultas/' not in page and 'nav-count' not in page


def test_anonymous_is_sent_to_login(client):
    item = make_inquiry()
    for url in ['/consultas/', f'/consultas/{item.public_id}/']:
        response = client.get(url)
        assert response.status_code == 302 and response['Location'].startswith('/login/')
    item.refresh_from_db()
    assert item.status == 'new'
