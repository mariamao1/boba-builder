/* Participant-facing group order entry.  The menu is canonical, so every
   submitted modifier is a value offered by the selected drink. */

const roomId = window.location.pathname.split('/').filter(Boolean).pop();
const apiBase = `/api/group-orders/${encodeURIComponent(roomId)}`;
const storageKey = `boba-builder:group-order:${roomId}:orders`;
const nameKey = `boba-builder:group-order:${roomId}:name`;
const participantNameKey = 'boba-builder:participant-name';
const MAX_VISIBLE_DRINKS = 40;

const elements = {
  roomTitle: document.getElementById('room-title'),
  roomSubtitle: document.getElementById('room-subtitle'),
  roomStrip: document.getElementById('room-strip'),
  statusBoard: document.getElementById('status-board'),
  statusSteps: document.getElementById('status-steps'),
  statusCurrent: document.getElementById('status-current'),
  statusHint: document.getElementById('status-hint'),
  statusNote: document.getElementById('status-note'),
  statusUpdated: document.getElementById('status-updated'),
  deadlineCard: document.getElementById('deadline-card'),
  deadlineCountdown: document.getElementById('deadline-countdown'),
  deadlineDetail: document.getElementById('deadline-detail'),
  budgetCard: document.getElementById('budget-card'),
  budgetLimit: document.getElementById('budget-limit'),
  budgetHint: document.getElementById('budget-hint'),
  orderCard: document.getElementById('order-card'),
  form: document.getElementById('order-form'),
  formTitle: document.getElementById('form-title'),
  person: document.getElementById('person-name'),
  favoritesPanel: document.getElementById('favorites-panel'),
  favoritesList: document.getElementById('favorites-list'),
  favoritesCount: document.getElementById('favorites-count'),
  favoriteStatus: document.getElementById('favorite-status'),
  popularPicks: document.getElementById('popular-picks'),
  popularPicksList: document.getElementById('popular-picks-list'),
  picker: document.getElementById('drink-picker'),
  search: document.getElementById('drink-search'),
  menuCount: document.getElementById('menu-count'),
  categories: document.getElementById('category-list'),
  drinkList: document.getElementById('drink-list'),
  menuMore: document.getElementById('menu-more'),
  surpriseMe: document.getElementById('surprise-me'),
  surpriseHint: document.getElementById('surprise-hint'),
  editor: document.getElementById('drink-editor'),
  selectedName: document.getElementById('selected-name'),
  selectedDescription: document.getElementById('selected-description'),
  selectedOptions: document.getElementById('selected-options'),
  selectedPrice: document.getElementById('selected-price'),
  rerollDrink: document.getElementById('reroll-drink'),
  editDrink: document.getElementById('edit-drink'),
  removeDrink: document.getElementById('remove-drink'),
  modifiers: document.getElementById('modifier-groups'),
  quantity: document.getElementById('quantity'),
  notes: document.getElementById('notes'),
  favoriteSave: document.getElementById('favorite-save'),
  saveFavorite: document.getElementById('save-favorite'),
  favoriteToggleTitle: document.getElementById('favorite-toggle-title'),
  favoriteToggleHelp: document.getElementById('favorite-toggle-help'),
  favoriteNameField: document.getElementById('favorite-name-field'),
  favoriteName: document.getElementById('favorite-name'),
  total: document.getElementById('estimated-total'),
  budgetSelection: document.getElementById('budget-selection'),
  submit: document.getElementById('submit-drink'),
  cancelEdit: document.getElementById('cancel-edit'),
  formStatus: document.getElementById('form-status'),
  groupOrders: document.getElementById('group-orders'),
  groupOrderCount: document.getElementById('group-order-count'),
  refreshOrders: document.getElementById('refresh-orders'),
  suggestionsCard: document.getElementById('suggestions-card'),
  suggestionsList: document.getElementById('suggestions-list'),
};

let session = null;
let menu = null;
let leaderboard = null;
let activeCategory = '';
let selectedItem = null;
let selections = {};
let editingOrderId = null;
let editingFavoriteId = null;
let confirmingSuggestionId = null;
let addingFavoriteId = null;
let refreshing = false;
let ownedOrders = readStorage(storageKey, {});
let serverOffsetMs = 0;
let deadlineTransitionHandled = false;
let budgetBlocked = false;

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
    const error = new Error(data.error || 'Something went wrong. Please try again.');
    error.status = response.status;
    error.code = data.code;
    throw error;
  }
  return data;
}

function setFormStatus(message, kind) {
  elements.formStatus.className = `status${kind ? ` ${kind}` : ''}`;
  elements.formStatus.textContent = message || '';
}

function setFavoriteStatus(message, kind) {
  elements.favoriteStatus.className = `favorite-status${kind ? ` ${kind}` : ''}`;
  elements.favoriteStatus.textContent = message || '';
}

