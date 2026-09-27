from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import transaction
from django.shortcuts import get_object_or_404
from .access import scoped
from .models import ActivityEvent, Request
from .notifications import notify

def activity(business, kind, text, actor=None, obj=None, client_visible=True):
    return ActivityEvent.objects.create(business=business,kind=kind,text=text,actor=actor,client_visible=client_visible,content_type=ContentType.objects.get_for_model(obj) if obj else None,object_id=obj.pk if obj else None)

def client_emails(business):
    emails=list(business.memberships.filter(user__is_active=True).values_list('user__email',flat=True))
    if business.email: emails.append(business.email)
    return list(dict.fromkeys(emails))

REQUEST_TRANSITIONS = {'requested': {'accepted','rejected'}, 'accepted': {'in_progress','rejected'}, 'in_progress': {'testing','rejected'}, 'testing': {'production','in_progress'}, 'production': set(), 'rejected': set()}

@transaction.atomic
def change_request(user, public_id, status, reason=''):
    if user.role not in {'admin','developer'}:
        from django.http import Http404
        raise Http404
    item = scoped(Request,user,public_id,lock=True)
    if status not in REQUEST_TRANSITIONS[item.status]:
        raise ValidationError('Cambio de estado no permitido.')
    if status == 'rejected' and not reason.strip():
        raise ValidationError('Indica el motivo del rechazo.')
    item.status = status
    item.rejected_reason = reason if status == 'rejected' else ''
    item.save()
    text = f'{item.title}: {item.get_status_display()}.' + (' ' + reason if reason else '')
    activity(item.business,'request_status',text,user,item)
    transaction.on_commit(lambda: notify('Tu petición ha cambiado de estado',client_emails(item.business),text,settings.SITE_URL+'/portal/'))
    return item

from django.utils import timezone
from django.db.models import Max
from .models import Business, StageChange, STAGES, Proposal, ProposalVersion, ProposalLine, ProposalSequence

STAGE_TRANSITIONS = {a[0]:{b[0]} for a,b in zip(STAGES[:8],STAGES[1:9])}
STAGE_TRANSITIONS['proposal'] = {'review'}
STAGE_TRANSITIONS['review'] = {'proposal'}
STAGE_TRANSITIONS['production'] = set()
STAGE_TRANSITIONS['discarded'] = set()
for stage in ['found','visited','profile_created','proposal','review']:
    STAGE_TRANSITIONS[stage].add('discarded')

@transaction.atomic
def transition_business(user,public_id,target,reason='',accepting=False):
    b=scoped(Business,user,public_id,lock=True)
    if user.role!='admin' and not accepting:
        from django.http import Http404
        raise Http404
    approval=accepting and target=='approved' and b.stage in {'proposal','review'}
    if not approval and target not in STAGE_TRANSITIONS.get(b.stage,set()):
        raise ValidationError('Transición de fase no permitida.')
    if target=='discarded' and not reason.strip():
        raise ValidationError('Indica el motivo para descartar.')
    StageChange.objects.create(business=b,from_stage=b.stage,to_stage=target,actor=user)
    b.stage=target
    b.stage_changed_at=timezone.now()
    b.discarded_reason=reason if target=='discarded' else ''
    b.save()
    activity(b,'stage_changed','Fase: '+b.get_stage_display(),user,b,client_visible=target not in {'found','visited','discarded'})
    return b

@transaction.atomic
def new_proposal(user,business,valid_until=None):
    if user.role!='admin':
        from django.http import Http404
        raise Http404
    business=scoped(Business,user,business.public_id)
    year=timezone.localdate().year
    seq,_=ProposalSequence.objects.get_or_create(year=year)
    seq=ProposalSequence.objects.select_for_update().get(pk=seq.pk)
    seq.value+=1
    seq.save()
    proposal=Proposal.objects.create(business=business,number=f'P-{year}-{seq.value:03d}',valid_until=valid_until)
    ProposalVersion.objects.create(proposal=proposal,number=1)
    return proposal

