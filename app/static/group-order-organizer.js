/* Organizer dashboard for a group-order room. The organizer token is carried
   in the URL fragment once, then kept in localStorage and sent only as a
   request header. Public room links never contain it. */

const pathParts = window.location.pathname.split('/').filter(Boolean);
const groupIndex = pathParts.indexOf('group-order');
const roomId = groupIndex >= 0 ? pathParts[groupIndex + 1] : '';
const apiBase = `/api/group-orders/${encodeURIComponent(roomId)}`;
const tokenKey = `boba-builder:group-order:${roomId}:organizer-token`;
const POLL_INTERVAL_MS = 5000;

const elements = {
  accessCard: document.getElementById('access-card'),
  accessForm: document.getElementById('access-form'),
  accessInput: document.getElementById('organizer-token'),
  accessSubmit: document.getElementById('access-submit'),
  accessStatus: document.getElementById('access-status'),
  dashboard: document.getElementById('dashboard-content'),
  roomTitle: document.getElementById('room-title'),
  roomSubtitle: document.getElementById('room-subtitle'),
  roomStrip: document.getElementById('room-strip'),
  copyLink: document.getElementById('copy-link'),
  openParticipant: document.getElementById('open-participant'),
  refresh: document.getElementById('refresh'),
  toggleLock: document.getElementById('toggle-lock'),
  forgetAccess: document.getElementById('forget-access'),
  drinkCount: document.getElementById('drink-count'),
  peopleCount: document.getElementById('people-count'),
  lineCount: document.getElementById('line-count'),
  liveCard: document.querySelector('.live-card'),
  liveLabel: document.getElementById('live-label'),
  lastUpdated: document.getElementById('last-updated'),
  moderationNote: document.getElementById('moderation-note'),
  peopleOrders: document.getElementById('people-orders'),
  drinkRollup: document.getElementById('drink-rollup'),
  finalize: document.getElementById('finalize'),
  continuePreview: document.getElementById('continue-preview'),
  finalizeCopy: document.getElementById('finalize-copy'),
  actionStatus: document.getElementById('action-status'),
};

let organizerToken = readToken();
let session = null;
let refreshing = false;

function readToken() {
  try {
    return window.localStorage.getItem(tokenKey) || '';
  } catch (_error) {
    return '';
  }
}

function saveToken(value) {
  organizerToken = String(value || '').trim();
  try {
    if (organizerToken) window.localStorage.setItem(tokenKey, organizerToken);
    else window.localStorage.removeItem(tokenKey);
  } catch (_error) {
    // Storage may be unavailable; the in-memory token still works this visit.
  }
}

