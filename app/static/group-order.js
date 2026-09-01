/* Participant-facing group order entry.  The menu is canonical, so every
   submitted modifier is a value offered by the selected drink. */

const roomId = window.location.pathname.split('/').filter(Boolean).pop();
const apiBase = `/api/group-orders/${encodeURIComponent(roomId)}`;
const storageKey = `boba-builder:group-order:${roomId}:orders`;
const nameKey = `boba-builder:group-order:${roomId}:name`;
const MAX_VISIBLE_DRINKS = 40;

const elements = {
  roomTitle: document.getElementById('room-title'),
  roomSubtitle: document.getElementById('room-subtitle'),
  roomStrip: document.getElementById('room-strip'),
  orderCard: document.getElementById('order-card'),
  form: document.getElementById('order-form'),
  formTitle: document.getElementById('form-title'),
  person: document.getElementById('person-name'),
  picker: document.getElementById('drink-picker'),
  search: document.getElementById('drink-search'),
  menuCount: document.getElementById('menu-count'),
  categories: document.getElementById('category-list'),
  drinkList: document.getElementById('drink-list'),
  menuMore: document.getElementById('menu-more'),
  editor: document.getElementById('drink-editor'),
  selectedName: document.getElementById('selected-name'),
  selectedDescription: document.getElementById('selected-description'),
  selectedPrice: document.getElementById('selected-price'),
  changeDrink: document.getElementById('change-drink'),
  modifiers: document.getElementById('modifier-groups'),
  quantity: document.getElementById('quantity'),
  notes: document.getElementById('notes'),
  total: document.getElementById('estimated-total'),
  submit: document.getElementById('submit-drink'),
  cancelEdit: document.getElementById('cancel-edit'),
  formStatus: document.getElementById('form-status'),
  groupOrders: document.getElementById('group-orders'),
  groupOrderCount: document.getElementById('group-order-count'),
  refreshOrders: document.getElementById('refresh-orders'),
};

let session = null;
let menu = null;
let activeCategory = '';
let selectedItem = null;
let selections = {};
let editingOrderId = null;
let refreshing = false;
let ownedOrders = readStorage(storageKey, {});

function readStorage(key, fallback) {
  try {
    const value = window.localStorage.getItem(key);
    return value ? JSON.parse(value) : fallback;
  } catch (_error) {
    return fallback;
  }
}

function writeStorage(key, value) {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch (_error) {
    // Private browsing and strict storage settings must not block ordering.
  }
}

function money(value) {
  return `$${Number(value || 0).toFixed(2)}`;
}

function plural(count, one, many) {
  return `${count} ${count === 1 ? one : (many || `${one}s`)}`;
}

function node(tag, className, text) {
  const result = document.createElement(tag);
  if (className) result.className = className;
  if (text !== undefined) result.textContent = text;
  return result;
}

async function request(url, options) {
  const response = await fetch(url, options);
  let data;
  try {
    data = await response.json();
  } catch (_error) {
    data = {};
  }
  if (!response.ok || !data.ok) {
    throw new Error(data.error || 'Something went wrong. Please try again.');
  }
  return data;
}

function setFormStatus(message, kind) {
  elements.formStatus.className = `status${kind ? ` ${kind}` : ''}`;
  elements.formStatus.textContent = message || '';
}

function formatDeadline(timestamp) {
  if (!timestamp) return '';
  const value = new Date(timestamp);
  if (Number.isNaN(value.getTime())) return '';
  return new Intl.DateTimeFormat(undefined, {
    weekday: 'short', hour: 'numeric', minute: '2-digit',
  }).format(value);
}

function renderRoom() {
  if (!session) return;
  document.title = `${session.title} — Boba Builder`;
  elements.roomTitle.textContent = session.title;
  const host = session.organizer_name ? `Hosted by ${session.organizer_name}` : 'Shared boba order';
  elements.roomSubtitle.textContent = menu && menu.store ? `${host} · ${menu.store}` : host;

  elements.roomStrip.className = `room-strip ${session.status}`;
  elements.roomStrip.textContent = '';
  elements.roomStrip.append(node('span', 'status-dot'));
  const summary = node('span');
  if (session.accepting_orders) {
    const deadline = formatDeadline(session.expires_at);
    summary.append(node('strong', null, 'Open for drinks'));
    summary.append(document.createTextNode(
      ` · ${plural(session.summary.drinks, 'drink')} from ${plural(session.summary.people, 'person', 'people')}`
      + (deadline ? ` · closes ${deadline}` : '')
    ));
  } else {
    const labels = { locked: 'temporarily locked', closed: 'closed', expired: 'expired' };
    summary.append(node('strong', null, `This order is ${labels[session.status] || session.status}`));
    summary.append(document.createTextNode(' · no changes can be made right now'));
  }
  elements.roomStrip.append(summary);
  elements.orderCard.classList.toggle('hidden', !session.accepting_orders);
  if (!session.accepting_orders && editingOrderId) resetEditor();
}

