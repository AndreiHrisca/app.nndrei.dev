"""Saved locations only: opening the map never calls a location provider."""
from django.http import JsonResponse
from django.views.decorators.http import require_GET
from django.views.decorators.cache import never_cache
from .auth_views import roles
from .hours import hours_status
from .models import Business, STAGES

# The same palette as app.css's stage tiles.
STAGE_COLORS = dict(zip([k for k, _ in STAGES], [
    '#EDEDEF', '#FDF1C7', '#FDE4D0', '#FDF1C7', '#FAD9D5',
    '#E7E1FD', '#D8EAF8', '#D9F1F1', '#E0F2D0', '#EDEDEF']))


@roles('admin')
@require_GET
@never_cache
def pipeline_map_data(request):
    items = Business.objects.visible_to(request.user).exclude(stage='discarded')
    phase = request.GET.get('fase', request.GET.get('stage', ''))
    if phase in dict(STAGES):
        items = items.filter(stage=phase)
    items = items.select_related('category', 'neighbourhood').only(
        'public_id', 'name', 'stage', 'address', 'latitude', 'longitude', 'opening_hours',
        'category__name', 'neighbourhood__name').order_by('name', 'pk')
    data = []
    for b in items:
        status = hours_status(b.opening_hours)
        data.append({'id': str(b.public_id), 'nombre': b.name,
                     'sector': b.category.name if b.category_id else '',
                     'fase': b.stage, 'fase_nombre': b.get_stage_display(), 'color': STAGE_COLORS[b.stage],
                     'direccion': b.address, 'barrio': b.neighbourhood.name if b.neighbourhood_id else '',
                     'latitud': b.latitude, 'longitud': b.longitude,
                     'abierto': None if status.key == 'unknown' else status.is_open,
                     'url': b.get_absolute_url()})
    return JsonResponse({'negocios': data})
