# Entrega por fases

## 1. Base

Proyecto, Docker, PostgreSQL, usuario por email, roles, invitaciones de 7 días con consumo transaccional, recuperación de contraseña, login limitado por Axes y TOTP obligatorio para personal interno. El cliente que activa TOTP también debe verificarlo en cada sesión. CSS y layout responsive según la sección 9; HTMX servido localmente.

Prueba: instrucciones de README, `pytest tests/test_base.py`. El admin de Django es una herramienta de inspección; las operaciones con reglas de negocio se realizan por vistas y servicios, para evitar saltarse auditoría. Los negocios y las relaciones de acceso nacen en esta fase porque las invitaciones dependen de ellos. No hay capturas adjuntas, por lo que la referencia visual es la especificación escrita.

## 2. Clientes

Fichas editables por admin, recursos por admin/developer asignado, documentos privados con descarga autorizada, peticiones con transiciones y motivo de rechazo, actividad y portal multinegocio. Vista fiscal separada del resto de la ficha para contador. Los clientes pueden solicitar cambios y consultar su roadmap. 40 pruebas acumuladas pasaron en PostgreSQL. Prueba específica: `pytest tests/test_clients.py`.

## 3. Propuestas

Numeración anual transaccional, editor de líneas, revisiones, aceptación presencial o por portal, enlaces firmados de lectura y PDF del mismo fragmento HTML con WeasyPrint. Se registra usuario, hora y dirección IP de conexión (no se confía en cabeceras de IP del cliente). La inmutabilidad de auditoría, contenido enviado y versiones aceptadas se refuerza con triggers PostgreSQL. La aceptación requiere login; el enlace público es solo lectura. El registro de fases se adelanta porque la aceptación depende de él. Prueba: `pytest tests/test_proposals.py`.

## 4. Pipeline y prospección

Pipeline, próximas acciones y visitas; cambios de fase permitidos explícitamente. Búsqueda en tareas django-q2, progreso HTMX, deduplicación, filtros, puntuación y límite mensual transaccional. 58 pruebas acumuladas pasaron. Prueba: `pytest tests/test_pipeline.py`.

Text Search (New) admite `locationRestriction.rectangle`, no círculo: se envía el rectángulo envolvente y se filtra por distancia geográfica. Referencia: https://developers.google.com/maps/documentation/places/web-service/reference/rest/v1/places/searchText . El máximo de presencia digital es 50 (incluido el bono de fotos), para no superar 100. No se etiqueta una web como «antigua» sin disponer de un dato fiable para determinarlo. Los datos Google caducan a los 30 días; se conservan los IDs y metadatos propios. La incorporación al pipeline exige confirmar los datos. La cuota configurable contabiliza intentos, incluidos errores; no se basa en un precio fijo del proveedor.

## 5. Finanzas

Cargos de cliente, movimientos internos, gastos fijos, comisiones, indicadores, gráfico SVG y CSV protegido frente a fórmulas. El contador tiene lectura y exportación. Recurrencias idempotentes con claves únicas por fecha y bloqueo transaccional. 65 pruebas acumuladas pasaron. Prueba: `pytest tests/test_finance.py`.

Se permite importe desconocido (`NULL`) en cargos, para que el dominio del cliente inicial no tenga un precio inventado. «Incluido» es explícito. Los movimientos recurrentes representan devengo/aviso y no acreditan pago bancario. El mantenimiento diario recupera vencimientos entre ejecuciones mensuales.

## 6. Monitorización y notificaciones

Comprobaciones cada cinco minutos con dos fallos para abrir incidencia, recuperación, agregados diarios y retención de 35 días. La barra usa disponibilidad ponderada por número de comprobaciones; los días sin mediciones son grises. HTTP solo a destinos públicos, validación en cada redirección y conexión a la IP validada para evitar acceso a la red privada. Emails HTML con bandeja persistente y reintentos; SMTP externo no se usa en pruebas. La entrega de email es «al menos una vez»: una caída entre entrega SMTP y registro del resultado podría duplicar un mensaje.

`setup_schedules` instala las tareas de forma idempotente. `seed_demo` conserva campos desconocidos vacíos y no sobrescribe registros existentes. Las versiones históricas tienen un estado de importación explícito, para poder mostrar «aceptada» sin inventar fecha de aceptación. Prueba: `pytest tests/test_monitoring.py`.

## Verificación de entrega

La matriz parametrizada ejecuta cada ruta propia con los cuatro roles, tanto con peticiones normales como HTMX, e impide que una ruta nueva quede fuera de la matriz. Se añaden casos de IDs de otro negocio, CSRF, repetición de TOTP, bloqueo de login, versiones obsoletas, triggers de inmutabilidad, PDF, recurrencias, cuota y paginación de Places, limpieza de caché y emails reintentables.