function formatDeadline(timestamp) {
  if (!timestamp) return '';
  const value = new Date(timestamp);
  if (Number.isNaN(value.getTime())) return '';
  return new Intl.DateTimeFormat(undefined, {
    weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit',
  }).format(value);
}

const FULFILLMENT_STAGES = [
  ['collecting', 'Collecting'],
  ['ordered', 'Ordered'],
  ['ready', 'Ready'],
  ['picked_up', 'Picked up'],
  ['distributed', 'Handed out'],
];

function renderFulfillment() {
  const board = session && session.fulfillment;
  if (!board) return;
  const active = Math.max(0, FULFILLMENT_STAGES.findIndex(([status]) => status === board.status));
  elements.statusSteps.textContent = '';
  FULFILLMENT_STAGES.forEach(([status, label], index) => {
    const item = node('li', index < active ? 'done' : index === active ? 'current' : '');
    if (index === active) item.setAttribute('aria-current', 'step');
    item.append(node('span', 'status-step-marker', index < active ? '✓' : String(index + 1)));
    item.append(node('span', 'status-step-label', label));
    elements.statusSteps.append(item);
  });
  elements.statusCurrent.textContent = board.label || 'Collecting drinks';
  elements.statusHint.textContent = board.hint || '';
  elements.statusNote.hidden = !board.note;
  elements.statusNote.textContent = board.note ? `Organizer note: ${board.note}` : '';
  elements.statusUpdated.textContent = board.updated_at
    ? `Updated ${formatDeadline(board.updated_at)}` : 'No pickup update yet';
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

function renderDeadline() {
  if (!session) return;
  const exact = formatDeadline(session.deadline_at || session.expires_at);
  const passed = session.deadline_passed || deadlineRemaining() <= 0;
  if (session.status === 'closed') {
    elements.deadlineCard.className = 'deadline-card closed';
    elements.deadlineCountdown.textContent = 'Order closed';
    elements.deadlineDetail.textContent = exact ? `The cutoff was ${exact}. Submitted drinks are read-only.` : 'Submitted drinks are read-only.';
  } else if (passed) {
    elements.deadlineCard.className = 'deadline-card closed';
    elements.deadlineCountdown.textContent = 'Deadline passed';
    elements.deadlineDetail.textContent = 'New drinks and edits are locked. Ask the organizer if you arrived late.';
  } else if (session.status === 'locked') {
    elements.deadlineCard.className = 'deadline-card paused';
    elements.deadlineCountdown.textContent = 'Submissions paused';
    elements.deadlineDetail.textContent = `${countdownText(deadlineRemaining())} · cutoff ${exact}`;
  } else {
    elements.deadlineCard.className = 'deadline-card';
    elements.deadlineCountdown.textContent = countdownText(deadlineRemaining());
    elements.deadlineDetail.textContent = `Submit by ${exact}. The order locks automatically.`;
  }
}

function tickDeadline() {
  if (!session) return;
  renderDeadline();
  if (session.accepting_orders && deadlineRemaining() <= 0 && !deadlineTransitionHandled) {
    deadlineTransitionHandled = true;
    session.accepting_orders = false;
    session.deadline_passed = true;
    session.status = 'locked';
    session.lock_reason = 'deadline';
    renderRoom();
    renderGroupOrders();
    refreshSession(false);
  }
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
    summary.append(node('strong', null, 'Open for drinks'));
    summary.append(document.createTextNode(
      ` · ${plural(session.summary.drinks, 'drink')} from ${plural(session.summary.people, 'person', 'people')}`
    ));
  } else {
    const labels = {
      locked: session.lock_reason === 'deadline' ? 'closed for new drinks' : 'temporarily paused',
      closed: 'finalized', expired: 'expired',
    };
    summary.append(node('strong', null, `This order is ${labels[session.status] || session.status}`));
    summary.append(document.createTextNode(
      session.lock_reason === 'deadline'
        ? ' · the deadline has passed'
        : ' · no participant changes can be made right now'));
  }
  elements.roomStrip.append(summary);
  elements.orderCard.classList.toggle('hidden', !session.accepting_orders);
  if (!session.accepting_orders && (editingOrderId || confirmingSuggestionId)) resetEditor();
  renderDeadline();
  renderBudget();
  renderFulfillment();
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
  renderSurpriseControls();
}

function renderSurpriseControls() {
  const randomizer = window.BobaRandomizer;
  const choices = menu && randomizer
    ? randomizer.eligibleItems(menu, activeCategory) : [];
  elements.surpriseMe.disabled = !choices.length;
  if (!menu) {
    elements.surpriseHint.textContent = 'Picking an orderable drink and options…';
  } else if (!choices.length) {
    elements.surpriseHint.textContent = 'Surprise Me is for drinks—choose All or another category.';
  } else if (activeCategory) {
    elements.surpriseHint.textContent = `A random ${activeCategory} drink with valid options, ready to tweak.`;
  } else {
    elements.surpriseHint.textContent = 'A random menu drink with valid options, ready to tweak.';
  }
}