function renderCategories() {
  elements.categories.textContent = '';
  const choices = [{ name: '', label: 'All' }].concat(
    (menu.categories || []).map((name) => ({ name, label: name }))
  );
  choices.forEach((choice) => {
    const button = node('button', `category-chip${activeCategory === choice.name ? ' active' : ''}`, choice.label);
    button.type = 'button';
    button.setAttribute('aria-pressed', activeCategory === choice.name ? 'true' : 'false');
    button.addEventListener('click', () => {
      activeCategory = choice.name;
      renderCategories();
      renderDrinkList();
    });
    elements.categories.append(button);
  });
}

function normalized(value) {
  return String(value || '').toLocaleLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
}

function renderDrinkList() {
  if (!menu) return;
  const query = normalized(elements.search.value);
  const words = query.split(' ').filter(Boolean);
  const matches = menu.items.filter((item) => {
    if (activeCategory && item.category !== activeCategory) return false;
    const haystack = normalized(`${item.name} ${item.category} ${item.description}`);
    return words.every((word) => haystack.includes(word));
  });
  const visible = matches.slice(0, MAX_VISIBLE_DRINKS);
  elements.drinkList.textContent = '';

  if (!visible.length) {
    elements.drinkList.append(node('p', 'empty-menu', 'No drinks match that search.'));
  }
  visible.forEach((item) => {
    const button = node('button', 'drink-choice');
    button.type = 'button';
    button.setAttribute('aria-label', `Choose ${item.name}, ${money(item.price)}`);
    const wordsBox = node('span');
    wordsBox.append(node('span', 'drink-choice-name', item.name));
    wordsBox.append(node('span', 'drink-choice-category', item.category));
    button.append(wordsBox, node('span', 'drink-choice-price', money(item.price)));
    button.addEventListener('click', () => chooseDrink(item));
    elements.drinkList.append(button);
  });

  elements.menuCount.textContent = plural(matches.length, 'drink');
  elements.menuMore.textContent = matches.length > visible.length
    ? `Showing ${visible.length}. Search to narrow the menu.` : '';
}

function initialSelection(group) {
  if (group.multiselect) return [];
  const marked = group.options.find((option) => option.is_default);
  if (group.required && marked) return marked.label;
  if (group.required && group.options.length === 1) return group.options[0].label;
  return '';
}

function chooseDrink(item, existing) {
  selectedItem = item;
  selections = {};
  item.option_groups.forEach((group) => {
    selections[group.key] = initialSelection(group);
  });
  if (existing) fillExistingSelections(existing);

  elements.picker.classList.add('hidden');
  elements.editor.classList.remove('hidden');
  elements.selectedName.textContent = item.name;
  elements.selectedDescription.textContent = item.description || '';
  elements.selectedPrice.textContent = money(item.price);
  renderModifiers();
  updateEstimate();
  setFormStatus('', '');
  elements.formTitle.textContent = editingOrderId ? 'Edit your drink' : 'Make it yours';
}

function fillExistingSelections(order) {
  const remainingToppings = (order.toppings || []).slice();
  selectedItem.option_groups.filter((group) => group.axis !== 'toppings').forEach((group) => {
    const offered = group.options.map((option) => option.label);
    const value = order[group.axis] || '';
    selections[group.key] = offered.includes(value) ? value : '';
  });
  const toppingGroups = selectedItem.option_groups.filter((group) => group.axis === 'toppings');
  toppingGroups.filter((group) => !group.multiselect).forEach((group) => {
    const offered = group.options.map((option) => option.label);
    const matchIndex = remainingToppings.findIndex((name) => offered.includes(name));
    selections[group.key] = matchIndex >= 0 ? remainingToppings.splice(matchIndex, 1)[0] : '';
  });
  toppingGroups.filter((group) => group.multiselect).forEach((group) => {
    const offered = group.options.map((option) => option.label);
    if (group.multiselect) {
      const picked = [];
      for (let index = remainingToppings.length - 1; index >= 0; index -= 1) {
        if (offered.includes(remainingToppings[index])) {
          picked.unshift(remainingToppings[index]);
          remainingToppings.splice(index, 1);
        }
      }
      selections[group.key] = picked;
    }
  });
}

