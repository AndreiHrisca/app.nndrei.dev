from django.shortcuts import redirect, render
from . import labels
from .access import scoped
from .auth_views import roles
from .models import Business, ClientCharge, Proposal, ProposalVersion

@roles('admin', 'developer', 'accountant', 'client')
def home(request):
    if request.user.role == 'client':
        return redirect('portal')
    if request.user.role != 'admin':
        return redirect('businesses')
    from .pipeline_views import pipeline
    return pipeline(request)

@roles('admin', 'developer', 'accountant')
def businesses(request):
    context = business_list_context(request)
    # The search box and the filters swap only the table, not the whole page.
    partial = request.headers.get('HX-Request') and not request.headers.get('HX-Boosted')
    response = render(request, 'core/business_table.html' if partial else 'core/list.html', context)
    if partial and ('lat' in request.GET or 'lng' in request.GET):
        # Where you are standing never reaches the address bar or the history.
        query = request.GET.copy()
        query.pop('lat', None); query.pop('lng', None)
        response['HX-Push-Url'] = request.path + ('?' + query.urlencode() if query else '')
    return response


def near_point(request):
    """The browser's position for "Cerca de mí", or None if absent or nonsense."""
    try:
        lat, lng = float(request.GET['lat']), float(request.GET['lng'])
    except (KeyError, ValueError):
        return None
    return (lat, lng) if -90 <= lat <= 90 and -180 <= lng <= 180 else None

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST
from .models import Request, Document, ActivityEvent, AccessLink, Website, REQUEST_STATES
from .forms import BusinessForm, RequestForm, DocumentForm, AccessLinkForm, WebsiteForm
from .services import activity, change_request, REQUEST_TRANSITIONS

@roles('admin')
def business_create(request):
    form = BusinessForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        item = form.save()
        activity(item,'created','Ficha de negocio creada.',request.user,client_visible=False)
        from .geo_sources import queue_placing
        queue_placing(item)
        return redirect(item)
    return render(request,'core/form.html',{'form':form,'title':labels.NEW_BUSINESS_TITLE})

@roles('admin','developer','accountant')
def business_detail(request,public_id):
    b = scoped(Business,request.user,public_id)
    if request.user.role == 'accountant':
        return render(request,'core/business_fiscal.html',{'business':b,'title':b.name,'charges':ClientCharge.objects.visible_to(request.user).filter(business=b)})
    context=business_context(request,b)
    from .services import STAGE_TRANSITIONS
    from .models import STAGES
    context['transitions']=[(k,v) for k,v in STAGES if k in STAGE_TRANSITIONS.get(b.stage,set())]
    from .models import StageChange
    context['stage_changes']=StageChange.objects.visible_to(request.user).filter(business=b).order_by('occurred_at')
    context['request_labels']=labels.REQUEST_STATE_LABELS
    from .hours_views import hours_context
    context.update(hours_context(request.user,b))
    from .forms import VisitLogForm
    from .visits import directions
    context.update({'go':directions(b),'visit_form':VisitLogForm(),'visit_results':labels.VISIT_RESULTS})
    if request.user.role=='admin':
        from .estimation_views import estimation_context
        context.update(estimation_context(request.user,b))
    return render(request,'core/business.html',context)

def business_context(request,b,preview=False):
    events = ActivityEvent.objects.visible_to(request.user).filter(business=b)
    if preview:
        events = events.filter(client_visible=True)
    context = {'business':b,'title':b.name,'events':events[:100],'requests':Request.objects.visible_to(request.user).filter(business=b),'documents':Document.objects.visible_to(request.user).filter(business=b),'access_links':AccessLink.objects.visible_to(request.user).filter(business=b) if not preview else [],'website':Website.objects.visible_to(request.user).filter(business=b).first(),'form':RequestForm(),'preview':preview, 'charges':ClientCharge.objects.visible_to(request.user).filter(business=b), 'proposals':Proposal.objects.visible_to(request.user).filter(business=b,pk__in=ProposalVersion.objects.exclude(sent_at__isnull=True,historical_status='').values('proposal_id'))}
    from .monitoring import uptime_context
    context.update(uptime_context(request.user,context['website']))
    context.update(demo_context(b))
    if not preview and request.user.role in STUDY_ROLES:
        from .models import Study
        context.update(study_context(request.user,b,Study.objects.visible_to(request.user).filter(business=b).first()))
    return context

