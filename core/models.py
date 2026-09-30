import secrets
import uuid
from decimal import Decimal
from datetime import timedelta
from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone
from . import labels

ROLES = list(labels.ROLE_LABELS.items())
# The single source of truth for the pipeline order: the tiles, the selects, the
# filters, the table sorting and STAGE_TRANSITIONS all read their order from here.
# 'discarded' stays last on purpose: it is a way out, not a step.
# Visible names come from core.labels.
STAGES = [(key, labels.STAGE_LABELS[key]) for key in ['found', 'profile_created', 'demo_ready', 'visited', 'proposal', 'approved', 'development', 'testing', 'production', 'discarded']]
STAGE_ORDER = {key: index for index, (key, _) in enumerate(STAGES)}
# The stage history is immutable, so it keeps accepting (and naming) retired stages.
STAGE_HISTORY_CHOICES = STAGES + list(labels.LEGACY_STAGE_LABELS.items())

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
    # Location and hours. Google data is cached for at most 30 days (see
    # tasks.daily_maintenance); hand-typed hours are ours and never expire.
    latitude = models.FloatField('Latitud', null=True, blank=True)
    longitude = models.FloatField('Longitud', null=True, blank=True)
    opening_hours = models.JSONField('Horario', null=True, blank=True, help_text='Periodos en el formato de Places API (New). Ver core/hours.py.')
    hours_source = models.CharField('Origen del horario', max_length=10, blank=True, choices=[('google', 'Google'), ('manual', 'Manual')])
    hours_updated_at = models.DateTimeField('Horario actualizado', null=True, blank=True)
    # The last hours Google gave, kept apart so "Volver al horario de Google"
    # works without calling the API again.
    google_hours = models.JSONField(null=True, blank=True)
    google_rating = models.FloatField(null=True, blank=True)
    google_reviews = models.PositiveIntegerField(null=True, blank=True)
    google_price_level = models.CharField(max_length=40, blank=True)
    google_maps_url = models.URLField(max_length=1000, blank=True)
    google_fetched_at = models.DateTimeField(null=True, blank=True)
    # Public demo address, /demos/<slug>/: the name plus a random part, so it
    # cannot be guessed from the business name.
    demo_slug = models.SlugField(max_length=60, unique=True, null=True, blank=True)
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
    # Whatever Claude writes through MCP is a draft until a person validates it.
    ai_draft = models.BooleanField('Borrador IA', default=False)
    ai_updated_at = models.DateTimeField('Escrito por la IA', null=True, blank=True)
    ai_sources = models.JSONField('Fuentes', default=list, blank=True)
    validated_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name='+')
    validated_at = models.DateTimeField(null=True, blank=True)

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

REQUEST_STATES = list(labels.REQUEST_STATE_LABELS.items())

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
    from_stage = models.CharField(max_length=30,choices=STAGE_HISTORY_CHOICES)
    to_stage = models.CharField(max_length=30,choices=STAGE_HISTORY_CHOICES)
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
    talked_to = models.CharField('Con quién hablé',max_length=150,blank=True)
    next_step = models.CharField('Próximo paso',max_length=300,blank=True)
    followup_date = models.DateField('Fecha de seguimiento',null=True,blank=True)
    # The pending action the visit created, so it shows in "Próximas acciones".
    followup = models.ForeignKey('FollowUp',null=True,blank=True,on_delete=models.SET_NULL)

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
    status = models.CharField(max_length=20,choices=[('new','Nuevo'),('pipeline',labels.OPPORTUNITY_IN_PIPELINE),('discarded','Descartado')],default='new')
    notes = models.TextField(blank=True)
    business = models.ForeignKey(Business,null=True,blank=True,on_delete=models.PROTECT)
    distance_m = models.PositiveIntegerField(default=0)
    latitude = models.FloatField(null=True,blank=True)
    longitude = models.FloatField(null=True,blank=True)
    opening_hours = models.JSONField(null=True,blank=True)
    price_level = models.CharField(max_length=40,blank=True)
    objects = AdminQuerySet.as_manager()

class SearchSlice(models.Model):
    """When a zone × category pair was last asked to Google.

    "Actualizar" skips pairs fetched in the last PLACES_REFRESH_MIN_DAYS, so
    pressing it twice does not spend the monthly quota twice.
    """
    zone = models.ForeignKey(SearchZone,on_delete=models.CASCADE)
    category = models.ForeignKey(SearchCategory,on_delete=models.CASCADE)
    fetched_at = models.DateTimeField()
    objects = AdminQuerySet.as_manager()
    class Meta:
        constraints = [models.UniqueConstraint(fields=['zone','category'],name='unique_search_slice')]

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
CATEGORIES=[('development','Desarrollo'),('maintenance','Mantenimiento'),('domain','Dominio'),('hosting',labels.HOSTING),('tools','Herramientas'),('materials','Material'),('commission','Comisión'),('tax','Impuestos'),('other','Otro')]

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
    # Login links: sent from the queue (so the response takes the same time
    # whether or not the email exists) and their URL is wiped once sent or expired.
    sensitive_until=models.DateTimeField(null=True,blank=True)


