import pytest
from django.core import signing
from django.core.exceptions import ValidationError
from core.models import Proposal,ProposalVersion,ProposalLine,StageChange
from core.services import new_proposal,proposal_action,revise_proposal
pytestmark=pytest.mark.django_db

@pytest.fixture
def proposal(accounts,businesses):
    b=businesses[0]; b.stage='visited'; b.save()
    p=new_proposal(accounts['admin'],b)
    v=p.versions.first()
    ProposalLine.objects.create(version=v,concept='Web',amount=500)
    v.development_total=500; v.save()
    return p

def test_revision_acceptance(accounts,proposal,django_capture_on_commit_callbacks):
    proposal_action(accounts['admin'],proposal.public_id,'send')
    proposal_action(accounts['client'],proposal.public_id,'changes','Cambiar carta')
    old=proposal.versions.first(); old.notes='Changed'
    with pytest.raises(ValueError): old.save()
    v=revise_proposal(accounts['admin'],proposal.public_id)
    assert v.number==2 and v.lines.count()==1
    proposal_action(accounts['admin'],proposal.public_id,'send')
    proposal_action(accounts['client'],proposal.public_id,'accept',ip='127.0.0.1')
    proposal.refresh_from_db(); proposal.business.refresh_from_db()
    assert proposal.status=='accepted' and proposal.business.stage=='approved'
    assert StageChange.objects.filter(business=proposal.business).count()==4
    with pytest.raises(ValidationError): proposal_action(accounts['client'],proposal.public_id,'accept')

@pytest.mark.parametrize('role',['admin','developer','accountant','client'])
def test_proposal_permissions(sign_in,accounts,proposal,businesses,role):
    client=sign_in(role)
    assert client.get(f'/propuestas/{proposal.public_id}/').status_code==(404 if role=='client' else 200)
    proposal_action(accounts['admin'],proposal.public_id,'send')
    assert client.get(f'/propuestas/{proposal.public_id}/').status_code==200
    other=Proposal.objects.create(business=businesses[1],number='P-other',status='sent')
    ProposalVersion.objects.create(proposal=other,number=1)
    assert client.get(f'/propuestas/{other.public_id}/').status_code==(200 if role in ['admin','accountant'] else 404)
    if role in ['developer','accountant']:
        for action in ['accept','changes','reject','send','revise']:
            assert client.post(f'/propuestas/{proposal.public_id}/accion/{action}/',HTTP_HX_REQUEST='true').status_code==404
    if role=='client':
        assert client.post(f'/propuestas/{proposal.public_id}/accion/send/').status_code==404
        assert client.post(f'/propuestas/{proposal.public_id}/accion/revise/').status_code==404

def test_signed_read_only_and_pdf(client,accounts,proposal):
    proposal_action(accounts['admin'],proposal.public_id,'send')
    v=proposal.versions.first()
    token=signing.dumps({'version':v.pk},salt='proposal-public')
    assert client.get(f'/propuesta-publica/{token}/').status_code==200
    response=client.get(f'/propuesta-publica/{token}/pdf/')
    assert response.status_code==200 and response.content.startswith(b'%PDF')
    assert client.get(f'/propuesta-publica/{token}invalid/').status_code==404

def test_sequential_numbering(accounts,businesses):
    first=new_proposal(accounts['admin'],businesses[0]); second=new_proposal(accounts['admin'],businesses[0])
    assert int(second.number.rsplit('-',1)[1])==int(first.number.rsplit('-',1)[1])+1

def test_database_immutability(accounts,proposal):
    from django.db import transaction,DatabaseError,connection
    proposal_action(accounts['admin'],proposal.public_id,'send')
    version=proposal.versions.first()
    with pytest.raises(DatabaseError),transaction.atomic():
        ProposalVersion.objects.filter(pk=version.pk).update(notes='Tamper')
    with pytest.raises(DatabaseError),transaction.atomic():
        ProposalLine.objects.filter(version=version).update(amount=1)
    with pytest.raises(DatabaseError),transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute('DELETE FROM core_activityevent')

def test_stale_version_cannot_be_accepted(sign_in,accounts,proposal):
    proposal_action(accounts['admin'],proposal.public_id,'send')
    first=proposal.versions.first()
    proposal_action(accounts['client'],proposal.public_id,'changes','Revise')
    revise_proposal(accounts['admin'],proposal.public_id)
    proposal_action(accounts['admin'],proposal.public_id,'send')
    c=sign_in('client')
    response=c.post(f'/propuestas/{proposal.public_id}/accion/accept/',{'version':first.number})
    assert response.status_code==400
    proposal.refresh_from_db(); assert proposal.status=='sent'

def test_client_keeps_previous_version_during_revision(sign_in,accounts,proposal):
    proposal_action(accounts['admin'],proposal.public_id,'send')
    proposal_action(accounts['client'],proposal.public_id,'changes','Cambiar')
    new=revise_proposal(accounts['admin'],proposal.public_id)
    new.notes='DRAFT_PRIVATE_NOTE'; new.save()
    response=sign_in('client').get(f'/propuestas/{proposal.public_id}/')
    assert response.status_code==200 and b'DRAFT_PRIVATE_NOTE' not in response.content
