"""What Claude can do through MCP, as plain Django functions.

`core/mcp_server.py` only wraps these for the protocol; everything here is
tested directly. Every function receives the user behind the token, allows
admins only, validates sizes and types, and records each write in the
business's internal history.
"""
import re
import uuid
from decimal import Decimal, InvalidOperation
from django.core.exceptions import ValidationError
from django.core.validators import URLValidator
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from . import labels
from .access import visible_queryset
from .models import Business, Estimation, PlaceSnapshot, STAGE_ORDER, STAGES, StageChange, Study
from .services import activity

MAX_TEXT = 5000
MAX_SOURCES_PER_CALL = 50
MAX_SOURCES = 100
MAX_URL = 2000
SEARCH_LIMIT = 50
PLACE_ID = re.compile(r'^[A-Za-z0-9_-]{1,255}$')

# Study fields by the names Claude uses (Spanish), with the model field behind each.
STUDY_FIELDS = {
    'quienes_son': 'who', 'que_venden': 'sells', 'publico_objetivo': 'audience',
    'ficha_google': 'has_google', 'instagram': 'has_instagram', 'facebook': 'has_facebook', 'tiktok': 'has_tiktok',
    'web_propia': 'has_website', 'reservas_online': 'has_booking',
    'enlaces': 'links', 'competencia': 'competitors', 'necesidades': 'needs', 'oportunidad': 'opportunity',
    'mejor_momento': 'best_time', 'persona_contacto': 'contact_to_find', 'notas': 'notes',
}
ESTIMATION_FIELDS = {
    'resenas': 'reviews', 'nota': 'rating', 'anos': 'years', 'ticket': 'ticket', 'mesas': 'tables',
    'personas_por_mesa': 'people_per_table', 'servicios': 'services', 'rotaciones': 'rotations',
    'dias_por_semana': 'days_per_week', 'ocupacion': 'occupancy_pct', 'concurrencia': 'concurrency',
    'facturacion_anual': 'declared_annual', 'fuente_facturacion': 'declared_source', 'usa_reparto': 'uses_delivery',
}
SERVICES = {'desayuno': 'breakfast', 'comida': 'lunch', 'cena': 'dinner'}
CONCURRENCY = {'baja': 'low', 'media': 'medium', 'alta': 'high'}


class ToolError(ValueError):
    """A message for Claude: what was wrong and how to fix it."""


def require_admin(user):
    if user is None or not user.is_active or user.role != 'admin':
        raise ToolError('Solo un administrador puede usar este conector.')


def business_for(user, cliente_id, lock=False):
    try:
        public_id = uuid.UUID(str(cliente_id))
    except ValueError:
        raise ToolError('El id de cliente no es válido. Usa el campo "id" que devuelve buscar_clientes.')
    queryset = visible_queryset(Business, user)
    if lock:
        queryset = queryset.select_for_update()
    business = queryset.filter(public_id=public_id).first()
    if business is None:
        raise ToolError('No existe ningún cliente con ese id.')
    return business


def text(value, name, limit=MAX_TEXT, required=False):
    if value is None:
        value = ''
    if not isinstance(value, str):
        raise ToolError(f'"{name}" debe ser texto.')
    value = value.strip()
    if required and not value:
        raise ToolError(f'"{name}" no puede estar vacío.')
    if len(value) > limit:
        raise ToolError(f'"{name}" supera los {limit} caracteres.')
    return value


def record(business, user, kind, message, obj=None):
    activity(business, 'mcp_' + kind, 'Claude: ' + message, user, obj, client_visible=False)


# The two proposal stages were merged into «Propuesta»: their old names still work.
STAGE_ALIASES = {'propuesta enviada': 'proposal', 'cambios en la propuesta': 'proposal', 'review': 'proposal'}