# --- Access: 2FA policy per role, remembered devices, email login links --------

TWO_FACTOR_POLICIES = [('required', 'Obligatoria'), ('optional', 'Opcional'), ('disabled', 'Desactivada')]
# Used when a role has no row yet. The app stores links to clients' credentials,
# so admins must use a second step by default.
DEFAULT_TWO_FACTOR = {'admin': 'required', 'developer': 'optional', 'accountant': 'optional', 'client': 'disabled'}


class TwoFactorPolicy(models.Model):
    role = models.CharField('Rol', max_length=20, choices=ROLES, unique=True)
    policy = models.CharField('Verificación en dos pasos', max_length=10, choices=TWO_FACTOR_POLICIES)
    objects = AdminQuerySet.as_manager()

    class Meta:
        verbose_name = 'Política de verificación en dos pasos'
        verbose_name_plural = 'Políticas de verificación en dos pasos'

    def __str__(self):
        return f'{self.get_role_display()}: {self.get_policy_display()}'

    @classmethod
    def for_role(cls, role):
        return cls.objects.filter(role=role).values_list('policy', flat=True).first() or DEFAULT_TWO_FACTOR.get(role, 'required')


class OwnRecordQuerySet(models.QuerySet):
    def visible_to(self, user):
        return self.filter(user=user) if user.is_authenticated else self.none()


class TrustedDevice(PublicIdModel):
    """A browser that skips the second step for a while. Only the token's hash is stored."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='trusted_devices')
    token_hash = models.CharField(max_length=64, unique=True)
    label = models.CharField('Dispositivo', max_length=120, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    objects = OwnRecordQuerySet.as_manager()


class LoginToken(models.Model):
    """One-use email login link. Only the token's hash is stored."""
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='login_tokens')
    token_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    objects = AdminRecordQuerySet.as_manager()


class LoginLinkRequest(models.Model):
    """Every request for a link, known email or not, so the limits say nothing about who exists."""
    email = models.EmailField()
    ip = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    objects = AdminRecordQuerySet.as_manager()


# --- Revenue estimate (internal: admin only) -------------------------------------

def default_price_tickets():
    return {'PRICE_LEVEL_INEXPENSIVE': 10, 'PRICE_LEVEL_MODERATE': 20, 'PRICE_LEVEL_EXPENSIVE': 35, 'PRICE_LEVEL_VERY_EXPENSIVE': 60}


def default_concurrency():
    return {'low': 30, 'medium': 55, 'high': 80}


class EstimationSettings(models.Model):
    """The assumptions behind every estimate, edited in /admin/. One row."""
    review_rate = models.DecimalField('Tasa de reseña (central)', max_digits=5, decimal_places=4, default=Decimal('0.02'), help_text='Parte de los clientes que deja reseña. Es la hipótesis más débil.')
    review_rate_low = models.DecimalField('Tasa de reseña baja', max_digits=5, decimal_places=4, default=Decimal('0.01'), help_text='Da la cifra alta.')
    review_rate_high = models.DecimalField('Tasa de reseña alta', max_digits=5, decimal_places=4, default=Decimal('0.04'), help_text='Da la cifra baja.')
    price_level_tickets = models.JSONField('Ticket por nivel de precios de Google (€)', default=default_price_tickets)
    concurrency_occupancy = models.JSONField('Ocupación por concurrencia observada (%)', default=default_concurrency, help_text='Se usa si no se anota la ocupación.')
    small_max = models.DecimalField('Pequeño: facturación mensual por debajo de (€)', max_digits=12, decimal_places=2, default=Decimal('15000'))
    medium_max = models.DecimalField('Mediano: facturación mensual por debajo de (€)', max_digits=12, decimal_places=2, default=Decimal('60000'))
    budget_min = models.DecimalField('Presupuesto mínimo (€)', max_digits=10, decimal_places=2, default=Decimal('1000'))
    budget_max = models.DecimalField('Presupuesto máximo (€)', max_digits=10, decimal_places=2, default=Decimal('3000'))
    gross_margin = models.DecimalField('Margen bruto', max_digits=4, decimal_places=3, default=Decimal('0.30'))
    delivery_commission = models.DecimalField('Comisión de las apps de reparto', max_digits=4, decimal_places=3, default=Decimal('0.30'))
    payback_months = models.PositiveSmallIntegerField('Meses para amortizar la web', default=12)
    objects = AdminQuerySet.as_manager()

    class Meta:
        verbose_name = 'Ajustes de la estimación'
        verbose_name_plural = 'Ajustes de la estimación'

    def __str__(self):
        return 'Ajustes de la estimación'

    @classmethod
    def load(cls):
        return cls.objects.get_or_create(pk=1)[0]

    def as_settings(self):
        from .estimation import Settings
        return Settings(**{f: getattr(self, f) for f in ['review_rate', 'review_rate_low', 'review_rate_high', 'price_level_tickets', 'concurrency_occupancy',
                                                         'small_max', 'medium_max', 'budget_min', 'budget_max', 'gross_margin', 'delivery_commission', 'payback_months']})


