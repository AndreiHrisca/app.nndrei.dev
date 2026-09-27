"""Step 1 of 3: add the opaque identifier as a nullable column.

Adding it nullable and without a default keeps the change instant and safe for
the rows already in the table; 0010 fills them and 0011 locks the column down.
"""
from django.db import migrations, models

MODELS = ['user', 'business', 'document', 'request', 'proposal', 'followup', 'placesnapshot', 'searchrun', 'clientcharge', 'transaction', 'recurringexpense', 'commission']


class Migration(migrations.Migration):
    dependencies = [('core', '0008_protect_historical_versions')]
    operations = [migrations.AddField(model_name=name, name='public_id', field=models.UUIDField(null=True, editable=False, db_index=True)) for name in MODELS]
