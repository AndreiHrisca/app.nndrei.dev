"""Monthly revenue estimate for a business (internal, admin only).

Pure functions on plain values: `estimate(inputs, settings)` never touches the
database, so every number here is tested with fixed inputs.

Two independent methods, and a declared figure that overrides both:

* Reviews: clients/year = (reviews / years) / review_rate. The review rate (how
  many clients leave a review) is the weakest assumption, so the range comes
  from it: the high rate gives the low figure and vice versa.
* Capacity: tables × people × rotations × services × days × occupancy × ticket,
  per week, times 52/12 for a month. ±20 % for the range.
* Declared revenue (annual accounts, Registro Mercantil) wins, with high confidence.
"""
import math
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal

WEEKS_PER_MONTH = Decimal(52) / Decimal(12)
CAPACITY_SPREAD = Decimal('0.20')
DECLARED_SPREAD = Decimal('0.10')
BANDS = {'small': 'Pequeño', 'medium': 'Mediano', 'large': 'Grande'}
CONFIDENCE = {'low': 'Baja', 'medium': 'Media', 'high': 'Alta'}
CONCURRENCY = {'low': 'Baja', 'medium': 'Media', 'high': 'Alta'}
PRICE_LEVELS = ['PRICE_LEVEL_INEXPENSIVE', 'PRICE_LEVEL_MODERATE', 'PRICE_LEVEL_EXPENSIVE', 'PRICE_LEVEL_VERY_EXPENSIVE']


@dataclass
class Settings:
    review_rate: Decimal = Decimal('0.02')
    review_rate_low: Decimal = Decimal('0.01')
    review_rate_high: Decimal = Decimal('0.04')
    price_level_tickets: dict = field(default_factory=lambda: {'PRICE_LEVEL_INEXPENSIVE': 10, 'PRICE_LEVEL_MODERATE': 20, 'PRICE_LEVEL_EXPENSIVE': 35, 'PRICE_LEVEL_VERY_EXPENSIVE': 60})
    concurrency_occupancy: dict = field(default_factory=lambda: {'low': 30, 'medium': 55, 'high': 80})
    small_max: Decimal = Decimal('15000')
    medium_max: Decimal = Decimal('60000')
    budget_min: Decimal = Decimal('1000')
    budget_max: Decimal = Decimal('3000')
    gross_margin: Decimal = Decimal('0.30')
    delivery_commission: Decimal = Decimal('0.30')
    payback_months: int = 12


@dataclass
class Inputs:
    reviews: int | None = None
    rating: Decimal | None = None
    years: Decimal | None = None
    ticket: Decimal | None = None
    price_level: str = ''
    tables: int | None = None
    people_per_table: Decimal | None = None
    services_per_day: int | None = None
    rotations: Decimal | None = None
    days_per_week: int | None = None
    occupancy_pct: Decimal | None = None
    concurrency: str = ''
    declared_annual: Decimal | None = None
    declared_source: str = ''
    uses_delivery: bool = False


@dataclass
class Result:
    low: Decimal | None
    mid: Decimal | None
    high: Decimal | None
    confidence: str
    band: str
    budget: Decimal | None
    ticket: Decimal | None
    ticket_source: str
    lines: list
    payback: dict


def D(value):
    return value if isinstance(value, Decimal) else Decimal(str(value))


def euros(value):
    return value.quantize(Decimal('1'), rounding=ROUND_HALF_UP)


def fmt(value):
    """1234.5 -> '1.235 €'; small amounts keep their cents: 5.4 -> '5,40 €'."""
    value = D(value)
    if value < 100 and value != value.to_integral():
        return f'{value:.2f} €'.replace('.', ',')
    return f'{int(euros(value)):,} €'.replace(',', '.')


def pct(value):
    text = f'{D(value) * 100:.1f}'.rstrip('0').rstrip('.')
    return text.replace('.', ',') + ' %'


def number(value):
    value = D(value)
    return (f'{value:,.0f}' if value == value.to_integral() else f'{value:,.1f}').replace(',', '_').replace('.', ',').replace('_', '.')


def ticket_for(inputs, settings):
    """(ticket, source): typed by hand, or from Google's price level through the settings table."""
    if inputs.ticket:
        return D(inputs.ticket), 'manual'
    if inputs.price_level in settings.price_level_tickets:
        return D(settings.price_level_tickets[inputs.price_level]), 'price_level'
    return None, ''


def reviews_method(inputs, settings, ticket):
    if not (inputs.reviews and inputs.years and ticket):
        return None
    per_year = D(inputs.reviews) / D(inputs.years)

    def monthly(rate):
        return per_year / D(rate) / 12 * ticket
    low, mid, high = monthly(settings.review_rate_high), monthly(settings.review_rate), monthly(settings.review_rate_low)
    clients = per_year / D(settings.review_rate)
    line = (f'Reseñas: {number(inputs.reviews)} en {number(inputs.years)} años ≈ {number(per_year)} al año. '
            f'Si deja reseña el {pct(settings.review_rate)} de los clientes, son unos {number(euros(clients))} clientes/año × {fmt(ticket)} '
            f'= {fmt(mid)}/mes (entre {fmt(low)} y {fmt(high)} con una tasa del {pct(settings.review_rate_high)} al {pct(settings.review_rate_low)}).')
    return low, mid, high, line


