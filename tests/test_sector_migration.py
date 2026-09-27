"""The sector data migration really runs, on a real historical schema."""
import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from core.categories import resolve_category
from core.models import SearchCategory

BEFORE = ('core', '0012_business_sector_and_next_action')
AFTER = ('core', '0014_remove_business_legacy_category')


@pytest.mark.django_db(transaction=True)
def test_free_text_sectors_become_search_categories():
    executor = MigrationExecutor(connection)
    executor.migrate([BEFORE])
    old_apps = executor.loader.project_state([BEFORE]).apps
    Business = old_apps.get_model('core', 'Business')
    Category = old_apps.get_model('core', 'SearchCategory')
    try:
        Category.objects.create(name='Bares')
        Category.objects.create(name='Peluquerías')
        quijote = Business.objects.create(name='El Rincón del Quijote', legacy_category='Bar')
        resto = Business.objects.create(name='Otro bar', legacy_category='Bares')
        shouty = Business.objects.create(name='Tercero', legacy_category='  BARES ')
        salon = Business.objects.create(name='Peluquería Ana', legacy_category='peluquería')
        unknown = Business.objects.create(name='Floristería', legacy_category='Floristería')
        empty = Business.objects.create(name='Sin sector', legacy_category='')

        executor.loader.build_graph()
        executor.migrate([AFTER])

        new_apps = executor.loader.project_state([AFTER]).apps
        Updated = new_apps.get_model('core', 'Business')
        Categories = new_apps.get_model('core', 'SearchCategory')
        names = {b.pk: (b.category.name if b.category else None) for b in Updated.objects.select_related('category')}
        assert names[quijote.pk] == 'Bares'
        assert names[resto.pk] == 'Bares'
        assert names[shouty.pk] == 'Bares'
        assert names[salon.pk] == 'Peluquerías'
        # An unseen sector is kept, as its own category, rather than silently dropped.
        assert names[unknown.pk] == 'Floristerías'
        assert names[empty.pk] is None
        # "Bar" never becomes a category of its own.
        assert not Categories.objects.filter(name__iexact='bar').exists()
    finally:
        executor.loader.build_graph()
        executor.migrate([AFTER])


@pytest.mark.django_db
def test_runtime_helper_matches_the_migration():
    bares = SearchCategory.objects.create(name='Bares')
    assert resolve_category(SearchCategory, 'Bar') == bares
    assert resolve_category(SearchCategory, 'bares') == bares
    assert resolve_category(SearchCategory, '') is None
    assert resolve_category(SearchCategory, 'Gimnasio').name == 'Gimnasios'
