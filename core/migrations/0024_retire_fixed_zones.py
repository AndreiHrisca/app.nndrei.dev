from django.db import migrations, models
import django.db.models.deletion


def drop_zone_slices(apps, schema_editor):
    # They recorded when a fixed zone was last searched; the new areas start afresh.
    apps.get_model('core', 'SearchSlice').objects.filter(area__isnull=True).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0023_cities_and_neighbourhoods'),
    ]

    operations = [
        migrations.RunPython(drop_zone_slices, migrations.RunPython.noop),
        migrations.RemoveConstraint(model_name='searchslice', name='unique_search_slice'),
        migrations.RemoveField(model_name='searchslice', name='zone'),
        migrations.AlterField(model_name='searchslice', name='area', field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='core.searcharea')),
        migrations.AddConstraint(model_name='searchslice', constraint=models.UniqueConstraint(fields=('area', 'category'), name='unique_search_area_slice')),
        migrations.RemoveField(model_name='placesnapshot', name='zone'),
        migrations.RemoveField(model_name='placesnapshot', name='distance_m'),
        migrations.RenameModel(old_name='SearchZone', new_name='LegacySearchZone'),
        migrations.RemoveField(model_name='legacysearchzone', name='radius'),
        migrations.AddField(model_name='legacysearchzone', name='resolved_area', field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='core.searcharea')),
    ]
