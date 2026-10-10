"""Demo mockups: self-contained HTML files per business, versioned.

Stored as files on the private volume (like Document), not as a database
column: a few MB of HTML has no place on every Business query, and the private
volume is already backed up and never served as static files. Every upload is a
new version; the newest one is public at /demos/<slug>/, and at /c/<code>
for whoever has the card (core/demo_codes.py).
"""
import hashlib
import re
import secrets
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage
from django.db import IntegrityError, transaction
from django.utils.text import slugify

MAX_DEMO_SIZE = 5 * 1024 * 1024
DEMO_EXTENSIONS = ('.html', '.htm')
SLUG_ALPHABET = 'abcdefghjkmnpqrstuvwxyz23456789'

# The demo is served on the CRM's own domain to someone without a session.
# `sandbox` without allow-same-origin gives it an opaque origin: its scripts run,
# but they cannot read the app's cookies or storage, and any request they make
# is cross-site, so the session cookie (SameSite=Lax) is never attached.
DEMO_HEADERS = {
    'Content-Security-Policy': 'sandbox allow-scripts allow-popups allow-forms',
    'X-Robots-Tag': 'noindex, nofollow',
    'Cache-Control': 'no-cache, no-store, must-revalidate',
    # The URL is the secret: never hand it to whatever the demo loads.
    'Referrer-Policy': 'no-referrer',
}


NOINDEX_META = b'<meta name="robots" content="noindex, nofollow">'
HEAD_TAG = re.compile(rb'<head\b[^>]*>', re.IGNORECASE)


def with_noindex(content):
    """The stored file stays as uploaded; the robots meta goes in on the way out."""
    match = HEAD_TAG.search(content)
    at = match.end() if match else 0
    return content[:at] + NOINDEX_META + content[at:]


class DemoError(ValueError):
    pass


def legacy_path(business):
    # Before versions there was one file per business, keyed by the primary key.
    return f'demos/{business.pk}.html'


def version_path(business, number):
    return f'demos/{business.pk}/v{number}.html'


def latest(business):
    from .models import DemoVersion
    return DemoVersion.objects.filter(business=business).order_by('-number').first()


def current_path(business):
    version = latest(business)
    if version is not None:
        return version.path
    return legacy_path(business) if default_storage.exists(legacy_path(business)) else None


def has_demo(business):
    return current_path(business) is not None


def read_demo(business):
    with default_storage.open(current_path(business), 'rb') as handle:
        return handle.read()


def clean_filename(name):
    name = (name or 'demo.html').strip().split('/')[-1].split('\\')[-1]
    if not name.lower().endswith(DEMO_EXTENSIONS):
        raise DemoError('El archivo debe terminar en .html.')
    stem = re.sub(r'[^A-Za-z0-9._-]+', '-', name[: name.rfind('.')]).strip('.-')[:80] or 'demo'
    return stem + '.html'


def check_content(content):
    if isinstance(content, str):
        content = content.encode('utf-8')
    if not content.strip():
        raise DemoError('La demo está vacía.')
    if len(content) > MAX_DEMO_SIZE:
        raise DemoError('La demo ocupa más de 5 MB.')
    try:
        content.decode('utf-8')
    except UnicodeDecodeError:
        raise DemoError('La demo debe estar en UTF-8.')
    return content


def ensure_slug(business):
    """Give the business its fixed public demo address, once."""
    if business.demo_slug:
        return business.demo_slug
    base = slugify(business.name)[:40].strip('-') or 'demo'
    from .models import Business
    for _ in range(5):
        slug = f"{base}-{''.join(secrets.choice(SLUG_ALPHABET) for _ in range(6))}"
        try:
            with transaction.atomic():
                updated = Business.objects.filter(pk=business.pk, demo_slug__isnull=True).update(demo_slug=slug)
        except IntegrityError:
            continue
        if not updated:
            business.refresh_from_db(fields=['demo_slug'])
            return business.demo_slug
        business.demo_slug = slug
        return slug
    raise DemoError('No se ha podido crear la dirección de la demo.')


@transaction.atomic
def save_demo(business, content, filename='demo.html', user=None, source='web'):
    """Store a new version and make it the public one."""
    from .models import Business, DemoVersion
    content = check_content(content)
    filename = clean_filename(filename)
    Business.objects.select_for_update().filter(pk=business.pk).first()
    last = latest(business)
    number = last.number + 1 if last else 1
    if last is None and default_storage.exists(legacy_path(business)):
        # Keep the single pre-versioning demo as version 1.
        with default_storage.open(legacy_path(business), 'rb') as handle:
            old = handle.read()
        old_path = default_storage.save(version_path(business, 1), ContentFile(old))
        DemoVersion.objects.create(business=business, number=1, filename='demo.html', path=old_path, size=len(old),
                                   sha256=hashlib.sha256(old).hexdigest())
        default_storage.delete(legacy_path(business))
        number = 2
    path = default_storage.save(version_path(business, number), ContentFile(content))
    version = DemoVersion.objects.create(business=business, number=number, filename=filename, path=path, size=len(content),
                                         sha256=hashlib.sha256(content).hexdigest(), source=source, created_by=user)
    ensure_slug(business)
    from .demo_codes import ensure_code
    ensure_code(business)
    return version


@transaction.atomic
def delete_demo(business):
    """Unpublish: remove every version. The slug stays, so a new demo reuses the address."""
    from .models import DemoVersion
    for version in DemoVersion.objects.filter(business=business):
        default_storage.delete(version.path)
    DemoVersion.objects.filter(business=business).delete()
    if default_storage.exists(legacy_path(business)):
        default_storage.delete(legacy_path(business))


def qr_png(url):
    import io
    import qrcode
    image = qrcode.make(url, box_size=12, border=2)
    buffer = io.BytesIO()
    image.save(buffer, format='PNG')
    return buffer.getvalue()
