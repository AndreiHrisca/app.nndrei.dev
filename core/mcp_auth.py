"""OAuth 2.1 for the MCP connector, the same way Diario does it, with one change.

Claude.ai only connects to remote MCP servers through OAuth: it registers a
client (DCR), sends the person to /authorize and trades the code at /token.
Diario asks for a shared password on its own screen; here /authorize sends you
to the web app instead (/conectar-claude/), so the normal login, the second
step and the login limits all apply, and each connection belongs to the admin
who approved it. PKCE, code expiry and redirect_uri checks are the SDK's.

State lives in PostgreSQL (restarts do not drop the connector) and tokens and
codes are stored as SHA-256, never in clear.
"""
import secrets
import time
from datetime import timedelta
from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import transaction
from django.utils import timezone
from mcp.server.auth.provider import AccessToken, AuthorizationCode, OAuthAuthorizationServerProvider, RefreshToken
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from .models import McpAuthRequest, McpClient, McpCode, McpToken, User
from .security import digest

SCOPE = 'nndrei'
ACCESS_TTL = 3600
REFRESH_TTL = 30 * 86400
CODE_TTL = 300
REQUEST_TTL = timedelta(minutes=10)
TOUCH_EVERY = timedelta(minutes=5)


def admin_by_subject(subject):
    """The admin a token belongs to; demoted or deactivated people lose access at once."""
    return User.objects.filter(public_id=subject, is_active=True, role='admin').first()


# --- Sync parts (ORM), wrapped below --------------------------------------------------

def get_client(client_id):
    item = McpClient.objects.filter(client_id=client_id).first()
    return OAuthClientInformationFull.model_validate(item.info) if item else None


def register_client(info):
    McpClient.objects.update_or_create(client_id=info.client_id, defaults={'info': info.model_dump(mode='json')})


def start_authorization(client_id, params):
    McpAuthRequest.objects.filter(expires_at__lt=timezone.now()).delete()
    txn = secrets.token_urlsafe(32)
    McpAuthRequest.objects.create(txn=txn, client_id=client_id, params=params.model_dump(mode='json'),
                                  expires_at=timezone.now() + REQUEST_TTL)
    return f'{settings.SITE_URL}/conectar-claude/?txn={txn}'


def issue_code(auth_request, user):
    """Called by the consent view once an admin says yes."""
    code = secrets.token_urlsafe(32)
    params = auth_request.params
    McpCode.objects.create(code_hash=digest(code), client_id=auth_request.client_id, user=user, params=params,
                           expires_at=time.time() + CODE_TTL)
    return code


def load_code(client_id, code):
    item = McpCode.objects.select_related('user').filter(code_hash=digest(code), client_id=client_id).first()
    if item is None:
        return None
    p = item.params
    return AuthorizationCode(code=code, scopes=p.get('scopes') or [SCOPE], expires_at=item.expires_at, client_id=client_id,
                             code_challenge=p['code_challenge'], redirect_uri=p['redirect_uri'],
                             redirect_uri_provided_explicitly=p['redirect_uri_provided_explicitly'],
                             resource=p.get('resource'), subject=str(item.user.public_id))


@transaction.atomic
def issue_grant(client_id, scopes, resource, subject):
    user = admin_by_subject(subject)
    if user is None:
        raise ValueError('La persona que autorizó la conexión ya no es administradora.')
    now = int(time.time())
    McpToken.objects.filter(expires_at__lt=now).delete()
    grant = secrets.token_urlsafe(16)
    access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    for token, kind, ttl in ((access, 'access', ACCESS_TTL), (refresh, 'refresh', REFRESH_TTL)):
        McpToken.objects.create(token_hash=digest(token), kind=kind, grant_id=grant, client_id=client_id, user=user,
                                scopes=list(scopes), resource=resource or '', expires_at=now + ttl)
    return OAuthToken(access_token=access, token_type='Bearer', expires_in=ACCESS_TTL, scope=' '.join(scopes), refresh_token=refresh)


