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