function optionPriceText(option) {
  return Number(option.price || 0) ? `+${money(option.price)}` : '';
}

function renderModifiers() {
  elements.modifiers.textContent = '';
  selectedItem.option_groups.forEach((group) => {
    const fieldset = node('fieldset', 'modifier-group');
    fieldset.dataset.groupKey = group.key;
    const legend = node('legend', null, group.name);
    if (group.required) {
      legend.append(document.createTextNode(' '));
      legend.append(node('span', 'required-mark', 'Required'));
    }
    fieldset.append(legend);

    if (group.multiselect) {
      const grid = node('div', 'option-grid');
      group.options.forEach((option) => {
        const label = node('label', 'option-check');
        const input = document.createElement('input');
        input.type = 'checkbox';
        input.value = option.label;
        input.checked = (selections[group.key] || []).includes(option.label);
        input.addEventListener('change', () => {
          const values = Array.from(grid.querySelectorAll('input:checked')).map((control) => control.value);
          selections[group.key] = values;
          enforceMaximum(group, grid);
          clearGroupError(fieldset);
          updateEstimate();
        });
        label.append(input, node('span', 'option-name', option.label));
        const price = optionPriceText(option);
        if (price) label.append(node('span', 'option-price', price));
        grid.append(label);
      });
      fieldset.append(grid);
      enforceMaximum(group, grid);
    } else {
      const select = node('select', 'modifier-select');
      select.setAttribute('aria-label', group.name);
      if (group.required) select.required = true;
      const blank = document.createElement('option');
      blank.value = '';
      blank.textContent = group.required ? 'Choose one…' : 'Store default / none';
      select.append(blank);
      group.options.forEach((option) => {
        const choice = document.createElement('option');
        choice.value = option.label;
        const extra = optionPriceText(option);
        choice.textContent = `${option.label}${extra ? ` · ${extra}` : ''}`;
        choice.selected = selections[group.key] === option.label;
        select.append(choice);
      });
      select.addEventListener('change', () => {
        selections[group.key] = select.value;
        clearGroupError(fieldset);
        updateEstimate();
      });
      fieldset.append(select);
    }
    fieldset.append(node('p', 'group-error'));
    elements.modifiers.append(fieldset);
  });
}

function enforceMaximum(group, grid) {
  if (!group.max) return;
  const checked = grid.querySelectorAll('input:checked').length;
  grid.querySelectorAll('input').forEach((control) => {
    control.disabled = !control.checked && checked >= group.max;
  });
}

function clearGroupError(fieldset) {
  const error = fieldset.querySelector('.group-error');
  if (error) error.textContent = '';
}

function selectedOption(group, label) {
  return group.options.find((option) => option.label === label);
}

function updateEstimate() {
  if (!selectedItem) {
    elements.total.textContent = '—';
    return;
  }
  let price = Number(selectedItem.price || 0);
  selectedItem.option_groups.forEach((group) => {
    const values = Array.isArray(selections[group.key])
      ? selections[group.key] : [selections[group.key]].filter(Boolean);
    values.forEach((value) => {
      const option = selectedOption(group, value);
      price += Number((option && option.price) || 0);
    });
  });
  const quantity = Math.max(1, Math.min(20, Number(elements.quantity.value) || 1));
  elements.total.textContent = money(price * quantity);
}

