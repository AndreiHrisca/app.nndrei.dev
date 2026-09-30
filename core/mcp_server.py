"""MCP server (Streamable HTTP) so Claude can research businesses and fill studies.

Runs as its own Compose service from the same image (`uvicorn core.mcp_server:app`),
sharing the database with the web app. Caddy sends /mcp, the OAuth endpoints and
their /.well-known documents here; everything else goes to Django.

The tools are thin: the work, the checks and the history entries live in
core/mcp_tools.py, which runs through Django's ORM in a worker thread.
"""
import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django  # noqa: E402

django.setup()

from typing import Annotated, Literal  # noqa: E402
from asgiref.sync import sync_to_async  # noqa: E402
from django.conf import settings  # noqa: E402
from mcp.server.auth.middleware.auth_context import get_access_token  # noqa: E402
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions  # noqa: E402
from mcp.server.mcpserver import MCPServer  # noqa: E402
from mcp.server.transport_security import TransportSecuritySettings  # noqa: E402
from pydantic import Field  # noqa: E402
from starlette.requests import Request  # noqa: E402
from starlette.responses import JSONResponse  # noqa: E402
from . import mcp_tools  # noqa: E402
from .mcp_auth import SCOPE, NndreiAuthProvider, admin_by_subject  # noqa: E402

SITE_URL = settings.SITE_URL
# Demos can be a few MB of HTML, escaped inside JSON.
MAX_BODY = 12 * 1024 * 1024

INSTRUCTIONS = (
    'Conector de app.nndrei.dev, la plataforma interna de un negocio que hace webs para negocios locales. '
    'Sirve para preparar el «Estudio previo» de un negocio antes de visitarlo: quiénes son, qué venden, a quién, '
    'su presencia digital, la competencia cercana, lo que necesitan y la oportunidad de venderles una web.\n\n'
    'Flujo habitual: buscar_clientes (o crear_cliente si no existe) → investigar en la web → guardar_estudio con lo '
    'que sea verificable, citando las páginas en guardar_fuentes → si procede, subir_demo con una web de muestra y '
    'guardar_estimacion con los datos que se conozcan.\n\n'
    'Todo lo que escribes queda como «Borrador IA» hasta que una persona lo valida en la web, y cada escritura se '
    'anota en el historial del negocio. No inventes datos: si algo no se puede comprobar, déjalo vacío o dilo en '
    '«notas». La estimación de facturación es interna: nunca se enseña al negocio.'
)

mcp = MCPServer(
    name='nndrei',
    instructions=INSTRUCTIONS,
    auth_server_provider=NndreiAuthProvider(),
    auth=AuthSettings(
        issuer_url=SITE_URL,
        resource_server_url=f'{SITE_URL}/mcp',
        required_scopes=[SCOPE],
        client_registration_options=ClientRegistrationOptions(enabled=True, valid_scopes=[SCOPE], default_scopes=[SCOPE]),
        revocation_options=RevocationOptions(enabled=True),
    ),
)


async def run(tool, *args):
    """Run a tool in Django's sync world as the admin behind the token."""
    token = get_access_token()
    subject = token.subject if token else None

    def call():
        return tool(admin_by_subject(subject) if subject else None, *args)
    return await sync_to_async(call)()


ClienteId = Annotated[str, Field(description='El «id» del cliente tal como lo devuelven buscar_clientes o crear_cliente.')]


@mcp.tool(description='Busca clientes (negocios) por texto en el nombre, la dirección o la zona, y opcionalmente por fase: '
                      'Encontrado, Estudiado, Demo lista, Visitado, Propuesta, Aprobado, En construcción, Revisión final, '
                      'Web publicada o Descartado. Sin fase no devuelve los descartados. Máximo 50 resultados.')
async def buscar_clientes(texto: Annotated[str | None, Field(description='Texto a buscar. Vacío: todos.')] = None,
                          estado: Annotated[str | None, Field(description='Fase del seguimiento, por su nombre: Encontrado, Estudiado, Demo lista, '
                                                                          'Visitado, Propuesta, Aprobado, En construcción, Revisión final, '
                                                                          'Web publicada o Descartado.')] = None) -> dict:
    return await run(mcp_tools.buscar_clientes, texto, estado)


@mcp.tool(description='Devuelve la ficha completa de un cliente: datos de contacto, fase, datos de Google, horario, el estudio '
                      'previo (con si es borrador de la IA y sus fuentes), la web de muestra si existe y la última estimación.')
async def get_cliente(id: ClienteId) -> dict:
    return await run(mcp_tools.get_cliente, id)


