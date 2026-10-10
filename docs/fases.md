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

## 9. Textos comprensibles

Todos los textos visibles que se renombran viven en `core/labels.py`: fases (`STAGE_LABELS`), roles, estados de petición, títulos, botones y mensajes. Los modelos construyen sus `choices` desde ahí y las plantillas leen las constantes con `{% label 'NOMBRE' %}` (cualquier constante en mayúsculas de texto). No cambian modelos, campos, URLs ni valores guardados: la migración `0016_visible_labels` solo altera los `choices` y no ejecuta SQL.

Cambios: «Pipeline» → «Seguimiento»; «+ Nuevo lead» → «+ Nuevo negocio»; «N en el embudo» → «N en marcha»; fases Estudiado, Propuesta enviada, Cambios en la propuesta, Aprobado (también en la chip, antes «Presupuesto aprobado»), En construcción, Revisión final y Web publicada; «En pipeline» → «En seguimiento»; «2FA» → «Verificación en dos pasos»; roles Administrador y Desarrollador; «Hosting» → «Alojamiento web»; «Demo» → «Web de muestra»; estados de petición En construcción, Revisión final y Publicada. El historial de actividad ya escrito conserva su texto antiguo porque es inmutable.

Resultado: **661 pruebas superadas**; `makemigrations --check` sin cambios.

## 10. Horarios, «abierto ahora» y «Cerca de mí»

**Datos.** `Business` gana `latitude`, `longitude`, `opening_hours` (JSON), `hours_source` (`google`/`manual`), `hours_updated_at`, y la copia de Google (`google_hours`, `google_rating`, `google_reviews`, `google_price_level`, `google_maps_url`, `google_fetched_at`). El horario usa el mismo formato que `regularOpeningHours.periods` de Places API (New): día 0 = domingo, un cierre en el día siguiente cruza la medianoche y un único periodo sin cierre es 24 horas. Un horario manual nunca se sobrescribe; «Volver al horario de Google» usa `google_hours` sin llamar a la API.

**Sin llamadas nuevas a Google.** No hay refresco diario: la cuota es de 800 llamadas al mes. La búsqueda de «Buscar clientes» pide además `regularOpeningHours`, `utcOffsetMinutes` y `priceLevel` (la máscara ya estaba en el nivel de precio de esos campos), y cada resultado cuyo `place_id` coincide con un negocio actualiza su horario, ubicación y nota con la misma llamada. `SearchSlice` guarda cuándo se consultó cada zona × sector: «Actualizar» se salta las combinaciones consultadas hace menos de `PLACES_REFRESH_MIN_DAYS` días (7 por defecto) y la pantalla dice antes de pulsar cuántas llamadas puede gastar. Para un negocio que no sale en ninguna búsqueda, la ficha ofrece «Actualizar desde Google» (Place Details, 1 llamada, con confirmación). Las vistas nunca llaman a la API al mostrarse.

Caducidad: igual que los resultados de búsqueda, la copia de Google en un negocio se borra a los 30 días en `daily_maintenance` (`expire_google_data`). El horario puesto a mano es dato propio y se conserva.

**Estado.** `core/hours.py` es puro (`hours_status(periods, now)`), en hora de Madrid: «Abierto, cierra a las 23:30», «Cierra en 40 min» (menos de 60), «Cerrado, abre a las 17:00», «Cerrado hoy, abre mañana a las 07:30», «Cerrado hoy, abre el martes a las 07:30», «Abierto 24 horas» y «Horario desconocido». Tramos contiguos se funden, incluido el paso de sábado a domingo.

**Clientes.** Cada negocio muestra su estado (el desconocido, en gris discreto). «Abiertos ahora» filtra; «Cerca de mí» pide la ubicación al navegador, la manda solo con esa petición y el servidor la quita de la URL que se guarda en el historial (`HX-Push-Url`). Ordena por distancia (haversine de `places.distance_m`) con los negocios sin coordenadas al final, y muestra «650 m» o «1,2 km». Sin permiso, la lista sigue igual y lo explica. Se calcula en Python porque la lista no está paginada.

