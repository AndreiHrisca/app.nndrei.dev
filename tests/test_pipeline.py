import pytest
from django.core.exceptions import ValidationError
from core.models import SearchZone,SearchCategory,PlaceSnapshot,PlacesUsage,StageChange
from core.services import transition_business
from core.places import opportunity_score,store_place,reserve_call,search_body
pytestmark=pytest.mark.django_db

@pytest.mark.parametrize('web,reviews,rating,photos,expected',[('',300,4.5,0,100),('',0,0,10,50),('https://instagram.com/bar',0,0,0,45),('https://mybar.es',0,4,10,10),('https://notinstagram.com',0,0,0,10)])
def test_score(web,reviews,rating,photos,expected):
    assert opportunity_score(web,reviews,rating,photos)==expected

def test_transitions(accounts,businesses):
    b=businesses[0]
    with pytest.raises(ValidationError): transition_business(accounts['admin'],b.public_id,'production')
    transition_business(accounts['admin'],b.public_id,'profile_created')
    with pytest.raises(ValidationError): transition_business(accounts['admin'],b.public_id,'discarded')
    transition_business(accounts['admin'],b.public_id,'discarded','Cerrado')
    assert StageChange.objects.count()==2

def test_dedup_and_circle(settings):
    z=SearchZone.objects.create(name='Madrid',latitude=40.4,longitude=-3.7)
    c=SearchCategory.objects.create(name='Bares')
    p={'id':'place','displayName':{'text':'Bar'},'location':{'latitude':40.4,'longitude':-3.7},'businessStatus':'OPERATIONAL'}
    store_place(p,z,c)
    item=PlaceSnapshot.objects.get(); item.status='discarded'; item.notes='Keep'; item.save()
    p['displayName']['text']='Nuevo nombre'; store_place(p,z,c)
    item.refresh_from_db(); assert item.status=='discarded' and item.notes=='Keep' and item.name=='Nuevo nombre'
    p['id']='far'; p['location']['latitude']=41; store_place(p,z,c)
    assert PlaceSnapshot.objects.count()==1
    assert 'rectangle' in search_body(z,c)['locationRestriction']
    settings.PLACES_MONTHLY_LIMIT=1
    assert reserve_call() and not reserve_call()

@pytest.mark.parametrize('role',['admin','developer','accountant','client'])
def test_pipeline_permissions(sign_in,businesses,role):
    client=sign_in(role); pid=businesses[0].public_id
    for url in ['/buscar/',f'/clientes/{pid}/seguimiento/',f'/clientes/{pid}/visita/']:
        assert client.get(url).status_code==(200 if role=='admin' else 404)
    assert client.post(f'/clientes/{pid}/fase/',{'stage':'profile_created'},HTTP_HX_REQUEST='true').status_code==(302 if role=='admin' else 404)
    if role!='admin':
        assert client.post('/buscar/actualizar/',HTTP_HX_REQUEST='true').status_code==404

def test_refresh_pagination_and_field_mask(settings):
    from unittest.mock import patch,Mock
    from core.places import refresh_places,FIELD_MASK
    from core.models import SearchRun
    settings.GOOGLE_PLACES_API_KEY='test-key'
    SearchZone.objects.create(name='Madrid',latitude=40.4,longitude=-3.7)
    SearchCategory.objects.create(name='Bares',included_type='bar')
    responses=[]
    for i in range(3):
        response=Mock()
        response.json.return_value={'places':[{'id':f'place-{i}','displayName':{'text':'Bar'},'location':{'latitude':40.4,'longitude':-3.7},'businessStatus':'OPERATIONAL'}],'nextPageToken':f'page-{i+1}'}
        responses.append(response)
    with patch('core.places.requests.post',side_effect=responses) as post:
        pk=refresh_places()
    assert post.call_count==3 and PlaceSnapshot.objects.count()==3
    assert SearchRun.objects.get(pk=pk).status=='done'
    assert post.call_args.kwargs['headers']['X-Goog-FieldMask']==FIELD_MASK
    assert post.call_args.kwargs['json']['pageToken']=='page-2'
    assert PlacesUsage.objects.get().requests==3

