/* Browser-scoped archive of completed cart snapshots. */

const body = document.getElementById('saved-body');
const back = document.getElementById('saved-back');
const subtitle = document.getElementById('saved-subtitle');
const pathParts = window.location.pathname.split('/').filter(Boolean);
const savedId = pathParts.length > 1 ? pathParts[1] : null;
const STORAGE_KEY = 'boba-builder:saved-orders-token';

const el = (tag, className, text) => {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
};
const money = (value) => `$${Number(value || 0).toFixed(2)}`;
const plural = (count, one, many) => `${count} ${count === 1 ? one : many || one + 's'}`;

function costShareText(order) {
  const costs = order.costs || {};
  return [order.label, ...(costs.by_person || []).map((entry) =>
    `${entry.person}: ${money(entry.total)}`), `Group total: ${money(costs.total)}`,
  'Shared costs split proportionally by drink subtotal.'].join('\n');
}

function costBlock(order) {
  const costs = order.costs || {};
  if (!(costs.by_person || []).length) return null;
  const section = el('section', 'saved-costs');
  const heading = el('div', 'cost-breakdown-heading');
  heading.append(el('h3', null, 'Who owes what'));
  const copy = el('button', 'text-button', 'Copy breakdown');
  copy.type = 'button';
  copy.addEventListener('click', async () => {
    const text = costShareText(order);
    try {
      await window.navigator.clipboard.writeText(text);
      copy.textContent = 'Copied!';
      window.setTimeout(() => { copy.textContent = 'Copy breakdown'; }, 1800);
    } catch (_error) {
      window.prompt('Copy this payment breakdown:', text);
    }
  });
  heading.append(copy);
  const list = el('ul', 'cost-breakdown-list');
  costs.by_person.forEach((entry) => {
    const item = el('li');
    const label = el('span');
    label.append(el('strong', null, entry.person),
      el('small', null, `${money(entry.subtotal)} drinks + ${money(entry.shared)} shared costs`));
    item.append(label, el('strong', 'cost-owed', money(entry.total)));
    list.append(item);
  });
  const total = el('div', 'cost-breakdown-total');
  total.append(el('span', null, 'Group total'), el('strong', null, money(costs.total)));
  section.append(heading, list, total,
    el('p', 'muted cost-breakdown-note',
      'Tax, tip, fees, discounts, and other cart-wide adjustments are split proportionally.'));
  return section;
}

function browserToken() {
  const stores = [];
  ['localStorage', 'sessionStorage'].forEach((name) => {
    try { if (window[name]) stores.push(window[name]); } catch (error) { /* unavailable */ }
  });
  for (const store of stores) {
    try {
      const found = store.getItem(STORAGE_KEY);
      if (/^[A-Za-z0-9_-]{20,128}$/.test(found || '')) return found;
    } catch (error) { /* private browsing may deny storage */ }
  }
  let token = '';
  if (window.crypto && window.crypto.randomUUID) {
    token = window.crypto.randomUUID().replace(/-/g, '') + window.crypto.randomUUID().replace(/-/g, '');
  } else if (window.crypto && window.crypto.getRandomValues) {
    const bytes = new Uint8Array(32);
    window.crypto.getRandomValues(bytes);
    token = Array.from(bytes, (value) => value.toString(16).padStart(2, '0')).join('');
  } else {
    token = `${Math.random().toString(36).slice(2)}${Date.now().toString(36)}`
      + Math.random().toString(36).slice(2);
    token = token.padEnd(32, 'x').slice(0, 64);
  }
  for (const store of stores) {
    try { store.setItem(STORAGE_KEY, token); return token; } catch (error) { /* try next */ }
  }
  return token;
}

const token = browserToken();

async function request(path, options) {
  const init = Object.assign({}, options || {});
  init.headers = Object.assign({}, init.headers || {}, {'X-Saved-Orders-Token': token});
  const response = await fetch(path, init);
  const data = await response.json().catch(() => ({}));
  if (!response.ok || !data.ok) throw new Error(data.error || `Server said ${response.status}.`);
  return data;
}

function formatDate(value) {
  if (!value) return 'Date not recorded';
  const date = new Date(`${value}T12:00:00`);
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat(undefined, {dateStyle: 'long'}).format(date);
}

