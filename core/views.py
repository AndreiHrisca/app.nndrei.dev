from django.shortcuts import redirect, render
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
    return render(request, 'core/business_table.html' if partial else 'core/list.html', context)

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
        return redirect(item)
    return render(request,'core/form.html',{'form':form,'title':'Nuevo lead'})

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
        form.save()
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
             .select_related('category')
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
    return {'title': 'Clientes', 'businesses': items, 'query': query,
            'show_study': request.user.role in STUDY_ROLES,
            'stages': STAGES, 'stage': stage,
            'categories': SearchCategory.objects.filter(active=True).order_by('name'), 'category': category,
            'sort': ('-' if descending else '') + key,
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
from .demos import DEMO_HEADERS, delete_demo, has_demo, read_demo, save_demo
from .forms import DemoForm


def demo_context(business):
    from django.conf import settings
    from django.urls import reverse
    url = settings.SITE_URL + reverse('demo_public', args=[business.public_id]) if has_demo(business) else ''
    return {'demo_url': url}


def demo_public(request, public_id):
    """The demo itself, reachable without signing in: the UUID is the key.

    A missing business and a business without a demo are the same 404.
    """
    b = Business.objects.filter(public_id=public_id).first()
    if b is None or not has_demo(b):
        raise Http404
    response = HttpResponse(read_demo(b), content_type='text/html; charset=utf-8')
    for header, value in DEMO_HEADERS.items():
        response[header] = value
    return response


@roles('admin')
@require_POST
def demo_upload(request, public_id):
    b = scoped(Business, request.user, public_id)
    form = DemoForm(request.POST, request.FILES)
    if not form.is_valid():
        return render(request, 'core/form.html', {'form': form, 'title': 'Subir demo'}, status=400)
    replacing = has_demo(b)
    save_demo(b, form.cleaned_data['file'])
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