function validateSelections() {
  let valid = true;
  selectedItem.option_groups.forEach((group) => {
    const fieldset = elements.modifiers.querySelector(`[data-group-key="${group.key}"]`);
    const error = fieldset && fieldset.querySelector('.group-error');
    const values = Array.isArray(selections[group.key])
      ? selections[group.key] : [selections[group.key]].filter(Boolean);
    const minimum = Math.max(Number(group.min || 0), group.required ? 1 : 0);
    if (values.length < minimum) {
      if (error) error.textContent = minimum === 1 ? 'Choose one option.' : `Choose at least ${minimum}.`;
      valid = false;
    } else if (group.max && values.length > group.max) {
      if (error) error.textContent = `Choose no more than ${group.max}.`;
      valid = false;
    } else if (error) {
      error.textContent = '';
    }
  });
  if (!valid) {
    const first = elements.modifiers.querySelector('.group-error:not(:empty)');
    if (first) first.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }
  return valid;
}

function buildPayload() {
  const payload = {
    person: elements.person.value.trim(),
    drink: selectedItem.name,
    size: '', sugar: '', ice: '', milk: '', temperature: '', toppings: [],
    quantity: Number(elements.quantity.value),
    notes: elements.notes.value.trim(),
  };
  selectedItem.option_groups.forEach((group) => {
    const value = selections[group.key];
    if (group.axis === 'toppings') {
      if (Array.isArray(value)) payload.toppings.push(...value);
      else if (value) payload.toppings.push(value);
    } else if (value) {
      payload[group.axis] = value;
    }
  });
  return payload;
}

function rememberOrder(orderId, token) {
  ownedOrders[orderId] = { token };
  writeStorage(storageKey, ownedOrders);
}

function forgetOrder(orderId) {
  delete ownedOrders[orderId];
  writeStorage(storageKey, ownedOrders);
}

function resetEditor(options) {
  const keepStatus = options && options.keepStatus;
  editingOrderId = null;
  selectedItem = null;
  selections = {};
  elements.quantity.value = '1';
  elements.notes.value = '';
  elements.editor.classList.add('hidden');
  elements.picker.classList.remove('hidden');
  elements.cancelEdit.classList.add('hidden');
  elements.submit.textContent = 'Add my drink';
  elements.formTitle.textContent = 'Choose something good';
  elements.search.value = '';
  if (!keepStatus) setFormStatus('', '');
  renderDrinkList();
}

function orderDetails(order) {
  const parts = [order.size, order.sugar, order.ice, order.milk].filter(Boolean);
  if (order.toppings && order.toppings.length) parts.push(order.toppings.join(', '));
  if (order.notes) parts.push(order.notes);
  return parts.length ? parts.join(' · ') : 'Store defaults';
}

function renderGroupOrders() {
  if (!session) return;
  elements.groupOrderCount.textContent = String(session.summary.drinks);
  elements.groupOrderCount.setAttribute('aria-label', plural(session.summary.drinks, 'drink'));
  elements.groupOrders.textContent = '';
  if (!session.orders.length) {
    elements.groupOrders.append(node('p', 'muted', 'No drinks yet. Be the first to add one!'));
    return;
  }

  const list = node('div', 'group-order-list');
  session.orders.forEach((order) => {
    const ownership = ownedOrders[order.id];
    const card = node('article', `group-order${ownership ? ' own-order' : ''}`);
    const copy = node('div');
    const person = node('p', 'order-person', order.person);
    if (ownership) {
      person.append(document.createTextNode(' '));
      person.append(node('span', 'yours-badge', 'Yours'));
    }
    copy.append(person);
    copy.append(node('h3', null, order.drink));
    copy.append(node('p', 'group-order-detail', orderDetails(order)));
    card.append(copy, node('span', 'group-order-qty', order.quantity > 1 ? `×${order.quantity}` : ''));
    if (ownership && session.accepting_orders) {
      const actions = node('div', 'group-order-actions');
      const edit = node('button', 'text-button', 'Edit');
      edit.type = 'button';
      edit.addEventListener('click', () => editOrder(order));
      const remove = node('button', 'text-button delete-order', 'Remove');
      remove.type = 'button';
      remove.addEventListener('click', () => deleteOrder(order));
      actions.append(edit, remove);
      card.append(actions);
    }
    list.append(card);
  });
  elements.groupOrders.append(list);
}

