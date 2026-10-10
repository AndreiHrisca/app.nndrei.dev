"""Opening hours on the business card: show, edit by hand, go back to Google."""
import re
from django import forms
from django.conf import settings
from django.contrib import messages
from django.db import transaction
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from .access import scoped
from .auth_views import roles
from .hours import DAY_NAMES, WEEK_ORDER, hours_status, period, slots_by_day, week_summary
from .models import Business, PlacesUsage
from .services import activity

SLOTS = 3
TIME = re.compile(r'^([01]\d|2[0-3]):[0-5]\d$')


def minutes(value):
    hour, minute = map(int, value.split(':'))
    return hour * 60 + minute


class HoursForm(forms.Form):
    """Seven days, up to three slots each. Built from the raw POST so the
    editor can be one plain grid of <input type=time>; `clean` returns periods."""

    def __init__(self, data):
        super().__init__(data)
        self.periods = []
        self.day_errors = {}

    def clean(self):
        periods = []
        for day in range(7):
            if self.data.get(f'd{day}_closed'):
                continue
            spans = []
            for slot in range(SLOTS):
                opens = self.data.get(f'd{day}_s{slot}_open', '').strip()
                closes = self.data.get(f'd{day}_s{slot}_close', '').strip()
                if not opens and not closes:
                    continue
                if not (TIME.match(opens) and TIME.match(closes)):
                    self.day_errors[day] = 'Rellena la apertura y el cierre de cada tramo.'
                    break
                if opens == closes and opens != '00:00':
                    self.day_errors[day] = 'La apertura y el cierre no pueden ser la misma hora (00:00 a 00:00 es todo el día).'
                    break
                start = minutes(opens)
                end = minutes(closes)
                if end <= start:
                    end += 1440  # Closes after midnight.
                spans.append((start, end, opens, closes))
            spans.sort()
            if any(later[0] < earlier[1] for earlier, later in zip(spans, spans[1:])):
                self.day_errors.setdefault(day, 'Los tramos se solapan.')
            if day not in self.day_errors:
                periods += [period(day, opens, closes) for _, _, opens, closes in spans]
        if self.day_errors:
            raise forms.ValidationError('Revisa los días marcados.')
        self.periods = periods
        return {}


def editor_days(business, form=None):
    """One row per day, Monday first, with what the inputs should show."""
    saved = slots_by_day(business.opening_hours)
    rows = []
    for day in WEEK_ORDER:
        if form is not None:
            values = [(form.data.get(f'd{day}_s{i}_open', ''), form.data.get(f'd{day}_s{i}_close', '')) for i in range(SLOTS)]
            closed = bool(form.data.get(f'd{day}_closed'))
        else:
            values = (saved[day] + [('', '')] * SLOTS)[:SLOTS]
            # Unknown hours start open with an empty slot; known hours with no slot that day are a closed day.
            closed = business.opening_hours is not None and not saved[day]
        slots = [{'index': i, 'open': o, 'close': c, 'visible': i == 0 or bool(o or c)} for i, (o, c) in enumerate(values)]
        rows.append({'day': day, 'name': DAY_NAMES[day].capitalize(), 'closed': closed, 'slots': slots,
                     'error': form.day_errors.get(day, '') if form is not None else ''})
    return rows


def hours_context(user, business, editing=False, form=None):
    now = timezone.now()
    today = DAY_NAMES[(timezone.localtime(now).weekday() + 1) % 7].capitalize()
    usage = PlacesUsage.objects.filter(month=timezone.localdate().replace(day=1)).first()
    return {'business': business, 'hours_state': hours_status(business.opening_hours, now),
            'hours_week': week_summary(business.opening_hours), 'today_name': today,
            'hours_editing': editing, 'hours_days': editor_days(business, form) if editing else [],
            'hours_form': form, 'can_edit_hours': user.role == 'admin',
            'places_left': max(0, settings.PLACES_MONTHLY_LIMIT - (usage.requests if usage else 0))}


def hours_card(request, business, editing=False, form=None, status=200):
    return render(request, 'core/hours.html', hours_context(request.user, business, editing, form), status=status)


@roles('admin')
def business_hours(request, public_id):
    b = scoped(Business, request.user, public_id)
    if request.method == 'POST':
        form = HoursForm(request.POST)
        if not form.is_valid():
            return hours_card(request, b, True, form, status=400 if not request.headers.get('HX-Request') else 200)
        with transaction.atomic():
            b = scoped(Business, request.user, public_id, lock=True)
            b.opening_hours, b.hours_source, b.hours_updated_at = form.periods, 'manual', timezone.now()
            b.save(update_fields=['opening_hours', 'hours_source', 'hours_updated_at'])
            activity(b, 'hours_updated', 'Horario puesto a mano.', request.user, client_visible=False)
        return hours_card(request, b) if request.headers.get('HX-Request') else redirect(b)
    return hours_card(request, b, editing=request.GET.get('edit') == '1')


@roles('admin')
@require_POST
def business_hours_reset(request, public_id):
    """Drop the hand-typed hours and use the last ones Google gave (no API call)."""
    with transaction.atomic():
        b = scoped(Business, request.user, public_id, lock=True)
        if b.hours_source == 'manual':
            b.opening_hours = b.google_hours
            b.hours_source = 'google' if b.google_hours else ''
            b.hours_updated_at = b.google_fetched_at if b.google_hours else timezone.now()
            b.save(update_fields=['opening_hours', 'hours_source', 'hours_updated_at'])
            activity(b, 'hours_updated', 'Horario: vuelve a usarse el de Google.', request.user, client_visible=False)
    return hours_card(request, b) if request.headers.get('HX-Request') else redirect(b)


@roles('admin')
@require_POST
def business_google_refresh(request, public_id):
    """"Actualizar desde Google": one Place Details call, asked for on purpose."""
    from .places import PlacesError, refresh_business
    b = scoped(Business, request.user, public_id)
    try:
        refresh_business(b)
        messages.success(request, 'Datos de Google actualizados.')
    except PlacesError as exc:
        messages.error(request, str(exc))
    return redirect(b)
