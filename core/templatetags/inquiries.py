from django import template
from core.models import Inquiry

register = template.Library()


@register.simple_tag(takes_context=True)
def new_inquiries(context):
    """Unread count for the menu badge; nothing for anyone but administrators."""
    user = context['request'].user
    if not user.is_authenticated or user.role != 'admin':
        return 0
    return Inquiry.objects.filter(status='new').count()
