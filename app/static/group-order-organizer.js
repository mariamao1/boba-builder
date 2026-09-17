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
  showQr: document.getElementById('show-qr'),
  downloadQr: document.getElementById('download-qr'),
  downloadQrDialog: document.getElementById('download-qr-dialog'),
  openQr: document.getElementById('open-qr'),
  closeQr: document.getElementById('close-qr'),
  closeQrButton: document.getElementById('close-qr-button'),
  qrDialog: document.getElementById('qr-dialog'),
  qrPreview: document.getElementById('qr-preview'),
  qrLarge: document.getElementById('qr-large'),
  qrCompactContext: document.getElementById('qr-compact-context'),
  qrDialogTitle: document.getElementById('qr-dialog-title'),
  qrDialogStore: document.getElementById('qr-dialog-store'),
  qrDialogDeadline: document.getElementById('qr-dialog-deadline'),
  qrDialogUrl: document.getElementById('qr-dialog-url'),
  qrStatus: document.getElementById('qr-status'),
  openParticipant: document.getElementById('open-participant'),
  refresh: document.getElementById('refresh'),
  toggleLock: document.getElementById('toggle-lock'),
  forgetAccess: document.getElementById('forget-access'),
  deadlineManager: document.getElementById('deadline-manager'),
  deadlineCountdown: document.getElementById('deadline-countdown'),
  deadlineDetail: document.getElementById('deadline-detail'),
  deadlineForm: document.getElementById('deadline-form'),
  deadlineInput: document.getElementById('deadline-input'),
  saveDeadline: document.getElementById('save-deadline'),
  deadlineStatus: document.getElementById('deadline-status'),
  deadlineQuickActions: document.querySelectorAll('[data-extend-minutes]'),
  budgetManager: document.getElementById('budget-manager'),
  budgetCurrent: document.getElementById('budget-current'),
  budgetDetail: document.getElementById('budget-detail'),
  budgetForm: document.getElementById('budget-form'),
  budgetInput: document.getElementById('budget-input'),
  saveBudget: document.getElementById('save-budget'),
  clearBudget: document.getElementById('clear-budget'),
  budgetStatus: document.getElementById('budget-status'),
  drinkCount: document.getElementById('drink-count'),
  peopleCount: document.getElementById('people-count'),
  lineCount: document.getElementById('line-count'),
  liveCard: document.querySelector('.live-card'),
  liveLabel: document.getElementById('live-label'),
  lastUpdated: document.getElementById('last-updated'),
  moderationNote: document.getElementById('moderation-note'),
  peopleOrders: document.getElementById('people-orders'),
  drinkRollup: document.getElementById('drink-rollup'),
  costBreakdown: document.getElementById('cost-breakdown'),
  costNote: document.getElementById('cost-note'),
  copyCosts: document.getElementById('copy-costs'),
  finalize: document.getElementById('finalize'),
  continuePreview: document.getElementById('continue-preview'),
  finalizeCopy: document.getElementById('finalize-copy'),
  actionStatus: document.getElementById('action-status'),
};

let organizerToken = readToken();
let session = null;
let refreshing = false;
let serverOffsetMs = 0;
let deadlineTransitionHandled = false;
let renderedQrUrl = '';

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

