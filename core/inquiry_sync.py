"""Move the nndrei.dev contact form's mailbox into the app.

portfolio-api writes each message to SQLite (/data/portfolio.sqlite3, table
`inbox`) and has no way out of its private network. The `inbox-sync` service
mounts that same volume, with portfolio-api's uid, and every 30 seconds:

1. reads the rows of `inbox`;
2. inserts each one here, keyed by its request_id (or a hash of date + email +
   message), so repeating a cycle never duplicates anything;
3. deletes from the mailbox only the rows whose insert was confirmed.

Logs carry counts and error types only: never names, emails or messages.
"""
import hashlib
import logging
import sqlite3
import time
from datetime import datetime, timedelta, timezone as dt_timezone
from pathlib import Path
import requests
from django.conf import settings
from django.db import DatabaseError, transaction
from django.utils import timezone
from .models import Inquiry

log = logging.getLogger(__name__)

BATCH = 200
BUSY_RETRIES = 4
TELEGRAM_ATTEMPTS = 5
COLUMNS = ['id', 'created_at', 'request_id', 'name', 'email', 'business', 'phone', 'need', 'message', 'budget', 'source', 'origin_page']


class MailboxUnavailable(Exception):
    pass


_last_warning = None


def warn_once(message):
    """A missing volume would otherwise repeat the same line every 30 seconds."""
    global _last_warning
    if message != _last_warning:
        log.warning(message)
    _last_warning = message


def is_busy(error):
    text = str(error).lower()
    return isinstance(error, sqlite3.OperationalError) and ('locked' in text or 'busy' in text)


def retry_busy(operation):
    """SQLITE_BUSY means portfolio-api is writing right now: wait a little and try again."""
    for attempt in range(BUSY_RETRIES):
        try:
            return operation()
        except sqlite3.OperationalError as error:
            if not is_busy(error) or attempt == BUSY_RETRIES - 1:
                raise
            time.sleep(0.25 * 2 ** attempt)


def open_mailbox(path):
    if not path.exists():
        raise MailboxUnavailable('Buzón de consultas no encontrado: ¿está montado el volumen portfolio-data?')
    # mode=rw: never create an empty database where portfolio-api's should be.
    db = sqlite3.connect(f'file:{path}?mode=rw', uri=True, timeout=5)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA secure_delete=ON')
    return db


def read_rows(db):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='inbox'").fetchone():
        raise MailboxUnavailable('El buzón de consultas no tiene la tabla inbox todavía.')
    present = {row[1] for row in db.execute('PRAGMA table_info(inbox)')}
    # A mailbox from before source/origin_page still syncs; those fields stay empty.
    select = ', '.join(c if c in present else f"'' AS {c}" for c in COLUMNS)
    return [dict(row) for row in db.execute(f'SELECT {select} FROM inbox ORDER BY id LIMIT {BATCH}')]


def sync_key(row):
    if row['request_id']:
        return row['request_id'][:64]
    raw = f"{row['created_at']}\0{row['email']}\0{row['message']}".encode()
    return hashlib.sha256(raw).hexdigest()


def text(value, length=None):
    value = (value or '').strip()
    return value[:length] if length else value


def import_row(row):
    """Insert one mailbox row, or find it if a previous cycle already did. Returns (inquiry, created)."""
    with transaction.atomic():
        return Inquiry.objects.get_or_create(sync_key=sync_key(row), defaults={
            'received_at': datetime.fromtimestamp(int(row['created_at']), dt_timezone.utc),
            'name': text(row['name'], 100), 'email': text(row['email'], 254), 'phone': text(row['phone'], 40),
            'business_name': text(row['business'], 160), 'need': text(row['need'], 80), 'message': text(row['message']),
            'budget': text(row['budget'], 100), 'source': text(row['source'], 20), 'origin_page': text(row['origin_page'], 64),
        })


def delete_rows(db, ids):
    with db:
        db.execute(f"DELETE FROM inbox WHERE id IN ({','.join('?' * len(ids))})", ids)


def sync_once(path=None):
    """One cycle. Returns how many new inquiries arrived; never raises for a missing mailbox."""
    path = Path(path or settings.PORTFOLIO_INBOX)
    try:
        db = retry_busy(lambda: open_mailbox(path))
    except MailboxUnavailable as error:
        warn_once(str(error))
        return 0
    except sqlite3.Error as error:
        warn_once(f'No se puede abrir el buzón de consultas ({type(error).__name__}): revisa permisos y uid.')
        return 0
    created = 0
    try:
        try:
            rows = retry_busy(lambda: read_rows(db))
        except MailboxUnavailable as error:
            warn_once(str(error))
            return 0
        except sqlite3.Error as error:
            warn_once(f'No se puede leer el buzón de consultas ({type(error).__name__}).')
            return 0
        imported = []
        for row in rows:
            try:
                _, is_new = import_row(row)
            except (DatabaseError, ValueError, TypeError) as error:
                # Stays in the mailbox and is tried again next cycle.
                log.error('Consulta %s no importada (%s); sigue en el buzón.', row['id'], type(error).__name__)
                continue
            imported.append(row['id'])
            created += is_new
        if imported:
            # Only rows that are safely stored here leave the mailbox.
            try:
                retry_busy(lambda: delete_rows(db, imported))
            except sqlite3.Error as error:
                # Already stored here: the next cycle finds them by key and deletes them then.
                log.warning('No se pudieron borrar %d consultas del buzón (%s); se reintentará.', len(imported), type(error).__name__)
    finally:
        db.close()
    global _last_warning
    _last_warning = None
    if rows:
        log.info('Buzón de consultas: %d importadas (%d nuevas), %d pendientes.', len(imported), created, len(rows) - len(imported))
    return created


# --- Telegram ----------------------------------------------------------------------

def telegram_text(inquiry):
    who = inquiry.name + (f' · {inquiry.business_name}' if inquiry.business_name else '')
    return f'Nueva consulta en nndrei.dev\n{who}\nOrigen: {inquiry.get_origin_display()}\n{settings.SITE_URL}{inquiry.get_absolute_url()}'


def notify_pending():
    """Tell Telegram about new inquiries of the last day. Without the two variables, it does nothing."""
    token, chat = settings.TELEGRAM_BOT_TOKEN, settings.TELEGRAM_CHAT_ID
    if not token or not chat:
        return 0
    sent = 0
    pending = Inquiry.objects.filter(status='new', notified_at__isnull=True, notify_attempts__lt=TELEGRAM_ATTEMPTS,
                                     received_at__gte=timezone.now() - timedelta(days=1)).order_by('received_at')
    for inquiry in pending:
        inquiry.notify_attempts += 1
        try:
            response = requests.post(f'https://api.telegram.org/bot{token}/sendMessage', timeout=10,
                                     json={'chat_id': chat, 'text': telegram_text(inquiry), 'disable_web_page_preview': True})
            response.raise_for_status()
        except requests.RequestException as error:
            # The exception text holds the URL, and the URL holds the token: log the type only.
            status = getattr(getattr(error, 'response', None), 'status_code', None)
            log.warning('Aviso de Telegram no enviado (%s%s).', type(error).__name__, f' {status}' if status else '')
        else:
            inquiry.notified_at = timezone.now()
            sent += 1
        inquiry.save(update_fields=['notify_attempts', 'notified_at'])
    return sent