function normalized(value) {
  return String(value || '').toLocaleLowerCase().replace(/[^a-z0-9]+/g, ' ').trim();
}

function personKey(value) {
  return String(value || '').toLocaleLowerCase().replace(/\s+/g, ' ').trim();
}

function submittedTotal(person, excludeOrderId) {
  if (!session) return 0;
  const key = personKey(person);
  if (!key) return 0;
  return session.orders.reduce((total, order) => {
    if (order.id === excludeOrderId || personKey(order.person) !== key
        || order.estimated_total == null) return total;
    return total + Number(order.estimated_total || 0);
  }, 0);
}

function selectedUnitPrice() {
  if (!selectedItem) return null;
  let price = Number(selectedItem.price || 0);
  selectedItem.option_groups.forEach((group) => {
    const values = Array.isArray(selections[group.key])
      ? selections[group.key] : [selections[group.key]].filter(Boolean);
    values.forEach((value) => {
      const option = selectedOption(group, value);
      price += Number((option && option.price) || 0);
    });
  });
  return price;
}

function selectedEstimate() {
  const unitPrice = selectedUnitPrice();
  if (unitPrice == null) return null;
  const quantity = Math.max(1, Math.min(20, Number(elements.quantity.value) || 1));
  return unitPrice * quantity;
}

function budgetState(person, draftTotal, orderId) {
  const cap = Number(session && session.budget_cap);
  if (!Number.isFinite(cap) || cap <= 0 || draftTotal == null) return null;
  const previous = submittedTotal(person, null);
  const candidate = submittedTotal(person, orderId) + draftTotal;
  const grandfathered = Boolean(orderId) && candidate > cap + 0.0001
    && candidate <= previous + 0.0001;
  return {
    cap, previous, candidate, grandfathered,
    blocked: candidate > cap + 0.0001 && !grandfathered,
  };
}

function renderBudget() {
  const cap = Number(session && session.budget_cap);
  const hasCap = Number.isFinite(cap) && cap > 0;
  elements.budgetCard.classList.toggle('hidden', !hasCap);
  if (!hasCap) {
    budgetBlocked = false;
    elements.budgetSelection.classList.add('hidden');
    return;
  }
  elements.budgetLimit.textContent = `${money(cap)} per person`;
  const person = elements.person.value.trim();
  const spent = submittedTotal(person, null);
  if (person && spent > cap + 0.0001) {
    elements.budgetHint.textContent = `${person} already has ${money(spent)} submitted. Existing drinks stay, but their priced total cannot increase.`;
  } else if (person && spent > 0) {
    elements.budgetHint.textContent = `${person} has ${money(spent)} submitted and ${money(Math.max(0, cap - spent))} left.`;
  } else {
    elements.budgetHint.textContent = 'This hard limit covers all priced drinks submitted under the same name.';
  }
  renderBudgetSelection();
}

