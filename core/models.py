import secrets
import uuid
from datetime import timedelta
from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

ROLES = [('admin', 'Admin'), ('developer', 'Developer'), ('accountant', 'Contador'), ('client', 'Cliente')]
# The single source of truth for the pipeline order: the tiles, the selects, the
# filters, the table sorting and STAGE_TRANSITIONS all read their order from here.
# 'discarded' stays last on purpose: it is a way out, not a step.
STAGES = [('found', 'Encontrado'), ('profile_created', 'Ficha creada'), ('visited', 'Visitado'), ('proposal', 'Propuesta'), ('review', 'Revisión'), ('approved', 'Presupuesto aprobado'), ('development', 'Desarrollo'), ('testing', 'Pruebas'), ('production', 'Producción'), ('discarded', 'Descartado')]
STAGE_ORDER = {key: index for index, (key, _) in enumerate(STAGES)}

class PublicIdModel(models.Model):
    """Adds an opaque identifier for URLs so the integer primary key never leaves the server."""
    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False, db_index=True)
    class Meta:
        abstract = True

def invitation_expiry():
    return timezone.now() + timedelta(days=7)

def invitation_token():
    return secrets.token_urlsafe(32)

class AdminRecordQuerySet(models.QuerySet):
    def visible_to(self, user):
        return self.all() if user.is_authenticated and user.role == 'admin' else self.none()

class UserManager(BaseUserManager):
    def visible_to(self, user):
        if not user.is_authenticated: return self.none()
        return self.all() if user.role=='admin' else self.filter(pk=user.pk)
    def create_user(self, email, password=None, **extra):
        user = self.model(email=self.normalize_email(email).lower(), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password, **extra):
        extra.update(role='admin', is_staff=True, is_superuser=True)
        return self.create_user(email, password, **extra)

class User(AbstractUser, PublicIdModel):
    username = None
    email = models.EmailField(unique=True)
    name = models.CharField('Nombre', max_length=150)
    role = models.CharField('Rol', max_length=20, choices=ROLES, default='client')
    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['name']
    objects = UserManager()

    def __str__(self):
        return self.name or self.email

class BusinessQuerySet(models.QuerySet):
    def visible_to(self, user):
        if not user.is_authenticated:
            return self.none()
        if user.role in ('admin', 'accountant'):
            return self.all()
        if user.role == 'developer':
            return self.filter(pk__in=Assignment.objects.filter(developer=user).values("business_id"))
        return self.filter(pk__in=ClientMembership.objects.filter(user=user).values("business_id"))

class Business(PublicIdModel):
    name = models.CharField('Nombre', max_length=200)
    category = models.ForeignKey('SearchCategory', verbose_name='Sector', null=True, blank=True, on_delete=models.PROTECT, related_name='businesses')
    address = models.CharField('Dirección', max_length=300, blank=True)
    phone = models.CharField('Teléfono', max_length=50, blank=True)
    email = models.EmailField(blank=True)
    contact = models.CharField('Persona de contacto', max_length=150, blank=True)
    zone = models.CharField('Zona', max_length=150, blank=True)
    google_place_id = models.CharField(max_length=255, unique=True, null=True, blank=True)
    current_website = models.URLField('Web actual', blank=True)
    stage = models.CharField('Fase', max_length=30, choices=STAGES, default='found')
    created_at = models.DateTimeField(auto_now_add=True)
    stage_changed_at = models.DateTimeField(null=True, blank=True)
    internal_notes = models.TextField('Notas internas', blank=True)
    legal_name = models.CharField('Razón social', max_length=200, blank=True)
    tax_id = models.CharField('NIF', max_length=30, blank=True)
    discarded_reason = models.TextField(blank=True)
    next_action = models.CharField('Próxima acción', max_length=200, blank=True)
    next_action_date = models.DateField('Fecha de la próxima acción', null=True, blank=True)
    objects = BusinessQuerySet.as_manager()

    def __str__(self):
        return self.name

    def get_absolute_url(self):
        return reverse('business_detail', args=[self.public_id])

