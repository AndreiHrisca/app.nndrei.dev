"""Step 2 of 3: give every existing row its own UUID."""
import uuid
from django.db import migrations

MODELS = ['user', 'business', 'document', 'request', 'proposal', 'followup', 'placesnapshot', 'searchrun', 'clientcharge', 'transaction', 'recurringexpense', 'commission']


def fill(apps, schema_editor):
    for name in MODELS:
        model = apps.get_model('core', name)
        for pk in model.objects.filter(public_id__isnull=True).values_list('pk', flat=True).iterator():
            model.objects.filter(pk=pk).update(public_id=uuid.uuid4())


class Migration(migrations.Migration):
    dependencies = [('core', '0009_public_id_nullable')]
    operations = [migrations.RunPython(fill, migrations.RunPython.noop)]
