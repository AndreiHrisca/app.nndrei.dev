"""Step 3 of 3: the identifier is now mandatory and unique."""
import uuid
from django.db import migrations, models

MODELS = ['user', 'business', 'document', 'request', 'proposal', 'followup', 'placesnapshot', 'searchrun', 'clientcharge', 'transaction', 'recurringexpense', 'commission']


class Migration(migrations.Migration):
    dependencies = [('core', '0010_fill_public_id')]
    operations = [migrations.AlterField(model_name=name, name='public_id', field=models.UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True)) for name in MODELS]
