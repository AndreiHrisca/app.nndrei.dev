"""Single source of truth for the wording people see.

Menus, titles, buttons, badges, flash messages, emails and the activity history
all read from here, so renaming something is a one-line change. Only visible
text lives here: model names, fields, URLs and stored values never change.
Templates read the constants with {% label 'NAME' %}.
"""

# --- Pipeline ("Seguimiento") --------------------------------------------------

PIPELINE = 'Seguimiento'
PIPELINE_SUBTITLE = 'Cada negocio, desde que lo encuentras hasta que su web está publicada.'
NEW_BUSINESS = '+ Nuevo negocio'
NEW_BUSINESS_TITLE = 'Nuevo negocio'
EMPTY_PIPELINE = 'Añade tu primer negocio para empezar.'
CLIENTS_SUBTITLE = 'Todos los negocios, uno por uno.'
STAGE = 'Fase'


def in_progress_count(count):
    return f'{count} en marcha'


# Stored key -> visible name. The order of the pipeline lives in models.STAGES.
STAGE_LABELS = {
    'found': 'Encontrado',
    'profile_created': 'Estudiado',
    'visited': 'Visitado',
    'proposal': 'Propuesta enviada',
    'review': 'Cambios en la propuesta',
    'approved': 'Aprobado',
    'development': 'En construcción',
    'testing': 'Revisión final',
    'production': 'Web publicada',
    'discarded': 'Descartado',
}


def stage_activity(stage):
    return 'Fase: ' + STAGE_LABELS[stage]


# --- Roles, requests and other visible choices --------------------------------

ROLE_LABELS = {'admin': 'Administrador', 'developer': 'Desarrollador', 'accountant': 'Contador', 'client': 'Cliente'}

REQUEST_STATE_LABELS = {
    'requested': 'Solicitada',
    'accepted': 'Aceptada',
    'in_progress': 'En construcción',
    'testing': 'Revisión final',
    'production': 'Publicada',
    'rejected': 'Rechazada',
}

OPPORTUNITY_IN_PIPELINE = 'En seguimiento'
HOSTING = 'Alojamiento web'

# --- Security and demos --------------------------------------------------------

TWO_FACTOR = 'Verificación en dos pasos'
TWO_FACTOR_SHORT = 'Verificación'
SECURITY = 'Seguridad · ' + TWO_FACTOR

DEMO = 'Web de muestra'
DEMO_LOWER = 'web de muestra'

# --- Opportunity actions ("Buscar clientes") -----------------------------------

CAPTURE = 'Captar'
DISCARD = 'Descartar'

CAPTURE_HINT = 'Captar el negocio: crea su ficha y lo añade a ' + PIPELINE
DISCARD_HINT = 'Descartar la oportunidad: deja de aparecer como nueva'
DISCARD_CONFIRM = '¿Descartar esta oportunidad?'

CAPTURE_ACTIVITY = CAPTURE + ': negocio añadido a ' + PIPELINE + ' desde la búsqueda.'


def capture_message(name):
    return f'{CAPTURE}: {name} ya está en {PIPELINE}.'


def discard_message(name):
    return f'{DISCARD}: {name} ya no aparecerá como oportunidad nueva.'
