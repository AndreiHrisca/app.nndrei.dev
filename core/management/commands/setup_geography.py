from django.core.management.base import BaseCommand
from core import geo_sources


class Command(BaseCommand):
    help = ('Load INE municipalities and Madrid neighbourhoods, turn the old fixed zones into '
            'search areas, and give businesses their city and neighbourhood. Safe to run again.')

    def add_arguments(self, parser):
        parser.add_argument('--no-geocode', action='store_true', help='Do not look up addresses of businesses without coordinates.')

    def handle(self, *args, **options):
        new, changed = geo_sources.load_municipalities()
        self.stdout.write(f'Municipios INE: {new} nuevos, {changed} actualizados.')
        self.stdout.write(f'Barrios de Madrid: {geo_sources.load_madrid_neighbourhoods()}.')
        for name, area in geo_sources.resolve_legacy_zones():
            self.stdout.write(f'Zona antigua «{name}» → {area}.')
        placed = geo_sources.assign_areas(geocode=not options['no_geocode'])
        self.stdout.write(self.style.SUCCESS(f'Negocios con ciudad o barrio actualizados: {placed}.'))
