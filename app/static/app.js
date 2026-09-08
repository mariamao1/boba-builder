/* Entry page behaviour: choose a collection path, create a group room, or
   import a sheet. Both paths continue through the same preview and cart flow. */

const $ = (id) => document.getElementById(id);
const statusBox = $('status');   // not `status`: window.status is a real global
const groupStatusBox = $('group-status');
let pendingFile = null;
let availableStores = [];
let matchingStores = [];
let activeStoreIndex = -1;

/* --- collection path ---------------------------------------------------- */

function selectPath(which) {
  const isGroup = which === 'group';
  $('group-path').hidden = !isGroup;
  $('sheet-path').hidden = isGroup;
  $('choose-group').classList.toggle('on', isGroup);
  $('choose-sheet').classList.toggle('on', !isGroup);
  $('choose-group').setAttribute('aria-expanded', String(isGroup));
  $('choose-sheet').setAttribute('aria-expanded', String(!isGroup));
  if (window.history && window.history.replaceState) {
    window.history.replaceState(null, '', `/?method=${which}`);
  }
}

$('choose-group').addEventListener('click', () => selectPath('group'));
$('choose-sheet').addEventListener('click', () => selectPath('sheet'));

/* --- tabs ---------------------------------------------------------------- */

function showTab(which) {
  const isFile = which === 'file';
  $('tab-file').classList.toggle('on', isFile);
  $('tab-link').classList.toggle('on', !isFile);
  $('tab-file').setAttribute('aria-selected', String(isFile));
  $('tab-link').setAttribute('aria-selected', String(!isFile));
  $('panel-file').hidden = !isFile;
  $('panel-link').hidden = isFile;
}
$('tab-file').addEventListener('click', () => showTab('file'));
$('tab-link').addEventListener('click', () => showTab('link'));

/* --- status -------------------------------------------------------------- */

function writeStatus(target, kind, title, detail) {
  target.className = 'status ' + kind;
  target.textContent = '';
  if (title) {
    const strong = document.createElement('strong');
    strong.textContent = title;
    target.append(strong);
  }
  if (detail) target.append(document.createTextNode(detail));
}
function say(kind, title, detail) { writeStatus(statusBox, kind, title, detail); }
function clearStatus() { statusBox.className = 'status'; statusBox.textContent = ''; }

/* --- store search ------------------------------------------------------- */

function storeText(store) {
  return [store.name, store.address, store.city, store.state, store.zip]
    .filter(Boolean).join(' ').toLocaleLowerCase();
}

function storeLabel(store) {
  const place = [store.city, store.state].filter(Boolean).join(', ');
  const zip = store.zip ? ` ${store.zip}` : '';
  return place ? `${store.name} — ${place}${zip}` : store.name;
}

function closeStoreResults() {
  $('store-results').hidden = true;
  $('store-search').setAttribute('aria-expanded', 'false');
  $('store-search').removeAttribute('aria-activedescendant');
  activeStoreIndex = -1;
}

function setActiveStore(index) {
  const options = [...$('store-results').querySelectorAll('.store-result')];
  if (!options.length) return;
  activeStoreIndex = (index + options.length) % options.length;
  options.forEach((option, optionIndex) => {
    const active = optionIndex === activeStoreIndex;
    option.classList.toggle('active', active);
    option.setAttribute('aria-selected', String(active));
  });
  const active = options[activeStoreIndex];
  $('store-search').setAttribute('aria-activedescendant', active.id);
  active.scrollIntoView({ block: 'nearest' });
}

function chooseStore(store) {
  $('group-store').value = store.restaurant_id;
  $('store-search').value = storeLabel(store);
  $('group-submit').disabled = false;
  closeStoreResults();
  writeStatus(groupStatusBox, '', '', '');
}

function renderStoreResults() {
  const results = $('store-results');
  const query = $('store-search').value.toLocaleLowerCase().trim();
  results.textContent = '';
  activeStoreIndex = -1;
  if (!query) {
    matchingStores = [];
    closeStoreResults();
    return;
  }

  const terms = query.split(/\s+/).filter(Boolean);
  matchingStores = availableStores
    .filter((store) => terms.every((term) => storeText(store).includes(term)))
    .sort((left, right) => {
      const score = (store) => {
        const name = String(store.name || '').toLocaleLowerCase();
        const city = String(store.city || '').toLocaleLowerCase();
        const zip = String(store.zip || '').toLocaleLowerCase();
        if (zip === query) return 0;
        if (name.startsWith(query)) return 1;
        if (city.startsWith(query)) return 2;
        return 3;
      };
      return score(left) - score(right) || storeLabel(left).localeCompare(storeLabel(right));
    })
    .slice(0, 10);

  if (!matchingStores.length) {
    const empty = document.createElement('p');
    empty.className = 'store-no-results';
    empty.textContent = 'No stores match that search.';
    results.append(empty);
  } else {
    matchingStores.forEach((store, index) => {
      const option = document.createElement('button');
      option.type = 'button';
      option.id = `store-result-${index}`;
      option.className = 'store-result';
      option.setAttribute('role', 'option');
      option.setAttribute('aria-selected', 'false');
      const name = document.createElement('strong');
      name.textContent = store.name;
      const detail = document.createElement('span');
      detail.textContent = [store.address, store.city, store.state, store.zip]
        .filter(Boolean).join(' · ');
      option.append(name, detail);
      option.addEventListener('click', () => chooseStore(store));
      results.append(option);
    });
  }
  results.hidden = false;
  $('store-search').setAttribute('aria-expanded', 'true');
}

