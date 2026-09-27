"""New pipeline order, plus the prior-study table.

Reordering STAGES only rewrites the `choices` metadata: the column keeps storing
the key ('visited', 'profile_created', …), never the position, so every business
stays in the phase it was in and no data migration is needed.
"""

import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0014_remove_business_legacy_category'),
    ]

    operations = [
        migrations.AlterField(
            model_name='business',
            name='stage',
            field=models.CharField(choices=[('found', 'Encontrado'), ('profile_created', 'Ficha creada'), ('visited', 'Visitado'), ('proposal', 'Propuesta'), ('review', 'Revisión'), ('approved', 'Presupuesto aprobado'), ('development', 'Desarrollo'), ('testing', 'Pruebas'), ('production', 'Producción'), ('discarded', 'Descartado')], default='found', max_length=30, verbose_name='Fase'),
        ),
        migrations.AlterField(
            model_name='stagechange',
            name='from_stage',
            field=models.CharField(choices=[('found', 'Encontrado'), ('profile_created', 'Ficha creada'), ('visited', 'Visitado'), ('proposal', 'Propuesta'), ('review', 'Revisión'), ('approved', 'Presupuesto aprobado'), ('development', 'Desarrollo'), ('testing', 'Pruebas'), ('production', 'Producción'), ('discarded', 'Descartado')], max_length=30),
        ),
        migrations.AlterField(
            model_name='stagechange',
            name='to_stage',
            field=models.CharField(choices=[('found', 'Encontrado'), ('profile_created', 'Ficha creada'), ('visited', 'Visitado'), ('proposal', 'Propuesta'), ('review', 'Revisión'), ('approved', 'Presupuesto aprobado'), ('development', 'Desarrollo'), ('testing', 'Pruebas'), ('production', 'Producción'), ('discarded', 'Descartado')], max_length=30),
        ),
        migrations.CreateModel(
            name='Study',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('public_id', models.UUIDField(db_index=True, default=uuid.uuid4, editable=False, unique=True)),
                ('who', models.TextField(blank=True, help_text='Tipo de negocio, tamaño, antigüedad aproximada.', verbose_name='Quiénes son')),
                ('sells', models.TextField(blank=True, verbose_name='Qué venden / servicios principales')),
                ('audience', models.TextField(blank=True, verbose_name='Público objetivo')),
                ('has_google', models.BooleanField(default=False, verbose_name='Ficha de Google')),
                ('has_instagram', models.BooleanField(default=False, verbose_name='Instagram')),
                ('has_facebook', models.BooleanField(default=False, verbose_name='Facebook')),
                ('has_tiktok', models.BooleanField(default=False, verbose_name='TikTok')),
                ('has_website', models.BooleanField(default=False, verbose_name='Web propia')),
                ('has_booking', models.BooleanField(default=False, verbose_name='Reservas online')),
                ('links', models.TextField(blank=True, help_text='Un enlace por línea.', verbose_name='Enlaces')),
                ('competitors', models.TextField(blank=True, help_text='Competidores de la zona y si tienen web.', verbose_name='Competencia cercana')),
                ('needs', models.TextField(blank=True, help_text='Puntos de dolor detectados.', verbose_name='Necesidades observadas')),
                ('opportunity', models.TextField(blank=True, help_text='Qué les propondríamos: tipo de web y funcionalidades clave.', verbose_name='Oportunidad')),
                ('best_time', models.CharField(blank=True, max_length=200, verbose_name='Mejor momento para visitar')),
                ('contact_to_find', models.CharField(blank=True, max_length=150, verbose_name='Persona de contacto a buscar')),
                ('notes', models.TextField(blank=True, verbose_name='Notas del estudio')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Fecha del estudio')),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('business', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='core.business')),
            ],
            options={
                'constraints': [models.UniqueConstraint(fields=('business',), name='one_study_per_business')],
            },
        ),
    ]
