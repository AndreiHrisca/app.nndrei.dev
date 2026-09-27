from decimal import Decimal, InvalidOperation
from django import template
register = template.Library()

DASH = '—'

@register.filter
def eur(value):
    if value is None:
        return 'Por definir'
    return f'{Decimal(value):,.2f}'.replace(',', 'X').replace('.', ',').replace('X', '.') + ' €'

@register.filter
def eur_short(value):
    """Rounded amount in Spanish notation: 1500 -> "1.500 €"."""
    try:
        amount = Decimal(value)
    except (TypeError, ValueError, InvalidOperation):
        return DASH
    return f'{amount:,.0f}'.replace(',', '.') + ' €'

@register.filter
def miles(value):
    """Integer with the Spanish thousands separator: 1960 -> "1.960"."""
    try:
        number = int(value)
    except (TypeError, ValueError):
        return DASH
    return f'{number:,}'.replace(',', '.')

@register.filter
def nota(value):
    """Google rating with a decimal comma: 4.7 -> "4,7"."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return DASH
    return f'{number:.1f}'.replace('.', ',')
