from django import forms
from django.contrib.auth.forms import SetPasswordForm
from .models import Invitation

class PublicIdModelForm(forms.ModelForm):
    """Renders related objects by their public id, so no row id reaches the HTML.

    Every choice field whose model carries a `public_id` is matched on that
    field instead of the primary key, both when rendering and when cleaning.
    """
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        for field in self.fields.values():
            queryset=getattr(field,'queryset',None)
            if queryset is not None and any(f.name=='public_id' for f in queryset.model._meta.get_fields()):
                field.to_field_name='public_id'

class InvitationForm(PublicIdModelForm):
    class Meta:
        model = Invitation
        fields = ['email', 'role', 'business']
        labels = {'business': 'Negocio', 'role': 'Rol'}

    def clean_email(self):
        from .models import User
        email=self.cleaned_data['email'].lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError('La persona ya tiene cuenta. Gestiona sus accesos en Usuarios.')
        return email

    def clean(self):
        data = super().clean()
        if data.get('role') == 'client' and not data.get('business'):
            self.add_error('business', 'Selecciona el negocio del cliente.')
        return data

class AcceptInvitationForm(SetPasswordForm):
    name = forms.CharField(label='Nombre', max_length=150)

from .models import Business, Request, Document, AccessLink, Website

class BusinessForm(PublicIdModelForm):
    class Meta:
        model = Business
        fields = ['name','category','address','phone','email','contact','zone','current_website','internal_notes','legal_name','tax_id','next_action','next_action_date']
        widgets = {'next_action_date':forms.DateInput(attrs={'type':'date'})}
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        from .models import SearchCategory
        # Same vocabulary as "Buscar clientes"; existing values stay selectable.
        self.fields['category'].queryset=SearchCategory.objects.filter(active=True).order_by('name')
        if self.instance.pk and self.instance.category_id:
            self.fields['category'].queryset=(SearchCategory.objects.filter(active=True)|SearchCategory.objects.filter(pk=self.instance.category_id)).distinct().order_by('name')

class RequestForm(forms.ModelForm):
    class Meta:
        model = Request
        fields = ['title','description']

class DocumentForm(forms.ModelForm):
    class Meta:
        model = Document
        fields = ['kind','file','status']
    def clean_file(self):
        f = self.cleaned_data['file']
        if f.size > 10 * 1024 * 1024:
            raise forms.ValidationError('Máximo 10 MB.')
        return f

class AccessLinkForm(forms.ModelForm):
    class Meta:
        model = AccessLink
        fields = ['name','url']
    def clean_url(self):
        from urllib.parse import urlparse
        url=self.cleaned_data['url']
        if urlparse(url).username or urlparse(url).password:
            raise forms.ValidationError('No incluyas credenciales en el enlace.')
        return url

class WebsiteForm(forms.ModelForm):
    class Meta:
        model = Website
        fields = ['url','domain','repository','launched_at','monitoring_enabled']

from .models import Study
class StudyForm(forms.ModelForm):
    class Meta:
        model=Study
        fields=['who','sells','audience','has_google','has_instagram','has_facebook','has_tiktok','has_website','has_booking','links','competitors','needs','opportunity','best_time','contact_to_find','notes']

from .models import Proposal, ProposalVersion, ProposalLine
class ProposalForm(PublicIdModelForm):
    class Meta:
        model=Proposal
        fields=['business','valid_until']
        labels={'business':'Negocio'}
        widgets={'valid_until':forms.DateInput(attrs={'type':'date'})}
class VersionForm(forms.ModelForm):
    class Meta:
        model=ProposalVersion
        fields=['monthly_fee','notes']
ProposalLineFormSet=forms.inlineformset_factory(ProposalVersion,ProposalLine,fields=['concept','description','amount','included'],extra=4,can_delete=True)

from .models import FollowUp,Visit
class FollowUpForm(PublicIdModelForm):
    class Meta:
        model=FollowUp
        fields=['description','date','owner','kind']
        labels={'owner':'Responsable'}
        widgets={'date':forms.DateInput(attrs={'type':'date'})}
class VisitForm(PublicIdModelForm):
    class Meta:
        model=Visit
        fields=['date','visitor','result','notes']
        labels={'visitor':'Quién fue'}
        widgets={'date':forms.DateTimeInput(attrs={'type':'datetime-local'})}

from .models import ClientCharge,Transaction,RecurringExpense,Commission
class ChargeForm(PublicIdModelForm):
    class Meta:
        model=ClientCharge
        fields=['business','concept','kind','amount','included','periodicity','next_date','status']
        labels={'business':'Negocio'}
        widgets={'next_date':forms.DateInput(attrs={'type':'date'})}
    def clean_amount(self):
        value=self.cleaned_data.get('amount')
        if value is not None and value<0: raise forms.ValidationError('El importe no puede ser negativo.')
        return value
class TransactionForm(PublicIdModelForm):
    class Meta:
        model=Transaction
        fields=['date','concept','business','category','amount','notes','receipt']
        labels={'business':'Negocio'}
        widgets={'date':forms.DateInput(attrs={'type':'date'})}
class ExpenseForm(forms.ModelForm):
    class Meta:
        model=RecurringExpense
        fields=['concept','amount','periodicity','next_date']
        widgets={'next_date':forms.DateInput(attrs={'type':'date'})}
    def clean_amount(self):
        amount=self.cleaned_data['amount']
        if amount<0: raise forms.ValidationError('Indica el gasto como importe positivo.')
        return amount
class CommissionForm(PublicIdModelForm):
    class Meta:
        model=Commission
        fields=['business','beneficiary','user','percentage','fixed_amount','status','paid_date']
        labels={'business':'Negocio','user':'Usuario (opcional)'}
        widgets={'paid_date':forms.DateInput(attrs={'type':'date'})}

from .models import User
class UserAccessForm(PublicIdModelForm):
    businesses=forms.ModelMultipleChoiceField(label='Acceso a negocios',queryset=Business.objects.all(),required=False,widget=forms.CheckboxSelectMultiple)
    class Meta:
        model=User
        fields=['name','role','is_active']
        labels={'is_active':'Cuenta activa'}

class DocumentStatusForm(forms.ModelForm):
    class Meta:
        model=Document
        fields=['status']

class RequestPlanningForm(PublicIdModelForm):
    class Meta:
        model=Request
        fields=['priority','target_date','proposal']
        labels={'proposal':'Propuesta asociada'}
        widgets={'target_date':forms.DateInput(attrs={'type':'date'})}

class DemoForm(forms.Form):
    file = forms.FileField(label='Demo (.html)', widget=forms.ClearableFileInput(attrs={'accept': '.html,.htm,text/html'}))
    def clean_file(self):
        from .demos import DEMO_EXTENSIONS, MAX_DEMO_SIZE
        f = self.cleaned_data['file']
        if not f.name.lower().endswith(DEMO_EXTENSIONS):
            raise forms.ValidationError('Sube un archivo .html.')
        if f.size > MAX_DEMO_SIZE:
            raise forms.ValidationError('Máximo 5 MB.')
        content = f.read()
        try:
            content.decode('utf-8')
        except UnicodeDecodeError:
            raise forms.ValidationError('El archivo debe estar en UTF-8.')
        return content
