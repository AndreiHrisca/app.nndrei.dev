from functools import wraps
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django_otp import login as otp_login
from django_otp.plugins.otp_totp.models import TOTPDevice
from .access import scoped
from .forms import AcceptInvitationForm, InvitationForm
from .models import User, Invitation, ClientMembership

def roles(*allowed):
    def decorator(view):
        @login_required
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if request.user.role not in allowed:
                raise Http404
            return view(request, *args, **kwargs)
        return wrapped
    return decorator

@login_required
def two_factor(request):
    import qrcode
    import qrcode.image.svg
    device = TOTPDevice.objects.filter(user=request.user, confirmed=True).first()
    enrolling = device is None
    if enrolling:
        device, _ = TOTPDevice.objects.get_or_create(user=request.user, confirmed=False, defaults={'name': 'Autenticador'})
    error = ''
    if request.method == 'POST':
        with transaction.atomic():
            device = TOTPDevice.objects.select_for_update().get(pk=device.pk)
            if device.verify_token(request.POST.get('token', '')):
                device.confirmed = True
                device.save()
                otp_login(request, device)
                return redirect('home')
        error = 'Código incorrecto o temporalmente bloqueado. Espera e inténtalo de nuevo.'
    qr = qrcode.make(device.config_url, image_factory=qrcode.image.svg.SvgPathImage).to_string().decode() if enrolling else ''
    response = render(request, 'registration/two_factor.html', {'enrolling': enrolling, 'qr': qr, 'error': error})
    response['Cache-Control'] = 'no-store'
    return response

def accept_invitation(request, token):
    with transaction.atomic():
        invitation = get_object_or_404(Invitation.objects.select_for_update(), token=token, accepted_at__isnull=True, expires_at__gt=timezone.now())
        if User.objects.filter(email__iexact=invitation.email).exists():
            raise Http404
        user = User(email=invitation.email.lower(), role=invitation.role)
        form = AcceptInvitationForm(user, request.POST or None)
        if request.method == 'POST' and form.is_valid():
            user.name = form.cleaned_data['name']
            user.is_staff = user.role == 'admin'
            form.save()
            if invitation.business and user.role == 'client':
                ClientMembership.objects.create(user=user, business=invitation.business)
            invitation.accepted_at = timezone.now()
            invitation.save(update_fields=['accepted_at'])
            login(request, user, backend='django.contrib.auth.backends.ModelBackend')
            return redirect('home')
    return render(request, 'registration/form.html', {'form': form, 'title': 'Acepta tu invitación', 'button': 'Crear mi acceso'})

@roles('admin')
def users(request):
    form = InvitationForm(request.POST or None)
    if request.method == 'POST' and form.is_valid():
        invitation = form.save(commit=False)
        invitation.invited_by = request.user
        invitation.save()
        from .notifications import notify
        from django.conf import settings
        notify('Tu invitación a nndrei', [invitation.email], 'Configura tu acceso. El enlace caduca en 7 días.', settings.SITE_URL + '/invitacion/' + invitation.token + '/')
        messages.success(request, 'Invitación enviada.')
        return redirect('users')
    return render(request, 'core/users.html', {'title': 'Usuarios', 'form': form, 'users': User.objects.visible_to(request.user).prefetch_related('totpdevice_set','clientmembership_set__business','assignment_set__business')})

@roles('admin')
def user_edit(request,public_id):
    from .forms import UserAccessForm
    from .models import Assignment,Business
    target=scoped(User,request.user,public_id)
    current=Business.objects.visible_to(target) if target.role in ['client','developer'] else Business.objects.none()
    form=UserAccessForm(request.POST or None,instance=target,initial={'businesses':current})
    if request.method=='POST' and form.is_valid():
        if target.pk==request.user.pk and (not form.cleaned_data['is_active'] or form.cleaned_data['role']!='admin'):
            form.add_error(None,'No puedes desactivar tu cuenta ni retirar tu propio rol de admin.')
        else:
            with transaction.atomic():
                target=form.save(commit=False)
                target.is_staff=target.role=='admin'
                if target.role!='admin': target.is_superuser=False
                target.save()
                ClientMembership.objects.filter(user=target).delete()
                Assignment.objects.filter(developer=target).delete()
                for business in form.cleaned_data['businesses']:
                    if target.role=='client': ClientMembership.objects.create(user=target,business=business)
                    elif target.role=='developer': Assignment.objects.create(developer=target,business=business)
            messages.success(request,'Usuario y accesos actualizados.')
            return redirect('users')
    return render(request,'core/form.html',{'form':form,'title':'Gestionar acceso · '+target.email})
