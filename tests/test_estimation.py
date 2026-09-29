"""Fase 5: revenue estimate. The pure module first, with hand-checked numbers."""
from decimal import Decimal as D
import pytest
from core.estimation import Inputs, Settings, estimate, fmt
from core.models import ActivityEvent, Estimation, EstimationSettings


def test_reviews_method_range_comes_from_the_review_rate():
    # 480 reviews in 4 years = 120/year. At 2 %: 6.000 clients/year = 500/month × 20 € = 10.000 €.
    r = estimate(Inputs(reviews=480, years=D(4), ticket=D(20)))
    assert (r.low, r.mid, r.high) == (D(5000), D(10000), D(20000))  # 4 % and 1 %.
    assert r.confidence == 'low' and r.band == 'small' and r.budget == D(1000)
    assert r.lines[0].startswith('Reseñas: 480 en 4 años ≈ 120 al año.') and '10.000 €/mes' in r.lines[0]


def test_capacity_method():
    # 10 tables × 4 × 1,5 rotations × 2 services × 6 days × 50 % = 360 covers/week × 25 € × 52/12 = 39.000 €.
    r = estimate(Inputs(tables=10, people_per_table=D(4), rotations=D('1.5'), services_per_day=2, days_per_week=6, occupancy_pct=D(50), ticket=D(25)))
    assert r.mid == D(39000) and r.low == D(31200) and r.high == D(46800)
    assert r.band == 'medium' and r.budget == D(2000) and r.confidence == 'low'
    assert '360 cubiertos/semana' in r.lines[0] and '(observada)' in r.lines[0]


def test_concurrency_stands_in_for_occupancy():
    r = estimate(Inputs(tables=10, people_per_table=D(4), rotations=D(1), services_per_day=1, days_per_week=7, concurrency='high', ticket=D(10)))
    # 10 × 4 × 1 × 1 × 7 × 80 % = 224 covers/week × 10 € × 52/12 = 9.706,67 €.
    assert r.mid == D(9707) and 'por concurrencia alta' in r.lines[0]


def test_both_methods_agreeing_give_medium_confidence_and_the_mean():
    r = estimate(Inputs(reviews=480, years=D(4), ticket=D(20), tables=5, people_per_table=D(4), rotations=D(1), services_per_day=2,
                        days_per_week=6, occupancy_pct=D(50)))
    capacity = D(5 * 4 * 2 * 6) * D('0.5') * 20 * D(52) / 12  # 120 covers/week × 20 € = 10.400 €
    assert r.confidence == 'medium'
    assert r.mid == ((D(10000) + capacity) / 2).quantize(D(1)) == D(10200)
    assert r.low == D(5000) and r.high == D(20000)  # The widest of both ranges.
    assert 'coinciden' in r.lines[-1]


def test_methods_far_apart_give_low_confidence():
    r = estimate(Inputs(reviews=4000, years=D(1), ticket=D(20), tables=2, people_per_table=D(2), rotations=D(1), services_per_day=1,
                        days_per_week=5, occupancy_pct=D(30)))
    assert r.confidence == 'low' and 'no coinciden' in r.lines[-1]


def test_declared_revenue_wins_with_high_confidence():
    r = estimate(Inputs(reviews=480, years=D(4), ticket=D(20), declared_annual=D(900000), declared_source='Cuentas 2025'))
    assert r.mid == D(75000) and r.low == D(67500) and r.high == D(82500)
    assert r.confidence == 'high' and r.band == 'large' and r.budget == D(3000)
    assert r.lines[0].startswith('Facturación declarada (Cuentas 2025): 900.000 €/año = 75.000 €/mes.')


def test_ticket_from_google_price_level():
    r = estimate(Inputs(reviews=240, years=D(2), price_level='PRICE_LEVEL_EXPENSIVE'))
    assert r.ticket == D(35) and r.ticket_source == 'price_level' and 'nivel de precios' in r.lines[0]


def test_not_enough_data():
    r = estimate(Inputs(reviews=100))
    assert r.mid is None and r.confidence == 'low' and r.lines[0].startswith('Faltan datos para estimar')


def test_bands_and_budgets_follow_the_settings():
    settings = Settings(small_max=D(5000), medium_max=D(8000), budget_min=D(1500), budget_max=D(2500))
    assert estimate(Inputs(declared_annual=D(48000)), settings).band == 'small'
    medium = estimate(Inputs(declared_annual=D(72000)), settings)
    assert medium.band == 'medium' and medium.budget == D(2000)
    assert estimate(Inputs(declared_annual=D(120000)), settings).budget == D(2500)