function renderBudgetSelection(total) {
  const draftTotal = total == null ? selectedEstimate() : total;
  const state = editingFavoriteId ? null
    : budgetState(elements.person.value.trim(), draftTotal, editingOrderId);
  if (!state || !selectedItem) {
    budgetBlocked = false;
    elements.budgetSelection.classList.add('hidden');
    elements.submit.disabled = false;
    return;
  }
  elements.budgetSelection.classList.remove('hidden');
  elements.budgetSelection.classList.toggle('over', state.blocked);
  const name = elements.person.value.trim() || 'Your order';
  if (state.blocked) {
    elements.budgetSelection.textContent = `${name}'s total would be ${money(state.candidate)}, over the ${money(state.cap)} limit. Choose a cheaper option or reduce the quantity.`;
  } else if (state.grandfathered) {
    elements.budgetSelection.textContent = `${name}'s existing ${money(state.candidate)} total is over the new limit but is grandfathered. This edit does not increase it.`;
  } else {
    elements.budgetSelection.textContent = `${money(state.candidate)} combined · ${money(Math.max(0, state.cap - state.candidate))} remaining after this selection.`;
  }
  budgetBlocked = state.blocked;
  elements.submit.disabled = budgetBlocked;
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

function chooseDrink(item, existing, chosenSelections) {
  selectedItem = item;
  selections = {};
  item.option_groups.forEach((group) => {
    selections[group.key] = initialSelection(group);
  });
  if (existing) fillExistingSelections(existing);
  if (chosenSelections) selections = chosenSelections;

  elements.picker.classList.add('hidden');
  elements.editor.classList.remove('hidden');
  elements.selectedName.textContent = item.name;
  elements.selectedDescription.textContent = item.description || '';
  elements.selectedPrice.textContent = money(item.price);
  elements.removeDrink.setAttribute('aria-label', `Remove ${item.name}`);
  renderModifiers();
  updateEstimate();
  setFormStatus('', '');
  if (editingFavoriteId) elements.formTitle.textContent = 'Edit your usual';
  else elements.formTitle.textContent = editingOrderId ? 'Edit your drink' : 'Make it yours';
  updateFavoriteSaveControls();
}

function surpriseMe() {
  if (!menu || !window.BobaRandomizer) return;
  const surprise = window.BobaRandomizer.pick(menu, activeCategory);
  if (!surprise) {
    setFormStatus('No orderable drinks are available in this category.', 'err');
    return;
  }
  chooseDrink(surprise.item, null, surprise.selections);
  setFormStatus(`✨ Surprise! We picked ${surprise.item.name}. Tweak anything, re-roll, or add it as-is.`,
    'surprise');
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
    elements.selectedOptions.textContent = '';
    renderBudgetSelection();
    return;
  }
  const summary = [];
  selectedItem.option_groups.forEach((group) => {
    const values = Array.isArray(selections[group.key])
      ? selections[group.key] : [selections[group.key]].filter(Boolean);
    if (values.length) summary.push(`${group.name}: ${values.join(', ')}`);
  });
  elements.selectedOptions.textContent = summary.length ? summary.join(' · ') : 'Store defaults';
  const unitPrice = selectedUnitPrice();
  const quantity = Math.max(1, Math.min(20, Number(elements.quantity.value) || 1));
  elements.selectedPrice.textContent = quantity > 1
    ? `${money(unitPrice)} each` : money(unitPrice);
  const total = selectedEstimate();
  elements.total.textContent = money(total);
  renderBudgetSelection(total);
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

function payloadEstimate(item, payload) {
  let price = Number(item.price || 0);
  const unmatchedToppings = (payload.toppings || []).slice();
  item.option_groups.forEach((group) => {
    let values = [];
    if (group.axis === 'toppings') {
      values = group.options
        .filter((option) => unmatchedToppings.includes(option.label))
        .map((option) => option.label);
      values.forEach((value) => unmatchedToppings.splice(unmatchedToppings.indexOf(value), 1));
    } else if (payload[group.axis]) {
      values = [payload[group.axis]];
    }
    values.forEach((value) => {
      const option = selectedOption(group, value);
      price += Number((option && option.price) || 0);
    });
  });
  return price * Math.max(1, Number(payload.quantity) || 1);
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
  editingFavoriteId = null;
  confirmingSuggestionId = null;
  selectedItem = null;
  selections = {};
  elements.person.required = true;
  elements.quantity.value = '1';
  elements.notes.value = '';
  elements.editor.classList.add('hidden');
  elements.picker.classList.remove('hidden');
  elements.cancelEdit.classList.add('hidden');
  elements.submit.textContent = 'Add my drink';
  elements.formTitle.textContent = 'Choose something good';
  elements.saveFavorite.checked = false;
  elements.saveFavorite.disabled = false;
  elements.favoriteName.value = '';
  elements.favoriteName.required = false;
  elements.favoriteNameField.classList.add('hidden');
  elements.favoriteToggleTitle.textContent = 'Save as a usual';
  elements.search.value = '';
  if (!keepStatus) setFormStatus('', '');
  budgetBlocked = false;
  elements.budgetSelection.classList.add('hidden');
  elements.submit.disabled = false;
  renderDrinkList();
  renderBudget();
}

function orderDetails(order) {
  const parts = [order.size, order.sugar, order.ice, order.milk, order.temperature].filter(Boolean);
  if (order.toppings && order.toppings.length) parts.push(order.toppings.join(', '));
  if (order.notes) parts.push(order.notes);
  return parts.length ? parts.join(' · ') : 'Store defaults';
}

function favoritesForStore() {
  if (!menu) return [];
  return window.BobaFavorites.list(window.localStorage, menu.restaurant_id);
}

function updateFavoriteSaveControls() {
  if (!menu) return;
  const atLimit = favoritesForStore().length >= window.BobaFavorites.MAX_PER_STORE;
  if (editingFavoriteId) {
    elements.saveFavorite.checked = true;
    elements.saveFavorite.disabled = true;
    elements.favoriteName.required = true;
    elements.favoriteNameField.classList.remove('hidden');
    elements.favoriteToggleTitle.textContent = 'Editing this usual';
    elements.favoriteToggleHelp.textContent = 'Its drink, options, quantity, and notes will be replaced.';
    return;
  }
  elements.saveFavorite.disabled = atLimit;
  elements.favoriteName.required = elements.saveFavorite.checked;
  elements.favoriteNameField.classList.toggle('hidden', !elements.saveFavorite.checked);
  elements.favoriteToggleTitle.textContent = 'Save as a usual';
  elements.favoriteToggleHelp.textContent = atLimit
    ? `You already have ${window.BobaFavorites.MAX_PER_STORE} usuals for this store.`
    : 'Keep this drink and every option in this browser.';
}

function renderFavorites() {
  if (!menu) return;
  const favorites = favoritesForStore();
  elements.favoritesPanel.classList.toggle('hidden', !favorites.length);
  elements.favoritesCount.textContent = `${favorites.length}/${window.BobaFavorites.MAX_PER_STORE}`;
  elements.favoritesList.textContent = '';

  favorites.forEach((favorite) => {
    const check = window.BobaFavorites.compatibility(favorite, menu);
    const card = node('article', `favorite-card${check.ok ? '' : ' needs-review'}`);
    const copy = node('div', 'favorite-copy');
    copy.append(node('h4', null, favorite.name));
    copy.append(node('p', 'favorite-drink', favorite.drink));
    copy.append(node('p', 'favorite-detail muted', orderDetails(favorite)));
    if (!check.ok) copy.append(node('p', 'favorite-warning', check.reason));

    const primary = node('button', `btn ${check.ok ? 'primary' : 'ghost'} compact favorite-add`,
      addingFavoriteId === favorite.id ? 'Adding…'
        : (check.ok ? 'Add' : (check.item ? 'Review' : 'Unavailable')));
    primary.type = 'button';
    primary.disabled = Boolean(addingFavoriteId) || !check.item;
    if (check.ok) primary.addEventListener('click', () => addFavorite(favorite));
    else if (check.item) primary.addEventListener('click', () => editFavorite(favorite));

    const actions = node('div', 'favorite-actions');
    const edit = node('button', 'text-button', 'Edit');
    edit.type = 'button';
    edit.disabled = Boolean(addingFavoriteId) || !check.item;
    edit.addEventListener('click', () => editFavorite(favorite));
    actions.append(edit);
    const remove = node('button', 'text-button delete-favorite', 'Delete');
    remove.type = 'button';
    remove.disabled = Boolean(addingFavoriteId);
    remove.addEventListener('click', () => deleteFavorite(favorite));
    actions.append(remove);
    card.append(copy, primary, actions);
    elements.favoritesList.append(card);
  });
  updateFavoriteSaveControls();
}

function renderPopularPicks() {
  if (!menu || !leaderboard || !window.BobaLeaderboard) return;
  const picks = window.BobaLeaderboard.quickPicks(
    leaderboard.entries, menu.items, 5);
  window.BobaLeaderboard.renderQuickPicks(
    document, elements.popularPicks, elements.popularPicksList, picks, (item) => {
      chooseDrink(item);
      elements.editor.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
}

async function refreshLeaderboard() {
  if (!menu || !session) return;
  try {
    const data = await request(
      `/api/leaderboard?restaurant_id=${encodeURIComponent(session.restaurant_id)}`);
    leaderboard = data.leaderboard;
    renderPopularPicks();
  } catch (_error) {
    // Recommendations are optional; a failed analytics read must not block ordering.
    elements.popularPicks.hidden = true;
  }
}

async function addFavorite(favorite) {
  const person = elements.person.value.trim();
  if (!person) {
    setFavoriteStatus('Add your name first, then tap Add again.', 'err');
    elements.person.reportValidity();
    elements.person.focus();
    return;
  }
  const check = window.BobaFavorites.compatibility(favorite, menu);
  if (!check.ok) {
    setFavoriteStatus(check.reason, 'err');
    if (check.item) editFavorite(favorite);
    return;
  }
  const favoriteBudget = budgetState(person, payloadEstimate(check.item, check.payload), null);
  if (favoriteBudget && favoriteBudget.blocked) {
    setFavoriteStatus(
      `${favorite.name} would put ${person} at ${money(favoriteBudget.candidate)}, over the ${money(favoriteBudget.cap)} per-person limit.`,
      'err'
    );
    return;
  }

  addingFavoriteId = favorite.id;
  setFavoriteStatus(`Adding ${favorite.name}…`, 'busy');
  renderFavorites();
  try {
    const data = await request(`${apiBase}/orders`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...check.payload, person: person }),
    });
    setSession(data.session);
    rememberOrder(data.order.id, data.order_token);
    writeStorage(participantNameKey, person);
    writeStorage(nameKey, person);
    setFavoriteStatus(`${favorite.name} was added to the group order.`, 'ok');
    renderRoom();
    renderGroupOrders();
    refreshLeaderboard();
  } catch (error) {
    setFavoriteStatus(error.message, 'err');
    if (error.code === 'deadline_passed') refreshSession(false);
  } finally {
    addingFavoriteId = null;
    renderFavorites();
  }
}