def occupancy_for(inputs, settings):
    """(fraction, how it was obtained) or (None, '')."""
    if inputs.occupancy_pct is not None:
        return D(inputs.occupancy_pct) / 100, 'observada'
    if inputs.concurrency in settings.concurrency_occupancy:
        return D(settings.concurrency_occupancy[inputs.concurrency]) / 100, f'por concurrencia {CONCURRENCY[inputs.concurrency].lower()}'
    return None, ''


def capacity_method(inputs, settings, ticket):
    occupancy, how = occupancy_for(inputs, settings)
    needed = [inputs.tables, inputs.people_per_table, inputs.services_per_day, inputs.rotations, inputs.days_per_week, occupancy, ticket]
    if any(value is None or value == 0 for value in needed):
        return None
    covers_week = D(inputs.tables) * D(inputs.people_per_table) * D(inputs.rotations) * D(inputs.services_per_day) * D(inputs.days_per_week) * occupancy
    mid = covers_week * ticket * WEEKS_PER_MONTH
    low, high = mid * (1 - CAPACITY_SPREAD), mid * (1 + CAPACITY_SPREAD)
    line = (f'Aforo: {number(inputs.tables)} mesas × {number(inputs.people_per_table)} personas × {number(inputs.rotations)} rotaciones × '
            f'{number(inputs.services_per_day)} servicios × {number(inputs.days_per_week)} días × {pct(occupancy)} de ocupación ({how}) '
            f'≈ {number(euros(covers_week))} cubiertos/semana × {fmt(ticket)} = {fmt(mid)}/mes (±{pct(CAPACITY_SPREAD)}).')
    return low, mid, high, line


def band_for(mid, settings):
    if mid < D(settings.small_max):
        return 'small'
    return 'medium' if mid < D(settings.medium_max) else 'large'


def budget_for(band, settings):
    """Small → bottom of the range, large → top, medium → the middle, rounded to 100 €."""
    low, high = D(settings.budget_min), D(settings.budget_max)
    if band == 'small':
        return low
    if band == 'large':
        return high
    return ((low + high) / 2 / 100).quantize(Decimal('1'), rounding=ROUND_HALF_UP) * 100


def payback_for(budget, ticket, inputs, settings):
    """"La web se paga con N clientes nuevos al mes durante X meses"."""
    if not (budget and ticket):
        return {}
    months = settings.payback_months
    margin = ticket * D(settings.gross_margin)
    clients = math.ceil(budget / (margin * months))
    result = {'months': months, 'clients_per_month': clients,
              'text': f'La web se paga con {clients} cliente{"s" if clients != 1 else ""} nuevo{"s" if clients != 1 else ""} al mes durante {months} meses '
                      f'(ticket de {fmt(ticket)} y margen bruto del {pct(settings.gross_margin)}: {fmt(margin)} por cliente).'}
    if inputs.uses_delivery:
        saved = ticket * D(settings.delivery_commission)
        orders = math.ceil(budget / (saved * months))
        result['delivery_orders_per_month'] = orders
        result['delivery_text'] = (f'O con {orders} pedido{"s" if orders != 1 else ""} al mes que hoy llegan por apps de reparto y pasen a ser directos '
                                   f'durante {months} meses (ahorra la comisión del {pct(settings.delivery_commission)}: {fmt(saved)} por pedido).')
    return result


def estimate(inputs, settings=None):
    settings = settings or Settings()
    ticket, ticket_source = ticket_for(inputs, settings)
    lines = []
    if ticket_source == 'price_level':
        lines.append(f'Ticket medio: {fmt(ticket)}, deducido del nivel de precios de Google.')
    methods = [m for m in (reviews_method(inputs, settings, ticket), capacity_method(inputs, settings, ticket)) if m]
    lines += [m[3] for m in methods]
    if inputs.declared_annual:
        mid = D(inputs.declared_annual) / 12
        low, high = mid * (1 - DECLARED_SPREAD), mid * (1 + DECLARED_SPREAD)
        source = f' ({inputs.declared_source})' if inputs.declared_source else ''
        lines.insert(0, f'Facturación declarada{source}: {fmt(inputs.declared_annual)}/año = {fmt(mid)}/mes. Manda sobre los demás métodos.')
        confidence = 'high'
    elif len(methods) == 2:
        mid = (methods[0][1] + methods[1][1]) / 2
        low, high = min(m[0] for m in methods), max(m[2] for m in methods)
        ratio = max(m[1] for m in methods) / min(m[1] for m in methods)
        agree = ratio <= 2
        confidence = 'medium' if agree else 'low'
        lines.append(f'Los dos métodos {"coinciden" if agree else "no coinciden"} (uno es {number(ratio)} veces el otro): se toma la media.')
    elif methods:
        low, mid, high = methods[0][:3]
        confidence = 'low'
    else:
        missing = []
        if not ticket:
            missing.append('ticket medio (o nivel de precios de Google)')
        if not (inputs.reviews and inputs.years):
            missing.append('reseñas y años del negocio')
        lines.append('Faltan datos para estimar: ' + ', '.join(missing or ['mesas, personas, servicios, rotaciones, días y ocupación']) + '.')
        return Result(None, None, None, 'low', '', None, ticket, ticket_source, lines, {})
    low, mid, high = euros(low), euros(mid), euros(high)
    band = band_for(mid, settings)
    budget = budget_for(band, settings)
    return Result(low, mid, high, confidence, band, budget, ticket, ticket_source, lines, payback_for(budget, ticket, inputs, settings))