function sourceLabel(source) {
  if (!source) return 'Spreadsheet';
  if (source.type === 'group_link') return 'Group link';
  if (source.kind === 'google_sheet') return 'Google Sheet';
  return source.filename ? `Spreadsheet · ${source.filename}` : 'Spreadsheet';
}

function statList(order) {
  const counts = order.counts || {};
  const totals = order.totals || {};
  const list = el('ul', 'stats saved-stats');
  const values = [
    [counts.people || 0, 'people'],
    [counts.order_lines || 0, 'order lines'],
    [counts.placed_drinks || 0, 'drinks placed'],
    [counts.not_placed_drinks || 0, 'not placed'],
  ];
  if (totals.total != null) values.push([money(totals.total), 'cart total']);
  values.forEach(([value, label]) => {
    const item = el('li');
    item.append(el('b', null, String(value)), el('span', null, label));
    list.append(item);
  });
  return list;
}

function emptyState() {
  body.textContent = '';
  const card = el('section', 'card saved-empty');
  card.append(el('h2', null, 'No saved orders yet'));
  card.append(el('p', 'muted',
    'After you build a cart from a spreadsheet or group link, save the finished order '
    + 'from its cart review screen. It will appear here.'));
  const start = el('a', 'btn primary', 'Start an order');
  start.href = '/';
  card.append(start);
  body.append(card);
}

function renderList(orders) {
  body.textContent = '';
  subtitle.textContent = 'Finished group orders saved in this browser.';
  back.href = '/';
  back.textContent = '← Back to start';
  if (!orders.length) return emptyState();

  const section = el('section', 'card');
  section.append(el('h2', null, 'Saved Orders'));
  section.append(el('p', 'muted',
    'These snapshots stay on this Boba Builder server and are visible only to this browser.'));
  const list = el('div', 'saved-order-list');
  orders.forEach((order) => {
    const link = el('a', 'saved-order-card');
    link.href = `/saved-orders/${encodeURIComponent(order.id)}`;
    const heading = el('div', 'saved-order-heading');
    heading.append(el('strong', null, order.label));
    if ((order.totals || {}).total != null) {
      heading.append(el('span', 'saved-total', money(order.totals.total)));
    }
    link.append(heading);
    link.append(el('div', 'saved-order-meta',
      `${formatDate(order.order_date)} · ${sourceLabel(order.source)}`));
    const counts = order.counts || {};
    link.append(el('div', 'saved-order-meta',
      `${plural(counts.placed_drinks || 0, 'drink')} · ${plural(counts.people || 0, 'person', 'people')}`));
    list.append(link);
  });
  section.append(list);
  body.append(section);
}

function manifest(items) {
  const list = el('ul', 'manifest saved-manifest');
  (items || []).forEach((line) => {
    const item = el('li');
    const head = el('div', 'manifest-head');
    head.append(el('strong', null,
      `${line.person || 'Unlabelled'} — ${line.quantity || 1}× ${line.drink || 'Unknown drink'}`));
    const total = line.actual_total == null ? line.estimated_total : line.actual_total;
    if (total != null) head.append(el('span', 'manifest-price', money(total)));
    item.append(head);
    const modifiers = (line.options || []).map((option) =>
      `${option.name}${Number(option.quantity || 1) > 1 ? ` ×${option.quantity}` : ''}`);
    if (modifiers.length) item.append(el('div', 'muted', modifiers.join(' · ')));
    if (line.notes) item.append(el('div', 'muted', `Note: ${line.notes}`));
    list.append(item);
  });
  return list;
}

function problemList(title, entries) {
  if (!entries || !entries.length) return null;
  const section = el('section', 'saved-problems');
  section.append(el('h3', null, title));
  const list = el('ul', 'issues');
  entries.forEach((line) => {
    list.append(el('li', 'warning',
      `Row ${line.row_number}: ${line.person || 'Unlabelled'} — ${line.quantity || 1}× `
      + `${line.drink || 'Unknown drink'}: ${line.reason || 'not placed'}`));
  });
  section.append(list);
  return section;
}