function editFavorite(favorite) {
  const item = window.BobaFavorites.findItem(favorite, menu);
  if (!item) {
    setFavoriteStatus('That saved drink is no longer available on this store menu.', 'err');
    return;
  }
  editingOrderId = null;
  editingFavoriteId = favorite.id;
  elements.person.required = false;
  elements.quantity.value = String(favorite.quantity || 1);
  elements.notes.value = favorite.notes || '';
  elements.favoriteName.value = favorite.name;
  elements.cancelEdit.classList.remove('hidden');
  elements.submit.textContent = 'Save usual';
  chooseDrink(item, favorite);
  elements.orderCard.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

function deleteFavorite(favorite) {
  if (!window.confirm(`Delete “${favorite.name}” from My usuals?`)) return;
  try {
    window.BobaFavorites.remove(window.localStorage, favorite.id);
    if (editingFavoriteId === favorite.id) resetEditor();
    setFavoriteStatus(`${favorite.name} was deleted.`, 'ok');
    renderFavorites();
  } catch (_error) {
    setFavoriteStatus('This usual could not be deleted from browser storage.', 'err');
  }
}

function saveFavorite(payload, existing) {
  const favorite = window.BobaFavorites.fromOrder(
    elements.favoriteName.value, payload, menu.restaurant_id, selectedItem, existing
  );
  return window.BobaFavorites.save(window.localStorage, favorite);
}

function renderGroupOrders() {
  if (!session) return;
  renderSuggestions();
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
    const cap = Number(session.budget_cap);
    const personOverBudget = Number.isFinite(cap) && cap > 0
      && submittedTotal(order.person, null) > cap + 0.0001;
    const card = node('article', `group-order${ownership ? ' own-order' : ''}${personOverBudget ? ' over-budget' : ''}`);
    const copy = node('div');
    const person = node('p', 'order-person', order.person);
    if (ownership) {
      person.append(document.createTextNode(' '));
      person.append(node('span', 'yours-badge', 'Yours'));
    }
    if (order.updated_at && order.created_at && order.updated_at !== order.created_at) {
      person.append(document.createTextNode(' '));
      person.append(node('span', 'edited-badge', 'Edited'));
    }
    copy.append(person);
    copy.append(node('h3', null, order.drink));
    copy.append(node('p', 'group-order-detail', orderDetails(order)));
    const cost = node('div', 'group-order-cost');
    if (order.quantity > 1) cost.append(node('span', 'group-order-qty', `×${order.quantity}`));
    if (order.estimated_total != null) cost.append(node('strong', null, money(order.estimated_total)));
    if (personOverBudget) cost.append(node('span', 'budget-badge', 'Over budget'));
    card.append(copy, cost);
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

/* --- expected orders: confirm, change, or drop a pending entry ------------- */
// A loaded template pre-fills the room with pending entries, never submitted
// orders: someone who is out that day must be able to walk away without a
// drink arriving in their name. Confirming converts one entry into a real
// order (with its own edit token); changing confirms edited values instead.

function renderSuggestions() {
  if (!elements.suggestionsCard || !elements.suggestionsList) return;
  const suggestions = (session && session.suggestions) || [];
  elements.suggestionsCard.hidden = !suggestions.length;
  elements.suggestionsList.textContent = '';
  suggestions.forEach((suggestion) => {
    const card = node('article', 'group-order');
    const copy = node('div');
    copy.append(node('p', 'order-person', suggestion.person));
    copy.append(node('h3', null, suggestion.drink));
    copy.append(node('p', 'group-order-detail', orderDetails(suggestion)));
    const cost = node('div', 'group-order-cost');
    if (suggestion.quantity > 1) cost.append(node('span', 'group-order-qty', `×${suggestion.quantity}`));
    card.append(copy, cost);
    if (session.accepting_orders) {
      const actions = node('div', 'group-order-actions');
      const confirm = node('button', 'btn primary compact', 'Confirm ✓');
      confirm.type = 'button';
      confirm.setAttribute('aria-label', `Confirm ${suggestion.drink} for ${suggestion.person}`);
      confirm.addEventListener('click', () => confirmSuggestion(suggestion.id, {}, confirm));
      const change = node('button', 'text-button', 'Change');
      change.type = 'button';
      change.addEventListener('click', () => changeSuggestion(suggestion));
      const drop = node('button', 'text-button delete-order', 'Not me');
      drop.type = 'button';
      drop.setAttribute('aria-label', `Drop the expected ${suggestion.drink} for ${suggestion.person}`);
      drop.addEventListener('click', () => dismissSuggestion(suggestion, drop));
      actions.append(confirm, change, drop);
      card.append(actions);
    }
    elements.suggestionsList.append(card);
  });
}

async function confirmSuggestion(suggestionId, payload, button) {
  const suggestion = ((session && session.suggestions) || [])
    .find((entry) => entry.id === suggestionId);
  if (!suggestion) {
    setFormStatus('That expected order is no longer waiting.', 'err');
    return;
  }
  if (button) button.disabled = true;
  setFormStatus(`Confirming ${suggestion.drink} for ${suggestion.person}…`, 'busy');
  try {
    const data = await request(
      `${apiBase}/suggestions/${encodeURIComponent(suggestionId)}/confirm`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
    setSession(data.session);
    rememberOrder(data.order.id, data.order_token);
    let savedFavorite = null;
    if (elements.saveFavorite.checked && payload && payload.drink) {
      try {
        savedFavorite = saveFavorite(payload, null);
      } catch (_error) {
        savedFavorite = null;
      }
    }
    resetEditor({ keepStatus: true });
    let message = `${data.order.drink} confirmed for ${data.order.person} — `
      + 'it is now a submitted order you can still edit or remove.';
    if (savedFavorite) message += ` ${savedFavorite.name} is now in My usuals.`;
    setFormStatus(message, 'ok');
    renderRoom();
    renderFavorites();
    renderGroupOrders();
    refreshLeaderboard();
  } catch (error) {
    setFormStatus(error.message, 'err');
    if (button) button.disabled = false;
    if (error.code === 'deadline_passed') refreshSession(false);
  }
}

function changeSuggestion(suggestion) {
  const item = menu.items.find((candidate) => candidate.name === suggestion.drink);
  if (!item) {
    setFormStatus(`“${suggestion.drink}” is not on this menu right now, so it cannot be changed here.`, 'err');
    return;
  }
  editingFavoriteId = null;
  editingOrderId = null;
  confirmingSuggestionId = suggestion.id;
  elements.person.required = true;
  elements.person.value = suggestion.person;
  elements.quantity.value = String(suggestion.quantity || 1);
  elements.notes.value = suggestion.notes || '';
  elements.cancelEdit.classList.remove('hidden');
  elements.submit.textContent = 'Confirm with changes';
  elements.saveFavorite.checked = false;
  elements.favoriteName.value = '';
  chooseDrink(item, suggestion);
  elements.orderCard.scrollIntoView({ behavior: 'smooth', block: 'start' });
}

async function dismissSuggestion(suggestion, button) {
  if (!window.confirm(
    `Drop the expected ${suggestion.drink} for ${suggestion.person}? `
    + 'They can still add a drink themselves.')) return;
  button.disabled = true;
  try {
    const data = await request(
      `${apiBase}/suggestions/${encodeURIComponent(suggestion.id)}`, { method: 'DELETE' });
    setSession(data.session);
    renderRoom();
    renderGroupOrders();
  } catch (error) {
    window.alert(error.message);
    button.disabled = false;
  }
}

function editOrder(order) {
  const item = menu.items.find((candidate) => candidate.name === order.drink);
  if (!item) {
    setFormStatus('That drink is no longer on this captured menu, so it cannot be edited here.', 'err');
    return;
  }
  editingFavoriteId = null;
  editingOrderId = order.id;
  elements.person.required = true;
  elements.person.value = order.person;
  elements.quantity.value = String(order.quantity || 1);
  elements.notes.value = order.notes || '';
  elements.cancelEdit.classList.remove('hidden');
  elements.submit.textContent = 'Save changes';
  elements.saveFavorite.checked = false;
  elements.favoriteName.value = '';
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
    setSession(data.session);
    forgetOrder(order.id);
    if (editingOrderId === order.id) resetEditor();
    renderRoom();
    renderGroupOrders();
    refreshLeaderboard();
  } catch (error) {
    window.alert(error.message);
    if (error.code === 'deadline_passed') refreshSession(false);
  }
}

async function submitOrder(event) {
  event.preventDefault();
  if (!selectedItem) return;
  if (editingFavoriteId) {
    if (!elements.quantity.reportValidity() || !elements.favoriteName.reportValidity()
        || !validateSelections()) return;
    const existing = favoritesForStore().find((favorite) => favorite.id === editingFavoriteId);
    if (!existing) {
      setFormStatus('That usual is no longer saved in this browser.', 'err');
      return;
    }
    try {
      const saved = saveFavorite(buildPayload(), existing);
      resetEditor({ keepStatus: true });
      setFormStatus(`${saved.name} was updated.`, 'ok');
      renderFavorites();
    } catch (error) {
      setFormStatus(error.message || 'This usual could not be saved.', 'err');
    }
    return;
  }
  if (confirmingSuggestionId && !editingOrderId) {
    // Change-and-confirm: the editor holds edits to a pending expected
    // order, and submitting confirms those values as a real order.
    if (!elements.form.reportValidity() || !validateSelections()) return;
    updateEstimate();
    if (budgetBlocked) {
      setFormStatus('This selection is over the per-person budget. Reduce the price or quantity before submitting.', 'err');
      return;
    }
    const payload = buildPayload();
    const suggestionId = confirmingSuggestionId;
    confirmingSuggestionId = null;
    elements.submit.disabled = true;
    const person = elements.person.value.trim();
    writeStorage(participantNameKey, person);
    writeStorage(nameKey, person);
    await confirmSuggestion(suggestionId, payload, null);
    return;
  }
  if (!elements.form.reportValidity() || !validateSelections()) return;
  updateEstimate();
  if (budgetBlocked) {
    setFormStatus('This selection is over the per-person budget. Reduce the price or quantity before submitting.', 'err');
    return;
  }

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
    setSession(data.session);
    if (!orderId) rememberOrder(data.order.id, data.order_token);
    const person = elements.person.value.trim();
    writeStorage(participantNameKey, person);
    writeStorage(nameKey, person);
    let savedFavorite = null;
    let favoriteError = '';
    if (elements.saveFavorite.checked) {
      try {
        savedFavorite = saveFavorite(payload, null);
      } catch (error) {
        favoriteError = error.message || 'it could not be saved as a usual';
      }
    }
    resetEditor({ keepStatus: true });
    let message = orderId ? 'Your drink was updated.' : 'Drink added. Pick another if you would like!';
    if (savedFavorite) message += ` ${savedFavorite.name} is now in My usuals.`;
    if (favoriteError) message += ` The drink was added, but ${favoriteError}`;
    setFormStatus(message, favoriteError ? 'err' : 'ok');
    renderRoom();
    renderFavorites();
    renderGroupOrders();
    refreshLeaderboard();
    elements.search.focus();
  } catch (error) {
    setFormStatus(error.message, 'err');
    if (error.code === 'deadline_passed' || error.code === 'budget_exceeded') refreshSession(false);
    if (error.code === 'sold_out' || error.code === 'unavailable') {
      // Availability changed since this page loaded: the picker is stale.
      // The message above already names alternatives; reload the menu so
      // the sold-out choice stops being offered.
      refreshSession(false);
      refreshMenu();
    }
  } finally {
    elements.submit.disabled = budgetBlocked;
  }
}

async function refreshMenu() {
  if (!session) return;
  try {
    const menuData = await request(
      `/api/menu?restaurant_id=${encodeURIComponent(session.restaurant_id)}`);
    if (!menuData.menu || menuData.menu.restaurant_id !== session.restaurant_id) return;
    menu = menuData.menu;
    if (selectedItem && !menu.items.some((item) => item.name === selectedItem.name)) {
      resetEditor({ keepStatus: true });
    }
    renderCategories();
    renderDrinkList();
    renderFavorites();
    renderPopularPicks();
  } catch (_error) {
    // Keep the current picker; the server message already names alternatives.
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
    setSession(data.session);
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
  elements.statusBoard.classList.add('hidden');
  document.getElementById('group-orders-card').classList.add('hidden');
  elements.roomTitle.textContent = 'Group order unavailable';
  elements.roomSubtitle.textContent = 'Ask the organizer for a fresh link.';
}

elements.search.addEventListener('input', renderDrinkList);
elements.surpriseMe.addEventListener('click', surpriseMe);
elements.rerollDrink.addEventListener('click', surpriseMe);
elements.editDrink.addEventListener('click', () => {
  const firstModifier = elements.modifiers.querySelector('select, input:not(:disabled)');
  const firstEditableControl = firstModifier || elements.quantity;
  firstEditableControl.scrollIntoView({ behavior: 'smooth', block: 'center' });
  firstEditableControl.focus();
});
elements.removeDrink.addEventListener('click', () => {
  resetEditor();
  elements.search.focus();
});
elements.cancelEdit.addEventListener('click', resetEditor);
elements.quantity.addEventListener('input', updateEstimate);
elements.person.addEventListener('input', () => {
  renderBudget();
  if (selectedItem) updateEstimate();
});
elements.saveFavorite.addEventListener('change', () => {
  if (elements.saveFavorite.checked && !elements.favoriteName.value.trim() && selectedItem) {
    elements.favoriteName.value = selectedItem.name;
  }
  updateFavoriteSaveControls();
  if (elements.saveFavorite.checked) elements.favoriteName.focus();
});
elements.form.addEventListener('submit', submitOrder);
elements.refreshOrders.addEventListener('click', () => refreshSession(true));

request(apiBase)
  .then(async (roomData) => {
    // Capture the server clock as soon as this response arrives; loading the
    // menu afterward should not make the countdown drift late.
    setSession(roomData.session);
    const restaurantId = roomData.session.restaurant_id;
    const menuData = await request(
      `/api/menu?restaurant_id=${encodeURIComponent(restaurantId)}`);
    if (menuData.menu.restaurant_id !== restaurantId) {
      throw new Error('The selected store menu could not be verified.');
    }
    return menuData;
  })
  .then((menuData) => {
    menu = menuData.menu;
    if (!menu || !menu.items || !menu.items.length) {
      throw new Error('The store menu is unavailable right now.');
    }
    const savedName = readStorage(participantNameKey, '') || readStorage(nameKey, '');
    if (typeof savedName === 'string') elements.person.value = savedName;
    renderRoom();
    renderCategories();
    renderDrinkList();
    renderFavorites();
    renderGroupOrders();
    refreshLeaderboard();
    window.setInterval(() => {
      if (!document.hidden) refreshSession(false);
    }, 15000);
  })
  .catch((error) => showFatal(error.message));

window.setInterval(tickDeadline, 1000);
