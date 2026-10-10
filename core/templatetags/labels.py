from django import template
from core import labels

register = template.Library()

# Every upper-case text constant in core.labels is available to templates.
NAMES = {name: value for name, value in vars(labels).items() if name.isupper() and isinstance(value, str)}


@register.simple_tag
def label(name):
    """Render a shared literal, e.g. {% label 'CAPTURE' %}."""
    return NAMES[name]


@register.simple_tag
def in_progress_count(count):
    return labels.in_progress_count(count)