@roles('admin')
def business_edit(request,public_id):
    b = scoped(Business,request.user,public_id)
    form = BusinessForm(request.POST or None,instance=b)
    if request.method == 'POST' and form.is_valid():
        item = form.save(commit=False)
        if 'address' in form.changed_data and item.latitude is None:
            item.geocoded_at = None  # A new address deserves one new lookup.
        item.save()
        if 'address' in form.changed_data or item.city_id is None:
            from .geo_sources import queue_placing
            queue_placing(item)
        return redirect(b)
    return render(request,'core/form.html',{'form':form,'title':'Editar '+b.name})

@roles('client','admin')
def portal(request,public_id=None):
    bs = Business.objects.visible_to(request.user)
    if request.user.role == 'admin' and public_id is None:
        raise Http404
    b = scoped(Business,request.user,public_id) if public_id else bs.first()
    if not b:
        return render(request,'core/no_business.html',{'title':'Mi negocio'})
    context = business_context(request,b,preview=request.user.role=='admin')
    context['businesses'] = bs
    context['title'] = 'Mi negocio'
    return render(request,'core/portal.html',context)

@roles('admin','client')
@require_POST
def request_create(request,public_id):
    b = scoped(Business,request.user,public_id)
    form = RequestForm(request.POST)
    if form.is_valid():
        with transaction.atomic():
            item = form.save(commit=False)
            item.business, item.created_by = b, request.user
            item.save()
            activity(b,'request_created','Nueva petición: '+item.title,request.user,item)
            from .notifications import notify
            from django.conf import settings
            transaction.on_commit(lambda: notify('Nueva petición',[settings.ADMIN_EMAIL],b.name+': '+item.title,settings.SITE_URL+b.get_absolute_url()))
        messages.success(request,'Petición creada.')
        return redirect('portal_business' if request.user.role=='client' else 'business_detail',public_id=b.public_id)
    return render(request,'core/form.html',{'form':form,'title':'Nueva petición'},status=400)

@roles('admin','developer')
@require_POST
def request_status(request,public_id):
    try:
        item = change_request(request.user,public_id,request.POST.get('status'),request.POST.get('reason',''))
    except ValidationError as exc:
        return render(request,'core/error.html',{'error':'; '.join(exc.messages)},status=400)
    return redirect(item.business)

@roles('admin','developer')
def business_resource(request,public_id,kind):
    b = scoped(Business,request.user,public_id)
    mapping = {'document':(DocumentForm,Document),'access':(AccessLinkForm,AccessLink),'website':(WebsiteForm,Website)}
    if kind not in mapping:
        raise Http404
    form_class, model = mapping[kind]
    instance = Website.objects.visible_to(request.user).filter(business=b).first() if kind=='website' else None
    old_domain = instance.domain if instance else ''
    old_launch = instance.launched_at if instance else None
    form = form_class(request.POST or None,request.FILES or None,instance=instance)
    if request.method == 'POST' and form.is_valid():
        with transaction.atomic():
            item = form.save(commit=False)
            item.business = b
            if kind=='document':
                item.uploaded_by = request.user
            item.save()
            if kind=='document':
                activity(b,'document_uploaded','Documento subido: '+item.get_kind_display(),request.user,item)
            if kind=='website' and item.domain and old_domain!=item.domain:
                activity(b,'domain_registered','Dominio registrado: '+item.domain,request.user,item)
            if kind=='website' and item.launched_at and old_launch!=item.launched_at:
                activity(b,'launched','La web está en producción.',request.user,item)
        return redirect(b)
    return render(request,'core/form.html',{'form':form,'title':{'document':'Subir documento','access':'Enlace al gestor de contraseñas','website':'Configurar web'}[kind]})

@roles('admin','developer','accountant','client')
def document_download(request,public_id):
    doc = scoped(Document,request.user,public_id)
    response = FileResponse(doc.file.open('rb'),as_attachment=True,filename=doc.file.name.rsplit('/',1)[-1])
    response['Cache-Control'] = 'private, no-store'
    return response

