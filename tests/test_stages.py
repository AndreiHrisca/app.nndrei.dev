"""Stages v2: «Propuesta» merges two stages, «Demo lista» is new, and Seguimiento shows the estimate."""
from decimal import Decimal
import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from core import mcp_tools as tools
from core.models import ActivityEvent, Business, Estimation, StageChange

pytestmark = pytest.mark.django_db

BEFORE = ('core', '0021_mcp_connector_and_demo_versions')
AFTER = ('core', '0022_merge_proposal_add_demo_ready')


# --- Data migration -----------------------------------------------------------------

@pytest.mark.django_db(transaction=True)
def test_migration_merges_proposal_stages_and_marks_demo_ready():
    from django.core.files.base import ContentFile
    from django.core.files.storage import default_storage
    executor = MigrationExecutor(connection)
    executor.migrate([BEFORE])
    old = executor.loader.project_state([BEFORE]).apps
    OldBusiness, OldDemo = old.get_model('core', 'Business'), old.get_model('core', 'DemoVersion')
    try:
        made = {name: OldBusiness.objects.create(name=name, stage=stage).pk for name, stage in [
            ('sent', 'proposal'), ('changes', 'review'), ('studied-versioned', 'profile_created'),
            ('studied-legacy', 'profile_created'), ('studied-nodemo', 'profile_created'), ('visited-demo', 'visited'), ('found-demo', 'found')]}
        for name in ['studied-versioned', 'visited-demo', 'found-demo']:
            OldDemo.objects.create(business_id=made[name], number=1, filename='demo.html', path='x', size=1, sha256='0' * 64)
        default_storage.save(f"demos/{made['studied-legacy']}.html", ContentFile(b'<html></html>'))

        executor.loader.build_graph()
        executor.migrate([AFTER])
        New = executor.loader.project_state([AFTER]).apps.get_model('core', 'Business')
        stages = {b.name: b.stage for b in New.objects.filter(pk__in=made.values())}
        assert stages == {'sent': 'proposal', 'changes': 'proposal', 'studied-versioned': 'demo_ready', 'studied-legacy': 'demo_ready',
                          'studied-nodemo': 'profile_created', 'visited-demo': 'visited', 'found-demo': 'found'}
    finally:
        default_storage.delete(f"demos/{made['studied-legacy']}.html") if 'made' in locals() else None
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())


@pytest.mark.django_db(transaction=True)
def test_migration_is_fine_with_nobody_in_the_old_stages():
    executor = MigrationExecutor(connection)
    executor.migrate([BEFORE])
    try:
        executor.loader.build_graph()
        executor.migrate([AFTER])
    finally:
        executor.loader.build_graph()
        executor.migrate(executor.loader.graph.leaf_nodes())


def test_old_history_keeps_its_name(accounts, businesses):
    """The stage history is immutable: rows written before the merge still read well."""
    change = StageChange.objects.create(business=businesses[0], from_stage='proposal', to_stage='review', actor=accounts['admin'])
    assert change.get_to_stage_display() == 'Cambios en la propuesta'


# --- «Demo lista» from subir_demo -----------------------------------------------------

@pytest.mark.parametrize('stage', ['found', 'profile_created'])
def test_demo_moves_an_early_business_to_demo_ready(accounts, businesses, stage):
    b = businesses[0]; b.stage = stage; b.save()
    result = tools.subir_demo(accounts['admin'], str(b.public_id), 'demo.html', '<!doctype html><p>hola</p>')
    b.refresh_from_db()
    assert b.stage == 'demo_ready' and result['fase'] == 'Demo lista' and result['fase_cambiada']
    assert StageChange.objects.filter(business=b, from_stage=stage, to_stage='demo_ready', actor=accounts['admin']).exists()
    assert ActivityEvent.objects.filter(business=b, kind='stage_changed', text='Fase: Demo lista', client_visible=False).exists()


@pytest.mark.parametrize('stage', ['demo_ready', 'visited', 'proposal', 'approved', 'production', 'discarded'])
def test_demo_never_moves_a_business_back(accounts, businesses, stage):
    b = businesses[0]; b.stage = stage; b.save()
    result = tools.subir_demo(accounts['admin'], str(b.public_id), 'demo.html', '<!doctype html><p>hola</p>')
    b.refresh_from_db()
    assert b.stage == stage and not result['fase_cambiada'] and not StageChange.objects.filter(business=b).exists()


def test_a_visit_from_demo_ready_goes_to_visited(sign_in, businesses):
    from django.utils import timezone
    b = businesses[0]; b.stage = 'demo_ready'; b.save()
    sign_in('admin').post(f'/clientes/{b.public_id}/visita/', {'date': timezone.localtime().strftime('%Y-%m-%dT%H:%M'), 'result': 'Interesado'}, HTTP_HX_REQUEST='true')
    b.refresh_from_db()
    assert b.stage == 'visited'


# --- buscar_clientes -----------------------------------------------------------------