La revisión con Chromium recorre ocho pantallas internas a 1440 px y cuatro del portal a 390 px; las capturas están en `docs/screenshots/`. No se recibieron mockups adjuntos. La prueba de Places usa respuestas simuladas y SMTP usa memoria: falta configurar las credenciales propias para probar ambos proveedores en real. No hay un dominio de producción elegido ni se ha conectado el proxy.

Resultado final: **507 tests superados** en Python 3.12 / PostgreSQL 16; `manage.py check` sin incidencias y `makemigrations --check --dry-run` sin cambios pendientes. La imagen final responde HTTP 200 con Gunicorn (`DEBUG=False`) y sirve el CSS compilado mediante WhiteNoise. Se comprobó además que un worker django-q2 procesa una tarea real a través del broker ORM PostgreSQL, con el planificador desactivado durante esa comprobación. Los contenedores de revisión son temporales; el arranque de la instalación propia está en README.

## 7. Captación, tabla de clientes e identificadores opacos

**Buscar clientes.** «Al pipeline» pasa a llamarse «Captar». El literal vive en `core/labels.py` y lo consumen el botón, el tooltip, el mensaje flash, el título del formulario de confirmación y la entrada de actividad; las plantillas lo leen con `{% label 'CAPTURE' %}` (`core/templatetags/labels.py`). Captar y Descartar son un par de botones horizontales de altura de badge (`.compact` + `.btn-success` / `.btn-danger`, sobre las variables `--btn-success-*` y `--btn-danger-*` reutilizables). Todas las filas miden lo mismo tengan botones o no, y las filas «En pipeline» ofrecen «Ver ficha» en lugar de una celda vacía. Descartar pide confirmación con `hx-confirm`. Ambas acciones responden con la fila cambiada y, fuera de banda, con el bloque de contadores (`core/search_stats.html`, `hx-swap-oob`), así que la página no se recarga.

Decisión: `POST /buscar/<uuid>/captar/` crea la ficha directamente con los datos de Google, porque «sin recargar la página» y «confirma los datos en un formulario aparte» no son compatibles. El formulario de confirmación sigue existiendo en el `GET` de la misma ruta y es el camino sin JavaScript; la ficha es editable después. La captación es idempotente bajo doble clic (`select_for_update` + comprobación de estado).

La tabla usa `table-layout: fixed` con `<colgroup>`. Las columnas numéricas (Nota, Reseñas, Fotos) llevan cabecera y celda alineadas a la derecha con `tabular-nums`; Estado y Web quedan a la izquierda como su cabecera. Las reseñas se formatean con separador de miles y la nota con coma decimal (`miles` y `nota` en `core/templatetags/money.py`).

**Clientes.** La tabla añade zona, sector (mismo badge por categoría que la búsqueda), teléfono con `tel:`, nota y reseñas de Google, estado de la web, última actividad relativa con fecha exacta en el `title`, próxima acción y presupuesto. Hay buscador HTMX con *debounce* de 300 ms, filtros por fase y sector, y cabeceras ordenables por Negocio, Fase, Última actividad y Próxima acción; el parámetro `sort` se valida contra una lista blanca. La fila entera navega a la ficha y el enlace del teléfono se excluye del clic. En móvil la tabla hace scroll horizontal dentro de su tarjeta en lugar de apilarse. La consulta es plana: `select_related` de la categoría y subconsultas para actividad, ficha de Google y presupuesto (5 consultas con 21 negocios).

`Business.category` deja de ser texto libre y pasa a ser una FK a `SearchCategory`, el mismo vocabulario que la búsqueda. La migración de datos `0013` traduce lo escrito antes: coincidencia exacta sin distinguir mayúsculas, alias conocidos («Bar» → «Bares») y pluralización castellana; un sector nunca visto se conserva creando su categoría en lugar de perderse. La lógica está duplicada a propósito en `core/categories.py` (tiempo de ejecución) y en la migración (histórico congelado). Se añaden `next_action` y `next_action_date`, editables en la ficha.

**Identificadores opacos.** Doce modelos con vista de detalle o acción por ID (`User`, `Business`, `Document`, `Request`, `Proposal`, `FollowUp`, `PlaceSnapshot`, `SearchRun`, `ClientCharge`, `Transaction`, `RecurringExpense`, `Commission`) reciben `public_id` en tres migraciones: columna nullable, relleno con `uuid4` y, por último, única y obligatoria. Todas las rutas de detalle y acción usan `<uuid:public_id>`, de modo que las antiguas con ID numérico ya no casan con ningún patrón y devuelven 404. Los formularios heredan de `PublicIdModelForm`, que cambia `to_field_name` a `public_id` en cada campo de elección cuyo modelo lo tenga, así que tampoco aparece un ID de fila en un `<option>` ni en un `value`.

