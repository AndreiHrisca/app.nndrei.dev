# Plataforma nndrei

Django 5.2, Python 3.12, PostgreSQL 16, HTMX y CSS propio. Interfaz en español y horas de Madrid.

## Arranque local

1. Copia `.env.example` a `.env`. Configura una `SECRET_KEY` aleatoria, contraseña PostgreSQL y `DATABASE_URL` con la misma contraseña. Rellena `SEED_ADMIN_EMAIL` y `SEED_ADMIN_PASSWORD` (contraseña robusta). Para correo real, configura SMTP y su backend.
2. `docker compose build`
3. `docker compose up -d db`
4. `docker compose run --rm web python manage.py migrate`
5. `docker compose run --rm web python manage.py seed_demo`
6. `docker compose run --rm web python manage.py setup_schedules`
7. `docker compose run --rm web python manage.py setup_geography` (municipios del INE, barrios de Madrid y ciudad/barrio de cada negocio; se puede repetir)
8. `docker compose up -d web worker mcp inbox-sync` (`inbox-sync` necesita que exista el volumen de nndrei-portfolio; ver «Consultas»)

Conecta tu proxy al servicio `web:8000` dentro de la red de Compose. No hay puertos públicos en el Compose principal. Para probar en localhost: `docker compose -f compose.yaml -f compose.local.yaml up -d`. Abre http://localhost:8000. El primer acceso de un administrador exige escanear el QR TOTP; la política por rol se cambia en `/admin/` (Políticas de verificación en dos pasos). Los clientes pueden entrar con un enlace al email, que envía el `worker`.

Configura `SITE_URL`, `ALLOWED_HOSTS` y `CSRF_TRUSTED_ORIGINS` para tu dominio. En producción con HTTPS activa las tres variables de seguridad de `.env.example`. El proxy debe sobrescribir `X-Forwarded-Proto`. Nunca publiques `/app/private` ni lo montes como estáticos. Haz copias de PostgreSQL y del volumen `private_data`.

## Consultas: dónde llegan

El formulario de nndrei.dev/hablamos lo guarda portfolio-api en su SQLite (`~/stacks/webs/nndrei-portfolio`, volumen `nndrei-portfolio_portfolio-data`). Ese SQLite es solo un buzón de paso: **la dueña de los datos es esta app**, sección **Consultas** (`/consultas/`, solo administradores).

```
nndrei.dev/hablamos → nginx → portfolio-api (red interna) → SQLite inbox
                                                               │  volumen compartido, uid 10001, 0600
                              inbox-sync (cada 30 s) ◄─────────┘
                                 │ 1. lee filas  2. inserta en PostgreSQL (por request_id)  3. borra del buzón
                                 ▼
                     Consultas en app.nndrei.dev  ──► aviso por Telegram (opcional)
```

- **Servicio `inbox-sync`** (`python manage.py sync_inquiries --loop`, código en `core/inquiry_sync.py`). Monta el volumen como `external` y corre con `user: 10001:10001`, el mismo uid que portfolio-api, así que la base sigue en 0600 sin lectura para nadie más. Sin puertos ni red `edge`; contenedor de solo lectura y sin capacidades.
- **Idempotente**: la clave es el `request_id` del formulario (o un hash de fecha + email + mensaje si faltase). Solo borra del buzón las filas cuya inserción se ha confirmado; si PostgreSQL falla, se quedan y se reintentan en el siguiente ciclo. Ante `SQLITE_BUSY` espera (timeout 5 s) y reintenta.
- **Sin volumen o sin tabla**: deja un aviso en los logs (una vez, no cada 30 s) y sigue. Los logs solo llevan recuentos y tipos de error, nunca nombres, emails ni mensajes.
- **Telegram**: si `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID` están en `.env`, cada consulta nueva manda nombre, negocio, origen y enlace directo. Si faltan, no avisa y todo lo demás funciona. Hasta 5 intentos por consulta, solo durante su primer día.
- **Estados**: nueva → leída (al abrirla) → respondida / convertida / archivada / spam, con historial de cambios. «Responder por email» abre un `mailto:` con asunto y saludo; «por WhatsApp», `wa.me` si hay teléfono; las dos la marcan como respondida.
- **Convertir en cliente**: crea una ficha prellenada o vincula una existente (sugiere coincidencias por teléfono, email o nombre del negocio). La ficha muestra sus consultas y la consulta enlaza a la ficha.
- **Eliminar definitivamente** (solicitudes de supresión): borra la consulta, sus notas y su historial. Si estaba vinculada, el historial del negocio solo anota «Consulta de nndrei.dev eliminada», sin datos personales.
- **Retención**: `daily_maintenance` borra las no convertidas a los 180 días (la misma política que tenía el portfolio). Las convertidas viven lo que viva la ficha del cliente.

Puesta en marcha: `docker compose build && docker compose run --rm web python manage.py migrate && docker compose up -d web inbox-sync`. Comprobar: `docker compose logs -f inbox-sync`. Para una pasada manual: `docker compose run --rm inbox-sync python manage.py sync_inquiries`.

## Pruebas aisladas

`docker build -t nndrei-business:dev .`

`docker compose -p nndrei-validation -f compose.test.yaml run --rm test pytest -q`

Las pruebas usan Python 3.12 y PostgreSQL 16 temporal, sin datos reales. Para verificar migraciones: sustituye `pytest -q` por `python manage.py makemigrations --check --dry-run`. Para aplicar migraciones: `python manage.py migrate`.

## Fases

El detalle de implementación, verificación y decisiones está en `docs/fases.md`.

Los estáticos se recopilan al construir la imagen. No es necesario ejecutar collectstatic en un contenedor efímero. Los accesos a negocios se gestionan en Usuarios; las coordenadas y categorías, en `/admin/`. «Actualizar» en Buscar clientes solo repite las zonas y sectores consultados hace más de `PLACES_REFRESH_MIN_DAYS` días. El contador nunca recibe repositorios, notas internas ni costes ajenos a sus vistas económicas.