@roles('client')
def portal_list(request,kind):
    if kind=='peticiones':
        items = Request.objects.visible_to(request.user).select_related('business')
    elif kind=='documentos':
        items = Document.objects.visible_to(request.user).select_related('business')
    else:
        raise Http404
    return render(request,'core/portal_list.html',{'title':kind.capitalize(),'items':items,'kind':kind})

@roles('admin','developer')
def document_edit(request,public_id):
    from .forms import DocumentStatusForm
    doc=scoped(Document,request.user,public_id)
    form=DocumentStatusForm(request.POST or None,instance=doc)
    if request.method=='POST' and form.is_valid():
        with transaction.atomic():
            form.save()
            activity(doc.business,'document_status','Documento '+doc.get_kind_display()+': '+doc.get_status_display(),request.user,doc)
        return redirect(doc.business)
    return render(request,'core/form.html',{'form':form,'title':'Estado del documento'})

@roles('admin')
def portal_preview(request,public_id):
    return portal(request,public_id)

@roles('admin')
def request_plan(request,public_id):
    from .forms import RequestPlanningForm
    item=scoped(Request,request.user,public_id)
    form=RequestPlanningForm(request.POST or None,instance=item)
    form.fields['proposal'].queryset=Proposal.objects.visible_to(request.user).filter(business=item.business)
    if request.method=='POST' and form.is_valid():
        with transaction.atomic():
            form.save()
            activity(item.business,'request_planned','Planificación actualizada: '+item.title,request.user,item)
        return redirect(item.business)
    return render(request,'core/form.html',{'title':'Planificar petición','form':form})

# --- Clientes: one worked table instead of three bare columns -----------------

from django.db.models import Case, Exists, IntegerField, OuterRef, Subquery, Value, When
from .models import PlaceSnapshot, SearchCategory, STAGES, STAGE_ORDER

# Header key -> field the queryset is ordered by. Anything else is ignored, so
# the sort parameter can never reach the ORM as a raw field name.
BUSINESS_SORTS = {'negocio': 'name', 'fase': 'stage_rank', 'actividad': 'last_activity', 'proxima': 'next_action_date'}


def business_list_context(request):
    snapshots = PlaceSnapshot.objects.filter(business=OuterRef('pk')).order_by('-fetched_at')
    versions = ProposalVersion.objects.filter(proposal__business=OuterRef('pk')).order_by('-proposal_id', '-number')
    items = (Business.objects.visible_to(request.user)
             .select_related('category', 'city', 'neighbourhood')
             .annotate(
                 last_activity=Subquery(ActivityEvent.objects.filter(business=OuterRef('pk')).order_by('-occurred_at', '-pk').values('occurred_at')[:1]),
                 has_snapshot=Exists(PlaceSnapshot.objects.filter(business=OuterRef('pk'))),
                 snapshot_rating=Subquery(snapshots.values('rating')[:1]),
                 snapshot_reviews=Subquery(snapshots.values('reviews')[:1]),
                 snapshot_photos=Subquery(snapshots.values('photos')[:1]),
                 snapshot_has_website=Subquery(snapshots.values('has_website')[:1]),
                 budget=Subquery(versions.values('development_total')[:1]),
                 # Sorting by Fase follows the pipeline order, not the alphabet.
                 stage_rank=Case(*[When(stage=key, then=Value(index)) for key, index in STAGE_ORDER.items()], default=Value(len(STAGE_ORDER)), output_field=IntegerField()),
             ))
    if request.user.role in STUDY_ROLES:
        studies = Study.objects.filter(business=OuterRef('pk'))
        items = items.annotate(has_study=Exists(studies), study_sells=Subquery(studies.values('sells')[:1]), study_needs=Subquery(studies.values('needs')[:1]))
    query = request.GET.get('q', '').strip()
    if query:
        items = items.filter(name__icontains=query)
    stage = request.GET.get('stage', '')
    if stage in dict(STAGES):
        items = items.filter(stage=stage)
    else:
        # Discarded businesses are out of the way unless asked for by their stage.
        items = items.exclude(stage='discarded')
    category = request.GET.get('category', '')
    if category.isdigit():
        items = items.filter(category_id=category)
    sort = request.GET.get('sort', 'negocio')
    descending = sort.startswith('-')
    field = BUSINESS_SORTS.get(sort.lstrip('-'), 'name')
    key = sort.lstrip('-') if sort.lstrip('-') in BUSINESS_SORTS else 'negocio'
    ordering = ('-' if descending else '') + field
    # Businesses with no activity or no planned action sort last either way.
    items = items.order_by(nulls_last(ordering), 'name' if field != 'name' else 'pk')
    # Hours and distance are worked out here, in Python: the list is short and
    # unpaginated, and "open now" depends on each business's own week.
    from .hours import format_distance, hours_status
    from .places import distance_m
    from django.utils import timezone
    now = timezone.now()
    point = near_point(request) if sort == 'cerca' else None
    open_now = request.GET.get('abierto') == '1'
    rows = list(items)
    for b in rows:
        b.hours_state = hours_status(b.opening_hours, now)
        b.distance = distance_m(point[0], point[1], b.latitude, b.longitude) if point and b.latitude is not None and b.longitude is not None else None
        b.distance_text = format_distance(b.distance)
    if open_now:
        rows = [b for b in rows if b.hours_state.is_open]
    if point:
        # Businesses without coordinates keep their order, after the located ones.
        rows.sort(key=lambda b: (b.distance is None, b.distance or 0))
    if sort == 'cerca':
        key, descending = 'cerca', False
    return {'title': 'Clientes', 'businesses': rows, 'open_now': open_now, 'near': bool(point), 'clients_subtitle': labels.CLIENTS_SUBTITLE, 'query': query,
            'show_study': request.user.role in STUDY_ROLES,
            'stages': STAGES, 'stage': stage,
            'categories': SearchCategory.objects.filter(active=True).order_by('name'), 'category': category,
            'sort': ('-' if descending else '') + key if key != 'cerca' else 'cerca',
            # Clicking the current column flips the direction; any other column starts ascending.
            'sort_toggles': {name: ('-' + name if name == key and not descending else name) for name in BUSINESS_SORTS},
            'sort_arrows': {name: ('▼' if descending else '▲') if name == key else '' for name in BUSINESS_SORTS}}