**Ficha.** Tarjeta «Horario» con la semana (hoy resaltado), origen y fecha. El editor (solo admin) tiene siete días con «Cerrado», hasta tres tramos por día, «+ Añadir tramo» y «Copiar el lunes a martes–viernes»; valida tramos incompletos y solapados. Cada cambio queda en el historial interno.

Rutas nuevas (en la matriz de permisos, solo admin): `/clientes/<uuid>/horario/`, `/clientes/<uuid>/horario/google/` y `/clientes/<uuid>/actualizar-google/`.

Resultado: **714 pruebas superadas** + 4 de navegador (nueva: editor de horario y «Cerca de mí» con y sin permiso; capturas `393-admin-horario*.png` y `393-admin-clientes-cerca.png`); `makemigrations --check` sin cambios.

## 11. Ir al negocio, «He ido» y descartados

**Ir al negocio.** Tarjeta con botones grandes a Google Maps (`/maps/dir/?api=1&destination=lat,lng&destination_place_id=…`), Waze (`ul?ll=…&navigate=yes`) y Apple Maps (`?daddr=…`). Sin coordenadas se usa la dirección codificada (o nombre y zona). En el móvil, la fila de acciones rápidas empieza por «He ido», luego Llamar, Nota rápida y Avanzar fase. Los enlaces se construyen en `core/visits.directions`.

**He ido.** Hoja inferior con HTMX (`/clientes/<uuid>/visita/`; sin JavaScript es una página normal): resultado (con sugerencias), con quién hablé, cuándo (ahora, editable), notas, próximo paso, fecha de seguimiento y tipo. `core/visits.record_visit` lo guarda en una transacción: la visita (con quien la registra como visitante), una entrada en el historial interno, y

- si el negocio estaba en «Encontrado» o «Estudiado», pasa a «Visitado» con su `StageChange`; nunca retrocede, y un descartado sigue descartado;
- el próximo paso pasa a «Próxima acción» del negocio y, si tiene fecha, crea un `FollowUp` que aparece en «Próximas acciones» y en la columna «Siguiente paso».

No se ha creado un «estado comercial»: se usan las fases existentes. `Visit` gana `talked_to`, `next_step`, `followup_date` y `followup`.

**Próximas acciones.** Ordenadas por fecha; las vencidas llevan fondo rojo y «Vencida ·», las de hoy «Hoy». Los descartados no aparecen. La columna «Siguiente paso» muestra la acción pendiente más cercana o la «Próxima acción» del negocio, también marcada si está vencida.

**Descartados.** «Descartado» ya existía como salida con motivo obligatorio. Ahora la lista de Clientes los oculta salvo que se filtre por esa fase, y la ficha muestra el motivo y «Reactivar» (`/clientes/<uuid>/reactivar/`), que devuelve el negocio a la fase desde la que se descartó.

Resultado: **738 pruebas superadas** + 5 de navegador (nueva: «He ido» en el móvil; capturas `393-admin-ficha-ir.png` y `393-admin-he-ido.png`).

## 12. Verificación en dos pasos por rol y enlace para clientes

**Política por rol** (`TwoFactorPolicy`, editable en `/admin/`; la migración `0019` crea los valores por defecto): administrador obligatoria (la app guarda enlaces a credenciales de clientes), desarrollador y contador opcional, cliente desactivada. Regla (`core/security.second_step_required`): se pide el código si el rol la exige **o** si la persona la tiene activada. Con «Opcional», cada cual la activa o desactiva en `/seguridad/` (desactivar pide un código actual). Con «Desactivada» no se ofrece a nadie nuevo, pero quien ya la tenía la sigue usando hasta que la quite: bajar la política nunca deja a nadie sin el segundo paso sin que lo decida.

Cambio de comportamiento al desplegar: desarrolladores y contadores sin TOTP configurado dejan de ver la pantalla de alta; los que ya lo tienen siguen igual.

