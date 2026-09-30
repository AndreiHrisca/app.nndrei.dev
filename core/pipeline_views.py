from datetime import timedelta
from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count,F,Min,OuterRef,Prefetch,Q,Subquery
from django.db.models.functions import Least
from django.http import HttpResponse
from django.shortcuts import get_object_or_404,redirect,render
from django.utils import timezone
from django.views.decorators.http import require_POST
from . import labels
from .access import scoped
from .auth_views import roles
from .models import Business,City,Estimation,Neighbourhood,STAGES,FollowUp,Visit,SearchArea,SearchRun,SearchCategory,PlaceSnapshot,PlacesUsage
from .forms import BusinessForm,FollowUpForm
from .services import transition_business,activity,STAGE_TRANSITIONS

@roles('admin')
def pipeline(request):
    bs=Business.objects.visible_to(request.user)
    counts=dict(bs.values_list('stage').annotate(count=Count('pk')))
    # Tapping a stage tile narrows "Clientes en curso" to it; tapping it again clears it.
    stage=request.GET.get('stage','')
    if stage not in dict(STAGES) or stage=='discarded': stage=''
    active=bs.exclude(stage='discarded')
    if stage: active=active.filter(stage=stage)
    pending=FollowUp.objects.filter(done=False).select_related('owner').order_by('date','pk')
    # The latest estimate of each business, read in the same query as the list.
    latest=Estimation.objects.filter(business=OuterRef('pk')).order_by('-created_at','-pk')
    # Nearest next step first (pending follow-up or the card's own next action); undated last.
    active=(active.annotate(next_date=Least(Min('followup__date',filter=Q(followup__done=False)),F('next_action_date')))
            .annotate(estimate_monthly=Subquery(latest.values('monthly_mid')[:1]),estimate_budget=Subquery(latest.values('suggested_budget')[:1]))
            .order_by(F('next_date').asc(nulls_last=True),F('stage_changed_at').desc(nulls_last=True),'pk')
            .select_related('category','city','neighbourhood')
            .prefetch_related(Prefetch('followup_set',queryset=pending,to_attr='pending_followups'),'assignments__developer'))
    return render(request,'core/pipeline.html',{'title':labels.PIPELINE,'businesses':active,'stage_filter':stage,'stage_label':dict(STAGES).get(stage,''),'stages':[{'key':key,'label':label,'count':counts.get(key,0)} for key,label in STAGES],'today':timezone.localdate()})

@roles('admin')
@require_POST
def stage(request,public_id):
    try:
        b=transition_business(request.user,public_id,request.POST.get('stage'),request.POST.get('reason',''))
    except ValidationError as exc:
        return render(request,'core/error.html',{'error':'; '.join(exc.messages)},status=400)
    return redirect(b)

@roles('admin')
def followup(request,public_id):
    b=scoped(Business,request.user,public_id)
    form=FollowUpForm(request.POST or None)
    form.fields['owner'].queryset=form.fields['owner'].queryset.filter(role__in=['admin','developer'],is_active=True)
    if request.method=='POST' and form.is_valid():
        item=form.save(commit=False); item.business=b; item.save()
        return redirect(b)
    return render(request,'core/form.html',{'form':form,'title':'Próxima acción'})

@roles('admin')
def visit(request,public_id):
    """"He ido". The sheet on the card posts here with HTMX; the plain page is the no-JS path."""
    from .forms import VisitLogForm
    from .visits import record_visit
    b=scoped(Business,request.user,public_id)
    form=VisitLogForm(request.POST or None)
    htmx=request.headers.get('HX-Request')
    if request.method=='POST' and form.is_valid():
        _,moved=record_visit(request.user,b.public_id,form.cleaned_data)
        messages.success(request,labels.visit_saved(moved))
        if htmx:
            response=HttpResponse(status=204)
            response['HX-Redirect']=b.get_absolute_url()
            return response
        return redirect(b)
    context={'form':form,'business':b,'visit_results':labels.VISIT_RESULTS}
    if htmx:
        return render(request,'core/visit_form.html',context)
    context['title']=labels.VISIT_TITLE+' · '+b.name
    return render(request,'core/visit_page.html',context)

@roles('admin')
@require_POST
def reactivate(request,public_id):
    from .visits import reactivate as reactivate_business
    try:
        b=reactivate_business(request.user,public_id)
    except ValidationError as exc:
        return render(request,'core/error.html',{'error':'; '.join(exc.messages)},status=400)
    messages.success(request,labels.REACTIVATE+': '+b.name+' vuelve a '+b.get_stage_display()+'.')
    return redirect(b)

