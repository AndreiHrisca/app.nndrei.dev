from datetime import timedelta
from django.conf import settings
from django.contrib import messages
from django.core.paginator import Paginator
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count,F,Min,Prefetch,Q
from django.db.models.functions import Least
from django.shortcuts import redirect,render
from django.utils import timezone
from django.views.decorators.http import require_POST
from . import labels
from .access import scoped
from .auth_views import roles
from .models import Business,STAGES,FollowUp,Visit,SearchRun,SearchZone,SearchCategory,PlaceSnapshot,PlacesUsage
from .forms import BusinessForm,FollowUpForm,VisitForm
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
    # Nearest next step first (pending follow-up or the card's own next action); undated last.
    active=(active.annotate(next_date=Least(Min('followup__date',filter=Q(followup__done=False)),F('next_action_date')))
            .order_by(F('next_date').asc(nulls_last=True),F('stage_changed_at').desc(nulls_last=True),'pk')
            .select_related('category')
            .prefetch_related(Prefetch('followup_set',queryset=pending,to_attr='pending_followups'),'assignments__developer'))
    return render(request,'core/pipeline.html',{'title':'Pipeline','businesses':active,'stage_filter':stage,'stage_label':dict(STAGES).get(stage,''),'stages':[{'key':key,'label':('Aprobado' if key=='approved' else label),'count':counts.get(key,0)} for key,label in STAGES],'followups':FollowUp.objects.visible_to(request.user).filter(done=False).order_by('date')[:20]})

@roles('admin')
@require_POST
def stage(request,public_id):
    try:
        b=transition_business(request.user,public_id,request.POST.get('stage'),request.POST.get('reason',''))
    except ValidationError as exc:
        return render(request,'core/error.html',{'error':'; '.join(exc.messages)},status=400)
    return redirect(b)

@roles('admin')
def followup(request,public_id,visit=False):
    b=scoped(Business,request.user,public_id)
    form=(VisitForm if visit else FollowUpForm)(request.POST or None)
    person='visitor' if visit else 'owner'
    form.fields[person].queryset=form.fields[person].queryset.filter(role__in=['admin','developer'],is_active=True)
    if request.method=='POST' and form.is_valid():
        item=form.save(commit=False); item.business=b; item.save()
        # The visit is now the step after the prior study, not the one after finding them.
        if visit and b.stage=='profile_created': transition_business(request.user,b.public_id,'visited')
        return redirect(b)
    return render(request,'core/form.html',{'form':form,'title':'Registrar visita' if visit else 'Próxima acción'})

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
    return render(request,'core/search.html',context)

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
        b.google_place_id=snapshot.place_id; b.save()
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
