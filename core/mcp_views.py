"""The web side of the Claude connector: approve a connection, see it, cut it."""
from django.contrib import messages
from django.db import transaction
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST
from mcp.server.auth.provider import construct_redirect_uri
from .auth_views import roles
from .mcp_auth import issue_code
from .models import McpAuthRequest, McpClient, McpToken


@roles('admin')
def consent(request):
    """/authorize lands here: signed in, second step done, admin. Yes issues the code; no says so."""
    txn = request.GET.get('txn') or request.POST.get('txn') or ''
    auth_request = McpAuthRequest.objects.filter(txn=txn, expires_at__gt=timezone.now()).first()
    if auth_request is None:
        return render(request, 'core/error.html', {'error': 'Esta solicitud de conexión ha caducado. Vuelve a conectar desde Claude.'}, status=400)
    client = McpClient.objects.filter(client_id=auth_request.client_id).first()
    params = auth_request.params
    if request.method == 'POST':
        with transaction.atomic():
            # One use: a second submit finds nothing.
            deleted, _ = McpAuthRequest.objects.filter(pk=auth_request.pk).delete()
            if not deleted:
                raise Http404
            if request.POST.get('decision') != 'allow':
                return redirect(construct_redirect_uri(params['redirect_uri'], error='access_denied', state=params.get('state')))
            code = issue_code(auth_request, request.user)
        return redirect(construct_redirect_uri(params['redirect_uri'], code=code, state=params.get('state')))
    response = render(request, 'core/mcp_consent.html', {'title': 'Conectar con Claude', 'txn': txn,
                                                         'client_name': client.name if client else 'Cliente MCP',
                                                         'redirect_host': params['redirect_uri'].split('/')[2] if '://' in params['redirect_uri'] else ''})
    response['Cache-Control'] = 'no-store'
    return response


@roles('admin')
@require_POST
def disconnect(request, grant_id):
    deleted, _ = McpToken.objects.visible_to(request.user).filter(grant_id=grant_id).delete()
    if not deleted:
        raise Http404
    messages.success(request, 'Conexión con Claude cortada. Para volver a usarla, conéctala de nuevo desde Claude.')
    return redirect('security')