@roles('admin')
@require_POST
def followup_done(request,public_id):
    item=scoped(FollowUp,request.user,public_id)
    item.done=True; item.save()
    return redirect('home')

def chosen_area(request):
    """(city, neighbourhood) from the filters, else the last ones used (kept in the session)."""
    params=request.GET
    if 'city' in params:
        city_id,barrio_id=params.get('city',''),params.get('barrio','')
        request.session['search_city'],request.session['search_barrio']=city_id,barrio_id
    else:
        city_id,barrio_id=request.session.get('search_city',''),request.session.get('search_barrio','')
    city=City.objects.filter(pk=city_id).first() if str(city_id).isdigit() else None
    barrio=Neighbourhood.objects.filter(pk=barrio_id,city=city).first() if city and str(barrio_id).isdigit() else None
    return city,barrio

def search_queryset(request):
    city,barrio=chosen_area(request)
    all_places=PlaceSnapshot.objects.visible_to(request.user).filter(business_status='OPERATIONAL',fetched_at__gte=timezone.now()-timedelta(days=30))
    # The summary cards follow the chosen city and neighbourhood too.
    if city: all_places=all_places.filter(city=city)
    if barrio: all_places=all_places.filter(neighbourhood=barrio)
    places=all_places
    category=request.GET.get('category','')
    if category.isdigit(): places=places.filter(category_id=category)
    status=request.GET.get('status','')
    if status: places=places.filter(status=status)
    if request.GET.get('quality')=='no_web': places=places.filter(has_website=False)
    if request.GET.get('quality')=='poor': places=places.filter(photos__lt=5)
    return all_places,places

def search_counters(all_places):
    return {'no_web':all_places.filter(has_website=False).count(),'poor':all_places.filter(photos__lt=5).count(),'in_pipeline':all_places.filter(status='pipeline').count(),'discarded':all_places.filter(status='discarded').count()}

@roles('admin')
def search(request):
    all_places,places=search_queryset(request)
    city,barrio=chosen_area(request)
    areas=SearchArea.objects.visible_to(request.user).select_related('city','neighbourhood').order_by('-active','city__name','neighbourhood__name')
    context={'title':'Buscar clientes','places':Paginator(places.select_related('city','neighbourhood','category','business').order_by('-opportunity_score','pk'),50).get_page(request.GET.get('page')),
             'city':city,'barrio':barrio,'neighbourhoods':list(city.neighbourhoods.all()) if city and city.neighbourhoods_fetched_at else None,
             'areas':areas,'categories':SearchCategory.objects.visible_to(request.user),'run':SearchRun.objects.visible_to(request.user).order_by('-pk').first(),
             'usage':PlacesUsage.objects.filter(month=timezone.localdate().replace(day=1)).first(),'limit':settings.PLACES_MONTHLY_LIMIT}
    context.update(search_counters(all_places))
    context.update(refresh_texts(context['usage'].requests if context['usage'] else 0))
    return render(request,'core/search.html',context)

def left_this_month():
    usage=PlacesUsage.objects.filter(month=timezone.localdate().replace(day=1)).first()
    return max(0,settings.PLACES_MONTHLY_LIMIT-(usage.requests if usage else 0))

def refresh_texts(used):
    """What pressing "Actualizar" will cost, said before it is pressed. Only active areas count."""
    from .places import estimate,pending_slices
    plan={}
    for area,category in pending_slices(): plan.setdefault(area,[]).append(category)
    pending=sum(len(c) for c in plan.values())
    low=sum(estimate(area,cats,locate=False)['low'] for area,cats in plan.items())
    high=sum(estimate(area,cats,locate=False)['high'] for area,cats in plan.items())
    left=max(0,settings.PLACES_MONTHLY_LIMIT-used)
    days=settings.PLACES_REFRESH_MIN_DAYS
    if not pending:
        return {'refresh_hint':f'Todas las zonas activas se han consultado en los últimos {days} días: actualizar ahora no gasta llamadas.',
                'refresh_confirm':f'Todo está al día (consultado hace menos de {days} días). No se gastará ninguna llamada. ¿Seguir?'}
    return {'refresh_hint':f'Actualizar consultará {pending} combinaciones de zona activa y sector sin revisar en {days} días (entre {low} y {high} llamadas; quedan {left} este mes).',
            'refresh_confirm':f'Se consultarán {pending} combinaciones de zona activa y sector: entre {low} y {high} llamadas de las {left} que te quedan este mes. ¿Seguir?'}

# --- Cities, neighbourhoods and search areas ----------------------------------------