**Recordar este dispositivo** (`TRUSTED_DEVICE_DAYS`, 30 por defecto): casilla en la pantalla del código. Cookie firmada, `HttpOnly`, `SameSite=Lax`, con el usuario y un token aleatorio; en base de datos solo el SHA-256 (`TrustedDevice`). Cuenta como segundo paso solo para su dueño, sin caducar y sin revocar. `/seguridad/` lista los dispositivos (sistema y navegador, alta, último uso, caducidad) y permite olvidar uno o todos; desactivar la verificación los olvida todos.

**Enlace para clientes** (`/entrar/enlace/`): la respuesta es siempre la misma («Si ese email corresponde a un cliente…»), exista el email o no, sea o no cliente, y aunque se supere el límite. Límites contados en base de datos (el caché no se comparte entre procesos): 3 peticiones por email cada 15 minutos y 10 por IP de conexión cada hora (`LoginLinkRequest`). El enlace caduca a los 15 minutos, sirve una vez y usarlo gasta los demás pendientes de esa persona; en base de datos solo su hash (`LoginToken`). Abrirlo (`GET`) solo muestra un botón «Entrar»: los escáneres de correo que abren enlaces no lo gastan. El email sale por la cola del `worker`, para que la respuesta tarde lo mismo exista o no el email, y la bandeja borra la URL al enviarlo; si caduca antes de salir, no se envía. La contraseña sigue funcionando, y si el cliente activó el segundo paso, se le pide después del enlace.

Resultado: **804 pruebas superadas** (incluye la matriz de login rol × política × dispositivo) + 5 de navegador; captura `393-*-seguridad.png`.

## 13. Estimación de facturación (uso interno)

Tarjeta «Estimación de facturación» en la ficha, **solo para administración**: `Estimation.objects.visible_to` devuelve vacío para cualquier otro rol, la tarjeta no se incluye para ellos y la entrada del historial («Nueva estimación de facturación.») no lleva cifras, porque los desarrolladores leen el historial interno.

**Entradas** (prellenadas desde la última estimación o desde Google): reseñas, nota, años; ticket medio (vacío = tabla por nivel de precios de Google); mesas, personas por mesa, servicios (desayuno, comida, cena), rotaciones, días por semana, ocupación observada y concurrencia en la visita; facturación anual declarada con su fuente; si usa apps de reparto.

**Cálculo** (`core/estimation.py`, puro y con pruebas):

- Reseñas: clientes/año = (reseñas / años) / tasa_reseña; ×ticket, ÷12. La tasa central (2 %) da la cifra media; la alta (4 %) la baja y la baja (1 %) la alta.
- Aforo: mesas × personas × rotaciones × servicios × días × ocupación × ticket por semana, ×52/12, ±20 %. Sin ocupación anotada se usa la de la concurrencia observada (baja 30 %, media 55 %, alta 80 %).
- Facturación declarada: manda sobre los dos métodos (±10 %) y la confianza es alta. Con los dos métodos, media de ambos y el rango más amplio; confianza media si uno no llega al doble del otro, baja si no. Con uno solo, baja.
- Banda por facturación mensual media (pequeño < 15.000 €, mediano < 60.000 €, grande), presupuesto sugerido dentro del rango (pequeño = mínimo, mediano = centro redondeado a 100 €, grande = máximo; 1.000–3.000 € por defecto).
- Argumento para el cliente: «La web se paga con N clientes nuevos al mes durante X meses» con ticket × margen bruto (30 %); con reparto, también cuántos pedidos directos al mes la amortizan con la comisión (30 %).

Todas las hipótesis están en `EstimationSettings` (`/admin/`, una fila). Cada estimación guarda sus entradas, el resultado, la explicación línea a línea y una copia de las hipótesis usadas, así que el histórico se puede reproducir aunque cambien los ajustes.

Resultado: **829 pruebas superadas** + prueba de navegador de la tarjeta (`393-admin-estimacion*.png`).

