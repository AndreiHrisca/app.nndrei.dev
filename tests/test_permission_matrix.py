"""All application routes are exercised as every role, with normal and HTMX requests."""
from datetime import date
import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone
from core.models import Business,Request,Document,Proposal,ProposalVersion,ProposalLine,FollowUp,SearchZone,SearchCategory,PlaceSnapshot,SearchRun,Transaction,ClientCharge,RecurringExpense,Commission
pytestmark=pytest.mark.django_db
ROLES=['admin','developer','accountant','client']
ALL=set(ROLES); INTERNAL={'admin','developer','accountant'}; AD={'admin','developer'}; AC={'admin','accountant'}; ADMIN={'admin'}; CLIENT={'client'}

@pytest.fixture
def route_objects(accounts,businesses):
    b=businesses[0]; b.stage='proposal'; b.save()
    req=Request.objects.create(business=b,title='Carta',description='Texto',created_by=accounts['client'])
    doc=Document.objects.create(business=b,kind='contract',uploaded_by=accounts['admin'],file=SimpleUploadedFile('contract.pdf',b'%PDF document'))
    p=Proposal.objects.create(business=b,number='P-2026-TEST',status='sent')
    v=ProposalVersion.objects.create(proposal=p,number=1)
    ProposalLine.objects.create(version=v,concept='Web',amount=500)
    v.sent_at=timezone.now(); v.save()
    draft=Proposal.objects.create(business=b,number='P-2026-DRAFT')
    ProposalVersion.objects.create(proposal=draft,number=1)
    f=FollowUp.objects.create(business=b,description='Llamada',date=date.today(),owner=accounts['admin'],kind='call')
    zone=SearchZone.objects.create(name='Zone'); category=SearchCategory.objects.create(name='Bar')
    place=PlaceSnapshot.objects.create(place_id='place',name='Bar',zone=zone,category=category)
    run=SearchRun.objects.create(status='done')
    tx=Transaction.objects.create(date=date.today(),concept='Expense',category='hosting',amount=-3,receipt=SimpleUploadedFile('receipt.txt',b'RECEIPT'))
    charge=ClientCharge.objects.create(business=b,concept='Cargo',kind='maintenance',amount=20)
    expense=RecurringExpense.objects.create(concept='Server',amount=5)
    commission=Commission.objects.create(business=b,beneficiary='Person',fixed_amount=10)
    return {'business':b.public_id,'request':req.public_id,'document':doc.public_id,'proposal':p.public_id,'draft':draft.public_id,'version':v.number,'followup':f.public_id,'place':place.public_id,'run':run.public_id,'transaction':tx.public_id,'charge':charge.public_id,'expense':expense.public_id,'commission':commission.public_id,'user':accounts['developer'].public_id}