@roles('admin')
def city_options(request):
    """Autocomplete for 8,000+ municipalities: name matches first, then the rest."""
    from .geo import normalize
    query=normalize(request.GET.get('q',''))
    cities=[]
    if len(query)>=2:
        starts=list(City.objects.filter(search_name__startswith=query).order_by('name')[:12])
        more=list(City.objects.filter(search_name__contains=query).exclude(pk__in=[c.pk for c in starts]).order_by('name')[:12-len(starts)])
        cities=starts+more
    return render(request,'core/search_city_options.html',{'cities':cities,'query':query,'prefix':request.GET.get('prefix','')})

@roles('admin')
def neighbourhood_options(request):
    """The neighbourhood picker for a city. The first time a city is opened, its
    neighbourhoods are fetched (Madrid's official ones, or OpenStreetMap's) and kept."""
    from .geo_sources import GeoError,ensure_neighbourhoods
    city_id=request.GET.get('city','')
    if not city_id.isdigit():
        return HttpResponse('Falta la ciudad.',status=400)
    city=get_object_or_404(City,pk=city_id)
    error=''
    try:
        neighbourhoods=list(ensure_neighbourhoods(city))
    except GeoError as exc:
        neighbourhoods,error=list(city.neighbourhoods.all()),str(exc)
    selected=request.GET.get('barrio','')
    return render(request,'core/search_barrio_select.html',{'city':city,'neighbourhoods':neighbourhoods,'selected':selected,'error':error,'prefix':request.GET.get('prefix','')})

class MissingArea(Exception):
    pass

def area_from(data):
    if not str(data.get('city','')).isdigit():
        raise MissingArea
    city=get_object_or_404(City,pk=data['city'])
    barrio=get_object_or_404(Neighbourhood,pk=data['barrio'],city=city) if str(data.get('barrio','')).isdigit() else None
    return city,barrio

def area_panel_context(request,city,barrio,category_id=''):
    from .geo_sources import GeoError
    from .places import estimate
    area=SearchArea.objects.filter(city=city,neighbourhood=barrio).first()
    categories=list(SearchCategory.objects.filter(active=True))
    if str(category_id).isdigit(): categories=[c for c in categories if str(c.pk)==str(category_id)]
    probe=area or SearchArea(city=city,neighbourhood=barrio)
    error=''
    try:
        numbers=estimate(probe,categories)
    except GeoError as exc:
        numbers,error=None,str(exc)
    return {'city':city,'barrio':barrio,'area':area,'estimate':numbers,'error':error,'category_count':len(categories),
            'category_id':category_id,'left':left_this_month(),'label':f'{barrio.name}, {city.name}' if barrio else f'{city.name} (toda la ciudad)'}

@roles('admin')
def area_panel(request):
    """What searching the chosen area would cost, and whether it is an active zone."""
    try:
        city,barrio=area_from(request.GET)
    except MissingArea:
        return HttpResponse('Elige una ciudad.',status=400)
    return render(request,'core/search_area_panel.html',area_panel_context(request,city,barrio,request.GET.get('category','')))

@roles('admin')
@require_POST
def area_search(request):
    """Search the chosen area now. Above PLACES_CONFIRM_ABOVE possible calls it must be confirmed."""
    from django_q.tasks import async_task
    try:
        city,barrio=area_from(request.POST)
    except MissingArea:
        return HttpResponse('Elige una ciudad.',status=400)
    category_id=request.POST.get('category','')
    context=area_panel_context(request,city,barrio,category_id)
    if context['estimate'] is None:
        return render(request,'core/search_area_panel.html',context,status=400)
    if context['estimate']['confirm'] and request.POST.get('confirmed')!='1':
        context['needs_confirmation']=True
        return render(request,'core/search_area_panel.html',context,status=400)
    with transaction.atomic():
        area,_=SearchArea.objects.get_or_create(city=city,neighbourhood=barrio)
        run=SearchRun.objects.filter(status__in=['queued','running'],created_at__gt=timezone.now()-timedelta(minutes=45)).first()
        if not run:
            run=SearchRun.objects.create()
            transaction.on_commit(lambda: async_task('core.places.refresh_places',run.pk,area.pk,int(category_id) if category_id.isdigit() else None))
    context.update(area=area,started=True)
    response=render(request,'core/search_area_panel.html',context)
    response.write(render(request,'core/search_progress.html',{'run':run,'oob':True}).content.decode())
    return response

@roles('admin')
@require_POST
def area_activate(request):
    """"Añadir a zonas activas": "Actualizar" will go through it from now on."""
    try:
        city,barrio=area_from(request.POST)
    except MissingArea:
        return HttpResponse('Elige una ciudad.',status=400)
    area,_=SearchArea.objects.get_or_create(city=city,neighbourhood=barrio)
    area.active=True; area.save(update_fields=['active'])
    if request.headers.get('HX-Request'):
        return render(request,'core/search_area_panel.html',area_panel_context(request,city,barrio,request.POST.get('category','')))
    messages.success(request,f'{area} está en tus zonas activas.')
    return redirect('search')