## 14. Conector MCP para Claude

**Servicio.** `mcp` en `compose.yaml`: misma imagen, `uvicorn core.mcp_server:app`, misma base de datos y volumen privado. Transporte HTTP streamable en `https://app.nndrei.dev/mcp` con el SDK oficial (`mcp==2.0.0`, fijado como en Diario). Va aparte porque la web sigue en Gunicorn (WSGI) y el SDK es ASGI; así un fallo de uno no tumba al otro.

**Autenticación, como Diario.** Claude.ai solo conecta por OAuth, así que el servicio es su propio servidor de autorización (OAuth 2.1 con registro dinámico de clientes y PKCE, del SDK). Diferencia con Diario: `/authorize` no pide una contraseña compartida, sino que manda a `/conectar-claude/` en la web. Allí se aplican el login normal, la verificación en dos pasos y los límites de intentos. Solo un administrador puede pulsar «Permitir», y la conexión queda ligada a él. Access token de 1 hora, refresh de 30 días con rotación, revocación RFC 7009. Todo en PostgreSQL (`McpClient`, `McpAuthRequest`, `McpCode`, `McpToken`) y códigos y tokens solo como SHA-256. Cada llamada comprueba que la persona sigue activa y es administradora: si se le quita el rol, pierde el acceso al momento. En **Más → Seguridad → Conexiones con Claude** se ven las conexiones activas y se cortan.

**Herramientas** (lógica en `core/mcp_tools.py`, probada sin el protocolo; descripciones en español en `core/mcp_server.py`): `buscar_clientes(texto?, estado?)`, `get_cliente(id)`, `crear_cliente(nombre, direccion, place_id?)` (idempotente por `place_id`; si ese sitio está en «Buscar clientes», lo enlaza y trae horario y ubicación), `guardar_estudio(cliente_id, campos, fuentes?)`, `guardar_fuentes(cliente_id, urls)`, `subir_demo(cliente_id, nombre_archivo, html)` y `guardar_estimacion(cliente_id, datos)`. Se validan tipos, campos conocidos, longitudes (texto ≤ 5000 o el máximo del campo), URLs http/https (≤ 50 por llamada, ≤ 100 por estudio) y HTML UTF-8 ≤ 5 MB. Cada escritura deja una entrada interna «Claude: …» en el historial (`mcp_*`; la estimación, «Nueva estimación de facturación. (Claude)»).

**Borrador IA.** El estudio gana `ai_draft`, `ai_updated_at`, `ai_sources`, `validated_by` y `validated_at`. Lo que entra por MCP queda como «Borrador IA», con fecha y fuentes. En la ficha, la etiqueta y «Validar estudio» (admin y desarrollador). Una escritura posterior de Claude lo vuelve a dejar como borrador.

**Webs de muestra versionadas.** Cada subida (web o MCP) es una `DemoVersion` en `demos/<pk>/v<n>.html`; la última es la pública en `/demos/<slug>/`. El slug es fijo por negocio: nombre + 6 caracteres aleatorios (`bar-pepe-7k3mzp`), para que no se pueda adivinar. Se sirve sin sesión, con `Content-Security-Policy: sandbox …` (origen opaco: no lee cookies ni nada de la app), `noindex`, `no-store` y `no-referrer`. La dirección antigua `/clientes/<uuid>/demo-cliente/` redirige (301) a la nueva. La demo única de antes pasa a ser la versión 1 en la siguiente subida. En la ficha se ven la versión, las anteriores y el QR (`/clientes/<uuid>/demo/qr.png`, PNG descargable).

**Caddy.** Añadir a `app.nndrei.dev` en `~/stacks/webs/_proxy/Caddyfile`, en lugar del `reverse_proxy` actual:

```caddyfile
	@mcp path /mcp /mcp/* /.well-known/oauth-authorization-server /.well-known/oauth-authorization-server/* /.well-known/oauth-protected-resource /.well-known/oauth-protected-resource/* /authorize /token /register /revoke
	handle @mcp {
		reverse_proxy nndrei-app-mcp:8000
	}
	handle {
		reverse_proxy nndrei-app-web:8000
	}
```

