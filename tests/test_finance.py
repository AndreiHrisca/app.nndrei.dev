from datetime import date
from decimal import Decimal
import pytest
from core.models import ClientCharge,Transaction,RecurringExpense
from core.finance import generate_recurring,advance_date
pytestmark=pytest.mark.django_db

@pytest.mark.parametrize('role',['admin','developer','accountant','client'])
def test_finance_permissions(sign_in,businesses,role):
    c=sign_in(role)
    for url in ['/finanzas/','/finanzas/exportar/']:
        assert c.get(url).status_code==(200 if role in ['admin','accountant'] else 404)
    for kind in ['transaction','charge','expense','commission']:
        assert c.get(f'/finanzas/nuevo/{kind}/').status_code==(200 if role=='admin' else 404)
        if role!='admin': assert c.post(f'/finanzas/nuevo/{kind}/',{},HTTP_HX_REQUEST='true').status_code==404

def test_recurring_idempotent_and_month_end(businesses):
    c=ClientCharge.objects.create(business=businesses[0],concept='Mantenimiento',kind='maintenance',amount=20,periodicity='monthly',next_date=date(2026,1,31))
    RecurringExpense.objects.create(concept='Servidor',amount=5,periodicity='monthly',next_date=date(2026,1,31))
    generate_recurring(date(2026,3,31)); generate_recurring(date(2026,3,31))
    assert Transaction.objects.count()==6
    c.refresh_from_db(); assert c.next_date==date(2026,4,30)
    assert sum(Transaction.objects.values_list('amount',flat=True))==Decimal(45)

def test_portal_no_internal_costs(sign_in,businesses):
    b,other=businesses
    ClientCharge.objects.create(business=b,concept='Cliente visible',kind='hosting',included=True,amount=0)
    ClientCharge.objects.create(business=other,concept='OTHER_CHARGE',kind='hosting',amount=90)
    Transaction.objects.create(business=b,date=date.today(),concept='INTERNAL_SECRET',category='hosting',amount=-10)
    c=sign_in('client'); response=c.get('/portal/gastos/')
    assert b'Cliente visible' in response.content and b'Incluido' in response.content
    assert b'OTHER_CHARGE' not in response.content and b'INTERNAL_SECRET' not in response.content

def test_csv_formula_safety(sign_in):
    Transaction.objects.create(date=date.today(),concept='=CMD()',category='other',amount=-1)
    response=sign_in('accountant').get('/finanzas/exportar/')
    assert "'=CMD()" in response.content.decode()


@pytest.mark.parametrize('maximum,top,step', [(0, 500, 100), (478, 500, 100), (320, 400, 100), (1200, 1250, 250), (100000, 100000, 20000), (41.99, 50, 10)])
def test_chart_axis_uses_round_steps(maximum, top, step):
    from decimal import Decimal
    from core.finance_views import axis_scale
    assert axis_scale(Decimal(str(maximum))) == (Decimal(top), Decimal(step))


def test_chart_shows_amounts_on_the_axis(sign_in):
    from datetime import date
    Transaction.objects.create(date=date.today(), concept='Cobro', category='development', amount=1200)
    body = sign_in('admin').get('/finanzas/').content.decode()
    for label in ['0 €', '250 €', '500 €', '750 €', '1.000 €', '1.250 €']:
        assert f'>{label}</text>' in body, label
    # SVG coordinates never carry the Spanish decimal comma.
    import re
    chart = body[body.index('<svg class="chart"'):body.index('</svg>', body.index('<svg class="chart"'))]
    assert not re.search(r' (x|y|x1|x2|y1|y2|width|height)="[^"]*,', chart)