$('store-search').addEventListener('input', () => {
  $('group-store').value = '';
  $('group-submit').disabled = true;
  renderStoreResults();
});
$('store-search').addEventListener('focus', renderStoreResults);
$('store-search').addEventListener('blur', () => {
  window.setTimeout(closeStoreResults, 150);
});
$('store-search').addEventListener('keydown', (event) => {
  if (event.key === 'Escape') {
    closeStoreResults();
    return;
  }
  if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    event.preventDefault();
    if ($('store-results').hidden) renderStoreResults();
    const next = activeStoreIndex < 0
      ? (event.key === 'ArrowDown' ? 0 : matchingStores.length - 1)
      : activeStoreIndex + (event.key === 'ArrowDown' ? 1 : -1);
    setActiveStore(next);
    return;
  }
  if (event.key === 'Enter' && matchingStores.length) {
    event.preventDefault();
    chooseStore(matchingStores[activeStoreIndex >= 0 ? activeStoreIndex : 0]);
  }
});
document.addEventListener('click', (event) => {
  if (!$('store-picker').contains(event.target)) closeStoreResults();
});

/* --- file selection ------------------------------------------------------ */

const input = $('file-input');
const drop = $('drop');
const chosen = $('chosen');

function describe(file) {
  pendingFile = file;
  const kb = file.size < 1024 * 1024
    ? (file.size / 1024).toFixed(0) + ' KB'
    : (file.size / 1024 / 1024).toFixed(1) + ' MB';
  chosen.hidden = false;
  chosen.textContent = '';
  const strong = document.createElement('strong');
  strong.textContent = file.name;
  chosen.append(strong, document.createTextNode(` · ${kb}`));
  $('file-submit').disabled = false;
  clearStatus();
}

input.addEventListener('change', () => { if (input.files[0]) describe(input.files[0]); });

drop.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); input.click(); }
});

['dragenter', 'dragover'].forEach((type) =>
  drop.addEventListener(type, (event) => {
    event.preventDefault();
    drop.classList.add('hot');
  }));
['dragleave', 'drop'].forEach((type) =>
  drop.addEventListener(type, () => drop.classList.remove('hot')));

drop.addEventListener('drop', (event) => {
  event.preventDefault();
  const file = event.dataTransfer.files && event.dataTransfer.files[0];
  if (!file) return;
  try {
    input.files = event.dataTransfer.files;   // so the field shows it too
  } catch (error) { /* older browsers: pendingFile still carries it */ }
  describe(file);
});

/* Paste a Sheets link anywhere on the page and it lands in the right box. */
document.addEventListener('paste', (event) => {
  if (event.target.tagName === 'INPUT') return;
  const text = (event.clipboardData || window.clipboardData).getData('text') || '';
  if (text.includes('docs.google.com/spreadsheets')) {
    selectPath('sheet');
    showTab('link');
    $('sheet-url').value = text.trim();
    $('sheet-url').focus();
    say('', 'Link pasted.', ' Press Import orders when you\'re ready.');
  }
});

/* --- group-order creation ------------------------------------------------ */

$('group-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  const submit = $('group-submit');
  if (!$('group-store').value) {
    writeStatus(groupStatusBox, 'err', 'Choose a store first.',
      ' Search for a location, then select it from the results.');
    $('store-search').focus();
    return;
  }
  const payload = {
    restaurant_id: $('group-store').value,
    organizer_name: $('organizer-name').value.trim(),
    title: $('group-title').value.trim(),
    expires_in_hours: Number($('group-duration').value),
  };
  writeStatus(groupStatusBox, 'busy', 'Creating your group link…', '');
  submit.disabled = true;
  try {
    const response = await fetch('/api/group-orders', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.ok || !data.organizer_url) {
      throw new Error(data.error || `Server said ${response.status}.`);
    }
    window.location.href = data.organizer_url;
  } catch (error) {
    writeStatus(groupStatusBox, 'err', 'Couldn’t create that group order.', ` ${error.message}`);
    submit.disabled = !$('group-store').value;
  }
});