function takeTokenFromFragment() {
  const raw = window.location.hash.replace(/^#/, '');
  if (!raw) return;
  const params = new URLSearchParams(raw);
  const token = params.get('token') || (raw.includes('=') ? '' : raw);
  if (token) saveToken(token);
  window.history.replaceState(null, '', window.location.pathname);
}

function node(tag, className, text) {
  const result = document.createElement(tag);
  if (className) result.className = className;
  if (text !== undefined) result.textContent = text;
  return result;
}

function plural(count, one, many) {
  return `${count} ${count === 1 ? one : (many || `${one}s`)}`;
}

function normalized(value) {
  return String(value || '').toLocaleLowerCase().replace(/\s+/g, ' ').trim();
}

async function request(url, options) {
  const init = { ...(options || {}) };
  init.headers = { ...(init.headers || {}), 'X-Organizer-Token': organizerToken };
  const response = await fetch(url, init);
  const data = await response.json().catch(() => ({}));
  if (!response.ok || !data.ok) {
    const error = new Error(data.error || 'Something went wrong. Please try again.');
    error.status = response.status;
    error.code = data.code;
    throw error;
  }
  return data;
}

function setStatus(element, message, kind) {
  element.className = `status${kind ? ` ${kind}` : ''}`;
  element.textContent = message || '';
}

function orderDetails(order) {
  const parts = [order.size, order.sugar, order.ice, order.milk, order.temperature].filter(Boolean);
  if (order.toppings && order.toppings.length) parts.push(order.toppings.join(', '));
  if (order.notes) parts.push(`“${order.notes}”`);
  return parts.length ? parts.join(' · ') : 'Store defaults';
}

function orderSignature(order) {
  return JSON.stringify([
    normalized(order.person), normalized(order.drink), normalized(order.size),
    normalized(order.sugar), normalized(order.ice), normalized(order.milk),
    normalized(order.temperature), (order.toppings || []).map(normalized).sort(),
    Number(order.quantity || 1), normalized(order.notes),
  ]);
}

function timeText(timestamp) {
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return '';
  return new Intl.DateTimeFormat(undefined, { hour: 'numeric', minute: '2-digit' }).format(date);
}

function participantUrl() {
  return `${window.location.origin}/group-order/${encodeURIComponent(roomId)}`;
}

function canModerate() {
  return session && (session.status === 'open' || session.status === 'locked');
}

function renderRoom() {
  if (!session) return;
  document.title = `${session.title} — organizer — Boba Builder`;
  elements.roomTitle.textContent = session.title;
  elements.roomSubtitle.textContent = session.organizer_name
    ? `Hosted by ${session.organizer_name}` : 'Organizer view';
  elements.openParticipant.href = `/group-order/${encodeURIComponent(roomId)}`;

  const labels = {
    open: ['Open for drinks', 'Participants can add and edit orders.'],
    locked: ['Submissions paused', 'Only you can remove lines while reviewing.'],
    closed: session.preview_url
      ? ['Order finalized', 'Submissions are permanently closed.']
      : ['Submissions closed', 'The saved drinks are ready to send to cart review.'],
    expired: ['Order expired', 'This room is read-only.'],
  };
  const label = labels[session.status] || [session.status, ''];
  elements.roomStrip.className = `room-strip organizer-strip ${session.status}`;
  elements.roomStrip.textContent = '';
  elements.roomStrip.append(node('span', 'status-dot'));
  const summary = node('span', 'strip-summary');
  summary.append(node('strong', null, label[0]), document.createTextNode(` · ${label[1]}`));
  elements.roomStrip.append(summary, node('span', 'strip-time', `Room updated ${timeText(session.updated_at)}`));

  elements.drinkCount.textContent = String(session.summary.drinks);
  elements.peopleCount.textContent = String(session.summary.people);
  elements.lineCount.textContent = String(session.summary.orders);
  elements.toggleLock.hidden = !['open', 'locked'].includes(session.status);
  elements.toggleLock.textContent = session.status === 'open' ? 'Pause submissions' : 'Reopen submissions';
  elements.moderationNote.textContent = canModerate()
    ? 'Remove junk or duplicate lines before finalizing.'
    : 'This room is read-only.';

  const hasPreview = Boolean(session.preview_url);
  elements.finalize.hidden = hasPreview;
  elements.continuePreview.hidden = !hasPreview;
  if (hasPreview) {
    elements.finalizeCopy.textContent = 'This room is finalized. Continue to the existing cart review whenever you are ready.';
  } else {
    elements.finalize.disabled = !session.orders.length || session.status === 'expired';
    elements.finalizeCopy.textContent = session.status === 'closed'
      ? 'This room is closed. Push its saved drinks into the cart review when ready.'
      : 'Finalizing closes submissions permanently and opens the existing cart review.';
  }
}

function renderOrders() {
  elements.peopleOrders.textContent = '';
  if (!session.orders.length) {
    elements.peopleOrders.append(node('p', 'muted empty-state', 'No drinks have been submitted yet.'));
    return;
  }

  const duplicateCounts = new Map();
  session.orders.forEach((order) => {
    const signature = orderSignature(order);
    duplicateCounts.set(signature, (duplicateCounts.get(signature) || 0) + 1);
  });

  const people = new Map();
  session.orders.forEach((order) => {
    const key = normalized(order.person);
    if (!people.has(key)) people.set(key, { name: order.person, orders: [], drinks: 0 });
    const group = people.get(key);
    group.orders.push(order);
    group.drinks += Number(order.quantity || 1);
  });

  people.forEach((person) => {
    const section = node('section', 'person-group');
    const heading = node('div', 'person-heading');
    heading.append(node('h3', null, person.name), node('span', 'person-total', plural(person.drinks, 'drink')));
    const lines = node('div', 'person-lines');
    person.orders.forEach((order) => {
      const line = node('article', 'organizer-order-line');
      const title = node('div', 'order-line-title');
      title.append(document.createTextNode(order.drink));
      if (duplicateCounts.get(orderSignature(order)) > 1) {
        title.append(node('span', 'duplicate-badge', 'Possible duplicate'));
      }
      const quantity = order.quantity > 1 ? ` ×${order.quantity}` : '';
      title.append(node('span', 'group-order-qty', quantity));
      line.append(title, node('p', 'group-order-detail', orderDetails(order)));
      const added = timeText(order.created_at);
      line.append(node('span', 'line-meta', added ? `Added ${added}` : 'Submitted order'));
      const remove = node('button', 'text-button remove-line', 'Remove');
      remove.type = 'button';
      remove.disabled = !canModerate();
      remove.setAttribute('aria-label', `Remove ${order.drink} for ${order.person}`);
      remove.addEventListener('click', () => removeOrder(order, remove));
      line.append(remove);
      lines.append(line);
    });
    section.append(heading, lines);
    elements.peopleOrders.append(section);
  });
}

function renderDrinkTotals() {
  elements.drinkRollup.textContent = '';
  if (!session.orders.length) {
    elements.drinkRollup.append(node('p', 'muted', 'Nothing to total yet.'));
    return;
  }
  const drinks = new Map();
  session.orders.forEach((order) => {
    const key = normalized(order.drink);
    const entry = drinks.get(key) || { name: order.drink, quantity: 0 };
    entry.quantity += Number(order.quantity || 1);
    drinks.set(key, entry);
  });
  const list = node('ul', 'drink-rollup');
  [...drinks.values()]
    .sort((left, right) => right.quantity - left.quantity || left.name.localeCompare(right.name))
    .forEach((drink) => {
      const item = node('li');
      item.append(node('span', null, drink.name), node('strong', null, `×${drink.quantity}`));
      list.append(item);
    });
  elements.drinkRollup.append(list);
}

function render() {
  renderRoom();
  renderOrders();
  renderDrinkTotals();
}

function applyPublicSession(updated) {
  const privateState = session && {
    finalized_at: session.finalized_at,
    preview_url: session.preview_url,
  };
  session = { ...updated, ...(privateState || {}) };
  render();
}

function showAccess(message) {
  elements.accessCard.hidden = false;
  elements.dashboard.hidden = true;
  elements.accessInput.value = organizerToken;
  if (message) setStatus(elements.accessStatus, message, 'err');
}

function showDashboard() {
  elements.accessCard.hidden = true;
  elements.dashboard.hidden = false;
}

function showLive(kind, label, detail) {
  elements.liveCard.className = `summary-card live-card${kind ? ` ${kind}` : ''}`;
  elements.liveLabel.textContent = label;
  elements.lastUpdated.textContent = detail;
}

async function refreshSession(options) {
  if (refreshing || !organizerToken || document.hidden) return;
  refreshing = true;
  const announce = options && options.announce;
  if (announce) elements.refresh.disabled = true;
  try {
    const data = await request(`${apiBase}/organizer`);
    session = data.session;
    showDashboard();
    render();
    showLive('', session.status === 'open' ? 'Live' : 'Monitoring', `Updated ${timeText(new Date())}`);
  } catch (error) {
    if (error.status === 403) {
      showAccess('That organizer token is not valid for this room.');
    } else {
      showLive('offline', 'Reconnecting', error.message);
      if (!session) showAccess(error.message);
    }
  } finally {
    refreshing = false;
    elements.refresh.disabled = false;
  }
}

async function changeStatus() {
  if (!session || !['open', 'locked'].includes(session.status)) return;
  const action = session.status === 'open' ? 'lock' : 'reopen';
  elements.toggleLock.disabled = true;
  setStatus(elements.actionStatus,
    action === 'lock' ? 'Pausing submissions…' : 'Reopening submissions…', 'busy');
  try {
    const data = await request(`${apiBase}/${action}`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
    });
    applyPublicSession(data.session);
    setStatus(elements.actionStatus,
      action === 'lock' ? 'Submissions are paused. You can still remove lines.' : 'Participants can submit again.', 'ok');
  } catch (error) {
    setStatus(elements.actionStatus, error.message, 'err');
  } finally {
    elements.toggleLock.disabled = false;
  }
}

