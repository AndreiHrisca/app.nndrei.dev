"""Consultas: the messages of the nndrei.dev contact form. Administrators only."""
import uuid
from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponseBadRequest, HttpResponseRedirect
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST
from . import labels
from .access import scoped
from .auth_views import roles
from .inquiries import MANUAL_STATUSES, change_status, convert, mailto_url, mark_opened, matching_businesses, whatsapp_url
from .models import Business, Inquiry, INQUIRY_STATUSES

# The default list is the work still to do: archived and spam only on request.
INBOX_EXCLUDED = ('archived', 'spam')
ORIGIN_FILTERS = {
    'card': Q(source='card') | Q(origin_page='/qr'),
    'web': Q(source__in=['', 'direct']) & ~Q(origin_page='/qr'),
    'campaign': ~Q(source__in=['', 'direct', 'card']) & ~Q(origin_page='/qr'),
}


class ExternalRedirect(HttpResponseRedirect):
    """The reply buttons end in the mail app or in WhatsApp."""
    allowed_schemes = ['https', 'mailto']


@roles('admin')
def inquiries(request):
    items = Inquiry.objects.visible_to(request.user).select_related('business')
    status = request.GET.get('estado', '')
    if status == 'todas':
        pass
    elif status in dict(INQUIRY_STATUSES):
        items = items.filter(status=status)
    else:
        status = ''
        items = items.exclude(status__in=INBOX_EXCLUDED)
    origin = request.GET.get('origen', '')
    if origin in ORIGIN_FILTERS:
        items = items.filter(ORIGIN_FILTERS[origin])
    else:
        origin = ''
    query = request.GET.get('q', '').strip()
    if query:
        items = items.filter(Q(name__icontains=query) | Q(business_name__icontains=query) | Q(email__icontains=query))
    order = 'antiguas' if request.GET.get('orden') == 'antiguas' else 'recientes'
    items = items.order_by(*(['received_at', 'pk'] if order == 'antiguas' else ['-received_at', '-pk']))
    context = {'title': labels.INQUIRIES, 'inquiries': items[:300], 'statuses': INQUIRY_STATUSES, 'status': status, 'origin': origin,
               'origins': labels.INQUIRY_ORIGIN_LABELS.items(), 'query': query, 'order': order, 'filtered': bool(status or origin or query)}
    partial = request.headers.get('HX-Request') and not request.headers.get('HX-Boosted')
    return render(request, 'core/inquiries_table.html' if partial else 'core/inquiries.html', context)


@roles('admin')
def inquiry_detail(request, public_id):
    inquiry = scoped(Inquiry, request.user, public_id)
    mark_opened(inquiry, request.user)
    search = request.GET.get('buscar', '').strip()
    found = Business.objects.filter(Q(name__icontains=search) | Q(legal_name__icontains=search)).order_by('name')[:10] if len(search) >= 2 else []
    return render(request, 'core/inquiry.html', {
        'title': str(inquiry), 'inquiry': inquiry, 'changes': inquiry.changes.select_related('actor'),
        'whatsapp': whatsapp_url(inquiry), 'manual_statuses': [(k, v) for k, v in INQUIRY_STATUSES if k in MANUAL_STATUSES and k != inquiry.status],
        'matches': matching_businesses(inquiry) if not inquiry.business_id else [], 'search': search, 'found': found,
    })


@roles('admin')
@require_POST
def inquiry_status(request, public_id):
    with transaction.atomic():
        inquiry = scoped(Inquiry, request.user, public_id, lock=True)
        status = request.POST.get('status')
        if status not in MANUAL_STATUSES or inquiry.status == 'converted':
            return HttpResponseBadRequest('Estado no permitido.')
        change_status(inquiry, status, request.user)
    messages.success(request, f'Consulta marcada como {labels.INQUIRY_STATUS_LABELS[status].lower()}.')
    return redirect('inquiries' if status in INBOX_EXCLUDED else inquiry)


@roles('admin')
@require_POST
def inquiry_notes(request, public_id):
    inquiry = scoped(Inquiry, request.user, public_id)
    inquiry.internal_notes = request.POST.get('internal_notes', '')[:10000]
    inquiry.save(update_fields=['internal_notes'])
    messages.success(request, 'Notas guardadas.')
    return redirect(inquiry)


@roles('admin')
@require_POST
def inquiry_reply(request, public_id, channel):
    with transaction.atomic():
        inquiry = scoped(Inquiry, request.user, public_id, lock=True)
        url = mailto_url(inquiry) if channel == 'email' else whatsapp_url(inquiry) if channel == 'whatsapp' else ''
        if not url:
            return HttpResponseBadRequest('No hay forma de responder por ese canal.')
        if inquiry.status != 'converted':
            change_status(inquiry, 'replied', request.user, 'Email' if channel == 'email' else 'WhatsApp')
    return ExternalRedirect(url)


@roles('admin')
@require_POST
def inquiry_convert(request, public_id):
    with transaction.atomic():
        inquiry = scoped(Inquiry, request.user, public_id, lock=True)
        if inquiry.business_id:
            return redirect(inquiry.business)
        try:
            target = uuid.UUID(request.POST['business']) if request.POST.get('business') else None
        except ValueError:
            return HttpResponseBadRequest('Ficha no válida.')
        business = scoped(Business, request.user, target) if target else None
        business = convert(inquiry, request.user, business)
    messages.success(request, f'Consulta convertida: {business.name}.')
    return redirect(business)


@roles('admin')
@require_POST
def inquiry_delete(request, public_id):
    """For erasure requests: the inquiry and its history are gone for good."""
    from .services import activity
    with transaction.atomic():
        inquiry = scoped(Inquiry, request.user, public_id, lock=True)
        if inquiry.business_id:
            activity(inquiry.business, 'inquiry', 'Consulta de nndrei.dev eliminada.', request.user, client_visible=False)
        inquiry.delete()
    messages.success(request, 'Consulta eliminada definitivamente.')
    return redirect('inquiries')
