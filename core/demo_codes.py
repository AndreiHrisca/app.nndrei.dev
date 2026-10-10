"""Card codes: five characters handwritten on a business card that open its demo.

nndrei.dev/qr sends whatever was typed to /c/<code>. A good code for a business
that is not discarded and has a demo redirects there; anything else gets the
same neutral page, so the endpoint never says whether a code exists. Every try is
logged with the address only as a keyed hash, and each address gets
ATTEMPTS_PER_HOUR tries an hour, right or wrong.
"""
import hashlib
import hmac
import secrets
from datetime import timedelta
from django.conf import settings
from django.db import IntegrityError, transaction
from django.utils import timezone

# No 0/O, 1/I/L: a code copied from handwriting has to survive the copying.
CODE_ALPHABET = '23456789ABCDEFGHJKMNPQRSTUVWXYZ'
CODE_LENGTH = 5
ATTEMPTS_PER_HOUR = 10
# Within this window a second open of the same demo leaves no new line in the
# history; the counters still see every open.
HISTORY_QUIET = timedelta(hours=1)
# Set on the /c/ redirect so the demo page it lands on is not counted twice.
VIA_CODE_COOKIE = 'demo_via_code'


def normalize(raw):
    """What the visitor typed, as stored: no whitespace anywhere, upper case."""
    return ''.join((raw or '').split()).upper()


def new_code():
    return ''.join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_LENGTH))


def ensure_code(business, regenerate=False):
    """Give the business a card code, once; `regenerate` replaces the current one."""
    from .models import Business
    if business.demo_code and not regenerate:
        return business.demo_code
    for _ in range(10):
        code = new_code()
        rows = Business.objects.filter(pk=business.pk)
        if not regenerate:
            rows = rows.filter(demo_code__isnull=True)
        try:
            with transaction.atomic():
                updated = rows.update(demo_code=code)
        except IntegrityError:
            continue  # Taken by another business: draw again.
        if not updated:
            business.refresh_from_db(fields=['demo_code'])
            return business.demo_code
        business.demo_code = code
        return code
    raise RuntimeError('No se ha podido generar un código libre.')


def visitor_ip(request):
    if settings.TRUST_X_FORWARDED_FOR:
        # The rightmost entry is the one our own proxy wrote.
        forwarded = request.META.get('HTTP_X_FORWARDED_FOR', '').split(',')[-1].strip()
        if forwarded:
            return forwarded
    return request.META.get('REMOTE_ADDR') or ''


def ip_hash(ip):
    return hmac.new(settings.SECRET_KEY.encode(), ('card-code:' + ip).encode(), hashlib.sha256).hexdigest()


def record_open(business, via):
    """Count one open and keep "Última actividad" current, without flooding the history."""
    from .models import ActivityEvent, DemoOpen
    from .services import activity
    now = timezone.now()
    DemoOpen.objects.create(business=business, via=via, opened_at=now)
    if not ActivityEvent.objects.filter(business=business, kind='demo_opened', occurred_at__gt=now - HISTORY_QUIET).exists():
        text = 'Demo abierta con el código de la tarjeta.' if via == 'code' else 'Demo abierta por su enlace.'
        activity(business, 'demo_opened', text, client_visible=False)


def redeem(request, raw):
    """(business or None, limited). Logs the attempt whatever the outcome."""
    from .demos import has_demo
    from .models import Business, CodeAttempt
    code = normalize(raw)
    hashed = ip_hash(visitor_ip(request))
    user_agent = request.META.get('HTTP_USER_AGENT', '')[:300]
    recent = CodeAttempt.objects.filter(ip_hash=hashed, created_at__gt=timezone.now() - timedelta(hours=1)).count()
    if recent >= ATTEMPTS_PER_HOUR:
        CodeAttempt.objects.create(code=code[:40], ip_hash=hashed, user_agent=user_agent, rate_limited=True)
        return None, True
    business = None
    if len(code) == CODE_LENGTH:
        business = Business.objects.filter(demo_code=code).exclude(stage='discarded').first()
        if business is not None and not has_demo(business):
            business = None
    CodeAttempt.objects.create(code=code[:40], ip_hash=hashed, user_agent=user_agent, valid=business is not None, business=business)
    if business is not None:
        record_open(business, 'code')
    return business, False


def open_stats(business):
    """For the card: how many opens, by which way, and the last one."""
    from django.db.models import Count, Max
    from .models import DemoOpen
    rows = {row['via']: row for row in DemoOpen.objects.filter(business=business).values('via').annotate(n=Count('pk'), last=Max('opened_at'))}
    lasts = [row['last'] for row in rows.values()]
    return {'total': sum(row['n'] for row in rows.values()),
            'code': rows.get('code', {}).get('n', 0), 'direct': rows.get('direct', {}).get('n', 0),
            'last': max(lasts) if lasts else None}
