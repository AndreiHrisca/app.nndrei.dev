"""Normalisation of the free-text sector that businesses used to carry.

Searching ("Buscar clientes") has always worked with the SearchCategory
taxonomy; the business card used a free CharField, so the same sector could be
written "Bar", "bares" or "BARES". These helpers map any old spelling onto a
single SearchCategory so both views share one vocabulary.
"""

# Singular spellings we have actually seen, mapped to the canonical plural.
ALIASES = {
    'bar': 'Bares',
    'cafetería': 'Cafeterías',
    'cafeteria': 'Cafeterías',
    'barbería': 'Barberías',
    'barberia': 'Barberías',
    'peluquería': 'Peluquerías',
    'peluqueria': 'Peluquerías',
    'dentista': 'Dentistas',
    'clínica': 'Clínicas',
    'clinica': 'Clínicas',
    'veterinario': 'Veterinarios',
    'gimnasio': 'Gimnasios',
    'hotel': 'Hoteles',
    'taller': 'Talleres',
    'comercio': 'Comercios',
    'restaurante': 'Restaurantes',
    'tienda': 'Comercios',
}

VOWELS = 'aeiouáéíóú'


def plural_candidates(name):
    """Spanish plural guesses for a singular sector name, best first."""
    lowered = name.lower()
    candidates = [name]
    if lowered.endswith('z'):
        candidates.append(name[:-1] + 'ces')
    elif lowered and lowered[-1] in VOWELS:
        candidates.append(name + 's')
    else:
        candidates.append(name + 'es')
    return candidates


def resolve_category(category_model, raw):
    """Return the SearchCategory that matches `raw`, creating it as a last resort.

    `category_model` is passed in so migrations can hand over their historical
    model instead of importing core.models.
    """
    name = (raw or '').strip()
    if not name:
        return None
    for candidate in [ALIASES.get(name.lower(), name)] + plural_candidates(name):
        match = category_model.objects.filter(name__iexact=candidate).first()
        if match:
            return match
    canonical = ALIASES.get(name.lower()) or plural_candidates(name)[-1]
    return category_model.objects.create(name=canonical[0].upper() + canonical[1:])