function totalsBlock(totals) {
  if (!totals || (totals.subtotal == null && totals.tax == null && totals.total == null)) return null;
  const block = el('dl', 'cart-totals');
  const add = (label, value, strong) => {
    if (value == null) return;
    block.append(el('dt', strong ? 'grand' : '', label),
      el('dd', strong ? 'grand' : '', money(value)));
  };
  add('Subtotal', totals.subtotal);
  add('Tax', totals.tax);
  Object.keys(totals.fees || {}).forEach((key) =>
    add(key.replace(/_/g, ' ').replace(/\b\w/g, (letter) => letter.toUpperCase()), totals.fees[key]));
  add('Tip', totals.tip);
  add('Total', totals.total, true);
  return block;
}

function downloadExport(order, button, feedback) {
  return async () => {
    button.disabled = true;
    feedback.textContent = 'Preparing spreadsheet…';
    try {
      const response = await fetch(`/api/saved-orders/${encodeURIComponent(order.id)}/export`, {
        headers: {'X-Saved-Orders-Token': token},
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.error || 'The export could not be created.');
      }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement('a');
      link.href = url;
      link.download = `${order.label.replace(/[^a-z0-9]+/gi, '-').replace(/^-|-$/g, '') || 'boba-order'}.csv`;
      document.body.append(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
      feedback.textContent = 'Export downloaded.';
    } catch (error) {
      feedback.className = 'save-feedback error';
      feedback.textContent = error.message;
    } finally {
      button.disabled = false;
    }
  };
}

function renderDetail(order) {
  body.textContent = '';
  subtitle.textContent = `${order.label} · ${formatDate(order.order_date)}`;
  back.href = '/saved-orders';
  back.textContent = '← All saved orders';

  const summary = el('section', 'card saved-detail-head');
  summary.append(el('p', 'choice-kicker', 'Saved order'));
  summary.append(el('h2', null, order.label));
  const source = sourceLabel(order.source);
  const storeName = (order.store || {}).name || (order.source || {}).store;
  summary.append(el('p', 'saved-order-meta',
    [formatDate(order.order_date), source, storeName].filter(Boolean).join(' · ')));
  summary.append(statList(order));

  const actions = el('div', 'actions');
  const repeat = el('button', 'btn primary', 'Use for a new cart');
  const exportButton = el('button', 'btn', 'Export CSV');
  const feedback = el('div', 'save-feedback');
  actions.append(repeat, exportButton);
  summary.append(actions, feedback);

  repeat.addEventListener('click', async () => {
    repeat.disabled = true;
    feedback.className = 'save-feedback';
    feedback.textContent = 'Preparing a fresh editable order…';
    try {
      const data = await request(`/api/saved-orders/${encodeURIComponent(order.id)}/repeat`, {
        method: 'POST',
      });
      window.location.href = data.preview_url;
    } catch (error) {
      feedback.className = 'save-feedback error';
      feedback.textContent = error.message;
      repeat.disabled = false;
    }
  });
  exportButton.addEventListener('click', downloadExport(order, exportButton, feedback));
  body.append(summary);

  const detail = el('section', 'card');
  detail.append(el('h2', null, 'Who gets what'));
  if ((order.items || []).length) detail.append(manifest(order.items));
  else detail.append(el('p', 'muted', 'No drinks were recorded in this cart.'));
  const failed = problemList('Could not be placed', order.failed);
  if (failed) detail.append(failed);
  const skipped = problemList('Not mapped, so not placed', order.skipped);
  if (skipped) detail.append(skipped);
  const split = costBlock(order);
  if (split) detail.append(split);
  const totals = totalsBlock(order.totals);
  if (totals) detail.append(totals);
  body.append(detail);
}

function showError(error) {
  body.textContent = '';
  const card = el('section', 'card saved-empty');
  card.append(el('h2', null, 'That saved order is not available'));
  card.append(el('p', 'muted', error.message));
  const link = el('a', 'btn primary', 'View Saved Orders');
  link.href = '/saved-orders';
  card.append(link);
  body.append(card);
}

if (savedId) {
  request(`/api/saved-orders/${encodeURIComponent(savedId)}`)
    .then((data) => renderDetail(data.order))
    .catch(showError);
} else {
  request('/api/saved-orders')
    .then((data) => renderList(data.orders || []))
    .catch(showError);
}