def nulls_last(ordering):
    """Order by `ordering` keeping empty values at the bottom in both directions."""
    from django.db.models import F
    expression = F(ordering.lstrip('-'))
    return expression.desc(nulls_last=True) if ordering.startswith('-') else expression.asc(nulls_last=True)


# --- Estudio previo: the groundwork done before visiting a business ----------

from .models import Study
from .forms import StudyForm

STUDY_ROLES = ('admin', 'developer')


def study_context(user, business, item, form=None):
    """Everything the study card needs, whether it renders alone or inside the page."""
    missing = item.missing() if item else Study().missing()
    return {'business': business, 'study': item, 'study_form': form, 'study_missing': missing,
            # Only an admin moves a business along the pipeline, and only once the
            # study answers the two questions a visit depends on.
            'can_mark_profile': user.role == 'admin' and business.stage == 'found' and not missing}


def study_card(request, business, item, form=None):
    return render(request, 'core/study.html', study_context(request.user, business, item, form))


@roles(*STUDY_ROLES)
def study(request, public_id):
    b = scoped(Business, request.user, public_id)
    item = Study.objects.visible_to(request.user).filter(business=b).first()
    if request.method == 'POST':
        creating = item is None
        form = StudyForm(request.POST, instance=item)
        if form.is_valid():
            with transaction.atomic():
                item = form.save(commit=False)
                item.business = b
                item.save()
                activity(b, 'study_created' if creating else 'study_updated',
                         'Estudio previo creado.' if creating else 'Estudio previo actualizado.',
                         request.user, item, client_visible=False)
            return study_card(request, b, item)
        return study_card(request, b, item, form)
    if request.GET.get('cancel'):
        return study_card(request, b, item)
    return study_card(request, b, item, StudyForm(instance=item))


@roles(*STUDY_ROLES)
@require_POST
def study_validate(request, public_id):
    """"Validar estudio": a person has read the AI draft and takes it as theirs."""
    from django.utils import timezone
    b = scoped(Business, request.user, public_id)
    with transaction.atomic():
        item = Study.objects.visible_to(request.user).select_for_update().filter(business=b).first()
        if item is not None and item.ai_draft:
            item.ai_draft, item.validated_by, item.validated_at = False, request.user, timezone.now()
            item.save(update_fields=['ai_draft', 'validated_by', 'validated_at', 'updated_at'])
            activity(b, 'study_validated', 'Estudio validado (era un borrador de la IA).', request.user, item, client_visible=False)
    return study_card(request, b, item) if request.headers.get('HX-Request') else redirect(b)


