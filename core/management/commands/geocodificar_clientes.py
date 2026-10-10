from collections import Counter
from django.core.management.base import BaseCommand
from django.db.models import Q
from core.models import Business
from core.geocoding import geocode_business, MonthlyLimit
from core.geo_sources import GeoError


class Command(BaseCommand):
    help = 'Rellena la ubicación de los clientes; reutiliza Buscar clientes y respeta el límite mensual.'

    def add_arguments(self, parser):
        parser.add_argument('--forzar', action='store_true', help='Volver a buscar también los que ya tienen coordenadas.')

    def handle(self, *args, **options):
        items = Business.objects.select_related('city').order_by('pk')
        if not options['forzar']:
            items = items.filter(Q(latitude__isnull=True) | Q(longitude__isnull=True))
        located = 0
        reasons = Counter()
        for business in items.iterator():
            try:
                result = geocode_business(business, force=options['forzar'])
            except MonthlyLimit as exc:
                self.stdout.write(self.style.WARNING(str(exc)))
                break
            except GeoError as exc:
                result = str(exc)
            business.refresh_from_db()
            if result in {'Google Places', 'Dirección', 'Ubicación guardada en Buscar clientes'} and business.latitude is not None and business.longitude is not None:
                located += 1
            else:
                reasons[result] += 1
                self.stdout.write(f'{business.name}: {result}')
        missing = Business.objects.filter(Q(latitude__isnull=True) | Q(longitude__isnull=True)).count()
        self.stdout.write(self.style.SUCCESS(f'{located} clientes ubicados; {missing} sin ubicación.'))
        for reason, count in sorted(reasons.items()):
            self.stdout.write(f'{count}: {reason}')