def test_payback_argument_with_and_without_delivery():
    # Budget 1.000 €, ticket 20 € × 30 % = 6 € per client; 12 months → ceil(1000 / 72) = 14 clients/month.
    r = estimate(Inputs(reviews=480, years=D(4), ticket=D(20)))
    assert r.payback['clients_per_month'] == 14 and 'delivery_orders_per_month' not in r.payback
    assert r.payback['text'].startswith('La web se paga con 14 clientes nuevos al mes durante 12 meses')
    r = estimate(Inputs(reviews=480, years=D(4), ticket=D(20), uses_delivery=True))
    assert r.payback['delivery_orders_per_month'] == 14 and 'apps de reparto' in r.payback['delivery_text']
    r = estimate(Inputs(reviews=480, years=D(4), ticket=D(20), uses_delivery=True), Settings(delivery_commission=D('0.15'), gross_margin=D('0.5')))
    assert r.payback['clients_per_month'] == 9 and r.payback['delivery_orders_per_month'] == 28


def test_money_format():
    assert fmt(D('1234.5')) == '1.235 €' and fmt(D(75000)) == '75.000 €' and fmt(D('5.4')) == '5,40 €' and fmt(D(6)) == '6 €'


# --- Views --------------------------------------------------------------------------

@pytest.mark.django_db
def test_admin_saves_history_and_sees_the_card(sign_in, accounts, businesses):
    b = businesses[0]; b.google_reviews = 480; b.google_price_level = 'PRICE_LEVEL_MODERATE'; b.save()
    c = sign_in('admin')
    form = c.get(f'/clientes/{b.public_id}/estimacion/?edit=1').content.decode()
    assert 'value="480"' in form and 'Vacío: 20 € según el nivel de precios' in form
    body = c.post(f'/clientes/{b.public_id}/estimacion/', {'reviews': 480, 'years': 4, 'services': ['lunch', 'dinner'], 'concurrency': 'medium'},
                  HTTP_HX_REQUEST='true').content.decode()
    assert '10.000 €' in body and 'Confianza baja' in body and 'La web se paga con' in body
    first = Estimation.objects.get()
    assert first.used_ticket == 20 and first.services == ['lunch', 'dinner'] and first.created_by == accounts['admin']
    assert first.assumptions['review_rate'] == '0.0200'
    c.post(f'/clientes/{b.public_id}/estimacion/', {'declared_annual': 240000}, HTTP_HX_REQUEST='true')
    assert Estimation.objects.count() == 2
    page = c.get(f'/clientes/{b.public_id}/').content.decode()
    assert 'Estimaciones anteriores' in page and 'Confianza alta' in page
    assert ActivityEvent.objects.filter(business=b, kind='estimation', client_visible=False).count() == 2


@pytest.mark.django_db
def test_invalid_inputs_are_rejected(sign_in, businesses):
    b = businesses[0]
    body = sign_in('admin').post(f'/clientes/{b.public_id}/estimacion/', {'occupancy_pct': 150, 'days_per_week': 9}, HTTP_HX_REQUEST='true').content.decode()
    assert 'errorlist' in body and not Estimation.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize('role', ['developer', 'accountant', 'client'])
def test_estimation_never_reaches_other_roles(sign_in, accounts, businesses, role):
    b = businesses[0]
    from core.estimation_views import save_estimation
    save_estimation(accounts['admin'], b, {'declared_annual': D(120000)})
    c = sign_in(role)
    assert Estimation.objects.visible_to(accounts[role]).count() == 0
    for url in [f'/clientes/{b.public_id}/', '/portal/', f'/clientes/{b.public_id}/portal/']:
        body = c.get(url).content.decode()
        assert 'estimation-card' not in body and '10.000 €' not in body


@pytest.mark.django_db
def test_settings_are_editable_and_used(sign_in, accounts, businesses):
    config = EstimationSettings.load()
    config.review_rate = D('0.04'); config.save()
    from core.estimation_views import save_estimation
    item = save_estimation(accounts['admin'], businesses[0], {'reviews': 480, 'years': D(4), 'ticket': D(20)})
    assert item.monthly_mid == D(5000)