# --- Phone screens -------------------------------------------------------------

@roles('admin', 'developer', 'accountant', 'client')
def more(request):
    """The "Más" tab: account, security and the sections that do not fit in the bottom bar."""
    return render(request, 'core/more.html', {'title': 'Cuenta' if request.user.role == 'client' else 'Más'})


@roles('admin', 'developer')
@require_POST
def business_note(request, public_id):
    """"Nota rápida" from the card: one line into the internal history."""
    b = scoped(Business, request.user, public_id)
    text = request.POST.get('text', '').strip()
    if text:
        activity(b, 'note', 'Nota: ' + text[:2000], request.user, client_visible=False)
        messages.success(request, 'Nota guardada en el historial.')
    return redirect(b)


# --- Demo: a self-contained HTML mockup to show the client --------------------

from django.http import HttpResponse
from .demos import DEMO_HEADERS, DemoError, delete_demo, has_demo, latest, qr_png, read_demo, save_demo
from .forms import DemoForm


def demo_url(business):
    from django.conf import settings
    from django.urls import reverse
    from .demos import ensure_slug
    return settings.SITE_URL + reverse('demo_slug', args=[ensure_slug(business)])


def demo_context(business):
    if not has_demo(business):
        return {'demo_url': ''}
    from .models import DemoVersion
    return {'demo_url': demo_url(business), 'demo_version': latest(business),
            'demo_versions': DemoVersion.objects.filter(business=business).order_by('-number')[:5]}


def serve_demo(business):
    response = HttpResponse(read_demo(business), content_type='text/html; charset=utf-8')
    for header, value in DEMO_HEADERS.items():
        response[header] = value
    return response


def demo_by_slug(request, slug):
    """The public demo, /demos/<slug>/: no session needed, nothing else of the app reachable.

    A missing slug and a business without a demo are the same 404.
    """
    b = Business.objects.filter(demo_slug=slug).first()
    if b is None or not has_demo(b):
        raise Http404
    return serve_demo(b)


def demo_public(request, public_id):
    """Old address (/clientes/<uuid>/demo-cliente/): links already sent keep working."""
    b = Business.objects.filter(public_id=public_id).first()
    if b is None or not has_demo(b):
        raise Http404
    from django.http import HttpResponsePermanentRedirect
    return HttpResponsePermanentRedirect(demo_url(b))


@roles('admin', 'developer')
def demo_qr(request, public_id):
    """QR of the public demo address, as a PNG to download and print or send."""
    b = scoped(Business, request.user, public_id)
    if not has_demo(b):
        raise Http404
    response = HttpResponse(qr_png(demo_url(b)), content_type='image/png')
    if request.GET.get('descargar'):
        response['Content-Disposition'] = f'attachment; filename="qr-{b.demo_slug}.png"'
    response['Cache-Control'] = 'private, no-store'
    return response


@roles('admin')
@require_POST
def demo_upload(request, public_id):
    b = scoped(Business, request.user, public_id)
    form = DemoForm(request.POST, request.FILES)
    if not form.is_valid():
        return render(request, 'core/form.html', {'form': form, 'title': 'Subir demo'}, status=400)
    replacing = has_demo(b)
    try:
        save_demo(b, form.cleaned_data['file'], request.FILES['file'].name, request.user)
    except DemoError as exc:
        form.add_error('file', str(exc))
        return render(request, 'core/form.html', {'form': form, 'title': 'Subir demo'}, status=400)
    activity(b, 'demo_uploaded', 'Demo reemplazada.' if replacing else 'Demo subida.', request.user, client_visible=False)
    messages.success(request, 'Demo reemplazada.' if replacing else 'Demo subida.')
    return redirect(b)


@roles('admin')
@require_POST
def demo_delete(request, public_id):
    b = scoped(Business, request.user, public_id)
    if has_demo(b):
        delete_demo(b)
        activity(b, 'demo_deleted', 'Demo eliminada.', request.user, client_visible=False)
        messages.success(request, 'Demo eliminada.')
    return redirect(b)