class ClientMembership(models.Model):
    objects = AdminRecordQuerySet.as_manager()
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, limit_choices_to={'role': 'client'})
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name='memberships')
    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'business'], name='unique_membership')]

class Assignment(models.Model):
    objects = AdminRecordQuerySet.as_manager()
    developer = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, limit_choices_to={'role': 'developer'})
    business = models.ForeignKey(Business, on_delete=models.CASCADE, related_name='assignments')
    class Meta:
        constraints = [models.UniqueConstraint(fields=['developer', 'business'], name='unique_assignment')]

class Invitation(models.Model):
    objects = AdminRecordQuerySet.as_manager()
    email = models.EmailField()
    role = models.CharField(max_length=20, choices=ROLES)
    business = models.ForeignKey(Business, null=True, blank=True, on_delete=models.PROTECT)
    token = models.CharField(max_length=100, default=invitation_token, unique=True, editable=False)
    expires_at = models.DateTimeField(default=invitation_expiry)
    invited_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    accepted_at = models.DateTimeField(null=True, blank=True)

class ScopedQuerySet(models.QuerySet):
    def visible_to(self, user):
        if not user.is_authenticated:
            return self.none()
        name = self.model.__name__
        if name in {'AccessLink', 'Website', 'Request', 'ActivityEvent', 'FollowUp', 'Visit', 'StageChange', 'Study'} and user.role == 'accountant':
            return self.none()
        if name in {'AccessLink','FollowUp','Visit','StageChange','Study'} and user.role == 'client':
            return self.none()
        qs = self.filter(business__in=Business.objects.visible_to(user))
        if name == 'ActivityEvent' and user.role == 'client':
            qs = qs.filter(client_visible=True)
        return qs

class BusinessOwned(models.Model):
    business = models.ForeignKey(Business, on_delete=models.PROTECT)
    objects = ScopedQuerySet.as_manager()
    class Meta:
        abstract = True

class Website(BusinessOwned):
    url = models.URLField('URL', blank=True)
    domain = models.CharField('Dominio', max_length=255, blank=True)
    repository = models.URLField('Repositorio', blank=True)
    launched_at = models.DateTimeField('Puesta en producción', null=True, blank=True)
    monitoring_enabled = models.BooleanField('Monitorización activada', default=False)
    incident_open = models.BooleanField(default=False)
    class Meta:
        constraints = [models.UniqueConstraint(fields=['business'], name='one_website_per_business')]