@roles('admin')
@require_POST
def area_toggle(request,pk):
    """Stop (or resume) refreshing a zone. Its businesses and results stay."""
    area=get_object_or_404(SearchArea.objects.visible_to(request.user),pk=pk)
    area.active=not area.active; area.save(update_fields=['active'])
    messages.success(request,f'{area}: {"se actualizará con «Actualizar»" if area.active else "ya no se actualizará; sus negocios se quedan"}.')
    return redirect('search')

def opportunity_response(request,snapshot):
    """Swap the acted-on row in place and refresh the counters out of band."""
    all_places,_=search_queryset(request)
    context={'p':snapshot,'oob':True}
    context.update(search_counters(all_places))
    # The phone list posts from a card and gets a card back; the table gets its row.
    return render(request,'core/search_card.html' if request.POST.get('layout')=='card' else 'core/search_row.html',context)

@roles('admin')
@require_POST
def refresh(request):
    from django_q.tasks import async_task
    with transaction.atomic():
        # Serialize refresh launches across web workers without a second queue backend.
        month=timezone.localdate().replace(day=1)
        usage,_=PlacesUsage.objects.get_or_create(month=month)
        PlacesUsage.objects.select_for_update().get(pk=usage.pk)
        run=SearchRun.objects.filter(status__in=['queued','running'],created_at__gt=timezone.now()-timedelta(minutes=45)).first()
        if not run:
            run=SearchRun.objects.create()
            transaction.on_commit(lambda: async_task('core.places.refresh_places',run.pk))
    return render(request,'core/search_progress.html',{'run':run})

@roles('admin')
def search_progress(request,public_id):
    run=scoped(SearchRun,request.user,public_id)
    response=render(request,'core/search_progress.html',{'run':run})
    if run.status in ['done','failed']: response['HX-Refresh']='true'
    return response

@roles('admin')
def place_capture(request,public_id):
    """GET confirms the data in a form; POST captures the opportunity straight away.

    HTMX posts the button, so the table updates without leaving the page; the
    plain form remains as the no-JavaScript path.
    """
    snapshot=scoped(PlaceSnapshot,request.user,public_id)
    if snapshot.status!='new':
        return opportunity_response(request,snapshot) if request.headers.get('HX-Request') else redirect('search')
    if request.method=='POST' and not request.POST.get('name'):
        snapshot=capture(request,snapshot)
        if request.headers.get('HX-Request'):
            return opportunity_response(request,snapshot)
        if not snapshot.business_id:
            return redirect('search')
        messages.success(request,labels.capture_message(snapshot.name))
        return redirect(snapshot.business)
    initial={'name':snapshot.name,'category':snapshot.category_id,'address':snapshot.address,'phone':snapshot.phone,'current_website':snapshot.website_url}
    form=BusinessForm(request.POST or None,initial=initial)
    if request.method=='POST' and form.is_valid():
        snapshot=capture(request,snapshot,form)
        if not snapshot.business_id:
            return redirect('search')
        messages.success(request,labels.capture_message(snapshot.name))
        return redirect(snapshot.business)
    return render(request,'core/form.html',{'form':form,'title':'Confirma los datos antes de '+labels.CAPTURE.lower()})

def capture(request,snapshot,form=None):
    with transaction.atomic():
        snapshot=PlaceSnapshot.objects.select_for_update().get(pk=snapshot.pk)
        if snapshot.status!='new':
            return snapshot
        if form is not None:
            b=form.save(commit=False)
        else:
            b=Business(name=snapshot.name,category=snapshot.category,address=snapshot.address,phone=snapshot.phone,current_website=snapshot.website_url)
        b.google_place_id=snapshot.place_id
        from .places import apply_snapshot
        apply_snapshot(b,snapshot)
        b.save()
        snapshot.business=b; snapshot.status='pipeline'; snapshot.save()
        activity(b,'created',labels.CAPTURE_ACTIVITY,request.user,client_visible=False)
    return snapshot

@roles('admin')
@require_POST
def place_discard(request,public_id):
    snapshot=scoped(PlaceSnapshot,request.user,public_id,status='new')
    snapshot.status='discarded'; snapshot.notes=request.POST.get('notes',''); snapshot.save()
    if request.headers.get('HX-Request'):
        return opportunity_response(request,snapshot)
    messages.success(request,labels.discard_message(snapshot.name))
    return redirect('search')
