"""Going to a business: directions, "He ido" and bringing a discarded one back."""
from urllib.parse import quote, urlencode
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from . import labels
from .access import scoped
from .models import Business, FollowUp, StageChange, STAGE_ORDER, Visit
from .services import activity


def destination(business):
    """What to navigate to when there are no coordinates: the address, or name and place."""
    return business.address or ' '.join(x for x in [business.name, business.area_label] if x)


def directions(business):
    """Links that open each maps app with the business as destination."""
    has_point = business.latitude is not None and business.longitude is not None
    point = f'{business.latitude},{business.longitude}' if has_point else ''
    target = point or destination(business)
    google = {'api': '1', 'destination': target}
    if business.google_place_id:
        google['destination_place_id'] = business.google_place_id
    return {
        'google': 'https://www.google.com/maps/dir/?' + urlencode(google),
        'waze': 'https://waze.com/ul?' + (urlencode({'ll': point, 'navigate': 'yes'}) if has_point else urlencode({'q': target, 'navigate': 'yes'})),
        'apple': 'https://maps.apple.com/?' + urlencode({'daddr': target}),
        'phone': 'tel:' + quote(business.phone.replace(' ', ''), safe='+') if business.phone else '',
    }


def move_forward_to_visited(business, user):
    """A visit puts an earlier business in "Visitado"; it never moves one back."""
    if STAGE_ORDER[business.stage] >= STAGE_ORDER['visited']:
        return False
    StageChange.objects.create(business=business, from_stage=business.stage, to_stage='visited', actor=user)
    business.stage = 'visited'
    business.stage_changed_at = timezone.now()
    activity(business, 'stage_changed', labels.stage_activity('visited'), user, business, client_visible=False)
    return True


@transaction.atomic
def record_visit(user, public_id, data):
    """Save "He ido": the visit, its next step and the stage, as one change."""
    business = scoped(Business, user, public_id, lock=True)
    followup = None
    if data.get('next_step') and data.get('followup_date'):
        followup = FollowUp.objects.create(business=business, description=data['next_step'], date=data['followup_date'],
                                           owner=user, kind=data.get('followup_kind') or 'visit')
    visit = Visit.objects.create(business=business, visitor=user, date=data.get('date') or timezone.now(),
                                 talked_to=data.get('talked_to', ''), result=data['result'], notes=data.get('notes', ''),
                                 next_step=data.get('next_step', ''), followup_date=data.get('followup_date'), followup=followup)
    text = f'{labels.VISIT_DONE}: {visit.result}'
    if visit.talked_to:
        text += f' · Hablé con {visit.talked_to}'
    if visit.notes:
        text += f'\n{visit.notes}'
    if visit.next_step:
        text += f'\nPróximo paso: {visit.next_step}' + (f' ({visit.followup_date:%d/%m/%Y})' if visit.followup_date else '')
    activity(business, 'visit', text, user, visit, client_visible=False)
    moved = move_forward_to_visited(business, user)
    fields = ['stage', 'stage_changed_at'] if moved else []
    if visit.next_step:
        # The next step also becomes the business's "Próxima acción" (Clientes table).
        business.next_action, business.next_action_date = visit.next_step[:200], visit.followup_date
        fields += ['next_action', 'next_action_date']
    if fields:
        business.save(update_fields=fields)
    return visit, moved


@transaction.atomic
def reactivate(user, public_id):
    """Take a discarded business back to the stage it was discarded from."""
    business = scoped(Business, user, public_id, lock=True)
    if business.stage != 'discarded':
        raise ValidationError('Solo se reactiva un negocio descartado.')
    last = StageChange.objects.filter(business=business, to_stage='discarded').order_by('-occurred_at', '-pk').first()
    target = last.from_stage if last else 'found'
    StageChange.objects.create(business=business, from_stage='discarded', to_stage=target, actor=user)
    business.stage, business.stage_changed_at, business.discarded_reason = target, timezone.now(), ''
    business.save(update_fields=['stage', 'stage_changed_at', 'discarded_reason'])
    activity(business, 'stage_changed', f'{labels.REACTIVATE}: {labels.stage_activity(target)}', user, business, client_visible=False)
    return business
