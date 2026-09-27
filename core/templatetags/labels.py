from django import template
from core import labels

register = template.Library()

NAMES = {name: getattr(labels, name) for name in ['CAPTURE', 'DISCARD', 'CAPTURE_HINT', 'DISCARD_HINT', 'DISCARD_CONFIRM']}


@register.simple_tag
def label(name):
    """Render a shared action literal, e.g. {% label 'CAPTURE' %}."""
    return NAMES[name]