@transaction.atomic
def exchange_code(client_id, code):
    item = McpCode.objects.select_for_update().select_related('user').filter(code_hash=digest(code.code), client_id=client_id).first()
    if item is None:
        raise ValueError('Código ya usado.')
    item.delete()  # One use: gone before anything is issued.
    return issue_grant(client_id, code.scopes, code.resource, str(item.user.public_id))


def load_refresh(client_id, token):
    item = McpToken.objects.select_related('user').filter(token_hash=digest(token), kind='refresh', client_id=client_id,
                                                          expires_at__gt=int(time.time())).first()
    if item is None:
        return None
    return RefreshToken(token=token, client_id=client_id, scopes=item.scopes, expires_at=item.expires_at, subject=str(item.user.public_id))


@transaction.atomic
def exchange_refresh(client_id, token, scopes):
    item = McpToken.objects.select_for_update().filter(token_hash=digest(token.token), kind='refresh').first()
    if item is None:
        raise ValueError('Token de renovación ya usado.')
    resource = item.resource
    # Rotation: the old pair dies when the new one is issued.
    McpToken.objects.filter(grant_id=item.grant_id).delete()
    return issue_grant(client_id, scopes or token.scopes, resource, token.subject)


def load_access(token):
    item = McpToken.objects.select_related('user').filter(token_hash=digest(token), kind='access', expires_at__gt=int(time.time())).first()
    if item is None or admin_by_subject(item.user.public_id) is None:
        return None
    now = timezone.now()
    if item.last_used_at is None or now - item.last_used_at > TOUCH_EVERY:
        McpToken.objects.filter(pk=item.pk).update(last_used_at=now)
    return AccessToken(token=token, client_id=item.client_id, scopes=item.scopes, expires_at=item.expires_at,
                       resource=item.resource or None, subject=str(item.user.public_id))


def revoke(token):
    item = McpToken.objects.filter(token_hash=digest(token)).first()
    if item is not None:
        # RFC 7009: revoking one token of the pair revokes the other.
        McpToken.objects.filter(grant_id=item.grant_id).delete()


def connections(user):
    """Live connections of `user` for the Seguridad page: one row per grant."""
    rows = {}
    for token in McpToken.objects.visible_to(user).filter(expires_at__gt=int(time.time())).order_by('created_at'):
        row = rows.setdefault(token.grant_id, {'grant_id': token.grant_id, 'client_id': token.client_id, 'since': token.created_at, 'last_used': None})
        if token.last_used_at and (row['last_used'] is None or token.last_used_at > row['last_used']):
            row['last_used'] = token.last_used_at
    names = {c.client_id: c.name for c in McpClient.objects.filter(client_id__in=[r['client_id'] for r in rows.values()])}
    for row in rows.values():
        row['name'] = names.get(row['client_id'], 'Cliente MCP')
    return list(rows.values())


class NndreiAuthProvider(OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]):
    async def get_client(self, client_id):
        return await sync_to_async(get_client)(client_id)

    async def register_client(self, client_info):
        await sync_to_async(register_client)(client_info)

    async def authorize(self, client, params):
        return await sync_to_async(start_authorization)(client.client_id, params)

    async def load_authorization_code(self, client, authorization_code):
        return await sync_to_async(load_code)(client.client_id, authorization_code)

    async def exchange_authorization_code(self, client, authorization_code):
        return await sync_to_async(exchange_code)(client.client_id, authorization_code)

    async def load_refresh_token(self, client, refresh_token):
        return await sync_to_async(load_refresh)(client.client_id, refresh_token)

    async def exchange_refresh_token(self, client, refresh_token, scopes):
        return await sync_to_async(exchange_refresh)(client.client_id, refresh_token, scopes)

    async def load_access_token(self, token):
        return await sync_to_async(load_access)(token)

    async def revoke_token(self, token):
        await sync_to_async(revoke)(token.token)