Ofuscar no es controlar el acceso: `core/access.scoped` resuelve cada objeto desde el *queryset* que el usuario tiene permitido ver, y un objeto fuera de ese alcance da 404 y no 403, para no confirmar que existe. Todas las vistas de detalle y todas las acciones pasan por ahí.

Decisiones tomadas por su cuenta y sus motivos: `ActivityEvent`, `StageChange`, `ProposalVersion` y `ProposalLine` no llevan `public_id`. Los tres primeros están protegidos por *triggers* que rechazan cualquier `UPDATE`, así que rellenar la columna sería imposible sin desmontar la inmutabilidad; además no tienen vista de detalle. Una versión de propuesta se direcciona ahora por su número dentro de la propuesta (`?version=2`), que es lo que el usuario ya ve. El *token* firmado de la propuesta pública conserva el ID interno porque va cifrado y firmado, y cambiarlo invalidaría los enlaces ya enviados. `SearchZone` y `SearchCategory` siguen filtrándose por ID: son taxonomía de configuración, sin vista de detalle ni acciones, y no permiten enumerar clientes. Los `id` ocultos del *formset* de líneas de propuesta también siguen siendo enteros: pertenecen al propio formulario, que ya está acotado a una versión que el usuario puede editar. Las entradas de actividad ya escritas conservan su texto antiguo: el historial es un registro de auditoría inmutable y no se reescribe.

Resultado: **536 pruebas superadas**; `manage.py check` sin incidencias y `makemigrations --check --dry-run` sin cambios pendientes.

## 8. Versión móvil (<768 px)

Escritorio (≥1024 px) no cambia: las capturas a 1440 px antes y después coinciden píxel a píxel salvo horas del reloj, la nueva pantalla «Más» y el orden de «Clientes en curso» (ahora por próxima acción más cercana; sin fecha, al final).

**Componentes** (`static/css/app.css`, sección «Phone layout»; comportamiento en `static/js/mobile.js`): `list-card` (tarjeta de fila; `.lc-link` hace clicable toda la tarjeta), `m-sheet` (bottom sheet `<dialog>` con «Aplicar» por HTMX, «Limpiar» y contador «Filtros · N»), `m-chips` (chip-scroller; chips-radio dentro del formulario o enlaces HTMX), `stat-strip`, `fab`, app bar (`m-appbar`) y barra inferior (`m-tabbar`). `.desk-only` / `.mob-only` eligen la representación por breakpoint: tabla en escritorio, tarjetas en móvil, con la misma consulta y sin lógica duplicada en vistas. Desaparece el patrón `td:before{content:attr(data-label)}`.

**Navegación** (`core/templatetags/mobile.py`): pestañas por rol (admin: Pipeline, Buscar, Clientes, Propuestas, Más; developer: Clientes, Propuestas, Más; contador: Clientes, Propuestas, Finanzas, Más; cliente: Mi negocio, Peticiones, Gastos, Documentos, Cuenta). «Más» es `/mas/` (cuenta, Finanzas, Usuarios, 2FA, cerrar sesión). Las vistas de detalle muestran «← Volver» al padre.

**Rutas nuevas**: `/mas/` (todos los roles) y `POST /clientes/<uuid>/nota/` («Nota rápida», admin y developer; entra en el historial interno). Ambas en la matriz de permisos. Pipeline acepta `?stage=` y Propuestas `?status=`; Finanzas calcula mes anterior/siguiente. Las acciones Captar/Descartar desde tarjeta devuelven la tarjeta (`layout=card`) y los contadores fuera de banda. El service worker sube a `v2` para no servir CSS/JS antiguos.

Decisión: el swipe para captar/descartar se omite; con la confirmación obligatoria de «Descartar» no resultaba sencillo ni robusto, y los dos botones a ancho completo cubren el caso.

**Pruebas de navegador**: `docker compose -p nndrei-validation -f compose.test.yaml run --rm e2e` (imagen `Dockerfile.e2e`, WebKit). Recorre 18 pantallas de los cuatro roles a 375, 393 y 430 px y falla si `scrollWidth > innerWidth`; prueba además los flujos HTMX (filtro por fase, bottom sheet, Captar, «Cargar más», búsqueda, cambio de mes, nota rápida). Capturas en `docs/screenshots/mobile/`: `393-<rol>-<pantalla>.png` (lo que se ve al abrir) y `-full.png` (página entera sin barras fijas); las de 1440 px, en `desktop/`. La suite normal la omite si no hay Playwright.

Resultado: **614 pruebas superadas** + 3 de navegador; `makemigrations --check` sin cambios.