def stage_key(estado):
    if not estado:
        return None
    if not isinstance(estado, str):
        raise ToolError('"estado" debe ser el nombre de una fase.')
    wanted = estado.strip().lower()
    for key, label in STAGES:
        if wanted in (key, label.lower()):
            return key
    if wanted in STAGE_ALIASES:
        return STAGE_ALIASES[wanted]
    raise ToolError('Estado desconocido. Usa uno de: ' + ', '.join(label for _, label in STAGES) + '.')


def move_to_demo_ready(business, user):
    """A demo on a found or studied business means only the visit is missing. Never moves back."""
    if STAGE_ORDER[business.stage] >= STAGE_ORDER['demo_ready']:
        return False
    StageChange.objects.create(business=business, from_stage=business.stage, to_stage='demo_ready', actor=user)
    business.stage, business.stage_changed_at = 'demo_ready', timezone.now()
    business.save(update_fields=['stage', 'stage_changed_at'])
    activity(business, 'stage_changed', labels.stage_activity('demo_ready'), user, business, client_visible=False)
    return True


def summary(business):
    return {'id': str(business.public_id), 'nombre': business.name, 'fase': business.get_stage_display(),
            'sector': business.category.name if business.category else '', 'direccion': business.address,
            'zona': business.zone, 'telefono': business.phone}


# --- Reading ----------------------------------------------------------------------

def buscar_clientes(user, texto=None, estado=None):
    require_admin(user)
    items = visible_queryset(Business, user).select_related('category')
    query = text(texto, 'texto', 200)
    if query:
        items = items.filter(Q(name__icontains=query) | Q(address__icontains=query) | Q(zone__icontains=query))
    key = stage_key(estado)
    items = items.filter(stage=key) if key else items.exclude(stage='discarded')
    items = list(items.order_by('name')[:SEARCH_LIMIT + 1])
    return {'clientes': [summary(b) for b in items[:SEARCH_LIMIT]], 'hay_mas': len(items) > SEARCH_LIMIT}


def study_data(study):
    if study is None:
        return None
    data = {name: getattr(study, field) for name, field in STUDY_FIELDS.items()}
    data.update({'borrador_ia': study.ai_draft, 'escrito_por_ia_en': study.ai_updated_at.isoformat() if study.ai_updated_at else None,
                 'fuentes': study.ai_sources, 'validado_en': study.validated_at.isoformat() if study.validated_at else None})
    return data


def estimation_data(item):
    if item is None:
        return None
    return {'fecha': item.created_at.isoformat(), 'mensual_bajo': str(item.monthly_low) if item.monthly_low is not None else None,
            'mensual_medio': str(item.monthly_mid) if item.monthly_mid is not None else None,
            'mensual_alto': str(item.monthly_high) if item.monthly_high is not None else None,
            'confianza': item.get_confidence_display(), 'banda': item.get_band_display() if item.band else '',
            'presupuesto_sugerido': str(item.suggested_budget) if item.suggested_budget is not None else None,
            'explicacion': item.explanation, 'retorno': item.payback}


def get_cliente(user, cliente_id):
    require_admin(user)
    from django.conf import settings
    from .demos import has_demo, latest
    from .hours import hours_status, week_summary
    b = business_for(user, cliente_id)
    data = summary(b)
    data.update({
        'fase_clave': b.stage, 'email': b.email, 'persona_de_contacto': b.contact, 'web_actual': b.current_website,
        'notas_internas': b.internal_notes, 'proxima_accion': b.next_action,
        'fecha_proxima_accion': b.next_action_date.isoformat() if b.next_action_date else None,
        'motivo_descarte': b.discarded_reason,
        'google': {'place_id': b.google_place_id, 'nota': b.google_rating, 'resenas': b.google_reviews,
                   'nivel_precios': b.google_price_level, 'maps': b.google_maps_url},
        'horario': {'estado': hours_status(b.opening_hours).text, 'semana': dict(week_summary(b.opening_hours))},
        'estudio': study_data(Study.objects.filter(business=b).first()),
        'ultima_estimacion': estimation_data(Estimation.objects.filter(business=b).first()),
        'url_ficha': settings.SITE_URL + b.get_absolute_url(),
    })
    if has_demo(b):
        from .views import demo_url
        version = latest(b)
        data['demo'] = {'url': demo_url(b), 'version': version.number if version else 1}
    return data


