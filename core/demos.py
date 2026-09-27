"""Demo mockups: one self-contained HTML file per business.

Stored as a file on the private volume (like Document), not as a database
column: a few MB of HTML has no place on every Business query, and the private
volume is already backed up and never served as static files.
"""
from django.core.files.base import ContentFile
from django.core.files.storage import default_storage

MAX_DEMO_SIZE = 5 * 1024 * 1024
DEMO_EXTENSIONS = ('.html', '.htm')

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


def demo_path(business):
    # Keyed by the primary key, which never changes and never leaves the server.
    return f'demos/{business.pk}.html'


def has_demo(business):
    return default_storage.exists(demo_path(business))


def read_demo(business):
    with default_storage.open(demo_path(business), 'rb') as handle:
        return handle.read()


def save_demo(business, content):
    path = demo_path(business)
    if default_storage.exists(path):
        default_storage.delete(path)
    default_storage.save(path, ContentFile(content))


def delete_demo(business):
    default_storage.delete(demo_path(business))