@transaction.atomic
def revise_proposal(user,public_id):
    if user.role!='admin':
        from django.http import Http404
        raise Http404
    proposal=scoped(Proposal,user,public_id,lock=True)
    old=proposal.versions.first()
    if proposal.status not in {'sent','changes','rejected'} or not old.sent_at:
        raise ValidationError('Solo se revisa una propuesta enviada y no aceptada.')
    version=ProposalVersion.objects.create(proposal=proposal,number=old.number+1,development_total=old.development_total,monthly_fee=old.monthly_fee,notes=old.notes)
    for line in old.lines.all():
        ProposalLine.objects.create(version=version,concept=line.concept,description=line.description,amount=line.amount,included=line.included)
    proposal.status='draft'
    proposal.save()
    return version

@transaction.atomic
def proposal_action(user,public_id,action,comment='',ip=None,expected_version=None):
    proposal=scoped(Proposal,user,public_id,lock=True)
    version=proposal.versions.select_for_update().first()
    # Versions are addressed by their number inside the proposal, never by row id.
    if expected_version is not None and str(version.number)!=str(expected_version):
        raise ValidationError('La versión ha cambiado. Revisa la propuesta actual.')
    if action=='send' and user.role=='admin':
        if version.sent_at or version.historical_status or proposal.status!='draft' or not version.lines.exists():
            raise ValidationError('La versión debe ser un borrador con líneas.')
        # 'visited' is the step just before 'proposal': a proposal follows a visit.
        if proposal.business.stage not in {'visited','proposal','review','production','development','testing','approved'}:
            raise ValidationError('Visita el negocio antes de enviar una propuesta.')
        if not client_emails(proposal.business):
            raise ValidationError('Añade el email del negocio o invita al cliente antes de enviar.')
        version.sent_at=timezone.now()
        proposal.status='sent'
        if proposal.business.stage in {'visited','review'}:
            transition_business(user,proposal.business.public_id,'proposal')
        from django.core import signing
        token=signing.dumps({'version':version.pk},salt='proposal-public')
        url=settings.SITE_URL+'/propuesta-publica/'+token+'/'
        transaction.on_commit(lambda: notify('Propuesta '+proposal.number,client_emails(proposal.business),'Tu propuesta está lista para revisar.',url))
        text='Propuesta enviada: '+proposal.number+f' · v{version.number}'
    elif action in {'accept','changes','reject'} and user.role in {'admin','client'}:
        if proposal.status!='sent' or not version.sent_at or version.accepted_at:
            raise ValidationError('La propuesta ya no está pendiente de respuesta.')
        if proposal.valid_until and proposal.valid_until<timezone.localdate():
            raise ValidationError('La propuesta ha caducado. Solicita una nueva versión.')
        if action=='changes' and not comment.strip():
            raise ValidationError('Cuéntanos qué cambios necesitas.')
        version.client_comment=comment
        proposal.status={'accept':'accepted','changes':'changes','reject':'rejected'}[action]
        if action=='accept':
            version.accepted_at=timezone.now()
            version.accepted_by=user
            version.acceptance_method='admin_recorded' if user.role=='admin' else 'portal'
            version.acceptance_ip=ip
            if proposal.business.stage in {'proposal','review'}:
                transition_business(user,proposal.business.public_id,'approved',accepting=True)
        elif action=='changes' and proposal.business.stage=='proposal':
            # The client decision authorizes the corresponding commercial transition.
            b=Business.objects.select_for_update().get(pk=proposal.business_id)
            StageChange.objects.create(business=b,from_stage=b.stage,to_stage='review',actor=user)
            b.stage='review'; b.stage_changed_at=timezone.now(); b.save()
            activity(b,'stage_changed','Fase: Revisión',user,b)
        text='Propuesta '+proposal.number+': '+proposal.get_status_display()
        transaction.on_commit(lambda: notify(text,[settings.ADMIN_EMAIL],comment or text,settings.SITE_URL+proposal.get_absolute_url()))
    else:
        from django.http import Http404
        raise Http404
    version.save(); proposal.save()
    activity(proposal.business,'proposal_'+action,text,user,version)
    return proposal
