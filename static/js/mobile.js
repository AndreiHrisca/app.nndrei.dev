// Phone behaviour: bottom sheets, the active-filter counter, folded sections and "Volver".
(() => {
  const phone = matchMedia('(max-width: 767px)');

  // Sections that start folded on a phone stay open on larger screens.
  if (phone.matches) document.querySelectorAll('details[data-m-closed]').forEach(el => el.removeAttribute('open'));

  // A sheet's filled fields, shown on its trigger as "Filtros · 2".
  const countFilters = () => document.querySelectorAll('[data-filter-count]').forEach(badge => {
    const sheet = document.getElementById(badge.dataset.filterCount);
    const filled = sheet ? [...sheet.querySelectorAll('[name]')].filter(field => field.value.trim() !== (field.dataset.default || '')).length : 0;
    badge.textContent = filled ? ` · ${filled}` : '';
  });
  countFilters();

  document.addEventListener('click', event => {
    const opener = event.target.closest('[data-sheet-open]');
    if (opener) {
      document.getElementById(opener.dataset.sheetOpen)?.showModal();
      return;
    }
    if (event.target.closest('[data-sheet-close]')) {
      event.target.closest('dialog')?.close();
      return;
    }
    // Tapping the dimmed backdrop closes the sheet.
    if (event.target instanceof HTMLDialogElement) {
      const box = event.target.getBoundingClientRect();
      if (event.clientY < box.top || event.clientY > box.bottom || event.clientX < box.left || event.clientX > box.right) event.target.close();
      return;
    }
    const clear = event.target.closest('[data-sheet-clear]');
    if (clear) {
      const sheet = clear.closest('dialog');
      sheet.querySelectorAll('[name]').forEach(field => { field.value = field.dataset.default || ''; });
      sheet.close();
      clear.form.requestSubmit();
      countFilters();
      return;
    }
    const back = event.target.closest('[data-back]');
    if (back && document.referrer.startsWith(location.origin) && history.length > 1) {
      event.preventDefault();
      history.back();
    }
    // A menu closes when you tap anywhere else.
    document.querySelectorAll('details.m-menu[open]').forEach(menu => { if (!menu.contains(event.target)) menu.open = false; });
  });

  // "Aplicar" submits the form around the sheet; the sheet gets out of the way.
  document.addEventListener('submit', event => {
    event.target.querySelectorAll('dialog[open]').forEach(sheet => sheet.close());
    countFilters();
  });
  document.addEventListener('htmx:afterSettle', countFilters);
})();

// On larger screens a fold is just a card: it cannot be closed.
document.addEventListener('toggle', event => {
  if (!matchMedia('(max-width: 767px)').matches && event.target.matches?.('details.fold') && !event.target.open) event.target.open = true;
}, true);

// Any screen: "Copiar enlace" buttons and forms that ask before they submit.
document.addEventListener('click', async event => {
  const button = event.target.closest('[data-copy]');
  if (!button) return;
  try {
    await navigator.clipboard.writeText(button.dataset.copy);
    const label = button.textContent;
    button.textContent = 'Copiado';
    setTimeout(() => { button.textContent = label; }, 2000);
  } catch {
    // No clipboard outside HTTPS: leave the link ready to copy by hand.
    prompt('Copia el enlace:', button.dataset.copy);
  }
});
document.addEventListener('submit', event => {
  const message = event.target.dataset?.confirm;
  if (message && !confirm(message)) event.preventDefault();
}, true);

// "Cerca de mí": the browser's position goes with the filter request only.
// The server strips it from the pushed URL, so it never lands in the history;
// without permission the list simply stays as it was, without distances.
(() => {
  const note = (form, text) => {
    const box = form.querySelector('[data-near-note]');
    if (box) { box.textContent = text; box.hidden = !text; }
  };
  const locate = (form, button) => {
    if (!navigator.geolocation) { note(form, 'Este navegador no puede darte la ubicación. La lista sigue igual.'); return; }
    const label = button?.innerHTML;
    if (button) { button.disabled = true; button.textContent = 'Buscando…'; }
    const done = () => { if (button) { button.disabled = false; button.innerHTML = label; } };
    navigator.geolocation.getCurrentPosition(position => {
      done();
      note(form, '');
      form.elements.lat.value = position.coords.latitude.toFixed(5);
      form.elements.lng.value = position.coords.longitude.toFixed(5);
      form.elements.sort.value = 'cerca';
      document.querySelectorAll('[data-near].chip').forEach(chip => chip.classList.add('is-on'));
      form.requestSubmit();
    }, () => {
      done();
      note(form, 'Sin permiso de ubicación: la lista sigue igual, sin distancias.');
    }, {enableHighAccuracy: true, timeout: 10000, maximumAge: 60000});
  };
  document.addEventListener('click', event => {
    const button = event.target.closest('[data-near]');
    if (button) locate(button.form, button);
  });
  // Choosing "Más cerca de mí" in the sheet asks for the position first.
  document.addEventListener('submit', event => {
    const form = event.target;
    if (form.elements?.sort?.value === 'cerca' && form.elements.lat && !form.elements.lat.value) {
      event.preventDefault();
      event.stopImmediatePropagation();
      form.querySelectorAll('dialog[open]').forEach(sheet => sheet.close());
      locate(form);
    }
  }, true);
  // Reopening a list sorted by distance: ask again only if already allowed.
  const form = document.querySelector('form [data-near]')?.form;
  if (form && new URLSearchParams(location.search).get('sort') === 'cerca' && navigator.permissions) {
    navigator.permissions.query({name: 'geolocation'}).then(state => { if (state.state === 'granted') locate(form); }).catch(() => {});
  }
})();