def test_closed_place_is_not_kept_operational():
    z=SearchZone.objects.create(name='Madrid',latitude=40.4,longitude=-3.7)
    c=SearchCategory.objects.create(name='Bar')
    PlaceSnapshot.objects.create(place_id='closed',name='Bar',zone=z,category=c,status='discarded')
    store_place({'id':'closed','businessStatus':'CLOSED_PERMANENTLY'},z,c)
    item=PlaceSnapshot.objects.get()
    assert item.business_status=='CLOSED_PERMANENTLY' and item.status=='discarded'

def test_stage_row_is_an_ordered_list_with_decorative_arrows(sign_in,businesses):
    body=sign_in('admin').get('/').content.decode()
    row=body[body.index('<ol class="stages">'):body.index('</ol>')+5]
    assert row.count('<li class="stage-step">')==9
    # Eight arrows for nine steps: none after Producción, and all of them decorative.
    assert row.count('stage-arrow')==8
    assert row.count('aria-hidden="true"')==8
    assert 'stage-arrow' not in row[row.rindex('<li class="stage-step">'):]
    assert 'stroke-dasharray' in row and '↻' in row

NEW_ORDER=['Encontrado','Ficha creada','Visitado','Propuesta','Revisión','Presupuesto aprobado','Desarrollo','Pruebas','Producción','Descartado']

def test_stage_order_is_defined_once():
    from core.models import STAGES,STAGE_ORDER
    assert [label for _,label in STAGES]==NEW_ORDER
    assert STAGE_ORDER['profile_created']<STAGE_ORDER['visited']<STAGE_ORDER['proposal']

def test_pipeline_tiles_follow_the_new_order(sign_in,businesses):
    import re
    row=sign_in('admin').get('/').content.decode()
    row=row[row.index('<ol class="stages">'):row.index('</ol>')]
    # The tile shortens 'Presupuesto aprobado' to 'Aprobado'; the order is what matters.
    assert re.findall(r'<b>([^<]*)</b>',row)==['Aprobado' if x=='Presupuesto aprobado' else x for x in NEW_ORDER[:9]]
    assert re.findall(r'<span class="stage-number">(\d+)</span>',row)==[f'{i:02d}' for i in range(1,10)]

def test_selects_follow_the_new_order(sign_in,businesses):
    import re
    body=sign_in('admin').get('/clientes/').content.decode()
    options=body[body.index('<select id="stage"'):body.index('</select>',body.index('<select id="stage"'))]
    assert re.findall(r'>([^<>]+)</option>',options)[1:]==NEW_ORDER

def test_transitions_follow_the_new_order():
    from core.services import STAGE_TRANSITIONS
    assert STAGE_TRANSITIONS['found']=={'profile_created','discarded'}
    assert STAGE_TRANSITIONS['profile_created']=={'visited','discarded'}
    assert STAGE_TRANSITIONS['visited']=={'proposal','discarded'}

def test_visit_now_follows_the_study(sign_in,accounts,businesses):
    from datetime import datetime
    b=businesses[0]; b.stage='profile_created'; b.save()
    response=sign_in('admin').post(f'/clientes/{b.public_id}/visita/',{'date':'2026-10-01T10:00','visitor':accounts['admin'].public_id,'result':'Hablamos con Santiago'})
    assert response.status_code==302
    b.refresh_from_db(); assert b.stage=='visited'

def test_clients_table_sorts_by_pipeline_order_not_alphabet(sign_in,businesses):
    own,other=businesses
    own.stage='production'; own.save()
    other.stage='found'; other.save()
    body=sign_in('admin').get('/clientes/',{'sort':'fase'}).content.decode()
    # Alphabetically 'found' > 'production'; by pipeline order it comes first.
    assert body.index(other.name)<body.index(own.name)
