/* Leaflet is loaded once; each HTMX replacement owns and releases its map. */
(() => {
  let active = null;
  let position = null;
  const el = (tag, text, cls) => { const n = document.createElement(tag); if (text) n.textContent = text; if (cls) n.className = cls; return n; };
  const link = (text, url) => { const a = el('a', text, 'button secondary small'); a.href = url; a.target = '_blank'; a.rel = 'noopener noreferrer'; return a; };
  const point = b => `${b.latitud},${b.longitud}`;
  const googleRoute = (stops, origin) => {
    const params = new URLSearchParams({api: '1', travelmode: 'walking', destination: point(stops[stops.length - 1])});
    if (origin) params.set('origin', origin);
    if (stops.length > 1) params.set('waypoints', stops.slice(0, -1).map(point).join('|'));
    return 'https://www.google.com/maps/dir/?' + params;
  };
  function init() {
    const root = document.querySelector('.visit-map');
    if (active && active.root === root) return;
    if (active) { active.controller.abort(); active.observer.disconnect(); active.map.stop(); active.map.remove(); active = null; }
    document.body.classList.toggle('has-visit-map', !!root);
    if (!root) return;
    const message = root.querySelector('[data-map-message]');
    if (!window.L || !L.markerClusterGroup) { message.textContent = 'No se ha podido cargar el mapa. Recarga para volver a intentarlo.'; return; }
    const stages = document.querySelector('#pipeline-stages .stages');
    const chosen = stages?.querySelector('.is-active');
    if (chosen && matchMedia('(max-width: 767px)').matches) {
      stages.scrollLeft += chosen.getBoundingClientRect().left - stages.getBoundingClientRect().left - (stages.clientWidth - chosen.offsetWidth) / 2;
    }
    const canvas = root.querySelector('.map-canvas');
    const map = L.map(canvas, {zoomControl: false, zoomAnimation: false, fadeAnimation: false, markerZoomAnimation: false}).setView([40.4168, -3.7038], 12);
    L.control.zoom({position: 'topleft'}).addTo(map);
    L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>', maxZoom: 19
    }).addTo(map);
    const controller = new AbortController();
    const layout = root.querySelector('.map-layout');
    const resize = () => {
      if (!root.isConnected) return;
      const top = layout.getBoundingClientRect().top + window.scrollY;
      root.style.setProperty('--map-top', `${top}px`);
      map.invalidateSize({pan: false});
    };
    const observer = new ResizeObserver(() => requestAnimationFrame(() => { if (root.isConnected && active?.map === map) resize(); }));
    observer.observe(root);
    resize();
    observer.observe(canvas);
    active = {root, map, observer, controller};
    const clusters = L.markerClusterGroup({animate: false, showCoverageOnHover: false, maxClusterRadius: 42,
      iconCreateFunction: cluster => L.divIcon({html: `<span>${cluster.getChildCount()}</span>`, className: 'visit-cluster', iconSize: [44, 44]})});
    map.addLayer(clusters);
    const markers = new Map(), selected = new Set();
    let businesses = [], userMarker, accuracyCircle;
    const panel = root.querySelector('.zone-panel');
    const handle = root.querySelector('.zone-handle');
    function expand(open) { panel.classList.toggle('expanded', open); handle.setAttribute('aria-expanded', String(open)); }
    handle.addEventListener('click', () => expand(!panel.classList.contains('expanded')));
    let startY;
    handle.addEventListener('touchstart', e => { startY = e.touches[0].clientY; }, {passive: true});
    handle.addEventListener('touchend', e => { const delta = e.changedTouches[0].clientY - startY; if (Math.abs(delta) > 20) expand(delta < 0); }, {passive: true});
    const route = root.querySelector('[data-route]');
    const routeMessage = root.querySelector('[data-route-message]');
    function selectionChanged() {
      route.disabled = !selected.size;
      root.querySelector('[data-selected-count]').textContent = selected.size ? `(${selected.size})` : '';
      root.querySelector('[data-route-links]').replaceChildren();
      routeMessage.textContent = '';
    }
    function focus(b) {
      const marker = markers.get(b.id);
      if (matchMedia('(max-width: 767px)').matches) expand(false);
      clusters.zoomToShowLayer(marker, () => { if (!root.isConnected || active?.map !== map) return; map.panTo(marker.getLatLng(), {animate: false}); marker.openPopup(); });
    }
    function fit(items) {
      if (items.length) map.fitBounds(L.latLngBounds(items.map(b => [b.latitud, b.longitud])), {padding: [48, 48], maxZoom: 16, animate: false});
    }
    function distance(b) { return position ? map.distance(position, [b.latitud, b.longitud]) : Infinity; }
    function renderZones() {
      const zones = root.querySelector('[data-zones]'); zones.replaceChildren();
      const groups = new Map();
      for (const b of businesses) { const name = b.barrio || 'Sin barrio indicado'; if (!groups.has(name)) groups.set(name, []); groups.get(name).push(b); }
      const ordered = [...groups].sort((a, b) => position ? Math.min(...a[1].map(distance)) - Math.min(...b[1].map(distance)) : a[0].localeCompare(b[0], 'es'));
      for (const [name, items] of ordered) {
        items.sort((a, b) => position ? distance(a) - distance(b) : a.nombre.localeCompare(b.nombre, 'es'));
        const section = el('section', '', 'zone-group');
        const heading = el('button', `${name} · ${items.length}`, 'zone-title'); heading.type = 'button'; heading.addEventListener('click', () => { expand(false); fit(items); }); section.append(heading);
        for (const b of items) {
          const row = el('div', '', 'zone-business'); const checkbox = el('input'); checkbox.type = 'checkbox'; checkbox.checked = selected.has(b.id); checkbox.setAttribute('aria-label', `Incluir ${b.nombre} en la ruta`);
          checkbox.addEventListener('change', () => {
            if (checkbox.checked && selected.size >= 9) { checkbox.checked = false; routeMessage.textContent = 'Puedes incluir hasta 9 negocios en una ruta.'; return; }
            if (checkbox.checked) selected.add(b.id); else selected.delete(b.id); selectionChanged();
          });
          const button = el('button', '', 'zone-business-name'); button.type = 'button'; button.append(el('b', b.nombre));
          const suffix = position ? ` · a ${distance(b) < 1000 ? Math.round(distance(b) / 10) * 10 + ' m' : (distance(b) / 1000).toFixed(1).replace('.', ',') + ' km'}` : '';
          button.append(el('small', `${b.sector || 'Sin sector'}${suffix}`)); button.addEventListener('click', () => focus(b));
          const dot = el('span', '', 'zone-dot'); dot.style.background = b.color; row.append(checkbox, dot, button); section.append(row);
        }
        zones.append(section);
      }
      if (!businesses.length) zones.append(el('p', 'No hay negocios con ubicación en esta fase.', 'empty'));
    }
    function showPosition(latlng, accuracy) {
      position = latlng;
      if (userMarker) map.removeLayer(userMarker);
      if (accuracyCircle) map.removeLayer(accuracyCircle);
      userMarker = L.circleMarker(latlng, {radius: 8, fillColor: '#2878e7', color: '#fff', weight: 3, fillOpacity: 1}).addTo(map).bindPopup('Estás aquí');
      if (accuracy) accuracyCircle = L.circle(latlng, {radius: accuracy, color: '#2878e7', weight: 1, fillOpacity: .08}).addTo(map);
      renderZones();
    }
    root.querySelector('[data-locate]').addEventListener('click', () => {
      if (!navigator.geolocation) { message.textContent = 'Este navegador no permite conocer tu ubicación.'; return; }
      message.textContent = 'Buscando tu ubicación…';
      navigator.geolocation.getCurrentPosition(result => {
        if (!root.isConnected) return;
        showPosition([result.coords.latitude, result.coords.longitude], result.coords.accuracy); map.setView(position, 16, {animate: false}); message.textContent = 'Negocios ordenados por cercanía.';
      }, error => {
        if (root.isConnected) message.textContent = error.code === 1 ? 'Permite el acceso a tu ubicación en los ajustes del navegador o del iPhone.' : 'No se ha podido encontrar tu ubicación. Vuelve a intentarlo.';
      }, {enableHighAccuracy: true, timeout: 15000, maximumAge: 60000});
    });
    route.addEventListener('click', () => {
      const stops = [...selected].map(id => businesses.find(b => b.id === id));
      if (!stops.length) return;
      if (stops.length > 9) { routeMessage.textContent = 'Puedes incluir hasta 9 negocios en una ruta.'; return; }
      const origin = position ? position.join(',') : null;
      // Mobile browsers support three intermediate waypoints. Offer complete segments.
      if (matchMedia('(max-width: 767px)').matches && stops.length > 4) {
        const links = root.querySelector('[data-route-links]'); links.replaceChildren();
        routeMessage.textContent = 'Abre cada tramo en orden para pasar por todos los negocios.';
        for (let i = 0; i < stops.length; i += 4) links.append(link(`Tramo ${i / 4 + 1}`, googleRoute(stops.slice(i, i + 4), i ? point(stops[i - 1]) : origin)));
      } else window.open(googleRoute(stops, origin), '_blank', 'noopener,noreferrer');
    });
    fetch(root.dataset.mapUrl, {credentials: 'same-origin', signal: controller.signal, headers: {Accept: 'application/json'}})
      .then(response => { if (!response.ok) throw new Error(); return response.json(); })
      .then(data => {
        if (!root.isConnected) return;
        const missing = data.negocios.filter(b => b.latitud === null || b.longitud === null);
        businesses = data.negocios.filter(b => b.latitud !== null && b.longitud !== null);
        const warning = root.querySelector('.map-missing');
        if (missing.length) { warning.hidden = false; warning.querySelector('summary').textContent = `${missing.length} ${missing.length === 1 ? 'negocio sin ubicación' : 'negocios sin ubicación'}`;
          for (const b of missing) { const li = el('li'); const a = el('a', b.nombre); a.href = b.url; li.append(a); warning.querySelector('ul').append(li); }
        }
        const legend = root.querySelector('.map-legend'); const seen = new Set();
        for (const b of businesses) {
          const iconNode = el('span', '', 'visit-pin'); iconNode.style.background = b.color;
          const marker = L.marker([b.latitud, b.longitud], {title: b.nombre, icon: L.divIcon({html: iconNode.outerHTML, className: 'visit-marker', iconSize: [32, 40], iconAnchor: [16, 38], popupAnchor: [0, -35]})});
          const popup = el('div', '', 'visit-popup'); popup.append(el('h3', b.nombre));
          const badge = el('span', b.fase_nombre, `chip ${b.fase}`); popup.append(badge);
          popup.append(el('p', [b.sector, b.barrio].filter(Boolean).join(' · ')), el('p', b.direccion || 'Dirección sin indicar'));
          popup.append(el('p', b.abierto === null ? 'Horario sin indicar' : b.abierto ? 'Abierto ahora' : 'Cerrado', 'visit-hours'));
          const actions = el('div', '', 'visit-popup-actions'); const detail = link('Ver ficha', b.url); detail.removeAttribute('target'); actions.append(detail, link('Cómo llegar', googleRoute([b], null)), link('Waze', 'https://waze.com/ul?' + new URLSearchParams({ll: point(b), navigate: 'yes'}))); popup.append(actions);
          marker.bindPopup(popup, {maxWidth: 280}); clusters.addLayer(marker); markers.set(b.id, marker);
          if (!seen.has(b.fase)) { seen.add(b.fase); const label = el('span', b.fase_nombre); const dot = el('i'); dot.style.background = b.color; label.prepend(dot); legend.append(label); }
        }
        renderZones(); fit(businesses);
        if (position) showPosition(position);
        message.textContent = businesses.length ? `${businesses.length} ${businesses.length === 1 ? 'negocio en el mapa' : 'negocios en el mapa'}` : 'No hay negocios con ubicación en esta fase.';
      }).catch(error => { if (error.name !== 'AbortError' && root.isConnected) message.textContent = 'No se han podido cargar los negocios. Recarga para volver a intentarlo.'; });
  }
  document.addEventListener('DOMContentLoaded', init);
  document.addEventListener('htmx:afterSettle', init);
  document.addEventListener('htmx:beforeCleanupElement', event => { if (active && event.detail.elt.contains(active.root)) { active.controller.abort(); active.observer.disconnect(); active.map.stop(); active.map.remove(); active = null; } });
  document.addEventListener('htmx:historyRestore', init);
})();