# --- Writing ----------------------------------------------------------------------

@transaction.atomic
def crear_cliente(user, nombre, direccion, place_id=None):
    require_admin(user)
    nombre = text(nombre, 'nombre', 200, required=True)
    direccion = text(direccion, 'direccion', 300)
    place_id = text(place_id, 'place_id', 255) or None
    if place_id and not PLACE_ID.match(place_id):
        raise ToolError('"place_id" no parece un id de Google Places.')
    if place_id:
        existing = Business.objects.filter(google_place_id=place_id).first()
        if existing is not None:
            return dict(summary(existing), ya_existia=True)
    b = Business(name=nombre, address=direccion, google_place_id=place_id)
    snapshot = PlaceSnapshot.objects.select_for_update().filter(place_id=place_id).first() if place_id else None
    if snapshot is not None:
        from .places import apply_snapshot
        b.category = snapshot.category
        b.phone = b.phone or snapshot.phone
        apply_snapshot(b, snapshot)
    b.save()
    if snapshot is not None and snapshot.status == 'new':
        snapshot.business, snapshot.status = b, 'pipeline'
        snapshot.save(update_fields=['business', 'status'])
    record(b, user, 'created', 'ficha creada.')
    return dict(summary(b), ya_existia=False)


def clean_sources(urls):
    if not isinstance(urls, list) or not all(isinstance(url, str) for url in urls):
        raise ToolError('"urls" debe ser una lista de enlaces.')
    if len(urls) > MAX_SOURCES_PER_CALL:
        raise ToolError(f'Como mucho {MAX_SOURCES_PER_CALL} enlaces por llamada.')
    check = URLValidator(schemes=['http', 'https'])
    cleaned = []
    for url in urls:
        url = url.strip()
        if len(url) > MAX_URL:
            raise ToolError(f'Un enlace supera los {MAX_URL} caracteres.')
        try:
            check(url)
        except ValidationError:
            raise ToolError(f'Enlace no válido (solo http o https): {url[:100]}')
        if url not in cleaned:
            cleaned.append(url)
    return cleaned


def add_sources(study, urls):
    merged = list(study.ai_sources or [])
    merged += [url for url in urls if url not in merged]
    if len(merged) > MAX_SOURCES:
        raise ToolError(f'El estudio ya tendría más de {MAX_SOURCES} fuentes.')
    study.ai_sources = merged


def mark_draft(study):
    study.ai_draft, study.ai_updated_at = True, timezone.now()
    study.validated_by, study.validated_at = None, None


@transaction.atomic
def guardar_estudio(user, cliente_id, campos, fuentes=None):
    require_admin(user)
    b = business_for(user, cliente_id, lock=True)
    if not isinstance(campos, dict) or not campos:
        raise ToolError('"campos" debe ser un objeto con al menos un campo del estudio.')
    unknown = sorted(set(campos) - set(STUDY_FIELDS))
    if unknown:
        raise ToolError(f'Campos desconocidos: {", ".join(unknown)}. Válidos: {", ".join(STUDY_FIELDS)}.')
    study = Study.objects.filter(business=b).first() or Study(business=b)
    for name, value in campos.items():
        field = Study._meta.get_field(STUDY_FIELDS[name])
        if field.get_internal_type() == 'BooleanField':
            if not isinstance(value, bool):
                raise ToolError(f'"{name}" debe ser true o false.')
        else:
            value = text(value, name, field.max_length or MAX_TEXT)
        setattr(study, field.name, value)
    if fuentes:
        add_sources(study, clean_sources(fuentes))
    mark_draft(study)
    study.save()
    record(b, user, 'study', f'estudio escrito como borrador ({", ".join(sorted(campos))}).', study)
    return {'id': str(b.public_id), 'estudio': study_data(study), 'aviso': 'Queda como «Borrador IA» hasta que alguien lo valide en la web.'}