@mcp.tool(description='Crea la ficha de un negocio nuevo en la fase «Encontrado». Si ya existe uno con ese place_id de Google, '
                      'devuelve el existente (ya_existia: true) en vez de duplicarlo.')
async def crear_cliente(nombre: Annotated[str, Field(description='Nombre comercial del negocio.', max_length=200)],
                        direccion: Annotated[str, Field(description='Dirección postal completa.', max_length=300)],
                        place_id: Annotated[str | None, Field(description='Id de Google Places, si lo conoces.')] = None) -> dict:
    return await run(mcp_tools.crear_cliente, nombre, direccion, place_id)


@mcp.tool(description='Escribe campos del estudio previo de un cliente. Solo cambia los campos que envíes. Campos de texto '
                      '(máx. 5000 caracteres): quienes_son, que_venden, publico_objetivo, enlaces (uno por línea), competencia, '
                      'necesidades, oportunidad, notas; mejor_momento (máx. 200) y persona_contacto (máx. 150). Campos sí/no: '
                      'ficha_google, instagram, facebook, tiktok, web_propia, reservas_online. Queda marcado como «Borrador IA» '
                      'con fecha hasta que alguien lo valide en la web.')
async def guardar_estudio(cliente_id: ClienteId,
                          campos: Annotated[dict, Field(description='Objeto campo → valor con los campos del estudio que quieras escribir.')],
                          fuentes: Annotated[list[str] | None, Field(description='Enlaces consultados (http/https), opcional.')] = None) -> dict:
    return await run(mcp_tools.guardar_estudio, cliente_id, campos, fuentes)


@mcp.tool(description='Añade al estudio los enlaces que has consultado como fuentes (http o https, hasta 50 por llamada y 100 '
                      'en total; los repetidos se ignoran). El estudio queda como «Borrador IA».')
async def guardar_fuentes(cliente_id: ClienteId,
                          urls: Annotated[list[str], Field(description='Lista de enlaces.')]) -> dict:
    return await run(mcp_tools.guardar_fuentes, cliente_id, urls)


@mcp.tool(description='Publica una web de muestra para el cliente: un único archivo HTML autocontenido (estilos y scripts dentro, '
                      'UTF-8, máx. 5 MB). Cada subida es una versión nueva y la última se ve en una dirección pública fija por '
                      'cliente (/demos/<slug>/), sin acceso al resto de la app. Si el cliente está en «Encontrado» o «Estudiado», pasa '
                      'a «Demo lista» (estudio y demo hechos, falta visitarlo); nunca retrocede. Devuelve la dirección y la fase.')
async def subir_demo(cliente_id: ClienteId,
                     nombre_archivo: Annotated[str, Field(description='Nombre del archivo, terminado en .html.')],
                     html: Annotated[str, Field(description='El documento HTML completo.')]) -> dict:
    return await run(mcp_tools.subir_demo, cliente_id, nombre_archivo, html)


@mcp.tool(description='Calcula y guarda una estimación interna de facturación mensual (nunca se enseña al negocio). Datos, todos '
                      'opcionales: resenas, nota (0–5), anos (antigüedad), ticket (€ por persona; si falta se deduce del nivel '
                      'de precios de Google), mesas, personas_por_mesa, servicios (lista con desayuno, comida, cena), rotaciones, '
                      'dias_por_semana (1–7), ocupacion (% 0–100), concurrencia (baja, media, alta), facturacion_anual (€, de '
                      'cuentas anuales o Registro Mercantil; manda sobre lo demás), fuente_facturacion y usa_reparto (true/false). '
                      'Devuelve el rango mensual, la confianza, la banda, el presupuesto sugerido y el argumento de retorno.')
async def guardar_estimacion(cliente_id: ClienteId,
                             datos: Annotated[dict, Field(description='Objeto con los datos conocidos.')]) -> dict:
    return await run(mcp_tools.guardar_estimacion, cliente_id, datos)


@mcp.custom_route('/healthz', methods=['GET'], include_in_schema=False)
async def healthz(request: Request):
    return JSONResponse({'ok': True})


def build_app():
    host = SITE_URL.split('://', 1)[-1].split('/', 1)[0]
    return mcp.streamable_http_app(
        streamable_http_path='/mcp',
        max_request_body_size=MAX_BODY,
        # Caddy terminates TLS and forwards the public Host; without this the
        # SDK's DNS-rebinding protection would reject the request.
        transport_security=TransportSecuritySettings(
            allowed_hosts=[host, 'nndrei-app-mcp:8000', 'localhost:8000', '127.0.0.1:8000', 'testserver'],
            allowed_origins=[SITE_URL],
        ),
    )


app = build_app()
