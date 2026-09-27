# Plataforma nndrei

Django 5.2, Python 3.12, PostgreSQL 16, HTMX y CSS propio. Interfaz en español y horas de Madrid.

## Arranque local

1. Copia `.env.example` a `.env`. Configura una `SECRET_KEY` aleatoria, contraseña PostgreSQL y `DATABASE_URL` con la misma contraseña. Rellena `SEED_ADMIN_EMAIL` y `SEED_ADMIN_PASSWORD` (contraseña robusta). Para correo real, configura SMTP y su backend.
2. `docker compose build`
3. `docker compose up -d db`
4. `docker compose run --rm web python manage.py migrate`
5. `docker compose run --rm web python manage.py seed_demo`
6. `docker compose run --rm web python manage.py setup_schedules`
7. `docker compose up -d web worker`

Conecta tu proxy al servicio `web:8000` dentro de la red de Compose. No hay puertos públicos en el Compose principal. Para probar en localhost: `docker compose -f compose.yaml -f compose.local.yaml up -d`. Abre http://localhost:8000. El primer acceso interno exige escanear el QR TOTP.

Configura `SITE_URL`, `ALLOWED_HOSTS` y `CSRF_TRUSTED_ORIGINS` para tu dominio. En producción con HTTPS activa las tres variables de seguridad de `.env.example`. El proxy debe sobrescribir `X-Forwarded-Proto`. Nunca publiques `/app/private` ni lo montes como estáticos. Haz copias de PostgreSQL y del volumen `private_data`.

## Pruebas aisladas

`docker build -t nndrei-business:dev .`

`docker compose -p nndrei-validation -f compose.test.yaml run --rm test pytest -q`

Las pruebas usan Python 3.12 y PostgreSQL 16 temporal, sin datos reales. Para verificar migraciones: sustituye `pytest -q` por `python manage.py makemigrations --check --dry-run`. Para aplicar migraciones: `python manage.py migrate`.

## Fases

El detalle de implementación, verificación y decisiones está en `docs/fases.md`.

Los estáticos se recopilan al construir la imagen. No es necesario ejecutar collectstatic en un contenedor efímero. Los accesos a negocios se gestionan en Usuarios; las coordenadas y categorías, en `/admin/`. El contador nunca recibe repositorios, notas internas ni costes ajenos a sus vistas económicas.