class Study(PublicIdModel, BusinessOwned):
    """Groundwork done before visiting a business: who they are and what they need.

    Kept in its own table rather than as more columns on Business: it is internal
    to the commercial team, so it needs its own visibility rule, and it would
    otherwise load a dozen long text fields on every Business query.
    """
    who = models.TextField('Quiénes son', blank=True, help_text='Tipo de negocio, tamaño, antigüedad aproximada.')
    sells = models.TextField('Qué venden / servicios principales', blank=True)
    audience = models.TextField('Público objetivo', blank=True)
    has_google = models.BooleanField('Ficha de Google', default=False)
    has_instagram = models.BooleanField('Instagram', default=False)
    has_facebook = models.BooleanField('Facebook', default=False)
    has_tiktok = models.BooleanField('TikTok', default=False)
    has_website = models.BooleanField('Web propia', default=False)
    has_booking = models.BooleanField('Reservas online', default=False)
    links = models.TextField('Enlaces', blank=True, help_text='Un enlace por línea.')
    competitors = models.TextField('Competencia cercana', blank=True, help_text='Competidores de la zona y si tienen web.')
    needs = models.TextField('Necesidades observadas', blank=True, help_text='Puntos de dolor detectados.')
    opportunity = models.TextField('Oportunidad', blank=True, help_text='Qué les propondríamos: tipo de web y funcionalidades clave.')
    best_time = models.CharField('Mejor momento para visitar', max_length=200, blank=True)
    contact_to_find = models.CharField('Persona de contacto a buscar', max_length=150, blank=True)
    notes = models.TextField('Notas del estudio', blank=True)
    created_at = models.DateTimeField('Fecha del estudio', auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # The two answers that have to exist before a visit is worth booking. The same
    # rule gates the stage button and the indicator in the Clientes table.
    REQUIRED = ['sells', 'needs']

    class Meta:
        constraints = [models.UniqueConstraint(fields=['business'], name='one_study_per_business')]

    def missing(self):
        return [self._meta.get_field(name).verbose_name for name in self.REQUIRED if not getattr(self, name).strip()]

    @property
    def complete(self):
        return not self.missing()

class AccessLink(BusinessOwned):
    name = models.CharField('Nombre', max_length=150)
    url = models.URLField('Enlace al gestor de contraseñas')

class Document(PublicIdModel, BusinessOwned):
    kind = models.CharField('Tipo', max_length=30, choices=[('contract','Contrato'),('gdpr','Encargo de tratamiento RGPD'),('estimate','Presupuesto'),('other','Otro')])
    file = models.FileField('Fichero', upload_to='documents/%Y/%m/')
    status = models.CharField('Estado', max_length=20, choices=[('pending','Pendiente'),('signed','Firmado')], default='pending')
    uploaded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

REQUEST_STATES = [('requested','Solicitada'),('accepted','Aceptada'),('in_progress','En desarrollo'),('testing','En pruebas'),('production','En producción'),('rejected','Rechazada')]

class Request(PublicIdModel, BusinessOwned):
    title = models.CharField('Título', max_length=200)
    description = models.TextField('Descripción')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    status = models.CharField('Estado', max_length=30, choices=REQUEST_STATES, default='requested')
    priority = models.CharField('Prioridad', max_length=20, choices=[('normal','Normal'),('high','Alta'),('low','Baja')], default='normal')
    target_date = models.DateField('Fecha objetivo', null=True, blank=True)
    rejected_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

class ImmutableQuerySet(ScopedQuerySet):
    def update(self, **kwargs):
        raise ValueError('Audit records cannot be updated.')
    def delete(self):
        raise ValueError('Audit records cannot be deleted.')
    def bulk_update(self, *args, **kwargs):
        raise ValueError('Audit records cannot be updated.')

class ActivityEvent(BusinessOwned):
    kind = models.CharField(max_length=50)
    text = models.TextField()
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT)
    occurred_at = models.DateTimeField(default=timezone.now)
    content_type = models.ForeignKey('contenttypes.ContentType', null=True, blank=True, on_delete=models.PROTECT)
    object_id = models.PositiveBigIntegerField(null=True, blank=True)
    related_object = GenericForeignKey('content_type','object_id')
    client_visible = models.BooleanField(default=True)
    objects = ImmutableQuerySet.as_manager()
    class Meta:
        ordering = ['-occurred_at', '-pk']
    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise ValueError('Audit records cannot be updated.')
        return super().save(*args, **kwargs)
    def delete(self, *args, **kwargs):
        raise ValueError('Audit records cannot be deleted.')

class StageChange(BusinessOwned):
    from_stage = models.CharField(max_length=30,choices=STAGES)
    to_stage = models.CharField(max_length=30,choices=STAGES)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    occurred_at = models.DateTimeField(default=timezone.now)
    objects = ImmutableQuerySet.as_manager()
    def save(self,*args,**kwargs):
        if not self._state.adding:
            raise ValueError('Stage history is immutable.')
        return super().save(*args,**kwargs)
    def delete(self,*args,**kwargs):
        raise ValueError('Stage history is immutable.')

class ProposalQuerySet(ScopedQuerySet):
    def visible_to(self,user):
        qs=super().visible_to(user)
        if user.is_authenticated and user.role=='client':
            published=ProposalVersion.objects.filter(Q(sent_at__isnull=False)|~Q(historical_status='')).values('proposal_id')
            return qs.filter(pk__in=published)
        return qs

class Proposal(PublicIdModel, BusinessOwned):
    number = models.CharField('Número',max_length=30,unique=True)
    status = models.CharField(max_length=20,choices=[('draft','Borrador'),('sent','Enviada'),('changes','Cambios pedidos'),('accepted','Aceptada'),('rejected','Rechazada')],default='draft')
    valid_until = models.DateField('Válida hasta',null=True,blank=True)
    objects = ProposalQuerySet.as_manager()

    def get_absolute_url(self):
        return reverse('proposal_detail', args=[self.public_id])

