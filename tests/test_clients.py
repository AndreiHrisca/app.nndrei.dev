import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from core.models import Request, ActivityEvent, Document
from core.services import change_request
pytestmark=pytest.mark.django_db

@pytest.mark.parametrize('role',['admin','developer','accountant','client'])
def test_business_permissions(sign_in,businesses,role):
    client=sign_in(role)
    own,other=businesses
    own.internal_notes='SECRET_INTERNAL'
    own.save()
    response=client.get(f'/clientes/{own.public_id}/')
    assert response.status_code==(404 if role=='client' else 200)
    if role=='accountant':
        assert b'SECRET_INTERNAL' not in response.content
    assert client.get(f'/clientes/{other.public_id}/').status_code==(200 if role in ['admin','accountant'] else 404)
    assert client.get(f'/clientes/{own.public_id}/editar/').status_code==(200 if role=='admin' else 404)

@pytest.mark.parametrize('role',['admin','developer','accountant','client'])
@pytest.mark.parametrize('kind',['document','access','website'])
def test_resource_permissions(sign_in,businesses,role,kind):
    client=sign_in(role)
    own,other=businesses
    assert client.get(f'/clientes/{own.public_id}/recurso/{kind}/').status_code==(200 if role in ['admin','developer'] else 404)
    assert client.post(f'/clientes/{other.public_id}/recurso/{kind}/',{},HTTP_HX_REQUEST='true').status_code==(200 if role=='admin' else 404)

@pytest.mark.parametrize('role',['admin','developer','accountant','client'])
def test_request_actions(sign_in,accounts,businesses,role):
    client=sign_in(role)
    own,other=businesses
    item=Request.objects.create(business=own,title='Cambio',description='Texto',created_by=accounts['client'])
    response=client.post(f'/peticiones/{item.public_id}/estado/',{'status':'accepted'},HTTP_HX_REQUEST='true')
    assert response.status_code==(302 if role in ['admin','developer'] else 404)
    response=client.post(f'/clientes/{own.public_id}/peticiones/',{'title':'Petición','description':'Descripción'},HTTP_HX_REQUEST='true')
    assert response.status_code==(302 if role in ['admin','client'] else 404)
    response=client.post(f'/clientes/{other.public_id}/peticiones/',{'title':'Petición','description':'Descripción'})
    assert response.status_code==(302 if role=='admin' else 404)

def test_portal_filters(sign_in,accounts,businesses):
    client=sign_in('client')
    own,other=businesses
    ActivityEvent.objects.create(business=own,text='VISIBLE',kind='test')
    ActivityEvent.objects.create(business=own,text='HIDDEN_EVENT',kind='test',client_visible=False)
    ActivityEvent.objects.create(business=other,text='OTHER_EVENT',kind='test')
    response=client.get('/portal/')
    assert response.status_code==200
    assert b'VISIBLE' in response.content
    assert b'HIDDEN_EVENT' not in response.content and b'OTHER_EVENT' not in response.content
    assert client.get(f'/portal/negocio/{other.public_id}/').status_code==404
    assert client.get(f'/clientes/{own.public_id}/portal/').status_code==404

def test_audit_immutable(accounts,businesses):
    event=ActivityEvent.objects.create(business=businesses[0],kind='test',text='Audit')
    with pytest.raises(ValueError): event.save()
    with pytest.raises(ValueError): event.delete()
    with pytest.raises(ValueError): ActivityEvent.objects.filter(pk=event.pk).update(text='Changed')
    with pytest.raises(ValueError): ActivityEvent.objects.filter(pk=event.pk).delete()

@pytest.mark.parametrize('role',['admin','developer','accountant','client'])
def test_private_documents(sign_in,accounts,businesses,role):
    client=sign_in(role)
    docs=[Document.objects.create(business=b,kind='contract',uploaded_by=accounts['admin'],file=SimpleUploadedFile('contract.txt',b'PRIVATE')) for b in businesses]
    assert client.get(f'/documentos/{docs[0].public_id}/').status_code==200
    assert client.get(f'/documentos/{docs[1].public_id}/').status_code==(200 if role in ['admin','accountant'] else 404)

def test_request_transition_and_email(accounts,businesses,django_capture_on_commit_callbacks,mailoutbox):
    from django.core.exceptions import ValidationError
    item=Request.objects.create(business=businesses[0],title='Carta',description='QR',created_by=accounts['client'])
    with pytest.raises(ValidationError): change_request(accounts['admin'],item.public_id,'production')
    with django_capture_on_commit_callbacks(execute=True): change_request(accounts['developer'],item.public_id,'accepted')
    assert len(mailoutbox)==1
    assert ActivityEvent.objects.filter(kind='request_status').count()==1
