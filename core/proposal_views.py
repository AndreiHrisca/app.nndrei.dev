from django.conf import settings
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import OuterRef,Subquery,Sum
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404,redirect,render
from django.template.loader import render_to_string
from django.views.decorators.http import require_POST,require_GET
from .access import scoped
from .auth_views import roles
from .models import Proposal, ProposalVersion
from .forms import ProposalForm, VersionForm, ProposalLineFormSet
from .services import new_proposal,revise_proposal,proposal_action

@roles('admin','developer','accountant','client')
def proposal_list(request):
    items=Proposal.objects.visible_to(request.user).select_related('business')
    statuses=Proposal._meta.get_field('status').choices
    status=request.GET.get('status','')
    if status in dict(statuses): items=items.filter(status=status)
    else: status=''
    # Amount of the newest version this user may see; a client never sees an unsent draft.
    latest=ProposalVersion.objects.visible_to(request.user).filter(proposal=OuterRef('pk')).order_by('-number')
    items=items.annotate(amount=Subquery(latest.values('development_total')[:1]))
    return render(request,'core/proposals.html',{'title':'Propuestas','proposals':items,'statuses':statuses,'status':status})

@roles('admin')
def proposal_create(request):
    form=ProposalForm(request.POST or None)
    form.fields['business'].queryset=form.fields['business'].queryset.visible_to(request.user)
    if request.method=='POST' and form.is_valid():
        p=new_proposal(request.user,form.cleaned_data['business'],form.cleaned_data['valid_until'])
        return redirect('proposal_edit',public_id=p.public_id)
    return render(request,'core/form.html',{'form':form,'title':'Nueva propuesta'})

@roles('admin')
def proposal_edit(request,public_id):
    p=scoped(Proposal,request.user,public_id)
    v=p.versions.first()
    if v.sent_at or v.historical_status:
        raise Http404
    form=VersionForm(request.POST or None,instance=v)
    lines=ProposalLineFormSet(request.POST or None,instance=v)
    if request.method=='POST' and form.is_valid() and lines.is_valid():
        with transaction.atomic():
            locked=ProposalVersion.objects.select_for_update().get(pk=v.pk)
            if locked.sent_at:
                raise Http404
            form.save(); lines.save()
            v.development_total=v.lines.filter(included=False).aggregate(total=Sum('amount'))['total'] or 0
            v.save()
        return redirect(p)
    return render(request,'core/proposal_edit.html',{'form':form,'lines':lines,'title':'Editar '+p.number})

@roles('admin','developer','accountant','client')
def proposal_detail(request,public_id,pdf=False):
    p=scoped(Proposal,request.user,public_id)
    versions=ProposalVersion.objects.visible_to(request.user).filter(proposal=p).prefetch_related('lines')
    # Versions are addressed by their number within the proposal, not by row id.
    v=versions.filter(number=request.GET['version']).first() if request.GET.get('version','').isdigit() else versions.first()
    if not v:
        raise Http404
    return document_response(request,p,v,versions,pdf)

def document_response(request,p,v,versions,pdf=False,public=False):
    context={'title':p.number,'proposal':p,'version':v,'versions':versions,'public':public,'pdf':pdf}
    if pdf:
        from weasyprint import HTML,CSS
        # No URL fetcher: proposal HTML contains only escaped text and local CSS.
        def deny_fetch(url,*args,**kwargs):
            raise ValueError('External resources are disabled for PDFs.')
        html=render_to_string('core/proposal_document.html',context)
        css=(settings.BASE_DIR/'static/css/app.css').read_text().split('\n',1)[1]
        content=HTML(string=html,url_fetcher=deny_fetch).write_pdf(stylesheets=[CSS(string=css)])
        response=HttpResponse(content,content_type='application/pdf')
        response['Content-Disposition']=f'attachment; filename="{p.number}-v{v.number}.pdf"'
    else:
        response=render(request,'core/proposal_detail.html',context)
    response['Cache-Control']='private, no-store'
    response['Referrer-Policy']='no-referrer'
    return response

@require_GET
def public_proposal(request,token,pdf=False):
    try:
        data=signing.loads(token,salt='proposal-public',max_age=settings.PROPOSAL_LINK_MAX_AGE)
    except signing.BadSignature:
        raise Http404
    v=get_object_or_404(ProposalVersion,pk=data['version'],sent_at__isnull=False)
    return document_response(request,v.proposal,v,[],pdf,True)

@roles('admin','client')
@require_POST
def action(request,public_id,action):
    try:
        if action=='revise':
            revise_proposal(request.user,public_id)
            return redirect('proposal_edit',public_id=public_id)
        expected=request.POST.get('version')
        if action in {'accept','changes','reject'}:
            p=scoped(Proposal,request.user,public_id)
            latest=p.versions.first()
            if not expected or str(latest.number)!=expected:
                raise ValidationError('La versión ha cambiado. Recarga y revisa la propuesta actual antes de responder.')
        proposal_action(request.user,public_id,action,request.POST.get('comment',''),request.META.get('REMOTE_ADDR'),expected_version=expected)
    except ValidationError as exc:
        return render(request,'core/error.html',{'error':'; '.join(exc.messages)},status=400)
    return redirect('proposal_detail',public_id=public_id)