class ProposalSequence(models.Model):
    objects = AdminRecordQuerySet.as_manager()
    year = models.PositiveIntegerField(unique=True)
    value = models.PositiveIntegerField(default=0)

class VersionQuerySet(models.QuerySet):
    def visible_to(self,user):
        qs=self.filter(proposal__in=Proposal.objects.visible_to(user))
        return qs.filter(Q(sent_at__isnull=False)|~Q(historical_status='')) if user.is_authenticated and user.role=='client' else qs

class ProposalVersion(models.Model):
    historical_status = models.CharField(max_length=20, blank=True, choices=[('changes','Cambios pedidos'),('accepted','Aceptada')])
    proposal = models.ForeignKey(Proposal,on_delete=models.PROTECT,related_name='versions')
    number = models.PositiveIntegerField()
    development_total = models.DecimalField('Desarrollo',max_digits=12,decimal_places=2,default=0)
    monthly_fee = models.DecimalField('Mantenimiento mensual',max_digits=12,decimal_places=2,default=0)
    notes = models.TextField('Notas',blank=True)
    client_comment = models.TextField(blank=True)
    sent_at = models.DateTimeField(null=True,blank=True)
    accepted_at = models.DateTimeField(null=True,blank=True)
    accepted_by = models.ForeignKey(settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=models.PROTECT)
    acceptance_method = models.CharField(max_length=30,blank=True)
    acceptance_ip = models.GenericIPAddressField(null=True,blank=True)
    objects = VersionQuerySet.as_manager()
    class Meta:
        ordering=['-number']
        constraints=[models.UniqueConstraint(fields=['proposal','number'],name='unique_proposal_version')]
    def save(self,*args,**kwargs):
        if self.pk:
            previous=type(self).objects.get(pk=self.pk)
            if (previous.sent_at or previous.historical_status) and any(getattr(previous,f)!=getattr(self,f) for f in ['development_total','monthly_fee','notes','proposal_id','number','sent_at']):
                raise ValueError('Sent proposal content is immutable.')
        return super().save(*args,**kwargs)

class LineQuerySet(models.QuerySet):
    def visible_to(self, user):
        return self.filter(version__in=ProposalVersion.objects.visible_to(user))

class ProposalLine(models.Model):
    objects = LineQuerySet.as_manager()
    version = models.ForeignKey(ProposalVersion,on_delete=models.PROTECT,related_name='lines')
    concept = models.CharField('Concepto',max_length=200)
    description = models.TextField('Descripción',blank=True)
    amount = models.DecimalField('Importe',max_digits=12,decimal_places=2,default=0)
    included = models.BooleanField('Incluido',default=False)
    def save(self,*args,**kwargs):
        if self.version.sent_at or self.version.historical_status:
            raise ValueError('Sent proposal lines are immutable.')
        return super().save(*args,**kwargs)
    def delete(self,*args,**kwargs):
        if self.version.sent_at or self.version.historical_status:
            raise ValueError('Sent proposal lines are immutable.')
        return super().delete(*args,**kwargs)

Request.add_to_class('proposal', models.ForeignKey(Proposal,null=True,blank=True,on_delete=models.PROTECT))

class FollowUp(PublicIdModel, BusinessOwned):
    description = models.CharField('Descripción',max_length=300)
    date = models.DateField('Fecha')
    owner = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    kind = models.CharField('Tipo',max_length=20,choices=[('visit','Visita'),('call','Llamada'),('proposal','Propuesta'),('collection','Cobro'),('renewal','Renovación')])
    done = models.BooleanField('Hecho',default=False)

class Visit(BusinessOwned):
    date = models.DateTimeField('Fecha')
    visitor = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    result = models.CharField('Resultado',max_length=200)
    notes = models.TextField('Notas',blank=True)

class AdminQuerySet(models.QuerySet):
    def visible_to(self,user):
        return self.all() if user.is_authenticated and user.role=='admin' else self.none()

