"""Public, session-independent PWA resources. No push or credential handlers."""
from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.template.loader import render_to_string
from django.views.decorators.http import require_safe


@require_safe
def manifest(request):
    response = JsonResponse({
        'id': '/', 'name': 'nndrei Negocio', 'short_name': 'nndrei',
        'start_url': '/?source=pwa', 'scope': '/', 'display': 'standalone',
        'orientation': 'portrait', 'background_color': '#F4F4F6',
        'theme_color': '#6D4AE8', 'lang': 'es',
        'icons': [
            {'src': '/static/icons/icon-192.png', 'sizes': '192x192', 'type': 'image/png', 'purpose': 'any'},
            {'src': '/static/icons/icon-512.png', 'sizes': '512x512', 'type': 'image/png', 'purpose': 'any'},
            {'src': '/static/icons/icon-maskable-512.png', 'sizes': '512x512', 'type': 'image/png', 'purpose': 'maskable'},
        ],
    }, content_type='application/manifest+json')
    response['Cache-Control'] = 'no-cache'
    return response


@require_safe
def service_worker(request):
    response = HttpResponse((settings.BASE_DIR / 'core' / 'sw.js').read_text(), content_type='application/javascript')
    response['Cache-Control'] = 'no-cache'
    return response


@require_safe
def offline(request):
    # Deliberately omit request/context processors: identical even when logged in.
    return HttpResponse(render_to_string('core/offline.html'))