class EstimationQuerySet(ScopedQuerySet):
    def visible_to(self, user):
        return super().visible_to(user) if user.is_authenticated and user.role == 'admin' else self.none()


class Estimation(PublicIdModel, BusinessOwned):
    """One estimate, kept as history: inputs, result and the assumptions used."""
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT)
    source = models.CharField(max_length=10, choices=[('web', 'Web'), ('mcp', 'Claude (MCP)')], default='web')
    # Inputs
    reviews = models.PositiveIntegerField('Reseñas totales', null=True, blank=True)
    rating = models.DecimalField('Nota', max_digits=2, decimal_places=1, null=True, blank=True)
    years = models.DecimalField('Antigüedad (años)', max_digits=5, decimal_places=1, null=True, blank=True)
    ticket = models.DecimalField('Ticket medio por persona (€)', max_digits=8, decimal_places=2, null=True, blank=True, help_text='Vacío: se deduce del nivel de precios de Google.')
    price_level = models.CharField('Nivel de precios de Google', max_length=40, blank=True)
    tables = models.PositiveSmallIntegerField('Mesas', null=True, blank=True)
    people_per_table = models.DecimalField('Personas por mesa', max_digits=4, decimal_places=1, null=True, blank=True)
    services = models.JSONField('Servicios', default=list, blank=True)
    rotations = models.DecimalField('Rotaciones por servicio', max_digits=4, decimal_places=1, null=True, blank=True)
    days_per_week = models.PositiveSmallIntegerField('Días abiertos por semana', null=True, blank=True)
    occupancy_pct = models.DecimalField('Ocupación media observada (%)', max_digits=5, decimal_places=1, null=True, blank=True)
    concurrency = models.CharField('Concurrencia en la visita', max_length=10, blank=True, choices=[('low', 'Baja'), ('medium', 'Media'), ('high', 'Alta')])
    declared_annual = models.DecimalField('Facturación anual declarada (€)', max_digits=14, decimal_places=2, null=True, blank=True)
    declared_source = models.CharField('Fuente de la facturación declarada', max_length=200, blank=True)
    uses_delivery = models.BooleanField('Usa apps de reparto', default=False)
    # Result
    monthly_low = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    monthly_mid = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    monthly_high = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    confidence = models.CharField(max_length=10, choices=[('low', 'Baja'), ('medium', 'Media'), ('high', 'Alta')])
    band = models.CharField(max_length=10, blank=True, choices=[('small', 'Pequeño'), ('medium', 'Mediano'), ('large', 'Grande')])
    suggested_budget = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    used_ticket = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    explanation = models.JSONField(default=list)
    payback = models.JSONField(default=dict)
    assumptions = models.JSONField(default=dict)
    objects = EstimationQuerySet.as_manager()

    class Meta:
        ordering = ['-created_at', '-pk']


# --- Demo versions ----------------------------------------------------------------

class DemoVersion(BusinessOwned):
    """Every demo ever published for a business; the newest one is the public one."""
    number = models.PositiveIntegerField()
    filename = models.CharField(max_length=120)
    path = models.CharField(max_length=200)
    size = models.PositiveIntegerField()
    sha256 = models.CharField(max_length=64)
    source = models.CharField(max_length=10, choices=[('web', 'Web'), ('mcp', 'Claude (MCP)')], default='web')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-number']
        constraints = [models.UniqueConstraint(fields=['business', 'number'], name='unique_demo_version')]


# --- MCP connector: OAuth 2.1 state (Claude.ai registers itself by DCR) ------------

class McpClient(models.Model):
    client_id = models.CharField(max_length=255, unique=True)
    info = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
    objects = AdminRecordQuerySet.as_manager()

    @property
    def name(self):
        return self.info.get('client_name') or 'Cliente MCP'


class McpAuthRequest(models.Model):
    """An /authorize waiting for an admin to approve it in the web app."""
    txn = models.CharField(max_length=64, unique=True)
    client_id = models.CharField(max_length=255)
    params = models.JSONField()
    expires_at = models.DateTimeField()
    objects = AdminRecordQuerySet.as_manager()


class McpCode(models.Model):
    code_hash = models.CharField(max_length=64, unique=True)
    client_id = models.CharField(max_length=255)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    params = models.JSONField()
    expires_at = models.FloatField()
    objects = AdminRecordQuerySet.as_manager()


class McpTokenQuerySet(models.QuerySet):
    def visible_to(self, user):
        return self.filter(user=user) if user.is_authenticated and user.role == 'admin' else self.none()


class McpToken(models.Model):
    """Access and refresh tokens, by hash. A grant is one approved connection."""
    token_hash = models.CharField(max_length=64, unique=True)
    kind = models.CharField(max_length=10, choices=[('access', 'Acceso'), ('refresh', 'Renovación')])
    grant_id = models.CharField(max_length=64, db_index=True)
    client_id = models.CharField(max_length=255)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='mcp_tokens')
    scopes = models.JSONField(default=list)
    resource = models.CharField(max_length=500, blank=True)
    expires_at = models.BigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    objects = McpTokenQuerySet.as_manager()