class SearchZone(models.Model):
    name = models.CharField('Nombre',max_length=150,unique=True)
    latitude = models.FloatField('Latitud',null=True,blank=True)
    longitude = models.FloatField('Longitud',null=True,blank=True)
    radius = models.PositiveIntegerField('Radio (m)',default=1000)
    active = models.BooleanField('Activa',default=True)
    objects = AdminQuerySet.as_manager()
    def __str__(self): return self.name

class SearchCategory(models.Model):
    name = models.CharField('Nombre',max_length=150,unique=True)
    included_type = models.CharField('Tipo Google',max_length=100,blank=True)
    query = models.CharField('Texto de búsqueda',max_length=200,blank=True)
    color = models.CharField('Color',max_length=20,default='blue',choices=[(v,v) for v in ['yellow','pink','green','blue','purple','teal','gray']])
    active = models.BooleanField('Activa',default=True)
    objects = AdminQuerySet.as_manager()
    def __str__(self): return self.name

class PlaceSnapshot(PublicIdModel):
    place_id = models.CharField(max_length=255,unique=True)
    name = models.CharField(max_length=200)
    address = models.CharField(max_length=400,blank=True)
    primary_type = models.CharField(max_length=100,blank=True)
    rating = models.FloatField(default=0)
    reviews = models.PositiveIntegerField(default=0)
    has_website = models.BooleanField(default=False)
    website_url = models.URLField(blank=True,max_length=1000)
    photos = models.PositiveIntegerField(default=0)
    phone = models.CharField(max_length=60,blank=True)
    maps_url = models.URLField(blank=True,max_length=1000)
    business_status = models.CharField(max_length=40,default='OPERATIONAL')
    fetched_at = models.DateTimeField(default=timezone.now)
    zone = models.ForeignKey(SearchZone,on_delete=models.PROTECT)
    category = models.ForeignKey(SearchCategory,on_delete=models.PROTECT)
    opportunity_score = models.PositiveSmallIntegerField(default=0)
    status = models.CharField(max_length=20,choices=[('new','Nuevo'),('pipeline','En pipeline'),('discarded','Descartado')],default='new')
    notes = models.TextField(blank=True)
    business = models.ForeignKey(Business,null=True,blank=True,on_delete=models.PROTECT)
    distance_m = models.PositiveIntegerField(default=0)
    objects = AdminQuerySet.as_manager()

class PlacesUsage(models.Model):
    month = models.DateField(unique=True)
    requests = models.PositiveIntegerField(default=0)
    objects = AdminQuerySet.as_manager()

class SearchRun(PublicIdModel):
    status = models.CharField(max_length=20,default='queued')
    completed = models.PositiveIntegerField(default=0)
    total = models.PositiveIntegerField(default=0)
    message = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True,blank=True)
    objects = AdminQuerySet.as_manager()

PERIODS=[('once','Única'),('monthly','Mensual'),('annual','Anual')]
CATEGORIES=[('development','Desarrollo'),('maintenance','Mantenimiento'),('domain','Dominio'),('hosting','Hosting'),('tools','Herramientas'),('materials','Material'),('commission','Comisión'),('tax','Impuestos'),('other','Otro')]

class ChargeQuerySet(ScopedQuerySet):
    def visible_to(self,user):
        return self.none() if user.is_authenticated and user.role=='developer' else super().visible_to(user)

class ClientCharge(PublicIdModel, BusinessOwned):
    concept=models.CharField('Concepto',max_length=200)
    kind=models.CharField('Tipo',max_length=20,choices=[x for x in CATEGORIES if x[0] in ['development','maintenance','domain','hosting','other']])
    amount=models.DecimalField('Importe',max_digits=12,decimal_places=2,null=True,blank=True)
    included=models.BooleanField('Incluido',default=False)
    periodicity=models.CharField('Periodicidad',max_length=20,choices=PERIODS,default='once')
    next_date=models.DateField('Próxima fecha',null=True,blank=True)
    status=models.CharField('Estado de pago',max_length=20,choices=[('pending','Pendiente'),('paid','Pagado')],default='pending')
    objects=ChargeQuerySet.as_manager()