// Hours editor: "Cerrado" hides the slots, "+ Añadir tramo" shows the next one,
// and Monday can be copied to the rest of the working week.
document.addEventListener('change', event => {
  const toggle = event.target.closest('[data-closed-toggle]');
  if (toggle) toggle.closest('[data-hours-day]').classList.toggle('is-closed', toggle.checked);
});
document.addEventListener('click', event => {
  const add = event.target.closest('[data-add-slot]');
  if (add) {
    const next = add.closest('[data-hours-day]').querySelector('[data-slot][hidden]');
    if (next) { next.hidden = false; next.querySelector('input').focus(); }
    if (!add.closest('[data-hours-day]').querySelector('[data-slot][hidden]')) add.hidden = true;
    return;
  }
  const copy = event.target.closest('[data-copy-monday]');
  if (!copy) return;
  const editor = copy.closest('[data-hours-editor]');
  const monday = editor.querySelector('[data-hours-day="1"]');
  const values = [...monday.querySelectorAll('input[type=time]')].map(input => input.value);
  const closed = monday.querySelector('[data-closed-toggle]').checked;
  for (const day of [2, 3, 4, 5]) {
    const row = editor.querySelector(`[data-hours-day="${day}"]`);
    row.querySelectorAll('input[type=time]').forEach((input, index) => { input.value = values[index]; });
    row.querySelectorAll('[data-slot]').forEach((slot, index) => { slot.hidden = index > 0 && !values[index * 2] && !values[index * 2 + 1]; });
    row.querySelector('[data-add-slot]').hidden = !row.querySelector('[data-slot][hidden]');
    const toggle = row.querySelector('[data-closed-toggle]');
    toggle.checked = closed;
    row.classList.toggle('is-closed', closed);
  }
});

// "Buscar clientes": city autocomplete (8,000+ municipalities) and its neighbourhoods.
(() => {
  const loadBarrios = (picker, cityId) => {
    const slot = picker.nextElementSibling;
    if (!slot?.matches('[data-barrio-slot]')) return;
    const prefix = slot.dataset.prefix || '';
    if (!cityId) {
      slot.innerHTML = `<label for="${prefix}barrio">Barrio</label><select id="${prefix}barrio" name="barrio" disabled><option value="">Elige primero una ciudad</option></select>`;
      return;
    }
    slot.innerHTML = '<label>Barrio</label><select disabled><option>Cargando barrios…</option></select>';
    htmx.ajax('GET', `/buscar/barrios/?city=${encodeURIComponent(cityId)}&prefix=${encodeURIComponent(prefix)}`, {target: slot, swap: 'innerHTML'});
  };
  document.addEventListener('click', event => {
    const option = event.target.closest('[data-city-option]');
    if (!option) return;
    const picker = option.closest('[data-city-picker]');
    picker.querySelector('[data-city-id]').value = option.dataset.cityOption;
    const input = picker.querySelector('[data-city-input]');
    input.value = option.dataset.cityLabel;
    input.setAttribute('aria-expanded', 'false');
    picker.querySelector('.city-options').innerHTML = '';
    loadBarrios(picker, option.dataset.cityOption);
  });
  document.addEventListener('input', event => {
    const input = event.target.closest('[data-city-input]');
    if (!input) return;
    input.setAttribute('aria-expanded', input.value.trim().length >= 2 ? 'true' : 'false');
    if (!input.value.trim()) {
      const picker = input.closest('[data-city-picker]');
      picker.querySelector('[data-city-id]').value = '';
      picker.querySelector('.city-options').innerHTML = '';
      loadBarrios(picker, '');
    }
  });
})();
