"""Single source of truth for the wording of the opportunity actions.

Buttons, tooltips, flash messages and the activity history all read from here,
so renaming an action is a one-line change.
"""

CAPTURE = 'Captar'
DISCARD = 'Descartar'

CAPTURE_HINT = 'Captar el negocio: crea su ficha y lo mete en el pipeline'
DISCARD_HINT = 'Descartar la oportunidad: deja de aparecer como nueva'
DISCARD_CONFIRM = '¿Descartar esta oportunidad?'

CAPTURE_ACTIVITY = CAPTURE + ': negocio incorporado al pipeline desde la búsqueda.'


def capture_message(name):
    return f'{CAPTURE}: {name} ya está en el pipeline.'


def discard_message(name):
    return f'{DISCARD}: {name} ya no aparecerá como oportunidad nueva.'