class FinanceQuerySet(models.QuerySet):
    def visible_to(self,user):
        return self.all() if user.is_authenticated and user.role in ['admin','accountant'] else self.none()

class Transaction(PublicIdModel):
    date=models.DateField('Fecha')
    concept=models.CharField('Concepto',max_length=250)
    business=models.ForeignKey(Business,null=True,blank=True,on_delete=models.PROTECT)
    category=models.CharField('Categoría',max_length=20,choices=CATEGORIES)
    amount=models.DecimalField('Importe con signo',max_digits=12,decimal_places=2)
    notes=models.TextField('Notas',blank=True)
    receipt=models.FileField('Justificante',upload_to='receipts/%Y/%m/',blank=True)
    source_key=models.CharField(max_length=100,unique=True,null=True,blank=True)
    objects=FinanceQuerySet.as_manager()

class RecurringExpense(PublicIdModel):
    concept=models.CharField('Concepto',max_length=200)
    amount=models.DecimalField('Importe',max_digits=12,decimal_places=2)
    periodicity=models.CharField('Periodicidad',max_length=20,choices=PERIODS[1:],default='monthly')
    next_date=models.DateField('Próxima fecha',null=True,blank=True)
    objects=FinanceQuerySet.as_manager()

class Commission(PublicIdModel, BusinessOwned):
    beneficiary=models.CharField('Beneficiario',max_length=200)
    user=models.ForeignKey(settings.AUTH_USER_MODEL,null=True,blank=True,on_delete=models.PROTECT)
    percentage=models.DecimalField('Porcentaje',max_digits=5,decimal_places=2,null=True,blank=True)
    fixed_amount=models.DecimalField('Importe fijo',max_digits=12,decimal_places=2,null=True,blank=True)
    status=models.CharField('Estado',max_length=20,choices=[('pending','Pendiente'),('paid','Pagada')],default='pending')
    paid_date=models.DateField('Fecha de pago',null=True,blank=True)
    objects=FinanceQuerySet.as_manager()
    class Meta:
        constraints=[models.CheckConstraint(condition=(Q(percentage__isnull=False,fixed_amount__isnull=True)|Q(percentage__isnull=True,fixed_amount__isnull=False)),name='commission_one_amount')]

class NotificationReceipt(models.Model):
    objects = AdminRecordQuerySet.as_manager()
    key=models.CharField(max_length=150,unique=True)
    created_at=models.DateTimeField(auto_now_add=True)

class UptimeQuerySet(models.QuerySet):
    def visible_to(self,user):
        return self.filter(website__in=Website.objects.visible_to(user))

class UptimeCheck(models.Model):
    website=models.ForeignKey(Website,on_delete=models.CASCADE)
    checked_at=models.DateTimeField(default=timezone.now)
    http_status=models.PositiveSmallIntegerField(null=True,blank=True)
    latency_ms=models.PositiveIntegerField(default=0)
    ok=models.BooleanField()
    error=models.TextField(blank=True)
    objects=UptimeQuerySet.as_manager()
    class Meta:
        indexes=[models.Index(fields=['website','-checked_at'])]

class UptimeDaily(models.Model):
    website=models.ForeignKey(Website,on_delete=models.CASCADE)
    date=models.DateField()
    checks=models.PositiveIntegerField()
    failures=models.PositiveIntegerField()
    availability=models.DecimalField(max_digits=6,decimal_places=3)
    average_latency=models.DecimalField(max_digits=12,decimal_places=2)
    objects=UptimeQuerySet.as_manager()
    class Meta:
        constraints=[models.UniqueConstraint(fields=['website','date'],name='unique_uptime_day')]

class EmailOutbox(models.Model):
    objects = AdminRecordQuerySet.as_manager()
    subject=models.CharField(max_length=250)
    recipients=models.JSONField(default=list)
    text=models.TextField()
    url=models.URLField(max_length=2000,blank=True)
    created_at=models.DateTimeField(auto_now_add=True)
    sent_at=models.DateTimeField(null=True,blank=True)
    attempts=models.PositiveIntegerField(default=0)
    last_error=models.TextField(blank=True)