async function removeOrder(order, button) {
  if (!window.confirm(`Remove ${order.drink} for ${order.person}?`)) return;
  button.disabled = true;
  try {
    const data = await request(`${apiBase}/orders/${encodeURIComponent(order.id)}`, { method: 'DELETE' });
    applyPublicSession(data.session);
    setStatus(elements.actionStatus, `${order.drink} for ${order.person} was removed.`, 'ok');
  } catch (error) {
    setStatus(elements.actionStatus, error.message, 'err');
    button.disabled = false;
  }
}

async function finalizeOrder() {
  if (!session || !session.orders.length) return;
  const message = session.status === 'closed'
    ? 'Send this closed group order to cart review now?'
    : 'Finalize this group order? Participants will not be able to change it afterward.';
  if (!window.confirm(message)) return;
  elements.finalize.disabled = true;
  setStatus(elements.actionStatus, 'Finalizing the group order…', 'busy');
  try {
    const data = await request(`${apiBase}/finalize`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: '{}',
    });
    window.location.assign(data.preview_url);
  } catch (error) {
    setStatus(elements.actionStatus, error.message, 'err');
    elements.finalize.disabled = false;
  }
}

elements.accessForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  saveToken(elements.accessInput.value);
  setStatus(elements.accessStatus, 'Checking organizer access…', 'busy');
  elements.accessSubmit.disabled = true;
  await refreshSession({ announce: true });
  elements.accessSubmit.disabled = false;
});

elements.copyLink.addEventListener('click', async () => {
  const url = participantUrl();
  try {
    await window.navigator.clipboard.writeText(url);
    elements.copyLink.textContent = 'Copied!';
    window.setTimeout(() => { elements.copyLink.textContent = 'Copy link'; }, 1800);
  } catch (_error) {
    window.prompt('Copy this participant link:', url);
  }
});
elements.refresh.addEventListener('click', () => refreshSession({ announce: true }));
elements.toggleLock.addEventListener('click', changeStatus);
elements.finalize.addEventListener('click', finalizeOrder);
elements.continuePreview.addEventListener('click', () => {
  if (session && session.preview_url) window.location.assign(session.preview_url);
});
elements.forgetAccess.addEventListener('click', () => {
  saveToken('');
  session = null;
  showAccess('Organizer access was removed from this browser.');
  elements.accessInput.value = '';
});
document.addEventListener('visibilitychange', () => {
  if (!document.hidden) refreshSession();
});

takeTokenFromFragment();
if (organizerToken) refreshSession({ announce: true });
else showAccess();
window.setInterval(() => refreshSession(), POLL_INTERVAL_MS);