`SITE_URL` debe ser exactamente `https://app.nndrei.dev` (sin barra final): es el `issuer` de OAuth y Claude.ai lo compara literalmente. En Claude: Configuración → Conectores → Añadir conector personalizado → `https://app.nndrei.dev/mcp`.

Comprobado: 19 pruebas (herramientas una a una y el flujo entero de Claude.ai con registro, autorización, consentimiento, token, `initialize`, `tools/list`, `tools/call`, error de validación, rotación del refresh y desconexión) y arranque real con Uvicorn: metadatos OAuth con `issuer` `https://app.nndrei.dev` y `/mcp` sin token → 401 con `resource_metadata`.

Resultado: **876 pruebas superadas** + 6 de navegador; `makemigrations --check` y `manage.py check` sin incidencias.

## 15. Embudo v2: «Propuesta», «Demo lista» y estimación en Seguimiento

**Fases.** 01 Encontrado, 02 Estudiado, 03 Demo lista, 04 Visitado, 05 Propuesta (↻, se repite), 06 Aprobado, 07 En construcción, 08 Revisión final, 09 Web publicada; «Descartado» sigue fuera del embudo. «Propuesta enviada» y «Cambios en la propuesta» se funden en «Propuesta» (clave `proposal`; `review` desaparece). «Demo lista» (`demo_ready`, color melocotón `--peach`) significa estudio y demo hechos, solo falta visitar.

Transiciones: Estudiado → Demo lista o directamente Visitado (visitar no exige demo); Demo lista → Visitado; «He ido» sigue llevando a Visitado desde cualquier fase anterior. Pedir cambios en una propuesta ya no mueve de fase: el negocio sigue en «Propuesta» hasta que se acepta (→ Aprobado). Se puede descartar desde Encontrado, Estudiado, Demo lista, Visitado y Propuesta.

**Migración `0022`.** Quien esté en `review` pasa a `proposal`; quien esté en Estudiado y ya tenga demo (una `DemoVersion` o el archivo único de antes) pasa a «Demo lista». Estos movimientos no se anotan en el historial de fases porque una migración no tiene a nadie que los firme. El historial es inmutable, así que las filas antiguas conservan «Cambios en la propuesta» (`LEGACY_STAGE_LABELS`).

**MCP.** `subir_demo` pasa a «Demo lista» un negocio en Encontrado o Estudiado (con su `StageChange` y entrada en el historial), nunca hacia atrás, y devuelve la fase. `buscar_clientes` lista las fases nuevas y acepta «Propuesta enviada» y «Cambios en la propuesta» como «Propuesta». Subir una demo desde la web no mueve de fase (el encargo lo pedía en la herramienta MCP).

**Seguimiento.** Sin el panel «Próximas acciones»: la tabla ocupa todo el ancho. Columnas: Negocio, Fase, Siguiente paso, Estimación de facturación («11.706 €/mes»), Presupuesto sugerido («1.000 €»), Actualizado; sin estimación, «—» en gris. La última estimación se lee con dos subconsultas en la misma consulta de la lista (el número de consultas no crece con las filas). Importes a la derecha con cifras tabulares; la tabla hace scroll horizontal dentro de su tarjeta. «✓ Hecha» pasa del panel a la celda «Siguiente paso», para que una acción vencida se pueda cerrar. En el móvil, cada tarjeta añade la línea de la estimación.

Resultado: **899 pruebas superadas** + 6 de navegador; `makemigrations --check` y `manage.py check` sin incidencias.

## 16. Ciudades, barrios y zonas activas