/* --- spreadsheet submission --------------------------------------------- */

async function send(body, headers) {
  say('busy', 'Reading your orders…', '');
  document.querySelectorAll('#sheet-path .btn.primary')
    .forEach((btn) => (btn.disabled = true));
  try {
    const response = await fetch('/api/import', { method: 'POST', body, headers });
    const data = await response.json().catch(() => ({}));
    if (data.preview_url) {
      // Warnings are shown on the preview page, where the rows are visible.
      window.location.href = data.preview_url;
      return;
    }
    say('err', 'Couldn\'t import that.', data.error || `Server said ${response.status}.`);
  } catch (error) {
    say('err', 'Couldn\'t reach the server.', ' Is it still running? ' + error.message);
  } finally {
    document.querySelectorAll('#sheet-path .btn.primary')
      .forEach((btn) => (btn.disabled = false));
    $('file-submit').disabled = !pendingFile;
  }
}

$('file-form').addEventListener('submit', (event) => {
  event.preventDefault();
  const file = pendingFile;
  if (!file) { say('err', 'No file chosen.', ' Pick a .xlsx or .csv first.'); return; }
  if (/\.(xls|numbers|ods|pdf|docx?)$/i.test(file.name)) {
    say('err', `We can't read ${file.name.split('.').pop()} files.`,
      ' Open it and save as .xlsx or .csv, then try again.');
    return;
  }
  const form = new FormData();
  form.append('file', file, file.name);
  send(form);
});

$('link-form').addEventListener('submit', (event) => {
  event.preventDefault();
  const url = $('sheet-url').value.trim();
  if (!url) { say('err', 'No link yet.', ' Paste the Google Sheets URL first.'); return; }
  send(JSON.stringify({ sheet_url: url }), { 'Content-Type': 'application/json' });
});

/* --- menu hints ---------------------------------------------------------- */

function pills(values, limit) {
  const list = document.createElement('div');
  list.className = 'pill-list';
  values.slice(0, limit).forEach((value) => {
    const pill = document.createElement('span');
    pill.className = 'pill';
    pill.textContent = value;
    list.append(pill);
  });
  if (values.length > limit) {
    const more = document.createElement('span');
    more.className = 'pill muted';
    more.textContent = `+${values.length - limit} more`;
    list.append(more);
  }
  return list;
}

fetch('/api/menu-hints').then((response) => response.json()).then((hints) => {
  if (hints.store) {
    const line = $('store-line');
    line.hidden = false;
    line.textContent = '';
    line.append(document.createTextNode('Ordering from '));
    const strong = document.createElement('strong');
    strong.textContent = hints.store;
    line.append(strong, document.createTextNode(` · ${hints.item_count} items on the menu`));
  }

  const body = $('hints-body');
  body.textContent = '';
  const list = document.createElement('dl');
  const groups = [
    ['Drinks', hints.drinks, 14, 'Spelling doesn\'t have to be exact.'],
    ['Sizes', hints.sizes, 8, 'Type the plain word — we resolve the shop\'s own labels.'],
    ['Sugar', hints.sugar, 8, 'A percentage is safest.'],
    ['Ice', hints.ice, 6, ''],
    ['Toppings', hints.toppings, 10, 'Comma-separate several.'],
    ['Milk', hints.milk, 4, ''],
  ];
  groups.forEach(([label, values, limit, note]) => {
    if (!values || !values.length) return;
    const dt = document.createElement('dt');
    dt.textContent = label;
    const dd = document.createElement('dd');
    dd.append(pills(values, limit));
    if (note) {
      const small = document.createElement('div');
      small.textContent = note;
      dd.append(small);
    }
    list.append(dt, dd);
  });
  body.append(list);
}).catch(() => {
  $('hints-body').textContent = 'Menu unavailable right now — the columns above still apply.';
});

const requestedPath = new URLSearchParams(window.location.search).get('method');
if (requestedPath === 'group' || requestedPath === 'sheet') selectPath(requestedPath);

fetch('/api/stores').then(async (response) => {
  const data = await response.json().catch(() => ({}));
  if (!response.ok || !data.ok || !data.stores || !data.stores.length) {
    throw new Error(data.error || 'No Kung Fu Tea stores are available.');
  }
  availableStores = data.stores;
  $('store-count').textContent = `(${data.stores.length} locations)`;
  $('store-search').disabled = false;
  $('store-search').placeholder = 'Search city, ZIP, address, or store name';
}).catch((error) => {
  writeStatus(groupStatusBox, 'err', 'Stores couldn’t be loaded.', ` ${error.message}`);
});
