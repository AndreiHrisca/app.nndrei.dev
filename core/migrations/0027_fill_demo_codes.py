"""Give a card code to every business that already has a demo."""
import secrets
from django.core.files.storage import default_storage
from django.db import migrations

# Frozen copy of core.demo_codes.CODE_ALPHABET: a migration must not change with the code.
ALPHABET = '23456789ABCDEFGHJKMNPQRSTUVWXYZ'


def fill(apps, schema_editor):
    Business = apps.get_model('core', 'Business')
    DemoVersion = apps.get_model('core', 'DemoVersion')
    with_versions = set(DemoVersion.objects.values_list('business_id', flat=True))
    taken = set(Business.objects.exclude(demo_code__isnull=True).values_list('demo_code', flat=True))
    for business in Business.objects.filter(demo_code__isnull=True).order_by('pk'):
        # Before versions, a demo was a single file keyed by the primary key.
        if business.pk not in with_versions and not default_storage.exists(f'demos/{business.pk}.html'):
            continue
        code = ''.join(secrets.choice(ALPHABET) for _ in range(5))
        while code in taken:
            code = ''.join(secrets.choice(ALPHABET) for _ in range(5))
        taken.add(code)
        Business.objects.filter(pk=business.pk).update(demo_code=code)


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0026_demo_codes'),
    ]

    operations = [
        migrations.RunPython(fill, migrations.RunPython.noop),
    ]