@transaction.atomic
def guardar_fuentes(user, cliente_id, urls):
    require_admin(user)
    b = business_for(user, cliente_id, lock=True)
    cleaned = clean_sources(urls)
    if not cleaned:
        raise ToolError('"urls" está vacía.')
    study = Study.objects.filter(business=b).first() or Study(business=b)
    add_sources(study, cleaned)
    mark_draft(study)
    study.save()
    record(b, user, 'sources', f'{len(cleaned)} fuente{"s" if len(cleaned) != 1 else ""} añadida{"s" if len(cleaned) != 1 else ""} al estudio.', study)
    return {'id': str(b.public_id), 'fuentes': study.ai_sources}


def subir_demo(user, cliente_id, nombre_archivo, html):
    require_admin(user)
    from .demos import DemoError, save_demo
    from .views import demo_url
    if not isinstance(html, str):
        raise ToolError('"html" debe ser el documento HTML como texto.')
    with transaction.atomic():
        b = business_for(user, cliente_id, lock=True)
        try:
            version = save_demo(b, html, nombre_archivo, user, source='mcp')
        except DemoError as exc:
            raise ToolError(str(exc))
        url = demo_url(b)
        record(b, user, 'demo', f'{labels.DEMO_LOWER} publicada (versión {version.number}).', version)
        moved = move_to_demo_ready(b, user)
    return {'id': str(b.public_id), 'url': url, 'version': version.number, 'bytes': version.size,
            'fase': b.get_stage_display(), 'fase_cambiada': moved}


def estimation_input(datos):
    if not isinstance(datos, dict) or not datos:
        raise ToolError('"datos" debe ser un objeto con al menos un dato.')
    unknown = sorted(set(datos) - set(ESTIMATION_FIELDS))
    if unknown:
        raise ToolError(f'Datos desconocidos: {", ".join(unknown)}. Válidos: {", ".join(ESTIMATION_FIELDS)}.')
    form_data = {}
    for name, value in datos.items():
        field = ESTIMATION_FIELDS[name]
        if value is None:
            continue
        if field == 'services':
            if not isinstance(value, list) or any(v not in SERVICES for v in value):
                raise ToolError('"servicios" es una lista con desayuno, comida y/o cena.')
            form_data[field] = [SERVICES[v] for v in value]
        elif field == 'concurrency':
            if value not in CONCURRENCY:
                raise ToolError('"concurrencia" es baja, media o alta.')
            form_data[field] = CONCURRENCY[value]
        elif field == 'uses_delivery':
            if not isinstance(value, bool):
                raise ToolError('"usa_reparto" debe ser true o false.')
            if value:
                form_data[field] = 'on'
        elif field == 'declared_source':
            form_data[field] = text(value, name, 200)
        else:
            if isinstance(value, bool) or not isinstance(value, (int, float, str)):
                raise ToolError(f'"{name}" debe ser un número.')
            try:
                Decimal(str(value))
            except InvalidOperation:
                raise ToolError(f'"{name}" debe ser un número.')
            form_data[field] = str(value)
    return form_data


def guardar_estimacion(user, cliente_id, datos):
    require_admin(user)
    from .estimation_views import EstimationForm, save_estimation
    b = business_for(user, cliente_id)
    form = EstimationForm(estimation_input(datos))
    if not form.is_valid():
        spanish = {v: k for k, v in ESTIMATION_FIELDS.items()}
        problems = '; '.join(f'{spanish.get(field, field)}: {" ".join(errors)}' for field, errors in form.errors.items())
        raise ToolError('Datos no válidos: ' + problems)
    # save_estimation writes the history entry itself ("… (Claude)").
    item = save_estimation(user, b, form.cleaned_data, source='mcp')
    return dict(estimation_data(item), id=str(b.public_id))
