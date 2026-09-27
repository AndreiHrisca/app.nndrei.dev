"""Sector becomes a SearchCategory foreign key, plus the next-action fields.

The old free text is kept as `legacy_category` so 0013 can translate it; 0014
drops it once every row points at a category.
"""
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('core', '0011_public_id_unique')]
    operations = [
        migrations.RenameField(model_name='business', old_name='category', new_name='legacy_category'),
        migrations.AddField(model_name='business', name='category', field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='businesses', to='core.searchcategory', verbose_name='Sector')),
        migrations.AddField(model_name='business', name='next_action', field=models.CharField(blank=True, max_length=200, verbose_name='Próxima acción')),
        migrations.AddField(model_name='business', name='next_action_date', field=models.DateField(blank=True, null=True, verbose_name='Fecha de la próxima acción')),
    ]
