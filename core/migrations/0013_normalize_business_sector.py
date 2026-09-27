"""Point every business at a SearchCategory, fixing spellings like "Bar".

The rules live in core.categories and are duplicated here on purpose: a data
migration has to keep working even if the runtime helper changes later.
"""
from django.db import migrations

ALIASES = {
    'bar': 'Bares', 'cafetería': 'Cafeterías', 'cafeteria': 'Cafeterías',
    'barbería': 'Barberías', 'barberia': 'Barberías',
    'peluquería': 'Peluquerías', 'peluqueria': 'Peluquerías',
    'dentista': 'Dentistas', 'clínica': 'Clínicas', 'clinica': 'Clínicas',
    'veterinario': 'Veterinarios', 'gimnasio': 'Gimnasios', 'hotel': 'Hoteles',
    'taller': 'Talleres', 'comercio': 'Comercios', 'restaurante': 'Restaurantes',
    'tienda': 'Comercios',
}
VOWELS = 'aeiouáéíóú'


def plural_candidates(name):
    lowered = name.lower()
    if lowered.endswith('z'):
        return [name, name[:-1] + 'ces']
    if lowered and lowered[-1] in VOWELS:
        return [name, name + 's']
    return [name, name + 'es']


def resolve_category(category_model, raw):
    name = (raw or '').strip()
    if not name:
        return None
    for candidate in [ALIASES.get(name.lower(), name)] + plural_candidates(name):
        match = category_model.objects.filter(name__iexact=candidate).first()
        if match:
            return match
    canonical = ALIASES.get(name.lower()) or plural_candidates(name)[-1]
    return category_model.objects.create(name=canonical[0].upper() + canonical[1:])


def forwards(apps, schema_editor):
    Business = apps.get_model('core', 'business')
    SearchCategory = apps.get_model('core', 'searchcategory')
    for business in Business.objects.exclude(legacy_category='').iterator():
        category = resolve_category(SearchCategory, business.legacy_category)
        if category:
            Business.objects.filter(pk=business.pk).update(category=category)


def backwards(apps, schema_editor):
    Business = apps.get_model('core', 'business')
    for business in Business.objects.exclude(category__isnull=True).select_related('category').iterator():
        Business.objects.filter(pk=business.pk).update(legacy_category=business.category.name)


class Migration(migrations.Migration):
    dependencies = [('core', '0012_business_sector_and_next_action')]
    operations = [migrations.RunPython(forwards, backwards)]