function editOrder(order) {
  const item = menu.items.find((candidate) => candidate.name === order.drink);
  if (!item) {
    setFormStatus('That drink is no longer on this captured menu, so it cannot be edited here.', 'err');
    return;
  }
  editingOrderId = order.id;
  elements.person.value = order.person;
  elements.quantity.value = String(order.quantity || 1);
  elements.notes.value = order.notes || '';
  elements.cancelEdit.classList.remove('hidden');
  elements.submit.textContent = 'Save changes';
  chooseDrink(item, order);
  elements.orderCard.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

async function deleteOrder(order) {
  if (!window.confirm(`Remove ${order.drink} from the group order?`)) return;
  const ownership = ownedOrders[order.id];
  if (!ownership) return;
  try {
    const data = await request(`${apiBase}/orders/${encodeURIComponent(order.id)}`, {
      method: 'DELETE',
      headers: { 'X-Order-Token': ownership.token },
    });
    session = data.session;
    forgetOrder(order.id);
    if (editingOrderId === order.id) resetEditor();
    renderRoom();
    renderGroupOrders();
  } catch (error) {
    window.alert(error.message);
  }
}

async function submitOrder(event) {
  event.preventDefault();
  if (!selectedItem) return;
  if (!elements.form.reportValidity() || !validateSelections()) return;

  elements.submit.disabled = true;
  setFormStatus(editingOrderId ? 'Saving your changes…' : 'Adding your drink…', 'busy');
  const payload = buildPayload();
  const orderId = editingOrderId;
  const ownership = orderId && ownedOrders[orderId];
  try {
    const data = await request(orderId ? `${apiBase}/orders/${encodeURIComponent(orderId)}` : `${apiBase}/orders`, {
      method: orderId ? 'PATCH' : 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(ownership ? { 'X-Order-Token': ownership.token } : {}),
      },
      body: JSON.stringify(payload),
    });
    session = data.session;
    if (!orderId) rememberOrder(data.order.id, data.order_token);
    writeStorage(nameKey, elements.person.value.trim());
    resetEditor({ keepStatus: true });
    setFormStatus(orderId ? 'Your drink was updated.' : 'Drink added. Pick another if you would like!', 'ok');
    renderRoom();
    renderGroupOrders();
    elements.search.focus();
  } catch (error) {
    setFormStatus(error.message, 'err');
  } finally {
    elements.submit.disabled = false;
  }
}

async function refreshSession(showError) {
  if (refreshing) return;
  refreshing = true;
  const original = elements.refreshOrders.textContent;
  elements.refreshOrders.disabled = true;
  elements.refreshOrders.textContent = 'Refreshing…';
  try {
    const data = await request(apiBase);
    session = data.session;
    renderRoom();
    renderGroupOrders();
  } catch (error) {
    if (showError) window.alert(error.message);
  } finally {
    refreshing = false;
    elements.refreshOrders.disabled = false;
    elements.refreshOrders.textContent = original;
  }
}

function showFatal(message) {
  elements.roomStrip.className = 'room-strip error';
  elements.roomStrip.textContent = '';
  elements.roomStrip.append(node('span', 'status-dot'));
  elements.roomStrip.append(node('strong', null, message));
  elements.orderCard.classList.add('hidden');
  document.getElementById('group-orders-card').classList.add('hidden');
  elements.roomTitle.textContent = 'Group order unavailable';
  elements.roomSubtitle.textContent = 'Ask the organizer for a fresh link.';
}

elements.search.addEventListener('input', renderDrinkList);
elements.changeDrink.addEventListener('click', () => {
  selectedItem = null;
  selections = {};
  elements.editor.classList.add('hidden');
  elements.picker.classList.remove('hidden');
  setFormStatus('', '');
  elements.search.focus();
});
elements.cancelEdit.addEventListener('click', resetEditor);
elements.quantity.addEventListener('input', updateEstimate);
elements.form.addEventListener('submit', submitOrder);
elements.refreshOrders.addEventListener('click', () => refreshSession(true));

Promise.all([request(apiBase), request('/api/menu')])
  .then(([roomData, menuData]) => {
    session = roomData.session;
    menu = menuData.menu;
    if (!menu || !menu.items || !menu.items.length) {
      throw new Error('The store menu is unavailable right now.');
    }
    const savedName = readStorage(nameKey, '');
    if (typeof savedName === 'string') elements.person.value = savedName;
    renderRoom();
    renderCategories();
    renderDrinkList();
    renderGroupOrders();
    window.setInterval(() => {
      if (!document.hidden) refreshSession(false);
    }, 15000);
  })
  .catch((error) => showFatal(error.message));
