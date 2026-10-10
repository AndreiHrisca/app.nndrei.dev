# Mapa de Seguimiento

Abre `/?vista=mapa`, o pulsa **Ver mapa** junto a **+ Nuevo negocio**. La fase se guarda en `fase`, por ejemplo `/?vista=mapa&fase=proposal`. Tocar otra vez la fase seleccionada quita el filtro. El mapa y su endpoint `/seguimiento/mapa/datos/` tienen el mismo acceso que Seguimiento: administrador con su verificación de sesión. Los descartados se excluyen siempre. El endpoint devuelve los negocios y los que aún no tienen ubicación, sin datos privados de contacto o notas, en una consulta con sector y barrio incluidos.

Se reutilizan los campos existentes `Business.latitude`, `longitude`, `geocoded_at`, `google_place_id`, `address`, `city` y `neighbourhood`; no hay cambios de esquema ni migración. Las coordenadas se exponen como `latitud` y `longitud` en MCP y en el JSON. `PlaceSnapshot` ya guarda coordenadas y barrio, que se copian al captar el negocio.

El mapa usa Leaflet 1.9.4 y markercluster 1.5.3 desde CDN, con teselas de OpenStreetMap. CARTO exige una clave desde septiembre de 2026, por eso se usa la alternativa OSM prevista. La ubicación del navegador solo se solicita al pulsar **Mi ubicación** y se guarda en memoria de la página; no se envía al servidor ni se añade a la URL. El punto azul indica la posición y el círculo su precisión. Los barrios se ordenan por su negocio más cercano; dentro de cada barrio, los negocios se ordenan por distancia.

El panel agrupa los negocios de la fase seleccionada que tienen coordenadas. Permite marcar hasta nueve, en el orden de selección. La ruta se abre a pie en Google Maps; no calcula un orden óptimo. Los navegadores móviles admiten tres paradas intermedias: con más de cuatro negocios se ofrecen tramos sucesivos, cuyo inicio es la última parada del tramo anterior.

En móvil las fases se desplazan horizontalmente para dejar espacio al mapa. El panel se abre tocando o deslizando su tirador. Las pruebas WebKit usan la configuración de iPhone y `navigator.standalone`, con geolocalización simulada; una PWA instalada en un iPhone real no se ha comprobado físicamente.

## Ubicar clientes

```sh
python manage.py geocodificar_clientes
python manage.py geocodificar_clientes --forzar
```

Sin `--forzar`, solo procesa clientes con alguna coordenada vacía. Recupera primero las coordenadas de Buscar clientes. Con ficha de Google pide exclusivamente `location` y reserva una llamada en `PlacesUsage`, con bloqueo de fila y el límite mensual existente. Al alcanzar el límite detiene el proceso. Sin ficha de Google busca la dirección en Nominatim, con el User-Agent de `GEO_USER_AGENT` y el límite compartido entre procesos de una llamada cada 1,1 segundos. Los errores y direcciones no encontradas se imprimen y quedan pendientes para la siguiente ejecución.

La creación desde interfaz o MCP y los cambios de dirección usan el trabajador Django Q. Cambiar una dirección vacía sus coordenadas, su barrio y su antiguo identificador de Google, para evitar copiar la ubicación antigua. El trabajo comprueba que la dirección y el identificador sigan siendo los mismos antes de guardar el resultado. Abrir el mapa no geocodifica. La caducidad de coordenadas de Google se controla con su fecha de geocodificación y no renueva horarios o reseñas cuando solo se pide ubicación.

Pruebas: `tests/test_map.py`, la matriz de permisos y `tests/e2e/test_map_screens.py`. Capturas de 1280 y 390 píxeles con todas las fases y con Propuesta en `docs/screenshots/mapa/`.

Ejecución sobre datos actuales (10/10/2026): 55 clientes, 54 en curso; cinco sin ubicación inicialmente. Se ubicaron cuatro con cuatro llamadas a Google, pasando de 77 a 81 de 800 llamadas mensuales. Quedó El Rincón del Quijote sin ubicación: Nominatim no encontró la dirección guardada `Calle pan y toros nº37  Madrid  28041 ( el Rincón del Quijote )`, y no tiene identificador de Google. No se modificó esa dirección ni se le asignó una ubicación aproximada.

Desplegado en app.nndrei.dev y comprobado con los 54 clientes en curso: 53 marcadores y un negocio sin ubicación. Capturas reales `actual-1280-todas.png`, `actual-1280-estudiado.png`, `actual-390-todas.png` y `actual-390-estudiado.png`; la fase Estudiado tiene 27 clientes. La sesión temporal utilizada para las capturas quedó cerrada.

Estilo actualizado: fondo oscuro monocromo inspirado en la referencia de Pinterest, con controles oscuros, grupos de marcadores en blanco y colores de fase conservados. Se aplican filtros solo a las teselas de OpenStreetMap, sin nuevas claves, proveedores ni llamadas de geocodificación.
