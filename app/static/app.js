/* Upload page behaviour: tabs, drag & drop, submit, error reporting.
   No framework — everything the page needs is about 150 lines. */

const $ = (id) => document.getElementById(id);
const statusBox = $('status');   // not `status`: window.status is a real global
let pendingFile = null;

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

function say(kind, title, detail) {
  statusBox.className = 'status ' + kind;
  statusBox.textContent = '';
  if (title) {
    const strong = document.createElement('strong');
    strong.textContent = title;
    statusBox.append(strong);
  }
  if (detail) statusBox.append(document.createTextNode(detail));
}
function clearStatus() { statusBox.className = 'status'; statusBox.textContent = ''; }

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
    showTab('link');
    $('sheet-url').value = text.trim();
    $('sheet-url').focus();
    say('', 'Link pasted.', ' Press Import orders when you\'re ready.');
  }
});

/* --- submitting ---------------------------------------------------------- */

async function send(body, headers) {
  say('busy', 'Reading your orders…', '');
  document.querySelectorAll('.btn.primary').forEach((btn) => (btn.disabled = true));
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
    document.querySelectorAll('.btn.primary').forEach((btn) => (btn.disabled = false));
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