**Modelo.** `City` (municipio del INE: código, nombre, provincia, comunidad; centro, caja y contorno de OpenStreetMap la primera vez que se usa), `Neighbourhood` (barrio con contorno GeoJSON o, como mínimo, caja) y `SearchArea` (ciudad entera o ciudad + barrio que se ha buscado; `active` = la recorre «Actualizar»). `Business` y `PlaceSnapshot` guardan ciudad y barrio. Sin PostGIS: los contornos van en columnas JSON y la geometría (punto en polígono, cajas, rejilla, UTM→WGS84) está en `core/geo.py`, pura y con pruebas.

**Fuentes y licencias.**

- Municipios: INE, «Relación de municipios y sus códigos por provincias» (`diccionario26.xlsx`), 8.132 municipios. Reutilización libre citando al INE. No trae coordenadas: se piden a Nominatim la primera vez que se usa una ciudad y se guardan.
- Barrios y distritos de Madrid: Ayuntamiento de Madrid, geoportal (`LIMITES_ADMINISTRATIVOS/Barrios/Barrios.zip`), 131 barrios. Datos abiertos, reutilización libre citando la fuente. Viene en ETRS89 / UTM 30N y se convierte a latitud/longitud al cargarlo.
- Resto de barrios, contornos de municipios y direcciones: OpenStreetMap (Nominatim y Overpass). © OpenStreetMap contributors, **ODbL: requiere atribución**, que se muestra en el selector de barrio y al pie de «Buscar clientes». Se piden bajo demanda, con User-Agent de la app (`GEO_USER_AGENT`, sin datos personales), como mucho una petición por segundo entre todos los procesos (`ExternalCall`) y siempre en caché: una ciudad o sus barrios no se vuelven a pedir.
- Barrios de OSM: se usa el nivel más fino completo (admin_level 10, si no 9, si no áreas `place=suburb/neighbourhood/quarter`). Los que solo son un punto se descartan: un punto no dice a qué lado de la calle empieza el barrio. Sin barrios, se busca en la ciudad entera.

**Datos existentes.** La migración `0023` crea Madrid, asigna ciudad = Madrid a todos los negocios y resultados y renombra `zone` a `legacy_zone` («zona antigua», solo trazabilidad; no aparece en filtros). Ningún otro campo cambia. La `0024` retira la zona fija: su tabla queda como `LegacySearchZone` y desaparecen el radio y la distancia. Lo que necesita red va en `manage.py setup_geography` (se puede repetir): carga municipios y barrios de Madrid, asigna barrio por coordenadas (geocodificando una sola vez la dirección de quien no las tenga) y convierte cada zona antigua activa en zona activa del barrio oficial que contiene su centro, sin forzar los nombres antiguos.

**Búsqueda.** Sin «Radio (m)». El área es el contorno del barrio, o el del municipio. Se divide en celdas de ~`PLACES_CELL_KM` km que tocan el contorno; cada celda va en `locationRestriction.rectangle` y después se descarta lo que cae fuera del contorno real. Cada búsqueda gasta como mucho `PLACES_SEARCH_BUDGET` llamadas. Si se corta, la combinación zona × sector recuerda la celda siguiente (`SearchSlice.next_cell`) y la próxima búsqueda sigue desde ahí. Antes de buscar, el panel de la zona dice entre cuántas llamadas costará y, por encima de `PLACES_CONFIRM_ABOVE`, pide confirmación (también en el servidor).

**Pantalla.** Filtros Ciudad (autocompletado entre los 8.000+ municipios, con la provincia entre paréntesis) | Barrio («Toda la ciudad» primero) | Categoría | Presencia digital | Estado. Se recuerdan la última ciudad y el último barrio (sesión). Los contadores siguen al área elegida. Panel de la zona con «Buscar en esta zona» y «Añadir a zonas activas»; lista «Zonas activas» con Activar/Desactivar (desactivar no borra nada).

**MCP.** `buscar_clientes(texto?, estado?, ciudad?, barrio?)`: ciudad y barrio por nombre sin mayúsculas ni tildes; `texto` busca en nombre, dirección y zona antigua. `crear_cliente` asigna ciudad y barrio (del resultado de búsqueda con ese `place_id`, o de la dirección). `buscar_clientes` y `get_cliente` devuelven ciudad, provincia y barrio.

