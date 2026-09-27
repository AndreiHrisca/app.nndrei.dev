from decimal import Decimal
import environ
from django.conf import settings
from django.contrib.auth.password_validation import validate_password
from django.core.management.base import BaseCommand,CommandError
from django.db import transaction
from core.models import User,Business,Website,Proposal,ProposalVersion,ProposalLine,ProposalSequence,ClientCharge,Request,SearchZone,SearchCategory

class Command(BaseCommand):
    help='Seed confirmed business data without inventing dates or unknown amounts.'
    @transaction.atomic
    def handle(self,*args,**options):
        email=environ.Env()('SEED_ADMIN_EMAIL',default='').lower()
        password=environ.Env()('SEED_ADMIN_PASSWORD',default='')
        if not email or not password:
            raise CommandError('Configura SEED_ADMIN_EMAIL y SEED_ADMIN_PASSWORD.')
        admin=User.objects.filter(email=email).first()
        if not admin:
            validate_password(password,User(email=email))
            admin=User.objects.create_superuser(email,password,name='Andrei')
        elif admin.role!='admin':
            raise CommandError('El email configurado ya pertenece a una cuenta que no es admin.')
        for zone in ['Villaverde Bajo','Miguel Hernández']:
            SearchZone.objects.get_or_create(name=zone)
        categories=[('Bares','bar'),('Cafeterías','cafe'),('Barberías','barber_shop'),('Peluquerías','hair_salon'),('Dentistas','dentist'),('Clínicas','medical_clinic'),('Veterinarios','veterinary_care'),('Gimnasios','gym'),('Hoteles','hotel'),('Talleres','car_repair'),('Comercios','store')]
        colors=['yellow','pink','green','blue','purple','teal']
        for i,(name,kind) in enumerate(categories):
            SearchCategory.objects.get_or_create(name=name,defaults={'included_type':kind,'color':colors[i%len(colors)]})
        bares=SearchCategory.objects.get(name='Bares')
        b,_=Business.objects.get_or_create(name='El Rincón del Quijote',defaults={'category':bares,'contact':'Santiago','stage':'production','current_website':'https://barrinconquijote.es'})
        Website.objects.get_or_create(business=b,defaults={'url':'https://barrinconquijote.es','domain':'barrinconquijote.es','repository':'https://github.com/AndreiHrisca/bar-rincon-quijote','monitoring_enabled':True})
        p,created=Proposal.objects.get_or_create(number='P-2026-001',defaults={'business':b,'status':'accepted'})
        if p.business_id!=b.pk:
            raise CommandError('P-2026-001 ya pertenece a otro negocio; revisa la numeración antes de importar.')
        if created:
            for n,total,comment in [(1,430,'Cambios pedidos (histórico importado; fecha no facilitada).'),(2,500,'Aceptada (histórico importado; fecha no facilitada).')]:
                v=ProposalVersion.objects.create(proposal=p,number=n,development_total=total,monthly_fee=20,client_comment=comment,acceptance_method='historical' if n==2 else '',notes='Importe cerrado del conjunto de servicios. Fechas originales pendientes de completar.')
                for i,concept in enumerate(['Carta digital con QR','Página del bar','Reservas','Gestión de personal']):
                    ProposalLine.objects.create(version=v,concept=concept,amount=total if i==0 else 0,included=i!=0,description='Precio conjunto' if i==0 else '')
                v.historical_status='changes' if n==1 else 'accepted'
                v.save(update_fields=['historical_status'])
            seq,_=ProposalSequence.objects.get_or_create(year=2026)
            if seq.value<1: seq.value=1; seq.save()
        for concept,kind,amount,period,status in [('Desarrollo','development',Decimal(500),'once','paid'),('Mantenimiento','maintenance',Decimal(20),'monthly','pending'),('Dominio','domain',None,'annual','pending')]:
            ClientCharge.objects.get_or_create(business=b,concept=concept,defaults={'kind':kind,'amount':amount,'periodicity':period,'status':status})
        for title,status in [('Carta digital con QR','production'),('Módulo de almacén','requested')]:
            Request.objects.get_or_create(business=b,title=title,defaults={'description':'','created_by':admin,'status':status})
        self.stdout.write(self.style.SUCCESS('Datos iniciales creados. No se han sobrescrito datos existentes ni inventado fechas.'))