CASES=[
 ('manifest',[],ALL,'get'),('service_worker',[],ALL,'get'),('offline',[],ALL,'get'),
 ('home',[],ALL,'get'),('businesses',[],INTERNAL,'get'),('portal',[],CLIENT,'get'),('users',[],ADMIN,'both'),('two_factor',[],ALL,'both'),('user_edit',['user'],ADMIN,'both'),
 ('business_create',[],ADMIN,'both'),('business_detail',['business'],INTERNAL,'get'),('business_edit',['business'],ADMIN,'both'),('portal_preview',['business'],ADMIN,'get'),
 ('request_plan',['request'],ADMIN,'both'),('study',['business'],AD,'both'),('request_create',['business'],{'admin','client'},'post'),('request_status',['request'],AD,'post'),('document_download',['document'],ALL,'get'),('document_edit',['document'],AD,'both'),('portal_business',['business'],{'admin','client'},'get'),
 ('portal_list',['=peticiones'],CLIENT,'get'),('portal_list',['=documentos'],CLIENT,'get'),('portal_charges',[],CLIENT,'get'),
 ('proposals',[],ALL,'get'),('proposal_create',[],ADMIN,'both'),('proposal_detail',['proposal'],ALL,'get'),('proposal_pdf',['proposal'],ALL,'get'),('proposal_edit',['draft'],ADMIN,'both'),
 ('proposal_action',['proposal','=accept'],{'admin','client'},'post'),('proposal_action',['proposal','=changes'],{'admin','client'},'post'),('proposal_action',['proposal','=reject'],{'admin','client'},'post'),('proposal_action',['proposal','=send'],ADMIN,'post'),('proposal_action',['proposal','=revise'],ADMIN,'post'),
 ('search',[],ADMIN,'get'),('search_refresh',[],ADMIN,'post'),('search_progress',['run'],ADMIN,'get'),('place_capture',['place'],ADMIN,'both'),('place_discard',['place'],ADMIN,'post'),('stage',['business'],ADMIN,'post'),('followup',['business'],ADMIN,'both'),('visit',['business'],ADMIN,'both'),('followup_done',['followup'],ADMIN,'post'),
 ('finance',[],AC,'get'),('finance_csv',[],AC,'get'),('receipt',['transaction'],AC,'get'),
 ('more',[],ALL,'get'),('business_note',['business'],AD,'post'),
 ('demo_upload',['business'],ADMIN,'post'),('demo_delete',['business'],ADMIN,'post'),
]+[('business_resource',['business','='+k],AD,'both') for k in ['document','access','website']]+[('finance_create',['='+k],ADMIN,'both') for k in ['transaction','charge','expense','commission']]+[('finance_edit',['='+k,k],ADMIN,'both') for k in ['transaction','charge','expense','commission']]

@pytest.mark.parametrize('role',ROLES)
@pytest.mark.parametrize('htmx',[False,True])
@pytest.mark.parametrize('name,args,allowed,method',CASES,ids=[f'{x[0]}-{i}' for i,x in enumerate(CASES)])
def test_entire_matrix(sign_in,route_objects,role,htmx,name,args,allowed,method):
    c=sign_in(role)
    url=reverse(name,args=[x[1:] if x.startswith('=') else route_objects[x] for x in args])
    headers={'HTTP_HX_REQUEST':'true'} if htmx else {}
    methods=['get','post'] if method=='both' else [method]
    for verb in methods:
        response=getattr(c,verb)(url,{'version':route_objects['version'],'status':'accepted','comment':'Cambiar algo'},**headers)
        if role in allowed:
            assert response.status_code in [200,302,400],(url,role,response.status_code)
        else:
            assert response.status_code==404,(url,role,response.status_code)

@pytest.mark.parametrize('role',['developer','client'])
def test_foreign_object_actions(sign_in,accounts,businesses,role):
    b=businesses[1]
    req=Request.objects.create(business=b,title='Other',description='Other',created_by=accounts['admin'])
    doc=Document.objects.create(business=b,kind='contract',uploaded_by=accounts['admin'],file=SimpleUploadedFile('private.txt',b'SECRET'))
    p=Proposal.objects.create(business=b,number='P-FOREIGN',status='sent')
    ProposalVersion.objects.create(proposal=p,number=1,sent_at=timezone.now())
    c=sign_in(role)
    for url in [f'/clientes/{b.public_id}/',f'/documentos/{doc.public_id}/',f'/propuestas/{p.public_id}/',f'/propuestas/{p.public_id}/pdf/']:
        assert c.get(url).status_code==404
    for url in [f'/peticiones/{req.public_id}/estado/',f'/propuestas/{p.public_id}/accion/accept/',f'/documentos/{doc.public_id}/estado/']:
        assert c.post(url,{'status':'accepted'},HTTP_HX_REQUEST='true').status_code==404

def test_matrix_covers_every_application_route():
    from config.urls import urlpatterns
    exempt={'login','logout','password_reset','password_reset_done','password_reset_confirm','password_reset_complete','accept_invitation','public_proposal','public_proposal_pdf','demo_public'}
    tested={case[0] for case in CASES}
    names={pattern.name for pattern in urlpatterns if getattr(pattern,'name',None)}
    assert names-tested-exempt==set()