**Prueba manual fuera de Madrid** (base temporal, datos reales, ninguna llamada a Google): INE cargado en 1,7 s y segunda carga sin cambios; Valladolid: contorno MultiPolygon y 39 barrios de OSM en 2,3 s, segunda apertura desde caché; toda la ciudad son 152 celdas (una categoría: tope de 60 llamadas por búsqueda, con continuación); un barrio, 1 celda (1 a 3 llamadas). Lagueruela (Teruel, municipio pequeño): contorno del municipio, 0 barrios: se busca en el municipio entero (23 celdas).

Resultado: **971 pruebas superadas** + 7 de navegador (nueva: ciudad y barrio en el móvil; capturas `393-admin-buscar-filtros.png` y `393-admin-buscar-zona.png`).

## 17. Códigos de tarjeta para las demos

**Flujo.** En la tarjeta va escrito a mano un código de 5 caracteres. `nndrei.dev/qr` tiene un campo que solo limpia lo escrito (quita espacios, pasa a mayúsculas) y navega a `app.nndrei.dev/c/<código>`; `nndrei.dev/qr?c=XXXXX` redirige allí directamente (nginx si es alfanumérico; si no, el script de la página). La web estática no consulta nada: solo el CRM sabe qué códigos existen.

**Códigos** (`core/demo_codes.py`). Alfabeto sin 0, O, 1, I ni L (31 caracteres, ~28,6 millones de combinaciones). `Business.demo_code`, único, en mayúsculas; se genera al subir la primera demo (web o MCP) y se puede regenerar desde la ficha (solo admin; el anterior deja de funcionar). La migración `0027` da código a los negocios que ya tenían demo, también a las de antes de las versiones. Se compara ignorando mayúsculas y espacios.

**Canje** (`/c/<código>`). Código correcto, negocio no descartado y con demo → `DemoOpen(via='code')` y 302 a `/demos/<slug>/` (las URLs de demo no cambian). Cualquier otro caso (formato malo, inexistente, descartado, sin demo) → la misma página neutra, 404, byte a byte idéntica. Límite: 10 intentos por IP y hora, correctos o no; después, 429 con un mensaje genérico de espera. Cada intento queda en `CodeAttempt`: fecha, código normalizado, válido/no, si lo cortó el límite, HMAC-SHA256 de la IP con `SECRET_KEY` y user agent. La vista `/codigos/` (admin, enlace «Códigos» en el menú) enseña los fallidos recientes.

**IP real.** Detrás de Caddy, `REMOTE_ADDR` es el proxy. Con `TRUST_X_FORWARDED_FOR=True` se usa la última entrada de `X-Forwarded-For`, que Caddy sobrescribe con la IP del cliente. Actívalo solo si el puerto de la app no es accesible más que a través de Caddy (así es en `compose.yaml`).

**Aperturas.** `/demos/<slug>/` registra `DemoOpen(via='direct')` salvo que la visita venga de `/c/` (cookie de 2 minutos limitada a esa ruta), sea un HEAD o la haga alguien del equipo con sesión. Cada apertura mueve «Última actividad» en Clientes (`GREATEST` del historial y de las aperturas) y deja una línea interna en el historial, como mucho una por hora. La ficha muestra «Demo abierta N veces · última hace X» separando código y URL directa.

**Descartados y buscadores.** Un negocio descartado responde 404 en `/demos/<slug>/` y en la dirección antigua, igual que una demo inexistente. Las demos llevan `X-Robots-Tag: noindex, nofollow` y un `<meta name="robots">` que se inserta al servirlas (el archivo guardado no cambia). Toda la app lleva la misma cabecera (`NoIndexMiddleware`). `robots.txt` no enumera rutas ni sitemap y deja rastrear: así un buscador que encuentre un enlace filtrado lee el noindex en vez de indexar la URL a ciegas. `/demos/` sigue en 404.
