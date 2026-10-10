"""Estimación de facturación on the business card (admin only)."""
from decimal import Decimal
from django import forms
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import transaction
from django.shortcuts import render
from .access import scoped
from .auth_views import roles
from .estimation import CONFIDENCE, BANDS, Inputs, estimate
from .models import Business, Estimation, EstimationSettings
from .services import activity

SERVICES = [('breakfast', 'Desayuno'), ('lunch', 'Comida'), ('dinner', 'Cena')]
INPUT_FIELDS = ['reviews', 'rating', 'years', 'ticket', 'tables', 'people_per_table', 'services', 'rotations', 'days_per_week',
                'occupancy_pct', 'concurrency', 'declared_annual', 'declared_source', 'uses_delivery']
LIMITS = {'rating': (0, 5), 'years': (Decimal('0.1'), 200), 'ticket': (Decimal('0.5'), 10000), 'tables': (1, 500),
          'people_per_table': (1, 50), 'rotations': (Decimal('0.1'), 20), 'days_per_week': (1, 7), 'occupancy_pct': (0, 100),
          'declared_annual': (0, Decimal('1e11'))}


class EstimationForm(forms.ModelForm):
    services = forms.MultipleChoiceField(label='Servicios al día', choices=SERVICES, required=False, widget=forms.CheckboxSelectMultiple)

    class Meta:
        model = Estimation
        fields = INPUT_FIELDS
        widgets = {'declared_source': forms.TextInput(attrs={'placeholder': 'Cuentas anuales 2025, Registro Mercantil…'})}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name, (low, high) in LIMITS.items():
            self.fields[name].validators += [MinValueValidator(low), MaxValueValidator(high)]
        for name in ['reviews', 'rating', 'years', 'ticket', 'tables', 'people_per_table', 'rotations', 'days_per_week', 'occupancy_pct', 'declared_annual']:
            self.fields[name].widget.attrs.setdefault('inputmode', 'decimal')


def inputs_from(data, price_level=''):
    """Estimation inputs (a dict of cleaned values) as the pure module's Inputs."""
    return Inputs(reviews=data.get('reviews'), rating=data.get('rating'), years=data.get('years'), ticket=data.get('ticket'),
                  price_level=price_level, tables=data.get('tables'), people_per_table=data.get('people_per_table'),
                  services_per_day=len(data.get('services') or []) or None, rotations=data.get('rotations'),
                  days_per_week=data.get('days_per_week'), occupancy_pct=data.get('occupancy_pct'), concurrency=data.get('concurrency') or '',
                  declared_annual=data.get('declared_annual'), declared_source=data.get('declared_source') or '', uses_delivery=bool(data.get('uses_delivery')))


@transaction.atomic
def save_estimation(user, business, data, source='web'):
    """Compute and store one estimate. `data` holds cleaned input values; used by the web and by MCP."""
    config = EstimationSettings.load()
    assumptions = config.as_settings()
    result = estimate(inputs_from(data, business.google_price_level), assumptions)
    item = Estimation(business=business, created_by=user, source=source, price_level=business.google_price_level,
                      monthly_low=result.low, monthly_mid=result.mid, monthly_high=result.high, confidence=result.confidence,
                      band=result.band, suggested_budget=result.budget, used_ticket=result.ticket, explanation=result.lines,
                      payback=result.payback, assumptions={k: str(v) if isinstance(v, Decimal) else v for k, v in vars(assumptions).items()})
    for name in INPUT_FIELDS:
        setattr(item, name, data.get(name) if name != 'services' else list(data.get('services') or []))
    item.uses_delivery = bool(data.get('uses_delivery'))
    item.declared_source = data.get('declared_source') or ''
    item.concurrency = data.get('concurrency') or ''
    item.save()
    # Developers read the internal history too: no figures here, only that it happened.
    activity(business, 'estimation', 'Nueva estimación de facturación.' + (' (Claude)' if source == 'mcp' else ''), user, item, client_visible=False)
    return item


def initial_for(business, last):
    """Start from the last estimate, or from what Google already told us."""
    if last:
        return {name: getattr(last, name) for name in INPUT_FIELDS}
    return {'reviews': business.google_reviews, 'rating': business.google_rating}


def estimation_card(request, business, editing=False, form=None):
    return render(request, 'core/estimation.html', estimation_context(request.user, business, editing, form))


def estimation_context(user, business, editing=False, form=None):
    history = Estimation.objects.visible_to(user).filter(business=business)
    last = history.first()
    if editing and form is None:
        form = EstimationForm(initial=initial_for(business, last))
    groups = []
    if form is not None:
        groups = [('Reseñas de Google', [form[n] for n in ['reviews', 'rating', 'years']]),
                  ('Ticket', [form['ticket']]),
                  ('Aforo observado', [form[n] for n in ['tables', 'people_per_table', 'services', 'rotations', 'days_per_week', 'occupancy_pct', 'concurrency']]),
                  ('Facturación declarada', [form[n] for n in ['declared_annual', 'declared_source']]),
                  ('Reparto', [form['uses_delivery']])]
    price_tickets = EstimationSettings.load().price_level_tickets
    return {
        'business': business, 'estimation': last, 'history': history[1:10], 'estimation_form': form, 'groups': groups, 'editing': editing,
        'price_ticket': price_tickets.get(business.google_price_level), 'bands': BANDS, 'confidences': CONFIDENCE}


@roles('admin')
def business_estimation(request, public_id):
    b = scoped(Business, request.user, public_id)
    if request.method == 'POST':
        form = EstimationForm(request.POST)
        if form.is_valid():
            save_estimation(request.user, b, form.cleaned_data)
            return estimation_card(request, b)
        return estimation_card(request, b, True, form)
    return estimation_card(request, b, editing=request.GET.get('edit') == '1')
