from datetime import timedelta
from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count,F,Min,OuterRef,Prefetch,Q,Subquery
from django.db.models.functions import Least
from django.http import HttpResponse
from django.shortcuts import redirect,render
from django.utils import timezone
from django.views.decorators.http import require_POST
from . import labels
from .access import scoped
from .auth_views import roles
from .models import Business,Estimation,STAGES,FollowUp,Visit,SearchRun,SearchZone,SearchCategory,PlaceSnapshot,PlacesUsage
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
            .select_related('category')
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

def search_queryset(request):
    all_places=PlaceSnapshot.objects.visible_to(request.user).filter(business_status='OPERATIONAL',fetched_at__gte=timezone.now()-timedelta(days=30))
    places=all_places
    for field in ['zone','category']:
        value=request.GET.get(field,'')
        if value.isdigit(): places=places.filter(**{field+'_id':value})
    status=request.GET.get('status','')
    if status: places=places.filter(status=status)
    if request.GET.get('quality')=='no_web': places=places.filter(has_website=False)
    if request.GET.get('quality')=='poor': places=places.filter(photos__lt=5)
    if request.GET.get('radius','').isdigit(): places=places.filter(distance_m__lte=int(request.GET['radius']))
    return all_places,places

def search_counters(all_places):
    return {'no_web':all_places.filter(has_website=False).count(),'poor':all_places.filter(photos__lt=5).count(),'in_pipeline':all_places.filter(status='pipeline').count(),'discarded':all_places.filter(status='discarded').count()}

@roles('admin')
def search(request):
    all_places,places=search_queryset(request)
    context={'title':'Buscar clientes','places':Paginator(places.select_related('zone','category','business').order_by('-opportunity_score','pk'),50).get_page(request.GET.get('page')),'zones':SearchZone.objects.visible_to(request.user),'categories':SearchCategory.objects.visible_to(request.user),'run':SearchRun.objects.visible_to(request.user).order_by('-pk').first(),'usage':PlacesUsage.objects.filter(month=timezone.localdate().replace(day=1)).first(),'limit':settings.PLACES_MONTHLY_LIMIT}
    context.update(search_counters(all_places))
    context.update(refresh_texts(context['usage'].requests if context['usage'] else 0))
    return render(request,'core/search.html',context)

def refresh_texts(used):
    """What pressing "Actualizar" will cost, said before it is pressed."""
    from .places import pending_slices
    pending=len(pending_slices())
    left=max(0,settings.PLACES_MONTHLY_LIMIT-used)
    days=settings.PLACES_REFRESH_MIN_DAYS
    if not pending:
        return {'refresh_hint':f'Todo se ha consultado en los últimos {days} días: actualizar ahora no gasta llamadas.',
                'refresh_confirm':f'Todo está al día (consultado hace menos de {days} días). No se gastará ninguna llamada. ¿Seguir?'}
    return {'refresh_hint':f'Actualizar consultará {pending} combinaciones de zona y sector sin revisar en {days} días (entre {pending} y {pending*3} llamadas; quedan {left} este mes).',
            'refresh_confirm':f'Se consultarán {pending} combinaciones de zona y sector: entre {pending} y {pending*3} llamadas de las {left} que te quedan este mes. ¿Seguir?'}

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
    initial={'name':snapshot.name,'category':snapshot.category_id,'address':snapshot.address,'phone':snapshot.phone,'zone':snapshot.zone.name,'current_website':snapshot.website_url}
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
            b=Business(name=snapshot.name,category=snapshot.category,address=snapshot.address,phone=snapshot.phone,zone=snapshot.zone.name,current_website=snapshot.website_url)
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
