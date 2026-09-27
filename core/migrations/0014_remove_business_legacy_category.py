"""The free-text sector is gone; SearchCategory is the only vocabulary left."""
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('core', '0013_normalize_business_sector')]
    operations = [migrations.RemoveField(model_name='business', name='legacy_category')]
