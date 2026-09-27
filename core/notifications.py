from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.db import transaction
from django.template.loader import render_to_string
from django.utils import timezone

def deliver(message):
    mail=EmailMultiAlternatives(message.subject,message.text+('\n'+message.url if message.url else ''),settings.DEFAULT_FROM_EMAIL,message.recipients)
    mail.attach_alternative(render_to_string('emails/message.html',{'title':message.subject,'text':message.text,'url':message.url}),'text/html')
    mail.send()

def notify(subject,recipients,text,url=''):
    from .models import EmailOutbox
    recipients=list(dict.fromkeys(x for x in recipients if x))
    if not recipients: return
    message=EmailOutbox.objects.create(subject=subject,recipients=recipients,text=text,url=url)
    # Persist before sending. SMTP errors do not lose the business operation.
    transaction.on_commit(lambda: send_outbox(message.pk))

def send_outbox(pk):
    from .models import EmailOutbox
    with transaction.atomic():
        message=EmailOutbox.objects.select_for_update().get(pk=pk)
        if message.sent_at: return
        message.attempts+=1
        try:
            deliver(message)
        except Exception:
            message.last_error='No se pudo entregar el email. Se reintentará automáticamente.'
        else:
            message.sent_at=timezone.now(); message.last_error=''
        message.save()

def retry_emails():
    from .models import EmailOutbox
    for pk in EmailOutbox.objects.filter(sent_at__isnull=True,attempts__lt=10).values_list('pk',flat=True):
        send_outbox(pk)