@pytest.mark.parametrize('estado', ['Propuesta', 'propuesta', 'Propuesta enviada', 'Cambios en la propuesta', 'cambios en la propuesta'])
def test_old_proposal_names_still_find_propuesta(accounts, businesses, estado):
    own, other = businesses
    own.stage = 'proposal'; own.save()
    found = tools.buscar_clientes(accounts['admin'], estado=estado)['clientes']
    assert [c['nombre'] for c in found] == ['Mi negocio'] and found[0]['fase'] == 'Propuesta'


def test_demo_lista_is_a_valid_filter_and_the_error_lists_the_new_stages(accounts, businesses):
    businesses[0].stage = 'demo_ready'; businesses[0].save()
    assert tools.buscar_clientes(accounts['admin'], estado='Demo lista')['clientes'][0]['nombre'] == 'Mi negocio'
    with pytest.raises(tools.ToolError) as error:
        tools.buscar_clientes(accounts['admin'], estado='Revisión')
    assert 'Demo lista' in str(error.value) and 'Propuesta,' in str(error.value) and 'Cambios en la propuesta' not in str(error.value)


def test_tool_descriptions_name_the_new_stages():
    import asyncio
    from core.mcp_server import mcp
    listed = {tool.name: tool for tool in asyncio.run(mcp.list_tools())}
    text = listed['buscar_clientes'].description + str(listed['buscar_clientes'].input_schema)
    assert 'Demo lista' in text and 'Propuesta enviada' not in text and 'Cambios en la propuesta' not in text
    assert 'Demo lista' in listed['subir_demo'].description


# --- Seguimiento ----------------------------------------------------------------------

def test_tiles_numbered_with_one_repeatable_propuesta(sign_in, businesses):
    import re
    row = sign_in('admin').get('/').content.decode()
    row = row[row.index('<ol class="stages">'):row.index('</ol>')]
    tiles = re.findall(r'<span class="stage-number">(\d+)</span><b>([^<]*)</b>', row)
    assert tiles == [('01', 'Encontrado'), ('02', 'Estudiado'), ('03', 'Demo lista'), ('04', 'Visitado'), ('05', 'Propuesta'),
                     ('06', 'Aprobado'), ('07', 'En construcción'), ('08', 'Revisión final'), ('09', 'Web publicada')]
    assert row.count('↻') == 1 and 'stage-tile demo_ready' in row


def test_amount_format():
    from core.templatetags.money import eur_mes, eur_short
    assert eur_mes(Decimal('11706')) == '11.706 €/mes'
    assert eur_mes(Decimal('11705.50')) == '11.706 €/mes'  # Rounded half up, no decimals.
    assert eur_mes(Decimal('950')) == '950 €/mes'
    assert eur_mes(None) == '—'
    assert eur_short(Decimal('1000.00')) == '1.000 €' and eur_short(Decimal('2500')) == '2.500 €'


def test_clients_in_progress_show_the_latest_estimate(sign_in, accounts, businesses):
    from core.estimation_views import save_estimation
    own, other = businesses
    own.stage = other.stage = 'visited'; own.save(); other.save()
    save_estimation(accounts['admin'], own, {'declared_annual': Decimal(60000)})       # 5.000 €/mes
    save_estimation(accounts['admin'], own, {'declared_annual': Decimal('140472')})    # 11.706 €/mes, the latest
    page = sign_in('admin').get('/').content.decode()
    table = page[page.index('<table class="pipeline-table">'):page.index('</table>')]
    head = [h.split('>')[-1] for h in table[:table.index('</thead>')].split('</th>')[:-1]]
    assert head == ['Negocio', 'Fase', 'Siguiente paso', 'Estimación de facturación', 'Presupuesto sugerido', 'Actualizado']
    own_row = table[table.index('Mi negocio'):]
    own_row = own_row[:own_row.index('</tr>')]
    assert '<td class="number">11.706 €/mes</td>' in own_row and '<td class="number">1.000 €</td>' in own_row
    assert '5.000 €/mes' not in table
    other_row = table[table.index('Otro negocio'):]
    other_row = other_row[:other_row.index('</tr>')]
    assert other_row.count('<td class="number"><span class="muted">—</span></td>') == 2
    assert 'table-scroll' in page[page.index('pipeline-list'):page.index('<table class="pipeline-table">')]


def test_estimates_do_not_add_a_query_per_row(sign_in, accounts):
    from core.estimation_views import save_estimation
    client = sign_in('admin')

    def queries_for_page():
        from django.db import connection as conn
        from django.test.utils import CaptureQueriesContext
        with CaptureQueriesContext(conn) as captured:
            assert client.get('/').status_code == 200
        return len(captured)

    few = [Business.objects.create(name=f'B{i}', stage='visited') for i in range(2)]
    for b in few:
        save_estimation(accounts['admin'], b, {'declared_annual': Decimal(12000)})
    baseline = queries_for_page()
    for i in range(10):
        b = Business.objects.create(name=f'C{i}', stage='visited')
        save_estimation(accounts['admin'], b, {'declared_annual': Decimal(12000)})
    assert queries_for_page() == baseline
    assert Estimation.objects.count() == 12
