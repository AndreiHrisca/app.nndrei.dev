"""Consultas from nndrei.dev: status changes, replies, conversion into a client and retention.

The sync that brings them here lives in core/inquiry_sync.py.
"""
import re
from datetime import timedelta
from urllib.parse import quote, urlencode
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from . import labels
from .models import Business, Inquiry, InquiryStatusChange, INQUIRY_RETENTION_DAYS

# What can be chosen by hand. "converted" only comes from converting.
MANUAL_STATUSES = {'read', 'replied', 'archived', 'spam'}


def change_status(inquiry, status, actor=None, note=''):
    if inquiry.status == status:
        return False
    InquiryStatusChange.objects.create(inquiry=inquiry, from_status=inquiry.status, to_status=status, actor=actor, note=note)
    inquiry.status = status
    inquiry.status_changed_at = timezone.now()
    inquiry.save(update_fields=['status', 'status_changed_at'])
    return True


def mark_opened(inquiry, actor):
    """Opening a new inquiry is reading it."""
    if inquiry.status == 'new':
        change_status(inquiry, 'read', actor)


def phone_digits(phone):
    digits = re.sub(r'\D', '', phone or '')
    if (phone or '').strip().startswith('00'):
        digits = digits[2:]
    # A Spanish number typed without its prefix.
    if len(digits) == 9 and digits[0] in '6789':
        digits = '34' + digits
    return digits


def first_name(inquiry):
    return (inquiry.name.split() or [''])[0]


def mailto_url(inquiry):
    body = f'Hola {first_name(inquiry)},\n\nGracias por escribirme desde nndrei.dev.\n\n'
    return 'mailto:' + quote(inquiry.email, safe='@') + '?' + urlencode({'subject': labels.INQUIRY_MAIL_SUBJECT, 'body': body}, quote_via=quote)


def whatsapp_url(inquiry):
    digits = phone_digits(inquiry.phone)
    if len(digits) < 8:
        return ''
    text = f'Hola {first_name(inquiry)}, soy Andrei, de nndrei.dev. Te escribo por la consulta que me dejaste en la web.'
    return f'https://wa.me/{digits}?' + urlencode({'text': text}, quote_via=quote)


def matching_businesses(inquiry, limit=8):
    """Client cards that may be this person's: same phone, same email or a similar business name."""
    query = Q(pk__in=[])
    digits = phone_digits(inquiry.phone)
    if len(digits) >= 9:
        # Phones are stored as typed; compare the last nine digits.
        tail = digits[-9:]
        candidates = [b.pk for b in Business.objects.exclude(phone='').only('pk', 'phone') if phone_digits(b.phone)[-9:] == tail]
        query |= Q(pk__in=candidates)
    if inquiry.email:
        query |= Q(email__iexact=inquiry.email)
    name = inquiry.business_name.strip()
    if len(name) >= 3:
        query |= Q(name__icontains=name) | Q(legal_name__icontains=name)
    return Business.objects.filter(query).select_related('category').order_by('name')[:limit]


def business_notes(inquiry):
    lines = [f'Llegó por el formulario de nndrei.dev ({inquiry.get_origin_display()}) el {timezone.localtime(inquiry.received_at):%d/%m/%Y}.']
    if inquiry.need:
        lines.append('Qué necesita: ' + inquiry.need)
    if inquiry.budget:
        lines.append('Presupuesto: ' + inquiry.budget)
    lines.append('Mensaje: ' + inquiry.message)
    return '\n'.join(lines)


@transaction.atomic
def convert(inquiry, actor, business=None):
    """Link the inquiry to a client card, creating it prefilled if none is given."""
    from .services import activity
    created = business is None
    if created:
        business = Business.objects.create(name=inquiry.business_name or inquiry.name, phone=inquiry.phone, email=inquiry.email,
                                           contact=inquiry.name, internal_notes=business_notes(inquiry))
        activity(business, 'created', 'Ficha creada desde una consulta de nndrei.dev.', actor, client_visible=False)
        from .geo_sources import queue_placing
        queue_placing(business)
    else:
        # The audit text names no person: deleting the inquiry later leaves nothing personal behind.
        activity(business, 'inquiry', 'Consulta de nndrei.dev vinculada a esta ficha.', actor, client_visible=False)
    inquiry.business = business
    inquiry.save(update_fields=['business'])
    change_status(inquiry, 'converted', actor, 'Ficha nueva' if created else 'Vinculada a una ficha existente')
    return business


def purge_old_inquiries(now=None):
    """180 days, as the portfolio did. Converted ones follow their client card."""
    cutoff = (now or timezone.now()) - timedelta(days=INQUIRY_RETENTION_DAYS)
    return Inquiry.objects.filter(received_at__lt=cutoff).exclude(status='converted').delete()[1].get('core.Inquiry', 0)
