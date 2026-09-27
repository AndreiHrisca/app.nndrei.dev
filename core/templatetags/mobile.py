"""Phone chrome: bottom tabs per role, the app bar's back link and small icons."""
import re
from django import template
from django.utils.html import format_html
from django.utils.timesince import timesince

register = template.Library()

ICONS = {
    'pipeline': 'M4 6h16M4 12h10M4 18h6',
    'search': 'M21 21l-5-5M17 10a7 7 0 1 1-14 0 7 7 0 0 1 14 0',
    'clients': 'M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8M4 21c1-8 15-8 16 0',
    'doc': 'M7 3h7l5 5v13H7zM14 3v5h5',
    'bars': 'M5 20V11M11 20V5M17 20v-6M3 20h18',
    'folder': 'M3 6h6l2 2h10v11H3z',
    'home': 'M3 11l9-7 9 7M5 10v10h14V10',
    'more': 'M5 12h.01M12 12h.01M19 12h.01',
    'user': 'M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8M4 21c1-8 15-8 16 0',
    'back': 'M15 18l-6-6 6-6',
    'phone': 'M5 4h4l2 5-2.5 1.5a11 11 0 0 0 5 5L15 13l5 2v4a2 2 0 0 1-2 2A16 16 0 0 1 3 6a2 2 0 0 1 2-2',
    'map': 'M12 21s-7-6.2-7-11a7 7 0 0 1 14 0c0 4.8-7 11-7 11M12 12.5a2.5 2.5 0 1 0 0-5 2.5 2.5 0 0 0 0 5',
    'note': 'M4 20h4L19 9l-4-4L4 16zM13.5 6.5l4 4',
    'next': 'M5 12h14M13 6l6 6-6 6',
    'refresh': 'M20 11a8 8 0 0 0-14.8-4M4 5v4h4M4 13a8 8 0 0 0 14.8 4M20 19v-4h-4',
    'filter': 'M4 6h16M7 12h10M10 18h4',
    'pencil': 'M4 20h4L19 9l-4-4L4 16z',
    'chevron': 'M9 6l6 6-6 6',
    'close': 'M6 6l12 12M18 6L6 18',
    'users': 'M9 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8M2 21c1-8 13-8 14 0M17 4v8M18 15l4 6',
    'shield': 'M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z',
    'logout': 'M15 4h4v16h-4M10 8l-4 4 4 4M6 12h10',
    'download': 'M12 4v11M7 10l5 5 5-5M5 20h14',
}


@register.simple_tag
def icon(name, size=22):
    return format_html('<svg width="{}" height="{}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" '
                       'stroke-linecap="round" stroke-linejoin="round" aria-hidden="true" focusable="false"><path d="{}"/></svg>',
                       size, size, ICONS[name])


# Five thumbs' worth of destinations per role; everything else lives under "Más".
TABS = {
    'admin': [('Pipeline', '/', 'pipeline'), ('Buscar', '/buscar/', 'search'), ('Clientes', '/clientes/', 'clients'), ('Propuestas', '/propuestas/', 'doc'), ('Más', '/mas/', 'more')],
    'developer': [('Clientes', '/clientes/', 'clients'), ('Propuestas', '/propuestas/', 'doc'), ('Más', '/mas/', 'more')],
    'accountant': [('Clientes', '/clientes/', 'clients'), ('Propuestas', '/propuestas/', 'doc'), ('Finanzas', '/finanzas/', 'bars'), ('Más', '/mas/', 'more')],
    'client': [('Mi negocio', '/portal/', 'home'), ('Peticiones', '/portal/peticiones/', 'doc'), ('Gastos', '/portal/gastos/', 'bars'), ('Documentos', '/portal/documentos/', 'folder'), ('Cuenta', '/mas/', 'user')],
}
# Screens opened from "Más" keep that tab lit when no tab of their own matches.
UNDER_MORE = ('/finanzas/', '/usuarios/', '/2fa/')


def active_href(tabs, path):
    matches = [href for _, href, _ in tabs if (path == '/' if href == '/' else path.startswith(href))]
    if matches:
        return max(matches, key=len)
    return '/mas/' if path.startswith(UNDER_MORE) else None


@register.inclusion_tag('core/tabbar.html', takes_context=True)
def tabbar(context):
    request = context['request']
    tabs = TABS.get(request.user.role, [])
    current = active_href(tabs, request.path)
    return {'tabs': [{'label': label, 'href': href, 'icon': icon(name), 'active': href == current} for label, href, name in tabs]}


# Tab roots show the logo; every other screen gets "← Volver" to its parent.
ROOTS = {href for tabs in TABS.values() for _, href, _ in tabs} | {'/finanzas/', '/usuarios/'}
PARENTS = [(re.compile(r'^/clientes/[0-9a-f-]{36}/.+'), None), ('/clientes/', '/clientes/'), ('/propuestas/', '/propuestas/'),
           ('/finanzas/', '/finanzas/'), ('/usuarios/', '/usuarios/'), ('/buscar/', '/buscar/'), ('/2fa/', '/mas/')]


@register.simple_tag(takes_context=True)
def back_url(context):
    request = context['request']
    path = request.path
    if path in ROOTS:
        return ''
    if request.user.role == 'client':
        return '/portal/'
    for prefix, parent in PARENTS:
        if isinstance(prefix, str):
            if path.startswith(prefix):
                return parent
        elif prefix.match(path):
            # A sub-page of a business card goes back to the card itself.
            return path[:len('/clientes/') + 37]
    return '/'


@register.filter
def ago(value):
    """Short relative time with one unit: "hace 2 días"."""
    if not value:
        return ''
    return 'hace ' + timesince(value, depth=1)