function money(value) {
  return `$${Number(value || 0).toFixed(2)}`;
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

function deadlineText(timestamp) {
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return '';
  return new Intl.DateTimeFormat(undefined, {
    weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
  }).format(date);
}

function setSession(updated) {
  session = updated;
  const serverTime = new Date(updated.server_now).getTime();
  if (Number.isFinite(serverTime)) serverOffsetMs = serverTime - Date.now();
  deadlineTransitionHandled = !updated.accepting_orders;
}

function deadlineRemaining() {
  if (!session) return 0;
  const deadline = new Date(session.deadline_at || session.expires_at).getTime();
  return Number.isFinite(deadline) ? deadline - (Date.now() + serverOffsetMs) : 0;
}

function countdownText(milliseconds) {
  const total = Math.max(0, Math.ceil(milliseconds / 1000));
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const seconds = total % 60;
  if (days) return `${days}d ${hours}h ${minutes}m left`;
  if (hours) return `${hours}h ${String(minutes).padStart(2, '0')}m ${String(seconds).padStart(2, '0')}s left`;
  return `${minutes}m ${String(seconds).padStart(2, '0')}s left`;
}

function localDateTimeValue(value) {
  const local = new Date(value.getTime() - value.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 16);
}

function renderDeadline(syncInput) {
  if (!session) return;
  const timestamp = session.deadline_at || session.expires_at;
  const exact = deadlineText(timestamp);
  const passed = session.deadline_passed || deadlineRemaining() <= 0;
  const closed = session.status === 'closed';
  elements.deadlineManager.classList.toggle('deadline-passed', passed || closed);
  if (closed) {
    elements.deadlineCountdown.textContent = 'Order closed';
    elements.deadlineDetail.textContent = exact ? `The cutoff was ${exact}.` : 'The room is read-only.';
  } else if (passed) {
    elements.deadlineCountdown.textContent = 'Deadline passed';
    elements.deadlineDetail.textContent = `Submissions locked automatically at ${exact}. Set a future time to reopen.`;
  } else if (session.status === 'locked') {
    elements.deadlineCountdown.textContent = 'Paused manually';
    elements.deadlineDetail.textContent = `${countdownText(deadlineRemaining())} · cutoff ${exact}`;
  } else {
    elements.deadlineCountdown.textContent = countdownText(deadlineRemaining());
    elements.deadlineDetail.textContent = `Submissions lock automatically at ${exact}.`;
  }

  const now = new Date(Date.now() + serverOffsetMs);
  elements.deadlineInput.min = localDateTimeValue(new Date(now.getTime() + 60 * 1000));
  elements.deadlineInput.max = localDateTimeValue(new Date(now.getTime() + 7 * 24 * 60 * 60 * 1000));
  if (syncInput && document.activeElement !== elements.deadlineInput) {
    elements.deadlineInput.value = localDateTimeValue(new Date(timestamp));
  }
  elements.deadlineInput.disabled = closed;
  elements.saveDeadline.disabled = closed;
  elements.deadlineQuickActions.forEach((button) => { button.disabled = closed; });
}

function estimatedTotalForPerson(person) {
  const key = normalized(person);
  return (session.orders || []).reduce((total, order) => {
    if (normalized(order.person) !== key || order.estimated_total == null) return total;
    return total + Number(order.estimated_total || 0);
  }, 0);
}

function overBudgetPeople() {
  const cap = Number(session && session.budget_cap);
  if (!Number.isFinite(cap) || cap <= 0) return [];
  const people = new Map();
  (session.orders || []).forEach((order) => {
    const key = normalized(order.person);
    if (!people.has(key)) people.set(key, order.person);
  });
  return [...people.values()].filter(
    (person) => estimatedTotalForPerson(person) > cap + 0.0001
  );
}

function renderBudget(syncInput) {
  if (!session) return;
  const cap = Number(session.budget_cap);
  const hasCap = Number.isFinite(cap) && cap > 0;
  const over = overBudgetPeople();
  elements.budgetManager.classList.toggle('over-budget', over.length > 0);
  elements.budgetCurrent.textContent = hasCap ? `${money(cap)} hard cap` : 'No spending cap';
  if (over.length) {
    elements.budgetDetail.textContent = `${plural(over.length, 'person', 'people')} over the new cap: ${over.join(', ')}. Their submitted drinks were kept.`;
  } else if (hasCap) {
    elements.budgetDetail.textContent = 'Each person’s combined priced drinks must stay within this amount.';
  } else {
    elements.budgetDetail.textContent = 'Participants can submit any priced total.';
  }
  if (syncInput && document.activeElement !== elements.budgetInput) {
    elements.budgetInput.value = hasCap ? cap.toFixed(2) : '';
  }
  const closed = session.status === 'closed';
  elements.budgetInput.disabled = closed;
  elements.saveBudget.disabled = closed;
  elements.clearBudget.disabled = closed || !hasCap;
}

function tickDeadline() {
  if (!session) return;
  renderDeadline(false);
  if (session.accepting_orders && deadlineRemaining() <= 0 && !deadlineTransitionHandled) {
    deadlineTransitionHandled = true;
    session.accepting_orders = false;
    session.deadline_passed = true;
    session.status = 'locked';
    session.lock_reason = 'deadline';
    render();
    refreshSession();
  }
}

function participantUrl() {
  return `${window.location.origin}/group-order/${encodeURIComponent(roomId)}`;
}

function qrDeadlineText() {
  if (!session) return 'Scan with your phone camera';
  const exact = deadlineText(session.deadline_at || session.expires_at);
  if (session.status === 'closed') return exact ? `Order closed · cutoff was ${exact}` : 'Order closed';
  if (session.deadline_passed) return exact ? `Deadline passed · ${exact}` : 'Deadline passed';
  return exact ? `Order by ${exact}` : 'Scan with your phone camera';
}

function renderQrCode(container, size) {
  const holder = document.createElement('div');
  const code = new QRCode(holder, {
    text: participantUrl(),
    width: 64,
    height: 64,
    colorDark: '#000000',
    colorLight: '#ffffff',
    correctLevel: QRCode.CorrectLevel.M,
  });
  const canvas = document.createElement('canvas');
  canvas.width = size;
  canvas.height = size;
  drawQrMatrix(canvas.getContext('2d'), code._oQRCode, 0, 0, size);
  container.textContent = '';
  container.append(canvas);
  container.removeAttribute('title');
  return code;
}

function renderInvite() {
  if (!session) return;
  const url = participantUrl();
  const detail = [session.store_name, qrDeadlineText()].filter(Boolean).join(' · ');
  elements.qrCompactContext.textContent = detail;
  elements.qrDialogTitle.textContent = session.title || 'Group order';
  elements.qrDialogStore.textContent = session.store_name || 'Boba Builder group order';
  elements.qrDialogDeadline.textContent = qrDeadlineText();
  elements.qrDialogUrl.textContent = url;
  if (renderedQrUrl === url && elements.qrPreview.firstChild && elements.qrLarge.firstChild) return;
  try {
    if (typeof QRCode !== 'function') throw new Error('QR generator unavailable');
    renderQrCode(elements.qrPreview, 192);
    renderQrCode(elements.qrLarge, 720);
    renderedQrUrl = url;
    elements.openQr.disabled = false;
    elements.showQr.disabled = false;
    elements.downloadQr.disabled = false;
    elements.downloadQrDialog.disabled = false;
    setStatus(elements.qrStatus, '', '');
  } catch (_error) {
    elements.qrPreview.textContent = 'QR unavailable';
    elements.qrLarge.textContent = 'The QR code could not be generated. Use the participant link instead.';
    elements.openQr.disabled = true;
    elements.showQr.disabled = true;
    elements.downloadQr.disabled = true;
    elements.downloadQrDialog.disabled = true;
    setStatus(elements.qrStatus, 'QR unavailable. Copy the participant link instead.', 'err');
  }
}

function showQrDialog() {
  renderInvite();
  if (typeof elements.qrDialog.showModal === 'function') elements.qrDialog.showModal();
  else elements.qrDialog.setAttribute('open', '');
}

function closeQrDialog() {
  if (typeof elements.qrDialog.close === 'function') elements.qrDialog.close();
  else elements.qrDialog.removeAttribute('open');
}

function drawQrMatrix(context, model, left, top, availableSize) {
  const quietModules = 4;
  const count = model.getModuleCount();
  const moduleSize = Math.floor(availableSize / (count + quietModules * 2));
  const size = moduleSize * (count + quietModules * 2);
  const x = left + Math.floor((availableSize - size) / 2);
  const y = top + Math.floor((availableSize - size) / 2);
  context.fillStyle = '#ffffff';
  context.fillRect(x, y, size, size);
  context.fillStyle = '#000000';
  for (let row = 0; row < count; row += 1) {
    for (let column = 0; column < count; column += 1) {
      if (model.isDark(row, column)) {
        context.fillRect(
          x + (column + quietModules) * moduleSize,
          y + (row + quietModules) * moduleSize,
          moduleSize,
          moduleSize,
        );
      }
    }
  }
}

function centeredLines(context, value, centerX, startY, maxWidth, lineHeight, maxLines) {
  const words = String(value || '').split(/\s+/).filter(Boolean);
  const lines = [];
  words.forEach((word) => {
    const current = lines[lines.length - 1] || '';
    const candidate = current ? `${current} ${word}` : word;
    if (current && context.measureText(candidate).width > maxWidth && lines.length < maxLines) {
      lines.push(word);
    } else if (!lines.length) {
      lines.push(word);
    } else {
      lines[lines.length - 1] = candidate;
    }
  });
  lines.slice(0, maxLines).forEach((line, index) => {
    context.fillText(line, centerX, startY + index * lineHeight, maxWidth);
  });
  return startY + Math.min(lines.length, maxLines) * lineHeight;
}

function qrDownloadCanvas() {
  const holder = document.createElement('div');
  const code = renderQrCode(holder, 64);
  const canvas = document.createElement('canvas');
  canvas.width = 1200;
  canvas.height = 1500;
  const context = canvas.getContext('2d');
  context.fillStyle = '#faf6f1';
  context.fillRect(0, 0, canvas.width, canvas.height);
  context.textAlign = 'center';
  context.textBaseline = 'top';
  context.fillStyle = '#7a4a28';
  context.font = '700 34px system-ui, sans-serif';
  context.fillText('SCAN TO JOIN THE GROUP ORDER', 600, 62);
  context.fillStyle = '#2c211b';
  context.font = '700 68px system-ui, sans-serif';
  const titleBottom = centeredLines(context, session.title || 'Group order', 600, 125, 1020, 76, 2);
  context.fillStyle = '#7b6a5e';
  context.font = '400 31px system-ui, sans-serif';
  context.fillText(session.store_name || 'Boba Builder', 600, Math.max(285, titleBottom + 15), 1020);
  drawQrMatrix(context, code._oQRCode, 140, 350, 920);
  context.fillStyle = '#2c211b';
  context.font = '700 34px system-ui, sans-serif';
  context.fillText(qrDeadlineText(), 600, 1290, 1040);
  context.fillStyle = '#7b6a5e';
  context.font = '400 24px system-ui, sans-serif';
  context.fillText(participantUrl(), 600, 1370, 1080);
  return canvas;
}

function qrFilename() {
  const base = String((session && session.title) || 'group-order')
    .normalize('NFKD').replace(/[^a-z0-9]+/gi, '-').replace(/^-|-$/g, '').toLowerCase();
  return `${base || 'group-order'}-qr.png`;
}

function downloadQrCode() {
  try {
    const canvas = qrDownloadCanvas();
    const save = (href, revoke) => {
      const link = document.createElement('a');
      link.download = qrFilename();
      link.href = href;
      document.body.append(link);
      link.click();
      link.remove();
      if (revoke) window.setTimeout(() => URL.revokeObjectURL(href), 1000);
      setStatus(elements.qrStatus, 'QR code downloaded.', 'ok');
    };
    if (typeof canvas.toBlob === 'function') {
      canvas.toBlob((blob) => {
        if (blob) save(URL.createObjectURL(blob), true);
        else save(canvas.toDataURL('image/png'), false);
      }, 'image/png');
    } else {
      save(canvas.toDataURL('image/png'), false);
    }
  } catch (_error) {
    setStatus(elements.qrStatus, 'Couldn’t download the QR code. Try showing it full screen.', 'err');
  }
}

function canModerate() {
  return session && (session.status === 'open' || session.status === 'locked');
}

function renderRoom() {
  if (!session) return;
  document.title = `${session.title} — organizer — Boba Builder`;
  elements.roomTitle.textContent = session.title;
  const host = session.organizer_name
    ? `Hosted by ${session.organizer_name}` : 'Organizer view';
  elements.roomSubtitle.textContent = session.store_name ? `${host} · ${session.store_name}` : host;
  elements.openParticipant.href = `/group-order/${encodeURIComponent(roomId)}`;

  const labels = {
    open: ['Open for drinks', 'Participants can add and edit orders.'],
    locked: session.lock_reason === 'deadline'
      ? ['Deadline reached', 'New submissions were automatically locked.']
      : ['Submissions paused', 'Only you can remove lines while reviewing.'],
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
  elements.toggleLock.hidden = !(session.status === 'open'
    || (session.status === 'locked' && session.lock_reason !== 'deadline'));
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
  renderInvite();
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
  const costByPerson = new Map(((session.costs || {}).by_person || []).map((entry) => [normalized(entry.person), entry]));
  session.orders.forEach((order) => {
    const key = normalized(order.person);
    if (!people.has(key)) people.set(key, { name: order.person, orders: [], drinks: 0 });
    const group = people.get(key);
    group.orders.push(order);
    group.drinks += Number(order.quantity || 1);
  });

  people.forEach((person) => {
    const cap = Number(session.budget_cap);
    const personSpend = estimatedTotalForPerson(person.name);
    const personOverBudget = Number.isFinite(cap) && cap > 0 && personSpend > cap + 0.0001;
    const section = node('section', `person-group${personOverBudget ? ' over-budget' : ''}`);
    const heading = node('div', 'person-heading');
    const personCost = costByPerson.get(normalized(person.name));
    const totalLabel = personCost
      ? `${plural(person.drinks, 'drink')} · ${money(personCost.total)}`
      : plural(person.drinks, 'drink');
    const name = node('h3', null, person.name);
    if (personOverBudget) {
      name.append(document.createTextNode(' '), node('span', 'duplicate-badge budget-over-badge', 'Over budget'));
    }
    heading.append(name, node('span', 'person-total', totalLabel));
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
      const lineTotal = order.actual_total == null ? order.estimated_total : order.actual_total;
      if (lineTotal != null) {
        title.append(node('span', 'order-line-price', money(lineTotal)));
      }
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

function costShareText() {
  const costs = session && session.costs;
  if (!costs || !costs.by_person || !costs.by_person.length) return '';
  return [session.title, ...costs.by_person.map((entry) => `${entry.person}: ${money(entry.total)}`),
    `Group total: ${money(costs.total)}`,
    costs.estimated ? 'Estimate before tax, tip, and fees.' : 'Shared costs split proportionally.'].join('\n');
}

function renderCosts() {
  const costs = session && session.costs;
  elements.costBreakdown.textContent = '';
  if (!costs || !costs.by_person || !costs.by_person.length) {
    elements.costBreakdown.append(node('p', 'muted', 'Add a drink to start the estimate.'));
    elements.copyCosts.disabled = true;
    return;
  }
  const list = node('ul', 'cost-split-list');
  costs.by_person.forEach((entry) => {
    const item = node('li');
    const name = node('span');
    name.append(document.createTextNode(entry.person), node('small', null, plural(entry.drinks, 'drink')));
    item.append(name, node('strong', null, money(entry.total)));
    list.append(item);
  });
  const total = node('div', 'cost-group-total');
  total.append(node('span', null, costs.estimated ? 'Estimated group total' : 'Group total'),
    node('strong', null, money(costs.total)));
  elements.costBreakdown.append(list, total);
  elements.copyCosts.disabled = false;
  const missing = Number(costs.unpriced_drinks || 0);
  elements.costNote.textContent = missing
    ? `${plural(missing, 'drink')} could not be priced and are not included.`
    : costs.estimated
      ? 'Captured menu estimate before tax, tip, and fees. The built cart replaces it with live totals.'
      : 'Live cart total. Tax and fees are split proportionally by drink subtotal.';
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
  renderDeadline(true);
  renderBudget(true);
  renderOrders();
  renderDrinkTotals();
  renderCosts();
}

function applyPublicSession(updated) {
  const privateState = session && {
    finalized_at: session.finalized_at,
    preview_url: session.preview_url,
  };
  setSession({ ...updated, ...(privateState || {}) });
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
    setSession(data.session);
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

async function updateDeadline(value) {
  const deadline = value instanceof Date ? value : new Date(value);
  const serverNow = Date.now() + serverOffsetMs;
  if (Number.isNaN(deadline.getTime()) || deadline.getTime() <= serverNow) {
    setStatus(elements.deadlineStatus, 'Choose a future deadline.', 'err');
    elements.deadlineInput.focus();
    return;
  }
  const wasPassed = Boolean(session && session.deadline_passed);
  elements.saveDeadline.disabled = true;
  elements.deadlineQuickActions.forEach((button) => { button.disabled = true; });
  setStatus(elements.deadlineStatus, 'Saving deadline…', 'busy');
  try {
    const data = await request(`${apiBase}/deadline`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ deadline_at: deadline.toISOString() }),
    });
    applyPublicSession(data.session);
    const message = wasPassed && data.session.accepting_orders
      ? 'Deadline extended. Participants can submit again.'
      : data.session.status === 'locked'
        ? 'Deadline saved. Submissions remain manually paused.'
        : 'Deadline updated.';
    setStatus(elements.deadlineStatus, message, 'ok');
  } catch (error) {
    setStatus(elements.deadlineStatus, error.message, 'err');
  } finally {
    renderDeadline(false);
  }
}

function extendDeadline(minutes) {
  if (!session || session.status === 'closed') return;
  const now = Date.now() + serverOffsetMs;
  const currentDeadline = new Date(session.deadline_at || session.expires_at).getTime();
  const base = Number.isFinite(currentDeadline) && currentDeadline > now ? currentDeadline : now;
  updateDeadline(new Date(base + minutes * 60 * 1000));
}

async function updateBudget(value) {
  const raw = value == null ? '' : String(value).trim();
  const cap = raw === '' ? null : Number(raw);
  if (cap !== null && (!Number.isFinite(cap) || cap < 0.01 || cap > 1000)) {
    setStatus(elements.budgetStatus, 'Choose a budget from $0.01 to $1,000.00, or remove the cap.', 'err');
    elements.budgetInput.focus();
    return;
  }
  elements.saveBudget.disabled = true;
  elements.clearBudget.disabled = true;
  setStatus(elements.budgetStatus, cap === null ? 'Removing budget cap…' : 'Saving budget cap…', 'busy');
  try {
    const data = await request(`${apiBase}/budget`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ budget_cap: cap }),
    });
    applyPublicSession(data.session);
    const over = overBudgetPeople();
    const message = cap === null
      ? 'Per-person budget removed.'
      : over.length
        ? `Budget saved. ${plural(over.length, 'person', 'people')} are over it; their submitted drinks were kept.`
        : `Budget saved at ${money(data.session.budget_cap)} per person.`;
    setStatus(elements.budgetStatus, message, 'ok');
  } catch (error) {
    setStatus(elements.budgetStatus, error.message, 'err');
  } finally {
    renderBudget(false);
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
elements.openQr.addEventListener('click', showQrDialog);
elements.showQr.addEventListener('click', showQrDialog);
elements.closeQr.addEventListener('click', closeQrDialog);
elements.closeQrButton.addEventListener('click', closeQrDialog);
elements.downloadQr.addEventListener('click', downloadQrCode);
elements.downloadQrDialog.addEventListener('click', downloadQrCode);
elements.copyCosts.addEventListener('click', async () => {
  const text = costShareText();
  if (!text) return;
  try {
    await window.navigator.clipboard.writeText(text);
    elements.copyCosts.textContent = 'Copied!';
    window.setTimeout(() => { elements.copyCosts.textContent = 'Copy'; }, 1800);
  } catch (_error) {
    window.prompt('Copy this payment breakdown:', text);
  }
});
elements.refresh.addEventListener('click', () => refreshSession({ announce: true }));
elements.toggleLock.addEventListener('click', changeStatus);
elements.deadlineForm.addEventListener('submit', (event) => {
  event.preventDefault();
  updateDeadline(elements.deadlineInput.value);
});
elements.deadlineQuickActions.forEach((button) => {
  button.addEventListener('click', () => extendDeadline(Number(button.dataset.extendMinutes)));
});
elements.budgetForm.addEventListener('submit', (event) => {
  event.preventDefault();
  updateBudget(elements.budgetInput.value);
});
elements.budgetInput.addEventListener('blur', () => {
  const amount = Number(elements.budgetInput.value);
  if (elements.budgetInput.value.trim() && Number.isFinite(amount)
      && amount >= 0.01 && amount <= 1000) {
    elements.budgetInput.value = amount.toFixed(2);
  }
});
elements.clearBudget.addEventListener('click', () => updateBudget(null));
elements.finalize.addEventListener('click', finalizeOrder);
elements.continuePreview.addEventListener('click', () => {
  if (session && session.preview_url) window.location.assign(session.preview_url);
});
elements.forgetAccess.addEventListener('click', () => {
  closeQrDialog();
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
window.setInterval(tickDeadline, 1000);
